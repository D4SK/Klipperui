import copy
from typing import Optional, Union

from .geometry import Rectangle, Cuboid
from .printerboxes import PrinterBoxes


class BoxCollision:
    """Contains collision detection and offset search.
    At this level all objects are abstracted to boxes from the geometry module.
    """

    def __init__(self, printer: PrinterBoxes):
        self.printer = printer

    def replicate_with_objects(
        self, objects: list[Cuboid], expand: bool = True
    ) -> "BoxCollision":
        """Return a new collision instance but with an expanded (or replaced)
        object list.
        """
        new_printer = copy.copy(self.printer)
        if expand:
            objects = copy.copy(self.printer.objects) + objects
        new_printer.objects = objects
        return BoxCollision(new_printer)

    def object_collides(self, new_object: Cuboid) -> bool:
        """Return True if printing this object would cause a collision, False
        if it can be safely printed (without any offset).

        new_object should be a Cuboid outlining the space needed by the object.
        """
        if not self.printer.fits(new_object):
            # Doesn't fit in the printer at all!
            return True

        mv_printhead = self.printer.printhead_space(new_object)
        mv_gantry = self.printer.gantry_space(new_object)
        padding = self.printer.padding
        for obj in self.printer.objects:
            if (new_object.collides_with(obj, padding) or
                mv_printhead.collides_with(obj.projection(), padding) or
                mv_gantry.collides_with(obj, padding)):
                return True
        return False

    def find_offset(self, object_cuboid: Cuboid) -> Optional[tuple[float, float]]:
        """For a given object search for an offset where it can be printed

        If successful, a 2-tuple containing the x and y offset is returned,
        otherwise returns None.
        """
        if not self.object_collides(object_cuboid):
            # Fits without any offset
            return (0, 0)

        new_object = object_cuboid.projection()
        printbed = self.printer.printbed
        if (object_cuboid.width > printbed.width or
            object_cuboid.height > printbed.height or
            object_cuboid.z_height > printbed.z_height):
            # Object is larger than printer, not possible
            return None

        centering_offset: tuple[float, float] = (0, 0)
        if not self.printer.fits(object_cuboid):
            # Object is not within printer bounds,
            # but we can fix that by centering it
            centering_offset = self.get_centering_offset(new_object)
            new_object = new_object.translate(*centering_offset)
            object_cuboid = object_cuboid.translate(*centering_offset, 0)
            if not self.printer.fits(object_cuboid):
                # If it still doesn't fit, the Z-Axis is at fault
                return None

        if not self.object_collides(object_cuboid):
            # Only centering was needed
            return centering_offset

        mv_printhead = self.printer.printhead_space(new_object)
        # Rectangle that represents the actual required space to print
        needed_space = mv_printhead.grow(self.printer.padding)

        gantry_blocked = self.get_gantry_collisions(new_object)
        object_boxes = [obj.projection() for obj in self.printer.objects]
        side_offsets = self._get_side_offsets(new_object, needed_space,
                                              object_boxes)

        offset = self._iterate_offset(new_object, needed_space, gantry_blocked,
                                      object_boxes, side_offsets)
        if offset:
            # Merge with the initial centering offset, if any
            return (offset[0] + centering_offset[0],
                    offset[1] + centering_offset[1])
        return None

    def get_centering_offset(
        self, new_object: Union[Rectangle, Cuboid]
    ) -> tuple[float, float]:
        """Return an offset that centers new_object on the printbed
        Works with Rectangles as well as Cuboids.
        """
        return (
            self.printer.printbed.width/2 - new_object.width/2 - new_object.x,
            self.printer.printbed.height/2 - new_object.height/2 - new_object.y)

    def get_gantry_collisions(self,
        new_object: Optional[Union[Rectangle, Cuboid]] = None
    ) -> list[Rectangle]:
        """Return a list of stripes (Rectangles) parallel to the gantry that
        cannot be moved into because the gantry would collide with existing
        objects.
        These areas account for the size of the gantry and also add padding.

        If Rectangle or Cuboid is provided for new_object, the distance between
        the stripes is always large enough to accomodate that object.
        """
        if new_object:
            min_space = (new_object.height if self.printer.gantry_x_oriented
                         else new_object.width)
        else:
            min_space = 0

        gantry = self.printer.gantry
        padding = self.printer.padding
        ranges = []
        for obj in self.printer.objects:
            if obj.max_z + padding > self.printer.gantry_height:
                if self.printer.gantry_x_oriented:
                    # Pad obj.y with gantry.max_y, because the gantry will
                    # approach from the outsides
                    new_range = [obj.y - gantry.max_y - padding,
                                 obj.max_y - gantry.y + padding]
                else:
                    new_range = [obj.x - gantry.max_x - padding,
                                 obj.max_x - gantry.x + padding]
                ranges.append(new_range)
        ranges = self._condense_ranges(ranges, min_space)
        if self.printer.gantry_x_oriented:
            boxes = [Rectangle(gantry.x, y, gantry.max_x, max_y)
                     for y, max_y in ranges]
        else:
            boxes = [Rectangle(x, gantry.y, max_x, gantry.max_y)
                     for x, max_x in ranges]
        return boxes

    @staticmethod
    def _condense_ranges(
        ranges: list[list[float]], min_space: float = 0
    ) -> list[list[float]]:
        """Consolidate ranges so that none of them overlap/border each other
        ranges must be a 2-dimensional list with shape (n, 2).
        WARNING: This function may mutate the ranges list as well as its
        contained lists!"""
        if not ranges:
            return []
        ranges.sort()
        condensed = ranges[:1]
        for r in ranges[1:]:
            prev = condensed[-1]
            if r[0] <= prev[1] + min_space:
                if r[1] > prev[1]:
                    prev[1] = r[1]
            else:
                condensed.append(r)
        return condensed

    def _get_side_offsets(
        self,
        new_object: Union[Rectangle, Cuboid],
        space: Rectangle,
        boxes: list[Rectangle]
    ) -> list[float]:
        """Return a list of all possible side offsets to be checked.
        The list is sorted by absolute values.
        Not all sides need to be accounted for: Upper sides that lie below the
        initial starting space don't need to be checked as there is always an
        opposing, closer side.
        """
        x_oriented = self.printer.gantry_x_oriented
        o_min, o_max = new_object.get_range_for_axis(not x_oriented)
        space_min, space_max = space.get_range_for_axis(
                not x_oriented)
        printer_min, printer_max = self.printer.printbed.get_range_for_axis(
                not x_oriented)
        # Put offsets in a set initially to remove any duplicates
        # Add 0 manually to search without any side offset first
        unique_offsets: set[float] = {0}
        for r in boxes:
            r_min, r_max = r.get_range_for_axis(not x_oriented)
            if r_max > space_min:
                # Upper bound counts
                offset = r_max - space_min
                if o_max + offset <= printer_max:
                    # Upper bound fits
                    unique_offsets.add(offset)
            if r_min < space_max:
                # Lower bound counts
                offset = r_min - space_max
                if o_min + offset >= printer_min:
                    # Lower bound fits
                    unique_offsets.add(offset)
        offsets = list(unique_offsets)
        # Sort using abs, meaning from the middle out
        offsets.sort(key=abs)
        return offsets

    def _iterate_offset(
        self,
        new_object: Rectangle,
        needed_space: Rectangle,
        gantry_blocked: list[Rectangle],
        objects: list[Rectangle],
        side_offsets: list[float]
    ) -> Optional[tuple[float, float]]:
        """Iterate over all possible offsets that were found for the secondary
        axis (the one parallel to the gantry) and execute a sweep for all of
        them.

        Parameters:
        new_object  Rectangle representing the space needed by the print object
        needed_space Rectangle similar to new_object but with expanded size to
                    accommodate the print head and includes padding
        gantry_blocked List of Rectangles specifying where new_object can't be
                    at because the gantry would then interfer with other
                    objects.
        objects     List of Rectangles representing all currently present print
                    objects
        side_offsets List of offsets to iterate over

        Returns:
        The offset where the print object fits as a 2-tuple.
        If no space was found, None is returned.
        """
        for offset in side_offsets:
            offsets = (offset * self.printer.gantry_x_oriented,
                       offset * (not self.printer.gantry_x_oriented))
            result = self._sweep(new_object.translate(*offsets),
                                 needed_space.translate(*offsets),
                                 gantry_blocked, objects)
            if result is not None:
                # Merge both offsets
                return (result[0] + offsets[0], result[1] + offsets[1])
        return None

    def _sweep(
        self,
        new_object: Rectangle,
        space: Rectangle,
        gantry_blocked: list[Rectangle],
        objects: list[Rectangle]
    ) -> Optional[list[float]]:
        """The main, innermost searching function.
        Scans along the main axis (the one perpendicular to the gantry) by
        iteratively increasing the offset just enough to clear all objects we
        have collided with so far by doing that. Iteration stops when on both
        sides the printer boundaries are met or a valid spot is found.

        Parameters:
        new_object and space are like in _iterate_offset, but with the current
        side offset applied.

        Returns:
        The offset where we found enough space as a 2-long list. Only the
        component of the main axis is set, the other will always be 0. If no
        space was found, None is returned.
        """
        x_oriented = self.printer.gantry_x_oriented
        offset: list[float] = [0, 0]
        # Set to True when reaching the printer boundaries without success
        reached_end_min = reached_end_max = False

        printer_min, printer_max = self.printer.printbed.get_range_for_axis(
            x_oriented)
        printhead_min, printhead_max = self.printer.printhead.get_range_for_axis(
            x_oriented)

        space_min, space_max = space.get_range_for_axis(x_oriented)
        # These are needed to check if the offset object fits on the printbed
        o_min, o_max = new_object.get_range_for_axis(x_oriented)

        # Positions where to move to next:
        # next_min_pos specifies where to move the upper edge down to
        # next_max_pos specifies where to move the lower edge up to
        next_min_pos, next_max_pos = space_max, space_min

        colliding = self._get_colliding_objects(space, objects)
        gantry_colliding = self._get_colliding_objects(
                new_object, gantry_blocked)
        while colliding or gantry_colliding:
            # Find furthest colliding object to clear in both directions
            for r in gantry_colliding:
                r_min, r_max = r.get_range_for_axis(x_oriented)
                # Change from comparing print object edges to needed space edges
                r_min += printhead_max + self.printer.padding
                r_max += printhead_min - self.printer.padding
                if r_min < next_min_pos:
                    next_min_pos = r_min
                if r_max > next_max_pos:
                    next_max_pos = r_max
            for r in colliding:
                r_min, r_max = r.get_range_for_axis(x_oriented)
                if r_min < next_min_pos:
                    next_min_pos = r_min
                if r_max > next_max_pos:
                    next_max_pos = r_max

            # Set next offset to the closest side that still fits
            neg_offset = next_min_pos - space_max
            pos_offset = next_max_pos - space_min
            reached_end_min = o_min + neg_offset < printer_min
            reached_end_max = o_max + pos_offset > printer_max
            if ((pos_offset <= -neg_offset or reached_end_min)
                and not reached_end_max):
                offset[x_oriented] = pos_offset
            elif not reached_end_min:
                offset[x_oriented] = neg_offset
            else:  # Reached both ends without success
                return None

            colliding = self._get_colliding_objects(
                    space.translate(*offset), objects)
            gantry_colliding = self._get_colliding_objects(
                    new_object.translate(*offset), gantry_blocked)

        return offset

    @staticmethod
    def _get_colliding_objects(
        one: Rectangle, other: list[Rectangle]
    ) -> list[Rectangle]:
        """Return a list of all objects in other that collide with one"""
        return [r for r in other if one.collides_with(r)]
