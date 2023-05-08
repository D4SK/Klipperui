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

    progress = NumericProperty(0)
    install_output = StringProperty()

    def __init__(self, data):
        """Read data from GitHub API into object. For Response schema see:
        https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28
        """
        self.register_event_type('on_download_end')
        self.register_event_type('on_decompressed')
        self.register_event_type('on_install_finished')
        # Aborts whatever process is currently running (download, decompression or installation)
        self.abort_process = False

        self.version = data['tag_name']
        self.draft = data['draft']
        self.prerelease = data['prerelease']
        self.title = data['name']
        self.message = data['body']
        self.distance = -1

        try:
            asset = next(a for a in data['assets']
                         if a['name'].startswith('image') and
                            a['name'].endswith('.xz'))
        except (StopIteration, LookupError):
            raise ValueError("Could not find image asset in release data") from None
        self.update_url = asset['url']
        self.update_filename = asset['name']
        self.update_path = os.path.join(location.update_dir(), self.update_filename)
        self.download_size = asset['size']
        # Uncompressed image
        self.image_path = self.update_path[:-3]

    def download(self):
        self.progress = 0
        self.abort_process = False
        if os.path.isfile(self.update_path):
            #TODO Verify existing file
            self.dispatch('on_download_end', True, 'Existing file found')
            return
        Thread(target=self._download_thread, name="Download-Update-Thread").start()

    def _download_thread(self):
        aborted = False
        # Download to PATH.part first
        download_path = self.update_path + '.part'
        try:
            if os.path.exists(download_path):
                os.remove(download_path)
            with requests.get(self.update_url,
                stream=True,
                allow_redirects=True,
                headers=Updater._headers | {"Accept": "application/octet-stream"},
                timeout=10,
            ) as r:
                r.raise_for_status()
                with open(download_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=262144):
                        if self.abort_process:
                            self.abort_process = False
                            aborted = True
                            break
                        f.write(chunk)
                        self.progress += len(chunk)
            if aborted:
                os.remove(download_path)
                logging.info("Aborted downloading of %s", self.update_filename)
                Clock.schedule_del_safe(partial(self.dispatch, 'on_download_end', False, 'Download aborted'))
            else:
                #TODO Verify downloaded file
                os.rename(download_path, self.update_path)
                logging.info("Downloaded update %s", self.update_filename)
                Clock.schedule_del_safe(partial(self.dispatch, 'on_download_end', True))
        except (OSError, requests.RequestException) as e:
            logging.exception("Downloading update failed")
            Clock.schedule_del_safe(partial(self.dispatch, 'on_download_end', False, str(e)))

    def decompress(self):
        self.progress = 0
        self.abort_process = False
        if os.path.isfile(self.image_path):
            self.dispatch('on_decompressed', True, 'Existing file found')
            return
        Thread(target=self._decompress_thread, name="Decompress-Update-Thread").start()

    def _decompress_thread(self):
        aborted = False
        decompress_path = self.image_path + '.decomp'
        try:
            if os.path.exists(decompress_path):
                os.remove(decompress_path)
            with lzma.open(self.update_path, 'rb') as compressed_file:
                with open(decompress_path, 'wb') as decompressed_file:
                    while True:
                        chunk = compressed_file.read(262144)
                        if chunk == b'':
                            break
                        if self.abort_process:
                            self.abort_process = False
                            aborted = True
                            break
                        decompressed_file.write(chunk)
                        self.progress += len(chunk)
            if aborted:
                os.remove(decompress_path)
                logging.info("Aborted decompressing %s", self.update_filename)
                Clock.schedule_del_safe(partial(self.dispatch, 'on_decompressed', False, 'Decompression aborted'))
            else:
                os.rename(decompress_path, self.image_path)
                logging.info("Decompressed image %s", self.image_path)
                Clock.schedule_del_safe(partial(self.dispatch, 'on_decompressed', True))
        except OSError as e:
            logging.exception("Decompressing image failed")
            Clock.schedule_del_safe(partial(self.dispatch, 'on_decompressed', False, str(e)))

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
            if self.abort_process:
                self.abort_process = False
                proc.terminate()
                logging.info("Update: Installation aborted!")
                Clock.schedule_del_safe(lambda: self.dispatch("on_install_finished", None))
                break
            line = proc.stdout.readline()
            if not line:
                rc = proc.wait()
                Clock.schedule_del_safe(lambda: self.dispatch("on_install_finished", rc))
                break
            # Highlight important lines
            #if line.startswith("===>"):
            #    line = "[b][color=ffffff]" + line + "[/color][/b]"
            self.install_output += line

    def on_download_end(self, success, reason=None, last=None):
        pass
    def on_decompressed(self, success, reason=None):
        pass
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

    def __init__(self, release, **kwargs):
        self.release = release
        self.set_total(self.release.download_size)
        super().__init__(**kwargs)
        self.msg = self.ids.msg
        self.set_progress(None, 0)

    def install(self):
        """Triggered by confirm button"""
        InstallPopup(self.release).open()
        self.dismiss()
        self.release.install()

    def abort(self):
        """Triggered by cancel button"""
        self.dismiss()
        self.release.abort_process = True

    def set_progress(self, _instance, value):
        self.msg.text = self.progress_fmt.format(value / 1024**2)

    def set_total(self, total):
        self.total = total
        if total is not None:
            self.progress_fmt = "{{:4.1f}}/{:4.1f} MiB".format(total / 1024**2)
        else:
            self.progress_fmt = "{:4.1f} MiB"

    def on_download_end(self, instance, success, reason=None):
        if success:
            self.set_total(self.release.get_uncompressed_size())
            self.title = "Decompressing " + self.release.version
            self.release.decompress()
        else:
            self.msg.text = "Download failed:\n" + reason

    def on_decompressed(self, instance, success, reason=None):
        self.release.unbind(progress=self.set_progress)
        if success:
            self.ids.confirm.enabled = True  # Allow moving on to install
            self.set_total(None)
            self.title = "Acquired update " + self.release.version
            self.msg.text = f"Press '{self.ids.confirm.text}' to apply update"
        else:
            self.msg.text = "Decompression failed:\n" + reason

    def on_open(self):
        super().on_open()
        self.release.bind(on_download_end=self.on_download_end)
        self.release.bind(on_decompressed=self.on_decompressed)
        self.release.bind(progress=self.set_progress)

    def on_dismiss(self):
        super().on_dismiss()
        self.release.unbind(on_download_end=self.on_download_end)
        self.release.unbind(on_decompressed=self.on_decompressed)
        self.release.unbind(progress=self.set_progress)


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
