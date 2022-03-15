from .elements import BasePopup, MICheckbox
from .settings import SetItem

import logging
import os
import subprocess
import requests
import time
from threading import Thread
from os.path import join, expanduser

from kivy.uix.screenmanager import Screen
from kivy.uix.label import Label
from kivy.app import App
from kivy.clock import Clock
from kivy.event import EventDispatcher
from kivy.properties import StringProperty, ListProperty, BooleanProperty, NumericProperty
from kivy.factory import Factory

from . import parameters as p


class Updater(EventDispatcher):

    INSTALL_SCRIPT = os.path.join(p.klipper_dir, "scripts/install-klippo.sh")
    RELEASES_URL = "https://api.github.com/repos/D4SK/klippo/releases"

    install_output = StringProperty()
    releases = ListProperty()
    raw_releases = ListProperty()
    show_all_versions = BooleanProperty(False)
    current_version_idx = NumericProperty(0)
    downloaded_bytes = NumericProperty(0)
    download_finished = BooleanProperty(False)

    def __init__(self):
        super().__init__()
        self._install_process = None
        self._terminate_installation = False
        self.abort_download = False
        try:
            with open(join(p.klipper_dir, "VERSION"), 'r') as f:
                self.current_version =  f.read()
        except:
            self.current_version = "Unknown"
        # Leave some time after startup in case WiFi isn't connected yet
        self._fetch_retries = 0
        self.fetch_clock = Clock.schedule_once(self.fetch, 15)
        self.bind(show_all_versions=self.process_releases)
        self.register_event_type("on_new_releases")

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
        Thread(target=self.do_fetch).start()

    def do_fetch(self, *args):
        raw_releases = requests.get(self.RELEASES_URL)
        if raw_releases.ok:
            self._fetch_retries = 0
            raw_releases = raw_releases.json()
            raw_releases.sort(key=self.semantic_versioning_key)
            self.raw_releases = raw_releases
            Clock.schedule_del_safe(self.process_releases)
        elif self._fetch_retries <= 3:
            self._fetch_retries += 1
            Clock.schedule_once(self.fetch, 20)

    def process_releases(self, *args):
        current_version_idx = 0
        releases = []
        for release in self.raw_releases:
            if not release['prerelease'] or self.show_all_versions:
                releases.append(release)
        for i, release in enumerate(releases):
            if release['tag_name'] == updater.current_version:
                current_version_idx = i
        for i in range(len(releases)):
            releases[i]['distance'] = i - current_version_idx
        self.current_version_idx = current_version_idx
        self.releases = releases
        self.dispatch("on_new_releases")

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

    def install(self):
        """Run the installation script"""
        if self._install_process is not None: # Install is currently running
            logging.warning("Update: Attempted to install while script is running")
            return
        # Capture both stderr and stdout in stdout
        self._install_process = subprocess.Popen(self.INSTALL_SCRIPT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        Thread(target=self._capture_install_output).start()

    def start_download(self, release):
        self.downloaded_bytes = 0
        self.local_filename = expanduser(f"~/{release['tag_name']}")
        Thread(target=self.download, args=[release]).start()

    def download(self, release):
        url = release['zipball_url']
        aborted = False
        if os.exists(self.local_filename):
            os.remove(self.local_filename)
        with requests.get(url, stream=True, allow_redirects=True) as r:
            r.raise_for_status()
            with open(self.local_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=262144):
                    self.downloaded_bytes += len(chunk)
                    if self.abort_download:
                        aborted = True
                        break
                    f.write(chunk)
        if aborted:
            os.remove(self.local_filename)
        else:
            self.download_finished = True

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

    def on_new_releases(self, *args):
        pass


updater = Updater()


class SIUpdate(SetItem):
    """Entry in SettingScreen, opens UpdateScreen"""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        updater.bind(releases=self.show_message)
        self.show_message()

    def show_message(self, *args):
        if updater.current_version_idx < len(updater.releases) - 1:
            self.right_title = "Updates Available"
        else:
            self.right_title = updater.current_version


class UpdateScreen(Screen):
    """Screen listing all releases"""

    def __init__(self, **kwargs):
        self.min_time = 0
        super().__init__(**kwargs)
        updater.bind(on_new_releases=self.draw_releases)

    def draw_releases(self, *args):
        additional_time = self.min_time - time.time()
        if additional_time > 0:
            Clock.schedule_once(self.draw_releases, additional_time)
            return
        self.ids.box.clear_widgets()
        if updater.current_version_idx < len(updater.releases) - 1:
            self.ids.message.text = f"An Update is available\nInstalled Version: {updater.current_version}"
        else:
             self.ids.message.text = f"Your System is up to Date\nInstalled Version: {updater.current_version}"
        if updater.show_all_versions:
            for release in reversed(updater.releases):
                self.ids.box.add_widget(SIRelease(release))
        elif updater.current_version_idx < (len(updater.releases) - 1):
            self.ids.box.add_widget(SIRelease(updater.releases[-1]))

    def on_pre_enter(self, *args):
        self.ids.message.text = "Checking for Updates..."
        self.min_time = time.time() + 2
        updater.fetch()

    def show_dropdown(self, button, *args):
        Factory.UpdateDropDown().open(button)


class MIShowAllVersions(MICheckbox):
    def __init__(self, **kwargs):
        self.active = updater.show_all_versions
        super().__init__(**kwargs)

    def on_active(self, *args):
        updater.show_all_versions = self.active


class SIRelease(Label):
    """Releases as displayed in a list on the UpdateScreen"""

    def __init__(self, release, **kwargs):
        self.release = release
        self.upper_title = self.release['tag_name']
        self.lower_title = ""
        super().__init__(**kwargs)
        self.ids.btn_install.text = "Install" if release['distance'] > 0 else "Reinstall" if release['distance'] == 0 else "Downgrade"


class ReleasePopup(BasePopup):
    """Dialog with release info and confirmation for installation"""

    def __init__(self, release, **kwargs):
        self.release = release
        super().__init__(**kwargs)


class DownloadPopup(BasePopup):
    downloaded_bytes = NumericProperty(0)

    def __init__(self, release, **kwargs):
        self.release = release
        self.updater = updater
        super().__init__(**kwargs)
        updater.start_download(release)


class InstallPopup(BasePopup):
    """Popup shown while installing with live stdout display"""

    def __init__(self, **kwargs):
        updater.bind(install_output=self.update)
        updater.bind(on_install_finished=self.on_finished)
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
        updater.terminate_installation()
