import logging

from .collision_check import BoxCollision
from .geometry import Rectangle, Cuboid
from ..virtual_sdcard import PrintJob


# Default padding, if not specified in config, in mm
DEFAULT_PADDING = 5

class CollisionInterface:

    def __init__(self, config):
        self._config = config
        self.printer = config.get_printer()

        # Read config
        self.continuous_printing = config.getboolean('continuous_printing', False)
        self.reposition = config.getboolean('reposition', False)
        self.printbed = Cuboid(*[0]*6)  # Received after klippy:connect
        self.printhead = self._read_printhead()
        self.gantry_x_oriented = config.getchoice("gantry_orientation",
                                                  {"x": True, "y": False})
        self.gantry = self._read_gantry(self.printbed)  # Preliminary
        self.gantry_height = config.getfloat("gantry_z_min")
        self.padding = config.getfloat("padding", DEFAULT_PADDING)

        self.printer.register_event_handler("klippy:connect", self.handle_connect)

    def handle_connect(self):
        """Get printbed size later and update gantry"""
        self.printbed = self._read_printbed()
        self.gantry = self._read_gantry(self.printbed)
        self.collision = BoxCollision(self.printbed, self.printhead,
                                      self.gantry, self.gantry_x_oriented,
                                      self.gantry_height, self.padding)
        self.printer.register_event_handler(
                "virtual_sdcard:print_end", self._handle_print_end)

    def _read_printbed(self):
        """Read the printer size from the config and return it as a Cuboid"""
        rails = self.printer.objects['toolhead'].kin.rails
        min_ = [rail.position_min for rail in rails]
        max_ = [rail.position_max for rail in rails]
        return Cuboid(*min_, *max_)

    def _read_printhead(self):
        """Return a Rectangle representing the size of the print head
        as viewed from above. The printing nozzle would be at (0, 0).
        """
        return Rectangle(
            -self._config.getfloat("printhead_x_min"),
            -self._config.getfloat("printhead_y_min"),
            self._config.getfloat("printhead_x_max"),
            self._config.getfloat("printhead_y_max"),
        )

    def _read_gantry(self, printbed):
        """Return a Rectangle representing the size of the gantry as viewed
        from above as well as if it is oriented parallel to the X-Axis or not.
        The printing nozzle would be at the 0-coordinate on the other axis.
        """
        xy_min = self._config.getfloat("gantry_xy_min")
        xy_max = self._config.getfloat("gantry_xy_max")
        if self.gantry_x_oriented:
            gantry = Rectangle(printbed.x, -xy_min,
                               printbed.width, xy_max)
        else:
            gantry = Rectangle(-xy_min, printbed.y,
                               xy_max, printbed.height)
        return gantry

    def check_available(self, printjob):
        if not self.continuous_printing:
            return not self.collision.current_objects, (0, 0)
        try:
            available = not self.printjob_collides(printjob)
            offset = (0, 0)
            if not available and self.reposition:
                offset = self.find_offset(printjob)
                available = offset is not None
            return available, offset
        except MissingMetadataError:
            return False, None

    def predict_availability(self, printjob, queue):
        """Look in the future and predict if printjob will be printable without
        collisions if everything in queue is printed first.

        printjob can be either a PrintJob object or metadata object.
        """
        try:
            object_queue = [self.printjob_to_cuboid(pj) for pj in queue]
            if isinstance(printjob, PrintJob):
                cuboid = self.printjob_to_cuboid(printjob)
            else:
                cuboid = self.metadata_to_cuboid(printjob)
        except MissingMetadataError:
            return False
        predict_collision = self.collision.replicate_with_objects(object_queue)
        available = not predict_collision.object_collides(cuboid)
        if not available and self.reposition:
            offset = predict_collision.find_offset(cuboid)
            available = offset is not None
        return available

    def _handle_print_end(self, printjobs, printjob):
        try:
            self.add_printjob(printjob)
        except MissingMetadataError:
            logging.warning("Collision: Couldn't read print dimensions for"
                    + printjob.path)
            # Save as entire printbed to force collision with all other prints
            self.collision.add_object(self.collision.printbed)

    ##
    ## Conversion functions
    ##
    def metadata_to_cuboid(self, metadata):
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

    def printjob_to_cuboid(self, printjob):
        """Create a Cuboid from a virtual_sdcard.PrintJob object.
        A ValueError can be propagated from metadata_to_cuboid.
        """
        return self.metadata_to_cuboid(printjob.md)

    ##
    ## Wrapper functions that take care of converting printjobs to cuboids
    ##
    def printjob_collides(self, printjob):
        cuboid = self.printjob_to_cuboid(printjob)
        return self.collision.object_collides(cuboid)

    def add_printjob(self, printjob):
        cuboid = self.printjob_to_cuboid(printjob)
        self.collision.add_object(cuboid)

    def clear_printjobs(self):
        self.collision.clear_objects()

    def find_offset(self, printjob):
        cuboid = self.printjob_to_cuboid(printjob)
        return self.collision.find_offset(cuboid)


    def get_config(self):
        return self.continuous_printing, self.reposition

    def set_config(self, continuous_printing, reposition):
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
