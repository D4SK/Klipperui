# Persistent Print Job History for virtual_sdcard
#
# Copyright (C) 2020  Gabriel Vogel
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, time, json
import os

import location


class PrintHistory:
    """
    Manage print history file

    The history file is json-formatted as a list of lists each containing
    the elements
        [path, state, timestamp],
    whith path being the path of the gcode file,
    status being a string defining the outcome of the print, one of
        "finished", "aborted",
    timestamp being the time of completion in seconds since epoch.
    """
    def __init__(self, config):
        self.printer = config.get_printer()
        self.history_path = location.history()
        self.history = self.read()
        self.trim_history()
        self.printer.register_event_handler("virtual_sdcard:print_end", self.add)
        self.printer.register_event_handler("klippy:ready", self.handle_ready)

    def handle_ready(self):
        self.printer.send_event("print_history:change", self.history)

    def trim_history(self):
        """ Remove all entries of deleted files, return number of removed entries """
        history = self.history
        to_remove = []
        for e in history:
            if not os.path.exists(e[0]):
                to_remove.append(e)
        if to_remove != []:
            for e in to_remove:
                history.remove(e)
            self.write(history)
            self.history = history
            self.printer.send_event("print_history:change", self.history)
            return len(to_remove)
        return 0

    def read(self):
        """ Read the history file and return it as a list object """
        try:
            with open(self.history_path, "r") as fp:
                history = json.load(fp)
        except (IOError, ValueError): # No file or incorrect JSON
            logging.info("History: Couldn't read file at {}".format(self.history_path))
            history = []
        if not self.verify_history(history):
            logging.warning("History: Malformed history file")
            history = []
        return history

    def verify_history(self, history):
        """ Only return True when the entire history has a correct structure """
        try:
            for e in history:
                if not (isinstance(e[0], str)              # path
                and (e[1] in ("finished", "aborted"))      # state
                and isinstance(e[2], (float, int))):       # timestamp
                    return False
        except:
            return False
        return True

    def write(self, history):
        """ Write the object to the history file """
        try:
            with open(self.history_path, "w") as fp:
                json.dump(history, fp, indent=True)
        except IOError:
            return

    def add(self, jobs, job):
        """ Add a new entry to the history from the specified PrintJob object """
        self.history.append([job.path, job.state, time.time(), job.continuous])
        self.write(self.history)
        self.printer.send_event("print_history:change", self.history)

def load_config(config):
    return PrintHistory(config)
