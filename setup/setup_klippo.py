#!/usr/bin/env python3

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
        AVRChip,
        ARMChip,
    ]

    def __init__(self):
        self.name_to_act = {a.name(): a for a in self.ALL_ACTIONS}
        self.config = Config(self.ALL_ACTIONS)
        self.actions = [self.name_to_act[n] for n in self.config.actions]

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
