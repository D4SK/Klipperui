#!/usr/bin/env python3

import logging
import os
from subprocess import run

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
    Usbmount,
    Swap,
    AVRChip,
    ARMChip,
)
from util import Config, apt_install, Pip

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
        Usbmount,
        Swap,
        AVRChip,
        ARMChip,
    ]

    def __init__(self) -> None:
        self.name_to_act = {a.name(): a for a in self.ALL_ACTIONS}
        self.config = Config(self.ALL_ACTIONS)
        self.actions = [self.name_to_act[n](self.config) for n in self.config.actions]

    def execute(self) -> None:
        if self.config.uninstall:
            self.uninstall()
            if self.config.cleanup:
                self.cleanup()
            return
        self.setup()
        if not self.config.skip_apt:
            self.apt_install()
        self.pre_pip()
        if not self.config.skip_pip:
            self.pip_install()
        self.run()
        if self.config.cleanup:
            self.cleanup()

    def setup(self) -> None:
        logging.info("SETUP:")
        for a in self.actions:
            a.setup()

    def apt_install(self) -> None:
        packages = set()
        for a in self.actions:
            packages |= a.apt_depends()
        apt_install(packages)

    def pre_pip(self) -> None:
        logging.info("PRE-PIP:")
        for a in self.actions:
            a.pre_pip()

    def pip_install(self) -> None:
        packages = set()
        for a in self.actions:
            packages |= a.pip_depends()
        Pip(self.config).install(packages)

    def run(self) -> None:
        logging.info("RUN:")
        for a in self.actions:
            a.run()
        run(['systemctl', 'daemon-reload'], check=True)

    def cleanup(self) -> None:
        logging.info("CLEANUP:")
        for a in self.actions:
            a.cleanup()

    def uninstall(self) -> None:
        logging.info("UNINSTALL:")
        for a in self.actions:
            a.uninstall()
        Pip(self.config).uninstall()

if __name__ == "__main__":
    Runner().execute()
