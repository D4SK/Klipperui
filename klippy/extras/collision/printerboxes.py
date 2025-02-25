from typing import Union, Optional

from .geometry import Rectangle, Cuboid

class PrinterBoxes:

    def __init__(self,
                 printbed: Cuboid,
                 printhead: Union[Rectangle, list[Cuboid]],
                 gantry: Rectangle,
                 gantry_x_oriented: bool,
                 gantry_height: float,
                 padding: float,
                 static_objects: Optional[list[Cuboid]] = None) -> None:
        self.printbed = printbed
        # Gantry size relative to nozzle, including height
        if gantry_x_oriented:
            gantry_rel = Cuboid(-printbed.width, gantry.y, gantry_height,
                                printbed.width, gantry.max_y, float('inf'))
        else:
            gantry_rel = Cuboid(gantry.x, -printbed.height, gantry_height,
                                gantry.max_x, printbed.height, float('inf'))
        if isinstance(printhead, Rectangle):
            self.printhead = printhead
            printhead_cuboid = Cuboid(printhead.x, printhead.y, 0,
                printhead.max_x, printhead.max_y, float('inf'))
            self.printhead_parts = [printhead_cuboid, gantry_rel]
        else:
            self.printhead = self.combined_printhead_space(printhead)
            self.printhead_parts = printhead + [gantry_rel]
            self.printhead_parts.sort(key=lambda p: p.z)

        self.gantry = gantry
        self.gantry_x_oriented = gantry_x_oriented
        self.gantry_height = gantry_height
        self.padding = padding
        self.objects = static_objects or []
        self.n_static = len(self.objects)

    def combined_printhead_space(self, parts: list[Cuboid]) -> Rectangle:
        """Create one Rectangle that encompasses all printhead parts"""
        if not parts:
            return Rectangle(*[0]*4)
        return Rectangle(
            min(iter(p.x for p in parts)),
            min(iter(p.y for p in parts)),
            max(iter(p.max_x for p in parts)),
            max(iter(p.max_y for p in parts)))

    def add_object(self, new_object: Cuboid) -> None:
        """Add an object, like a finished print job, to be considered in the
        future.
        """
        self.objects.append(new_object)

    def clear_objects(self) -> None:
        """Empty the list of print objects to keep track of.
        Only static objects are kept.
        """
        del self.objects[self.n_static:]

    def printhead_space(
        self, print_object: Union[Rectangle, Cuboid]
    ) -> Rectangle:
        """Return collision box for the printhead
        This contains all the space that the printhead could move into while
        printing the given object.
        """
        return Rectangle(
            print_object.x + self.printhead.x,
            print_object.y + self.printhead.y,
            print_object.max_x + self.printhead.max_x,
            print_object.max_y + self.printhead.max_y)
        
    def gantry_space(self, print_object: Cuboid) -> Cuboid:
        """Return collision box for the gantry
        This includes all the space that the gantry could move in when printing
        the given object.
        """
        if self.gantry_x_oriented:
            return Cuboid(
                self.gantry.x,
                print_object.y + self.gantry.y,
                self.gantry_height + print_object.z,
                self.gantry.max_x,
                print_object.max_y + self.gantry.max_y,
                float('inf'))  # Make the box extend ALL THE WAY to the top
        else:
            return Cuboid(
                print_object.x + self.gantry.x,
                self.gantry.y,
                self.gantry_height + print_object.z,
                print_object.max_x + self.gantry.max_x,
                self.gantry.max_y,
                float('inf'))

    def fits(self, new_object: Cuboid) -> bool:
        """Return True if the object is situated within the printer boundaries
        NOTE: This assumes that new_object has nonzero volume
        """
        return self.printbed.intersection(new_object) == new_object

    def cuboid_collides(self, new_object: Cuboid) -> bool:
        """Return True if printing new_object would cause a collision with the
        current objects.
        """
        mv_printhead = self.printhead_space(new_object)
        mv_gantry = self.gantry_space(new_object)
        padding = self.padding
        for obj in self.objects:
            if (new_object.collides_with(obj, padding) or
                mv_printhead.collides_with(obj.projection(), padding) or
                mv_gantry.collides_with(obj, padding)):
                return True
        return False
