import ast
import configparser
import logging
from typing import Optional, Union, Any

from ..gcode_metadata.base_parser import BaseParser
from ..virtual_sdcard import PrintJob

from .collision_check import BoxCollision
from .geometry import Rectangle, Cuboid
from .pathfinder import PathFinderManager
from .printerboxes import PrinterBoxes


# Default padding, if not specified in config, in mm
DEFAULT_PADDING = 5

class CollisionInterface:

    def __init__(self, config) -> None:
        self._config = config
        self.printer = config.get_printer()

        # Read config
        self.continuous_printing: bool = config.getboolean(
                'continuous_printing', False)
        self.reposition: bool = config.getboolean('reposition', False)

        #TODO: Different printbed sizes for offset search and path finding
        self.configuration = {
            "printhead": self._read_printhead(),
            "gantry_x_oriented": config.getchoice("gantry_orientation",
                                                  {"x": True, "y": False}),
            "gantry_height": config.getfloat("gantry_z_min"),
            "padding": config.getfloat("padding", DEFAULT_PADDING),
            "static_objects": self._read_static_objects(),
            "gantry_xy_min": config.getfloat("gantry_xy_min"),
            "gantry_xy_max": config.getfloat("gantry_xy_max")}

        self.printer.register_event_handler("klippy:connect", self.handle_connect)

    def handle_connect(self) -> None:
        """Get printbed size later and update gantry"""
        conf = self.configuration
        printbed = self._read_printbed()
        gantry = self._read_gantry(conf, printbed)
        self.dimensions = PrinterBoxes(
            printbed,
            conf["printhead"],
            gantry,
            conf["gantry_x_oriented"],
            conf["gantry_height"],
            conf["padding"],
            conf["static_objects"])
        self.collision = BoxCollision(self.dimensions)
        self.pathfinder = PathFinderManager(self.dimensions)
        self.printer.register_event_handler(
                "virtual_sdcard:print_end", self._handle_print_end)

    def _read_static_objects(self) -> list[Cuboid]:
        value = self._config.get("static_objects", None)
        if value is None:
            return []
        try:
            figures = ast.literal_eval(value)
            objects = []
            for e in figures:
                for x in e:
                    if not isinstance(x, (int, float)):
                        raise TypeError("All values must be numbers")
                objects.append(Cuboid(*e))
        except (ValueError, TypeError, SyntaxError, MemoryError,
                RecursionError, KeyError):
            raise configparser.Error(
                "Invalid object list: Should be a sequence of 6-tuples")
        return objects

    def _read_printbed(self) -> Cuboid:
        """Read the printer size from the config and return it as a Cuboid"""
        rails = self.printer.objects['toolhead'].kin.rails
        min_ = [rail.print_area_min for rail in rails]
        max_ = [rail.print_area_max for rail in rails]
        return Cuboid(*min_, *max_)

    def _read_printhead(self) -> Union[Rectangle, list[Cuboid]]:
        """Return a Rectangle representing the size of the print head
        as viewed from above. The printing nozzle would be at (0, 0).
        """
        parts_conf = self._config.get("printhead_parts", None)
        if parts_conf is None:
            return Rectangle(
                -self._config.getfloat("printhead_x_min"),
                -self._config.getfloat("printhead_y_min"),
                self._config.getfloat("printhead_x_max"),
                self._config.getfloat("printhead_y_max"))

        try:
            values = ast.literal_eval(parts_conf)
            parts = []
            for v in values:
                for x in v:
                    if not isinstance(x, (int, float)):
                        raise TypeError("All values must be numbers")
                if v[4] < 0:
                    raise configparser.Error(
                        "Printhead parts cannot be lower than the nozzle"
                        "(negative starting height)")
                parts.append(Cuboid(-v[0], -v[1], v[4],
                                    v[2], v[3], float('inf')))
        except (ValueError, TypeError, SyntaxError, MemoryError,
                RecursionError, LookupError):
            raise configparser.Error(
                "Invalid parts list: Should be a sequence of 5-tuples")
        return parts

    def _read_gantry(self, conf: dict[str, Any], printbed: Cuboid) -> Rectangle:
        """Return a Rectangle representing the size of the gantry as viewed
        from above as well as if it is oriented parallel to the X-Axis or not.
        The printing nozzle would be at the 0-coordinate on the other axis.
        """
        xy_min = conf["gantry_xy_min"]
        xy_max = conf["gantry_xy_max"]
        if conf["gantry_x_oriented"]:
            gantry = Rectangle(printbed.x, -xy_min,
                               printbed.width, xy_max)
        else:
            gantry = Rectangle(-xy_min, printbed.y,
                               xy_max, printbed.height)
        return gantry

    def check_available(
        self, printjob: PrintJob
    ) -> tuple[bool, Optional[tuple[float, float]]]:
        if not self.continuous_printing:
            return not self.dimensions.objects, (0, 0)
        try:
            available = not self.printjob_collides(printjob)
            offset: Optional[tuple[float, float]] = (0, 0)
            if not available and self.reposition:
                offset = self.find_offset(printjob)
                available = offset is not None
            return available, offset
        except MissingMetadataError:
            return False, None

    def predict_availability(
        self, printjob: Union[PrintJob, BaseParser], queue: list[PrintJob]
    ) -> bool:
        """Look in the future and predict if printjob will be printable without
        collisions if everything in queue is printed first.

        printjob can be either a PrintJob object or metadata object.
        """
        offset = (0, 0)
        try:
            object_queue = [self.printjob_to_cuboid(pj) for pj in queue]
            if isinstance(printjob, PrintJob):
                cuboid = self.printjob_to_cuboid(printjob)
            else:
                cuboid = self.metadata_to_cuboid(printjob)
        except MissingMetadataError:
            return False, offset
        predict_collision = self.collision.replicate_with_objects(object_queue)
        available = not predict_collision.object_collides(cuboid)
        if not available and self.reposition:
            offset = predict_collision.find_offset(cuboid)
            available = offset is not None
        return available, offset

    def _handle_print_end(self, _printjobs, printjob: PrintJob) -> None:
        try:
            self.add_printjob(printjob)
        except MissingMetadataError:
            logging.warning("Collision: Couldn't read print dimensions for"
                    + printjob.path)
            # Save as entire printbed to force collision with all other prints
            self.dimensions.add_object(self.dimensions.printbed)

    ##
    ## Conversion functions
    ##
    def metadata_to_cuboid(self, metadata: BaseParser) -> Cuboid:
        """From a gcode metadata object return a Cuboid of the size of the print
        object. If the size isn't specified in the metadata, a ValueError is
        raised.
        """
        dimensions = metadata.get_print_dimensions()
        for e in dimensions.values():
            if e is None:
                raise MissingMetadataError(
                        "Missing print dimensions in GCode Metadata")
        return Cuboid(dimensions["MinX"], dimensions["MinY"],
                      dimensions["MinZ"], dimensions["MaxX"],
                      dimensions["MaxY"], dimensions["MaxZ"])

    def printjob_to_cuboid(self, printjob: PrintJob) -> Cuboid:
        """Create a Cuboid from a virtual_sdcard.PrintJob object.
        A ValueError can be propagated from metadata_to_cuboid.
        """
        return self.metadata_to_cuboid(printjob.md)

    ##
    ## Wrapper functions that take care of converting printjobs to cuboids
    ##
    def printjob_collides(self, printjob: PrintJob) -> bool:
        cuboid = self.printjob_to_cuboid(printjob)
        return self.collision.object_collides(cuboid)

    def add_printjob(self, printjob: PrintJob) -> None:
        cuboid = self.printjob_to_cuboid(printjob)
        self.dimensions.add_object(cuboid)

    def clear_printjobs(self) -> None:
        self.dimensions.clear_objects()

    def find_offset(self, printjob: PrintJob) -> Optional[tuple[float, float]]:
        cuboid = self.printjob_to_cuboid(printjob)
        return self.collision.find_offset(cuboid)

    def get_config(self) -> tuple[bool, bool]:
        return self.continuous_printing, self.reposition

    def set_config(self, continuous_printing: bool, reposition: bool) -> None:
        self.continuous_printing = continuous_printing
        self.reposition = reposition
        configfile = self.printer.lookup_object('configfile')
        configfile.set("collision", "continuous_printing", continuous_printing)
        configfile.set("collision", "reposition", reposition)
        configfile.save_config(restart=False)

class MissingMetadataError(AttributeError):
    pass

def load_config(config):
    return CollisionInterface(config)
