from typing import Union

from .geometry import Rectangle, Cuboid

class PrinterBoxes:

    def __init__(self,
                 printbed: Cuboid,
                 printhead: Rectangle,
                 gantry: Rectangle,
                 gantry_x_oriented: bool,
                 gantry_height: float,
                 padding: float,
                 objects: list[Cuboid] = None) -> None:
        self.printbed = printbed
        self.printhead = printhead
        self.gantry = gantry
        self.gantry_x_oriented = gantry_x_oriented
        self.gantry_height = gantry_height
        self.padding = padding
        self.objects = objects or []
    
    def add_object(self, new_object: Cuboid) -> None:
        """Add an object, like a finished print job, to be considered in the
        future.
        """
        self.objects.append(new_object)

    def clear_objects(self) -> None:
        """Empty the list of print objects to keep track of"""
        self.objects.clear()

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
        
    def gantry_space(
        self, print_object: Union[Rectangle, Cuboid]
    ) -> Cuboid:
        """Return collision box for the gantry
        This includes all the space that the gantry could move in when printing
        the given object.
        """
        if self.gantry_x_oriented:
            return Cuboid(
                self.gantry.x,
                print_object.y + self.gantry.y,
                self.gantry_height,
                self.gantry.max_x,
                print_object.max_y + self.gantry.max_y,
                float('inf'))  # Make the box extend ALL THE WAY to the top
        else:
            return Cuboid(
                print_object.x + self.gantry.x,
                self.gantry.y,
                self.gantry_height,
                print_object.max_x + self.gantry.max_x,
                self.gantry.max_y,
                float('inf'))

    def fits(self, new_object: Cuboid) -> bool:
        """Return True if the object is situated within the printer boundaries
        """
        return self.printbed.intersection(new_object) == new_object
