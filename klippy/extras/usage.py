# Support for storing usage statistics
#
# Copyright (C) 2022  Konstantin Vogel <konstantin.vogel@gmx.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, time
import json
import logging

class Usage:
    usage_path = os.path.expanduser('~/usage.json')

    def __init__(self, config):
        # TODO add total print time, up time, aborted prints, finished prints
        self.printer = config.get_printer()
        self.reactor = config.get_reactor()
        self.distance_tracker = [0, 0, 0, 0]

        self.usage = {
            'distance': [0, 0, 0, 0],
            'startups': 0,
            'shutdowns': 0,
            'disconnects': 0,
            'critical_errors': 0
        }
        self.read_usage_json()
        self.usage['startups'] += 1

        self.printer.register_event_handler("klippy:ready", self.handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self.handle_shutdown)
        self.printer.register_event_handler("klippy:disconnect", self.handle_disconnect)

    def handle_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.gcode = self.printer.lookup_object('gcode')
        self.toolhead.distance_tracker = self.distance_tracker

    def handle_shutdown(self):
        self.usage['shutdowns'] += 1
        self.update_usage()
        self.write_usage_json()

    def handle_disconnect(self):
        self.usage['disconnects'] += 1
        self.update_usage()
        self.write_usage_json()

    def get_status(self):
        self.update_usage()
        return self.usage

    def read_usage_json(self):
        """Read the usage file and return it as a list object"""
        try:
            with open(self.usage_path, "r") as f:
                usage = json.load(f)
            if not self.verify_usage_json(usage):
                logging.info("Usage: Malformed usage file at " + self.usage_path)
            else:
                self.usage = usage
        except (IOError, ValueError): # No file or incorrect JSON
            logging.info("Usage: Couldn't read usage-file at " + self.usage_path)

    def verify_usage_json(self, usage):
        """Only return True when the entire file has a correct structure"""
        return True
        # try:
        #     for mat in usage['loaded']:
        #         if not (
        #             mat['state'] in {'loaded', 'loading', 'unloading', 'no usage'} and
        #             isinstance(mat['guid'], (str, type(None))) and
        #             isinstance(mat['amount'], (float, int)) and
        #             isinstance(mat['all_time_extruded_length'], (float, int))):
        #             return False
        #     for mat in usage['unloaded']:
        #         if not (
        #             isinstance(mat['guid'], str) and
        #             isinstance(mat['amount'], (float, int))):
        #             return False
        #     return True
        # except:
        #     return False

    def write_usage_json(self):
        """Write the object to the usage file"""
        try:
            with open(self.usage_path, "w") as f:
                json.dump(self.usage, f, indent=True)
        except IOError:
            logging.warning("Usage: Couldn't write usage at " + self.usage_path, exc_info=True)
        logging.info("wrote usage")

    def update_usage(self):
        for i in (0, 1, 2, 3):
            self.usage['distance'][i] += self.distance_tracker[i]
            self.distance_tracker[i] = 0

def load_config(config):
    return Usage(config)
