#!/usr/bin/env python3

import logging
import os

from actions import (
    Action,
    Kivy,
    Install,
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

# Remove HOME environment variable so that path.expanduser
# works in unprivileged mode
try:
    del os.environ["HOME"]
except KeyError:
    pass

class Runner:

    ALL_ACTIONS: list[type[Action]] = [
        Kivy,
        Install,
        Graphics,
        KlipperDepends,
        Wifi,
        MonitorConf,
        Cura,
        MjpgStreamer,
        AVRChip,
        ARMChip,
    ]

    def __init__(self) -> None:
        self.name_to_act = {a.name(): a for a in self.ALL_ACTIONS}
        self.config = Config(self.ALL_ACTIONS)
        self.actions = [self.name_to_act[n](self.config) for n in self.config.actions]

    def execute(self) -> None:
        logging.info("SETUP:")
        self.setup()
        self.apt_install()
        self.pip_install()
        logging.info("RUN:")
        self.run()
        logging.info("CLEANUP:")
        self.cleanup()

    def setup(self) -> None:
        for a in self.actions:
            a.setup()

    def apt_install(self) -> None:
        packages = set()
        for a in self.actions:
            packages |= a.apt_depends()
        Apt(self.config).install(packages)

    def pip_install(self) -> None:
        packages = set()
        for a in self.actions:
            packages |= a.pip_depends()
        Pip(self.config).install(packages)

    def run(self) -> None:
        for a in self.actions:
            a.run()

    def cleanup(self) -> None:
        for a in self.actions:
            a.cleanup()

if __name__ == "__main__":
    Runner().execute()
