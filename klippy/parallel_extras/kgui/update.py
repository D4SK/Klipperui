from functools import partial
import logging
import lzma
import os
import requests
import subprocess
from threading import Thread
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.event import EventDispatcher
from kivy.properties import (StringProperty, ListProperty, BooleanProperty,
    NumericProperty, ObjectProperty)
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen

from .elements import BasePopup, Divider
from .settings import SetItem
import location


class Updater(EventDispatcher):

    RELEASES_URL = "https://api.github.com/repos/D4SK/servoklippo/releases"
    _headers = {"X-GitHub-Api-Version": "2022-11-28"}

    releases = ListProperty()
    current_version_idx = NumericProperty(0)

    def __init__(self):
        super().__init__()
        try:
            with open(location.version_file(), 'r') as f:
                self.current_version = f.read()
        except OSError:
            self.current_version = "Unknown"
        try:
            with open(os.path.expanduser("~/TOKEN"), 'r') as f:
                token = f.read()
                self._headers["Authorization"] = "Bearer " + token
        except OSError:
            logging.info('Updater: no token found')

        # Leave some time after startup in case WiFi isn't connected yet
        self._fetch_retries = 0
        self.total_bytes = 1
        self.fetch_clock = Clock.schedule_once(self.fetch, 15)

    def fetch(self, *args):
        # Fetch automatically every 24h = 86400s
        self.fetch_clock.cancel()
        self.fetch_clock = Clock.schedule_once(self.fetch, 86400)
        Thread(target=self.do_fetch).start()

    def do_fetch(self, *args):
        try:
            requ = requests.get(self.RELEASES_URL,
                headers=self._headers | {"Accept": "application/vnd.github+json"},
                timeout=10)
        except requests.exceptions.RequestException:
            requ = None
        if requ and requ.ok:
            self._fetch_retries = 0
            raw_releases = requ.json()
            raw_releases.sort(key=self.semantic_versioning_key)
            Clock.schedule_del_safe(partial(self.process_releases, raw_releases))
        elif self._fetch_retries <= 3:
            self._fetch_retries += 1
            Clock.schedule_once(self.fetch, 20)

    def process_releases(self, raw_releases):
        current_version_idx = 0
        releases = []
        for release in raw_releases:
            try:
                rel = Release(release)
            except (ValueError, LookupError, TypeError) as e:
                logging.info("Error while reading release info: %s", str(e))
            else:
                releases.append(rel)
        for i, release in enumerate(releases):
            if release.version == self.current_version:
                current_version_idx = i
        for i in range(len(releases)):
            releases[i].distance = i - current_version_idx
        self.current_version_idx = current_version_idx
        self.releases = releases

    def semantic_versioning_key(self, release):
        try:
            tag = release.version
            tag = tag.lstrip('vV')
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


class Release(EventDispatcher):

    downloaded_bytes = NumericProperty(0)
    download_finished = BooleanProperty(False)
    install_output = StringProperty()

    def __init__(self, data):
        """Read data from GitHub API into object. For Response schema see:
        https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28
        """
        self.register_event_type('on_install_finished')
        self.abort_download = False
        self.terminate_installation = False

        self.version = data['tag_name']
        self.draft = data['draft']
        self.prerelease = data['prerelease']
        self.title = data['name']
        self.message = data['body']
        self.distance = -1

        try:
            asset = next(a for a in data['assets'] if a['name'].startswith('image'))
        except (StopIteration, LookupError):
            raise ValueError("Could not find image asset in release data") from None
        self.update_url = asset['url']
        self.update_filename = asset['name']
        self.update_path = os.path.join(location.update_dir(), self.update_filename)
        # Download to PATH.part first
        self.download_path = self.update_path + '.part'
        self.download_size = asset['size']

    def download(self):
        if os.path.isfile(self.update_filename):
            #TODO Verify existing file
            self.download_finished = True
            return
        self.downloaded_bytes = 0
        Thread(target=self._download_thread, name="Download-Update-Thread").start()

    def _download_thread(self):
        aborted = False
        if os.path.exists(self.download_path):
            os.remove(self.download_path)
        with requests.get(self.update_url,
            stream=True,
            allow_redirects=True,
            headers=Updater._headers | {"Accept": "application/octet-stream"},
            timeout=10,
        ) as r:
            r.raise_for_status()
            with open(self.download_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=262144):
                    self.downloaded_bytes += len(chunk)
                    if self.abort_download:
                        aborted = True
                        break
                    f.write(chunk)
        if aborted:
            os.remove(self.download_path)
            logging.info("Aborted downloading of %s", self.update_filename)
        else:
            os.rename(self.download_path, self.update_path)
            self.download_finished = True
        #TODO Verify downloaded file

    def decompress(self):
        """Run the installation script"""
        with lzma.open(self.local_filename, 'rb') as compressed_file:
            with open("/tmp/test", 'wb') as decompressed_file:
                for chunk in iter(lambda: compressed_file.read(1024), b''):
                    decompressed_file.write(chunk)

    def install(self):
        cmd = ['sudo', 'svup', '-h']
        # Capture both stderr and stdout in stdout
        proc = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        Thread(target=self._capture_install_output, args=(proc,),
               name="Install-Output-Thread").start()

    def _capture_install_output(self, proc):
        """
        Run in a seperate thread as proc.stdout.readline() blocks until
        the next line is received.
        """
        self.install_output = ""
        while True:
            if self._terminate_installation:
                self._install_process.terminate()
                logging.info("Update: Installation aborted!")
                Clock.schedule_del_safe(lambda: self.dispatch("on_install_finished", None))
                self._terminate_installation = False
                self._install_process = None
                break
            line = proc.stdout.readline()
            if not line:
                rc = proc.wait()
                Clock.schedule_del_safe(lambda: self.dispatch("on_install_finished", rc))
                self._install_process = None
                break
            # Highlight important lines
            if line.startswith("===>"):
                line = "[b][color=ffffff]" + line + "[/color][/b]"
            self.install_output += line

    def on_install_finished(self, *args):
        pass


class SIUpdate(SetItem):
    """Entry in SettingScreen, opens UpdateScreen"""

    screen_manager = ObjectProperty(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.updater = None
        Clock.schedule_once(self.late_setup, 0)

    def late_setup(self, dt):
        self.updater = self.screen_manager.get_screen("UpdateScreen").updater
        self.updater.bind(releases=self.show_message)
        self.show_message()

    def show_message(self, *args):
        if self.updater.current_version_idx < len(self.updater.releases) - 1:
            self.right_title = "Updates Available"
        else:
            self.right_title = self.updater.current_version


class UpdateScreen(Screen):
    """Screen listing all releases"""

    show_all_versions = BooleanProperty(False)

    def __init__(self, **kwargs):
        self.min_time = 0
        super().__init__(**kwargs)
        self.updater = Updater()
        self.updater.bind(releases=self.draw_releases)

    def draw_releases(self, *args):
        additional_time = self.min_time - time.time()
        if additional_time > 0:
            Clock.schedule_once(self.draw_releases, additional_time)
            return
        self.ids.box.clear_widgets()
        if self.updater.current_version_idx < len(self.updater.releases) - 1:
            self.ids.message.text = "An Update is available"
            self.ids.message.state = 'transparent'
        else:
             self.ids.message.text = "Your System is up to Date"
             self.ids.message.state = 'green'
        self.ids.version_label.text = f"Current Version: {self.updater.current_version}"
        self.ids.box.add_widget(Divider(pos_hint={'center_x': 0.5}))
        if self.show_all_versions:
            for release in reversed(self.updater.releases):
                self.ids.box.add_widget(SIRelease(release))
        elif self.updater.current_version_idx < (len(self.updater.releases) - 1):
            self.ids.box.add_widget(SIRelease(self.updater.releases[-1]))
        Clock.schedule_once(self._align, 0)

    def _align(self, *args):
        self.ids.version_label.top = self.ids.message.y

    def on_pre_enter(self, *args):
        self.ids.message.text = "Checking for Updates"
        self.ids.message.state = "loading"
        self.min_time = time.time() + 2
        self.updater.fetch()

    def show_dropdown(self, button, *args):
        UpdateDropDown(self).open(button)

    def on_show_all_versions(self, instance, value):
        self.draw_releases()

# Avoid importing kivy.core.window outside Kivy thread. Also see late_define in
# settings.py
def late_define(dt):
    from kivy.uix.dropdown import DropDown

    global UpdateDropDown
    class UpdateDropDown(DropDown):
        mi_all_versions = ObjectProperty(None)

        def __init__(self, update_screen, **kwargs):
            self.update_screen = update_screen
            super().__init__(**kwargs)
            self.mi_all_versions.active = self.update_screen.show_all_versions
            self.mi_all_versions.bind(active=self.set_all_versions)

        def set_all_versions(self, instance, value):
            self.update_screen.show_all_versions = value

Clock.schedule_once(late_define, 0)


class SIRelease(Label):
    """Releases as displayed in a list on the UpdateScreen"""

    def __init__(self, release, **kwargs):
        self.release = release
        self.upper_title = self.release.version
        status = ""
        if release.distance == 0:
            status = "Currently installed"
            action = "Reinstall"
        elif release.distance > 0:
            action = "Install"
        else:
            action = "Downgrade"
        self.lower_title = status
        super().__init__(**kwargs)
        self.ids.btn_install.text = action

    def install(self):
        ReleasePopup(self.release).open()


class ReleasePopup(BasePopup):
    """Dialog with release info and confirmation for installation"""

    def __init__(self, release, **kwargs):
        self.release = release
        super().__init__(**kwargs)

    def download(self):
        DownloadPopup(self.release).open()
        self.dismiss()
        self.release.download()


class DownloadPopup(BasePopup):
    downloaded_bytes = NumericProperty(0)

    def __init__(self, release, **kwargs):
        self.release = release
        super().__init__(**kwargs)

    def install(self):
        InstallPopup(self.release).open()
        self.dismiss()
        self.release.install()


class InstallPopup(BasePopup):
    """Popup shown while installing with live stdout display"""

    def __init__(self, release, **kwargs):
        self.release = release
        self.release.bind(install_output=self.update)
        self.release.bind(on_install_finished=self.on_finished)
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
        self.release.terminate_installation = True
