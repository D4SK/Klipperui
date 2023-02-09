#!/usr/bin/env python3

import logging
import os
import sys

if os.geteuid() == 0:
    print("This script must not run as root")
    sys.exit(63)

from actions import (
    Action,
    Kivy,
    Graphics,
    KlipperDepends,
    Wifi,
    MonitorConf,
    Cura,
    MjpgStreamer,
    AVRChip,
    ARMChip,
    )
from util import Config, Apt, Pip


class Runner:

    ALL_ACTIONS: list[type[Action]] = [
        Kivy,
        Graphics,
        KlipperDepends,
        Wifi,
        MonitorConf,
        Cura,
        MjpgStreamer,
        AVRChip,
        ARMChip,
    ]

    def __init__(self):
        self.name_to_act = {a.name(): a for a in self.ALL_ACTIONS}
        self.config = Config(self.ALL_ACTIONS)
        self.actions = [self.name_to_act[n](self.config) for n in self.config.actions]
        self.execute()

    def execute(self):
        logging.info("SETUP:")
        self.setup()
        self.apt_install()
        self.pip_install()
        logging.info("RUN:")
        self.run()
        logging.info("CLEANUP:")
        self.cleanup()

    def setup(self):
        for a in self.actions:
            a.setup()

    def apt_install(self):
        packages = set()
        for a in self.actions:
            packages |= a.apt_depends()
        Apt(self.config).install(packages)

    def pip_install(self):
        packages = set()
        for a in self.actions:
            packages |= a.pip_depends()
        Pip(self.config).install(packages)

    def run(self):
        for a in self.actions:
            a.run()

    def cleanup(self):
        for a in self.actions:
            a.cleanup()

if __name__ == "__main__":
    Runner()
