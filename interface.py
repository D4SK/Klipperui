from .collision_check import BoxCollision
from .geometry import Rectangle, Cuboid


# Default padding, if not specified in config, in mm
DEFAULT_PADDING = 5

class CollisionInterface:

    def __init__(self, config):
        self._config = config
        self.continuous_printing = config.getboolean(
                'continuous_printing', False)
        self.reposition = config.getboolean('reposition', False)
        self.condition = config.getchoice('condition',
                {'exact': "exact", "type": "type", "any": "any"}, "any")

        printbed = self._read_printbed()
        printhead = self._read_printhead()
        gantry, gantry_x_oriented = self._read_gantry(printbed)
        gantry_height = config.getfloat("gantry_z_min")
        padding = config.getfloat("padding", DEFAULT_PADDING)

        self.collision = BoxCollision(printbed, printhead, gantry,
                                      gantry_x_oriented, gantry_height, padding)
        self.printer = config.get_printer()
        self.printer.register_event_handler(
                "virtual_sdcard:print_end", self.add_printjob)

    def _read_printbed(self):
        """Read the printer size from the config and return it as a Cuboid"""
        stepper_configs = [self._config.getsection("stepper_" + axis)
                           for axis in "xyz"]
        min_ = [cfg.getfloat("position_min") for cfg in stepper_configs]
        max_ = [cfg.getfloat("position_max") for cfg in stepper_configs]
        return Cuboid(*min_, *max_)

    def _read_printhead(self):
        """Return a Rectangle representing the size of the print head
        as viewed from above. The printing nozzle would be at (0, 0).
        """
        config = self._config
        return Rectangle(
            -config.getfloat("printhead_x_min"),
            -config.getfloat("printhead_y_min"),
            config.getfloat("printhead_x_max"),
            config.getfloat("printhead_y_max"),
        )

    def _read_gantry(self, printbed):
        """Return a Rectangle representing the size of the gantry as viewed
        from above as well as if it is oriented parallel to the X-Axis or not.
        The printing nozzle would be at the 0-coordinate on the other axis.
        """
        config = self._config
        xy_min = config.getfloat("gantry_xy_min")
        xy_max = config.getfloat("gantry_xy_max")
        x_oriented = config.getchoice("gantry_orientation",
                                      {"x": True, "y": False})
        if x_oriented:
            gantry = Rectangle(printbed.x, -xy_min,
                               printbed.width, xy_max)
        else:
            gantry = Rectangle(-xy_min, printbed.y,
                               xy_max, printbed.height)
        return gantry, x_oriented

    def check_available(self, printjob):
        if not self.continuous_printing:
            return not self.collision.current_objects, (0, 0)
        available = not self.printjob_collides(printjob)
        offset = (0, 0)
        if not available and self.reposition:
            offset = self.find_offset(printjob)
            available = offset != None
        return available, offset

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
                raise ValueError("Missing print dimensions in GCode Metadata")
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

    def add_printjob(self, printjobs, printjob):
        cuboid = self.printjob_to_cuboid(printjob)
        self.collision.add_object(cuboid)

    def clear_printjobs(self):
        self.collision.clear_objects()
        self.printer.lookup_object('virtual_sdcard').check_queue()

    def find_offset(self, printjob):
        cuboid = self.printjob_to_cuboid(printjob)
        return self.collision.find_offset(cuboid)


    def get_config(self):
        return self.continuous_printing, self.reposition, self.condition

    def set_config(self, continuous_printing, reposition, condition):
        self.continuous_printing = continuous_printing
        self.reposition = reposition
        self.condition = condition
        configfile = self.printer.lookup_object('configfile')
        configfile.set("collision", "continuous_printing", continuous_printing)
        configfile.set("collision", "reposition", reposition)
        configfile.set("collision", "condition", condition)
        configfile.save_config(restart=False)

def load_config(config):
    return CollisionInterface(config)
