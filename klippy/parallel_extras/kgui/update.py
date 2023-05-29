from enum import Enum, auto
from functools import partial
import logging
import lzma
import os
import requests
from signal import SIGINT
import subprocess
from threading import Thread
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.event import EventDispatcher
from kivy.properties import (ListProperty, BooleanProperty,
    NumericProperty, ObjectProperty, OptionProperty)
from kivy.uix.label import Label
from packaging.version import Version, InvalidVersion

from .elements import BasePopup, Divider, UltraScreen
from .settings import SetItem
import location


class Updater(EventDispatcher):

    RELEASES_URL = "https://api.github.com/repos/D4SK/servoklippo/releases"
    _headers = {"X-GitHub-Api-Version": "2022-11-28"}

    releases = ListProperty()

    def __init__(self):
        super().__init__()
        try:
            with open(location.version_file(), 'r') as f:
                self.current_version = Version(f.read())
        except (OSError, InvalidVersion):
            logging.exception("Failed to read current version")
            self.current_version = None
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
        self._fetch_retries = 0
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
            releases = []
            for release in raw_releases:
                try:
                    rel = Release(release)
                except (ValueError, LookupError, TypeError, InvalidVersion) as e:
                    logging.info("Error while reading release info: %s", str(e))
                else:
                    releases.append(rel)
            releases.sort(key=lambda r: r.version_obj)
            def _set_releases(dt):
                self.releases = releases
            Clock.schedule_once(_set_releases, 0)
        elif self._fetch_retries <= 3:
            self._fetch_retries += 1
            Clock.schedule_once(self.fetch, 20)

    def has_newer(self):
        """Return True if a version newer than the currently installed one was
        found.
        """
        return (self.current_version is None or
                self.releases and self.current_version < self.releases[-1].version_obj)


class Release():

    def __init__(self, data):
        """Read data from GitHub API into object. For Response schema see:
        https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28
        """
        self.version = data['tag_name']
        self.version_obj = Version(self.version)
        self.draft = data['draft']
        self.prerelease = data['prerelease']
        self.title = data['name']
        self.message = data['body']

        try:
            asset = next(a for a in data['assets']
                         if a['name'].startswith('image') and
                            a['name'].endswith('.xz'))
        except (StopIteration, LookupError):
            raise ValueError("Could not find image asset in release data") from None
        self.update_url = asset['url']
        self.update_path = os.path.join(location.update_dir(), asset['name'])
        self.download_size = asset['size']
        # Uncompressed image
        self.image_path = self.update_path[:-3]

    def get_uncompressed_size(self):
        cmd = ['xz', '--list', '--robot', self.update_path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except OSError:
            return None
        for line in proc.stdout.splitlines():
            parts = line.split()
            try:
                if parts and parts[0] == 'file':
                    return int(parts[4])
            except (LookupError, ValueError):
                return None


class IState(Enum):
    IDLE = auto()
    DOWNLOADING = auto()
    DOWNLOADED = auto()
    DECOMPRESSING = auto()
    DECOMPRESSED = auto()
    INSTALLING = auto()
    INSTALLED = auto()
    ABORTING = auto()
    ABORTED = auto()
    FAILED = auto()

class Installer(EventDispatcher):

    progress = NumericProperty(0)
    state = OptionProperty(IState.IDLE, options=list(IState))

    def __init__(self, release):
        self.release = release
        self._install_proc = None
        self._abort_process = False
        # Name of the subvolume that is created in installation
        self.subvol = None

    def set_state(self, state):
        self.state = state

    def download(self):
        self.progress = 0
        self._abort_process = False
        if os.path.isfile(self.release.update_path):
            #TODO Verify existing file
            self.state = IState.DOWNLOADED
            return
        self.state = IState.DOWNLOADING
        Thread(target=self._download_thread, name="Download-Update-Thread").start()

    def _download_thread(self):
        aborted = False
        # Download to PATH.part first
        download_path = self.release.update_path + '.part'
        try:
            if os.path.exists(download_path):
                os.remove(download_path)
            with requests.get(self.release.update_url,
                stream=True,
                allow_redirects=True,
                headers=Updater._headers | {"Accept": "application/octet-stream"},
                timeout=10,
            ) as r:
                r.raise_for_status()
                with open(download_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=262144):
                        if self._abort_process:
                            self._abort_process = False
                            aborted = True
                            break
                        f.write(chunk)
                        self.progress += len(chunk)
            if aborted:
                os.remove(download_path)
                logging.info("Aborted downloading of %s", self.release.update_path)
                Clock.schedule_del_safe(partial(self.set_state, IState.ABORTED))
            else:
                #TODO Verify downloaded file
                os.rename(download_path, self.release.update_path)
                logging.info("Downloaded update %s", self.release.update_path)
                Clock.schedule_del_safe(partial(self.set_state, IState.DOWNLOADED))
        except (OSError, requests.RequestException):
            logging.exception("Downloading update failed")
            #TODO Something like: self.error_msg = str(e)
            Clock.schedule_del_safe(partial(self.set_state, IState.FAILED))

    def decompress(self):
        self.progress = 0
        self._abort_process = False
        if os.path.isfile(self.release.image_path):
            self.state = IState.DECOMPRESSED
            return
        self.state = IState.DECOMPRESSING
        Thread(target=self._decompress_thread, name="Decompress-Update-Thread").start()

    def _decompress_thread(self):
        aborted = False
        decompress_path = self.release.image_path + '.decomp'
        try:
            if os.path.exists(decompress_path):
                os.remove(decompress_path)
            with lzma.open(self.release.update_path, 'rb') as compressed_file:
                with open(decompress_path, 'wb') as decompressed_file:
                    while True:
                        chunk = compressed_file.read(262144)
                        if chunk == b'':
                            break
                        if self._abort_process:
                            self._abort_process = False
                            aborted = True
                            break
                        decompressed_file.write(chunk)
                        self.progress += len(chunk)
            if aborted:
                os.remove(decompress_path)
                logging.info("Aborted decompressing %s", self.release.update_path)
                Clock.schedule_del_safe(partial(self.set_state, IState.ABORTED))
            else:
                os.rename(decompress_path, self.release.image_path)
                logging.info("Decompressed image %s", self.release.image_path)
                Clock.schedule_del_safe(partial(self.set_state, IState.DECOMPRESSED))
        except OSError:
            logging.exception("Decompressing image failed")
            Clock.schedule_del_safe(partial(self.set_state, IState.FAILED))

    def install(self):
        if self._install_proc is not None:
            raise Exception("Installation already running")
        self._abort_process = False
        cmd = ['sudo', 'svup', '-t', 'install', self.release.image_path]
        def _preexec_install():
            os.setpgid(0, 0)
            os.nice(16)
        self._install_proc = subprocess.Popen(cmd, text=True,
                preexec_fn=_preexec_install,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.state = IState.INSTALLING
        Thread(target=self._wait_install, name="Install-Wait-Thread").start()

    def _wait_install(self):
        proc = self._install_proc
        while True:
            try:
                stdout, _stderr = proc.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                # Keep punching it until it's dead
                if self._abort_process:
                    proc.send_signal(SIGINT)
            else:
                break
        self._abort_process = False
        rc = proc.returncode
        if rc == 0:
            lines = stdout.splitlines()
            subvol = None
            for l in lines:
                if l.startswith('subvolume'):
                    parts = l.split()
                    if len(parts) == 2:
                        subvol = parts[1]
                        break
            self.subvol = subvol
            Clock.schedule_del_safe(partial(self.set_state, IState.INSTALLED))
        # 20: rsync stopped by signal. negative or > 128: svup stopped by signal
        elif rc == 20 or rc < 0 or rc > 128:
            Clock.schedule_del_safe(partial(self.set_state, IState.ABORTED))
        else:
            logging.error("svup installed failed with exit code %s. Output:\n\n%s\n", rc, stdout)
            Clock.schedule_del_safe(partial(self.set_state, IState.FAILED))
        self._install_proc = None

    def abort_process(self):
        if self.state is IState.DOWNLOADING or self.state is IState.DECOMPRESSING:
            self.state = IState.ABORTING
            self._abort_process = True
        elif self.state is IState.INSTALLING and self._install_proc is not None:
            logging.info("Terminating install process")
            self.state = IState.ABORTING
            self._abort_process = True
            # Stop with SIGINT because sudo has trouble forwarding SIGTERM
            self._install_proc.send_signal(SIGINT)


def stage_reboot(subvol):
    Thread(target=_stage_reboot_thread, args=(subvol,),
           name="Stage-Update-Thread").start()

def _stage_reboot_thread(subvol):
    #TODO Verify that this is an appropriate time to reboot
    proc = stage(subvol)
    if proc.returncode == 0:
        #TODO Go through klipper shutdown process instead
        tryboot()
    else:
        def error_msg(dt):
            notify = App.get_running_app().notify
            notify.show(f"Error while staging {subvol}",
                        f"'svup stage' returned exit code {proc.returncode}",
                        level="error")
            logging.error(proc.stdout)
        Clock.schedule_once(error_msg, 0)

def stage(subvol):
    return subprocess.run(['sudo', 'svup', '-v', 'stage', '-f', subvol],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def tryboot():
    subprocess.Popen(['sudo', 'svup', 'reboot'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
        if self.updater.has_newer():
            self.right_title = "Updates Available"
        else:
            self.right_title = str(self.updater.current_version)


class UpdateScreen(UltraScreen):
    """Screen listing all releases"""

    show_all_versions = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.updater = Updater()
        self.updater.bind(releases=self.draw_releases)

    def draw_releases(self, _instance, releases):
        self.ids.box.clear_widgets()
        if self.updater.current_version is None:
            self.ids.message.text = "Could not determine installed version"
            self.ids.message.state = 'transparent'
        elif self.updater.has_newer():
            self.ids.message.text = "An Update is available"
            self.ids.message.state = 'transparent'
        else:
             self.ids.message.text = "Your system is up to date"
             self.ids.message.state = 'green'
        self.ids.version_label.text = f"Current Version: {self.updater.current_version}"
        if self.show_all_versions:
            if len(releases):
                self.ids.box.add_widget(Divider(pos_hint={'center_x': 0.5}))
            for release in reversed(releases):
                self.ids.box.add_widget(SIRelease(release, self.updater))
        elif self.updater.has_newer():
            # Show just the newest update
            self.ids.box.add_widget(Divider(pos_hint={'center_x': 0.5}))
            self.ids.box.add_widget(SIRelease(releases[-1], self.updater))
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
        self.draw_releases(None, self.updater.releases)


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

    def __init__(self, release, updater, **kwargs):
        self.release = release
        self.upper_title = str(self.release.version)
        status = ""
        if updater.current_version is None or release.version_obj > updater.current_version:
            action = "Install"
        elif release.version_obj == updater.current_version:
            status = "Currently installed"
            action = "Reinstall"
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
        installer = Installer(self.release)
        InstallPopup(installer).open()
        self.dismiss()


class InstallPopup(BasePopup):

    # Used in progress display. Set to 0 to hide progress bar.
    total = NumericProperty(0)

    def __init__(self, installer, **kwargs):
        self.installer = installer
        self.release = installer.release
        self.installer.bind(state=self.state_change)
        self.installer.bind(progress=self.set_progress)
        self.progress_fmt = "{}"
        super().__init__(**kwargs)
        self.msg = self.ids.msg
        installer.download()

    def abort(self):
        """Triggered by cancel button"""
        if self.installer.state in {IState.DOWNLOADING, IState.DECOMPRESSING, IState.INSTALLING}:
            self.installer.abort_process()
        elif self.installer.state in {IState.IDLE, IState.INSTALLED, IState.ABORTED, IState.FAILED}:
            self.dismiss()

    def finalize(self):
        self.ids.confirm.enabled = False
        if not self.installer.subvol:
            raise Exception("Updater: Subvolume is not set while calling finalize()")
        self.msg.text = "Staging for reboot..."
        stage_reboot(self.installer.subvol)

    def set_progress(self, _instance, value):
        self.msg.text = self.progress_fmt.format(value / 1024**2)

    def set_total(self, total):
        """Set total to 0 disables the progress bar"""
        self.total = total
        if total:
            self.progress_fmt = "{{:4.1f}}/{:4.1f} MiB".format(total / 1024**2)
        else:
            self.progress_fmt = "{:4.1f} MiB"
        # Update display
        self.set_progress(None, self.installer.progress)

    def state_change(self, instance, state):
        if state is IState.DOWNLOADING:
            self.title = "Downloading " + self.release.version
            self.set_total(self.release.download_size)
        elif state is IState.DOWNLOADED:
            self.installer.decompress()
        elif state is IState.DECOMPRESSING:
            self.title = "Decompressing " + self.release.version
            self.set_total(self.release.get_uncompressed_size() or 0)
        elif state is IState.DECOMPRESSED:
            self.set_total(0)
            self.installer.install()
        elif state is IState.INSTALLING:
            self.title = "Installing " + self.release.version
            self.msg.text = "Installing..."
        elif state is IState.INSTALLED:
            if self.installer.subvol:
                self.ids.confirm.enabled = True
                self.ids.btn_cancel.text = "Cancel"
                self.msg.text = "Successfully installed"
                self.title = "Installed " + self.release.version
            else:
                self.msg.text = "Installation failed\nCould not determine subvolume"
                self.ids.btn_cancel.text = "Close"
        elif state is IState.ABORTING:
            self.title = "Aborting..."
        elif state is IState.ABORTED:
            self.title = "Aborted"
            self.msg.text = "Installation aborted"
            self.ids.btn_cancel.text = "Close"
        elif state is IState.FAILED:
            self.title = "Error"
            #TODO More useful error messages
            self.msg.text = "Installation failed"
            self.ids.btn_cancel.text = "Close"
