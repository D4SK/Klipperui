#!/usr/bin/env python3
"""
Usage:

./test.py PATH

Where PATH is the location of a gcode or ufp file to read.
"""

import configparser
import site
import sys
from os.path import dirname, realpath

klippo_dir = dirname(dirname(dirname(realpath(__file__))))
site.addsitedir(klippo_dir)

import configfile
from extras.gcode_metadata import load_config
from extras import filament_manager

class DummyPrinter:
    class Reactor:
        process_name = "printer"
    reactor = Reactor()
    def register_event_handler(*args):
        pass

test_config = {
    "extruder": {
        "filament_diameter": 1.75
    },
    "filament_manager": {}
}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("A path to parse must be provided")
    path = sys.argv[1]
    fileconfig = configparser.ConfigParser()
    fileconfig.read_dict(test_config)
    config = configfile.ConfigWrapper(
        DummyPrinter(), fileconfig, {}, "gcode_metadata")
    mm = load_config(config)
    mm.filament_manager = filament_manager.load_config(
            config.getsection("filament_manager"))
    md = mm.get_metadata(path)
    general_interface = [
        "get_path",
        "get_gcode_stream",
        "get_file_size",
        "get_slicer",
        "get_filetype",
        "get_extruder_count",
        "get_print_dimensions",
        "get_time",
        "get_flavor",
        "get_thumbnail_path",
        ]
    extruder_interface = [
        "get_material_amount",
        "get_material_guid",
        "get_material_type",
        "get_material_brand",
        "get_material_color",
        "get_density",
        "get_diameter",
        ]
    max_len = max([len(e) for e in general_interface + extruder_interface])
    for fname in general_interface:
        print(fname[4:] + ":" + " " * (max_len - len(fname) + 1)
              + str(getattr(md, fname)()))
    for i in range(md.get_extruder_count()):
        print(f"\nExtruder {i}:")
        for fname in extruder_interface:
            print(fname[4:] + ":" + " " * (max_len - len(fname) + 1)
                  + str(getattr(md, fname)(i)))
