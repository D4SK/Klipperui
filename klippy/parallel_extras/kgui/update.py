from .elements import BasePopup
from .settings import SetItem

import logging
import os
import subprocess
import requests
import time
from threading import Thread
from os.path import join

from kivy.uix.screenmanager import Screen
from kivy.uix.label import Label
from kivy.app import App
from kivy.clock import Clock
from kivy.event import EventDispatcher
from kivy.properties import StringProperty, ListProperty, BooleanProperty

from . import parameters as p


class UpdateHelper(EventDispatcher):

    INSTALL_SCRIPT = os.path.join(p.klipper_dir, "scripts/install-klippo.sh")
    RELEASES_URL = "https://api.github.com/repos/D4SK/klippo/releases"

    install_output = StringProperty()
    releases = ListProperty()

    def __init__(self):
        super().__init__()
        self._install_process = None
        self._terminate_installation = False
        self.releases = []
        try:
            with open(join(p.klipper_dir, "VERSION"), 'r') as f:
                self.current_version =  f.read()
        except:
            self.current_version = "Unknown"
        # Leave some time after startup in case WiFi isn't connected yet
        self._fetch_retries = 0
        self.fetch_clock = Clock.schedule_once(self.fetch, 15)
        Clock.schedule_once(self.fetch, 0)

    def _execute(self, cmd, ignore_errors=False):
        """Execute a command, and return its stdout
        This function blocks until it returns.
        In case of an error an error message is displayed,
        unless ignore_errors is set to True.
        """
        cmd = self._base_cmd + cmd
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 and not ignore_errors:
            logging.error("Command failed: " + " ".join(cmd) + "\n" + proc.stdout + " " + proc.stderr)
        # The output always ends with a newline, we don't want that
        return proc.stdout.rstrip("\n")

    def fetch(self, *args):
        # Fetch automatically every 24h = 86400s
        self.fetch_clock.cancel()
        self.fetch_clock = Clock.schedule_once(self.fetch, 86400)
        releases = requests.get(self.RELEASES_URL)
        if releases.ok:
            self._fetch_retries = 0
            self.releases = releases.json()
        elif self._fetch_retries <= 3:
            self._fetch_retries += 1
            Clock.schedule_once(self.fetch, 20)

    def install(self, tag_name):
        """Run the installation script"""
        if self._install_process is not None: # Install is currently running
            logging.warning("Update: Attempted to install while script is running")
            return
        # Capture both stderr and stdout in stdout
        self._install_process = subprocess.Popen(self.INSTALL_SCRIPT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        Thread(target=self._capture_install_output).start()

    def _capture_install_output(self):
        """
        Run in a seperate thread as proc.stdout.readline() blocks until
        the next line is received.
        """
        proc = self._install_process
        self.install_output = ""
        success = False
        while True:
            if self._terminate_installation:
                self._install_process.terminate()
                logging.info("Update: Installation aborted!")
                self.dispatch("on_install_finished", None)
                self._terminate_installation = False
                self._install_process = None
                break
            line = proc.stdout.readline()
            if not line:
                rc = proc.wait()
                self.dispatch("on_install_finished", rc)
                self._install_process = None
                success = rc == 0
                break
            # Highlight important lines
            if line.startswith("===>"):
                line = "[b][color=ffffff]" + line + "[/color][/b]"
            self.install_output += line
        if not success:
            notify = App.get_running_app().notify
            notify.show("Installation failed", delay=60)

    def terminate_installation(self):
        self._terminate_installation = True


update_helper = UpdateHelper()


class SIUpdate(SetItem):
    """Entry in SettingScreen, opens UpdateScreen"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.right_title = update_helper.current_version


class UpdateScreen(Screen):
    """Screen listing all releases"""

    show_all_versions = BooleanProperty(False)
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.update_helper = UpdateHelper()
        self.bind(show_all_versions=self.draw_releases)

    def draw_releases(self, *args):
        self.ids.box.clear_widgets()
        releases = update_helper.releases
        releases.sort(key=self.semantic_versioning_key)
        current_version = 0
        eligible_releases = []
        for release in releases:
            if not release['prerelease'] or self.show_all_versions:
                eligible_releases.append(release)
        for i, release in enumerate(eligible_releases):
            if release['tag_name'] == update_helper.current_version:
                current_version = i
        if current_version < len(eligible_releases) - 1:
            self.ids.message.text = f"An Update is available\nInstalled Version: {update_helper.current_version}"
        else:
             self.ids.message.text = f"Your System is up to Date\nInstalled Version: {update_helper.current_version}"
        if self.show_all_versions:
            for i, release in reversed(enumerate(eligible_releases)):
                self.ids.box.add_widget(SIRelease(release), distance = i - current_version)
        elif current_version < (len(eligible_releases) - 1):
            self.ids.box.add_widget(SIRelease(eligible_releases[-1]), 1)

    def semantic_versioning_key(self, release):
        try:
            tag = release['tag_name']
            tag = tag.lstrip('V')
            tag = tag.replace('-beta.', ".")
            tag = tag.replace('-beta', ".0")
            tag = tag.split('.')
            major = int(tag[0])
            minor = int(tag[1])
            patch = int(tag[2])
            prerelease = int(len(tag) > 3)
            prerelease_version = 0 if not prerelease else int(tag[3])
            key =  major*10000000 + minor*100000 + patch*1000 - prerelease*100 + prerelease_version
        except:
            return 0
        return key

    def on_enter(self, *args):
        self.ids.message.text = "Checking for Updates..."
        Clock.schedule_once(self.fetch, 0)

    def fetch(self, *args):
        start = time.time()
        update_helper.fetch()
        additional_time = start + 2 - time.time()
        if additional_time > 0:
            Clock.schedule_once(self.draw_releases, additional_time)
        else:
            self.draw_releases()

    def show_kebap_menu(self, *args):
        self.ids.kebap_menu.hidden = not self.ids.kebap_menu.hidden

class SIRelease(Label):
    """Releases as displayed in a list on the UpdateScreen"""

    upper_title = StringProperty()
    lower_title = StringProperty()
    def __init__(self, release, distance, **kwargs):
        self.release = release
        super().__init__(**kwargs)
        self.upper_title = self.release['tag_name']
        self.lower_title = ""
        self.ids.btn_install.text = "Install" if distance > 0 else "Reinstall" if distance == 0 else "Downgrade"

    def install(self):
        ReleasePopup(self.release).open()


class ReleasePopup(BasePopup):
    """Dialog with release info and confirmation for installation"""

    def __init__(self, release, **kwargs):
        self.release = release
        super().__init__(**kwargs)

    def install(self):
        self.dismiss()
        try:
            update_helper.install(self.release['tag_name'])
        except FileExistsError:
            return
        InstallPopup().open()


class InstallPopup(BasePopup):
    """Popup shown while installing with live stdout display"""

    def __init__(self, **kwargs):
        update_helper.bind(install_output=self.update)
        update_helper.bind(on_install_finished=self.on_finished)
        super().__init__(**kwargs)

    def update(self, instance, value):
        self.ids.output_label.text = value

    def on_finished(self, instance, returncode):
        """Replace the abort button with reboot prompt"""
        self.ids.content.remove_widget(self.ids.btn_abort)
        if returncode == 0:
            # Theses buttons were previously on mars
            self.ids.btn_cancel.y = self.y
            self.ids.btn_reboot.y = self.y
        else:
            notify = App.get_running_app().notify
            if returncode is None:
                notify.show("Installation aborted", delay=3)
            elif returncode > 0:
                notify.show("Installation failed",
                        f"Installer exited with returncode {returncode}",
                        level="error")
            # Repurpose the "Reboot later" button for exiting
            self.ids.btn_cancel.y = self.y
            self.ids.btn_cancel.width = self.width
            self.ids.btn_cancel.text = "Close"

    def terminate(self):
        update_helper.terminate_installation()
