from abc import ABC
import configparser
import io
import logging
import os
from pathlib import Path
import shutil
from subprocess import run, DEVNULL, CalledProcessError
import tarfile
from typing import Union
from urllib.request import urlopen

from util import (Config, apt_uninstall, Pip, PipPkg, git_checkout,
                  unprivileged, Unprivileged, username)


class Action(ABC):

    def __init__(self, config: Config):
        self.general = config
        if config.parser.has_section(self.name()):
            self.config = config.parser[self.name()]
        else:
            self.config = configparser.SectionProxy(config.parser, self.name())

    @classmethod
    def name(cls):
        return cls.__name__.lower()

    @classmethod
    def description(cls):
        return cls.__doc__

    def setup(self) -> None:
        pass

    def apt_depends(self) -> set[str]:
        return set()

    def apt_build_depends(self) -> set[str]:
        return set()

    def pre_pip(self) -> None:
        pass

    def pip_depends(self) -> set[Union[str, PipPkg]]:
        return set()

    def run(self) -> None:
        pass

    def cleanup(self) -> None:
        pass

    def uninstall(self) -> None:
        pass


class Kivy(Action):
    """Install Kivy GUI Framework"""

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self.version = self.config.get('version')
        self.from_source = self.config.getboolean('from-source')
        self.cython_version = self.config.get('cython-version')

    def apt_depends(self) -> set[str]:
        packages = {
            "libgl1-mesa-glx",
            "libgles2-mesa",
            "libmtdev1",
            "libegl1-mesa",
            "libjpeg62",
            "xclip",
            "xsel",
        }
        if self.from_source:
            # https://github.com/kivy/kivy/blob/master/doc/sources/installation/installation-rpi.rst
            packages |= {
                "gstreamer1.0-alsa",
                "gstreamer1.0-omx",
                "gstreamer1.0-plugins-bad",
                "gstreamer1.0-plugins-base",
                "gstreamer1.0-plugins-good",
                "gstreamer1.0-plugins-ugly",
                "libbz2-dev",
                "libc6-dev",
                "libgdbm-dev",
                "libgl1-mesa-dev",
                "libgles2-mesa-dev",
                "libgstreamer1.0-dev",
                "libjpeg-dev",
                "libmtdev-dev",
                "libncursesw5-dev",
                "libsqlite3-dev",
                "libssl-dev",
                "pkg-config",
                "python3-dev",
                "python3-setuptools",
                "tk-dev",
                "libgif-dev",

                # Kivy Raspberry 4 specifics
                "build-essential",
                "gir1.2-ibus-1.0",
                "libasound2-dev",
                "libdbus-1-dev",
                "libdrm-dev",
                "libegl1-mesa-dev",
                "libfreetype6-dev",
                "libgbm-dev",
                "libibus-1.0-5",
                "libibus-1.0-dev",
                "libice-dev",
                "liblzma-dev",
                "libsm-dev",
                "libsndio-dev",
                "libtiff-dev",
                "libudev-dev",
                "libwayland-bin",
                "libwayland-dev",
                "libwebp-dev",
                "libxi-dev",
                "libxinerama-dev",
                "libxkbcommon-dev",
                "libxrandr-dev",
                "libxss-dev",
                "libxt-dev",
                "libxv-dev",
                "x11proto-randr-dev",
                "x11proto-scrnsaver-dev",
                "x11proto-video-dev",
                "x11proto-xinerama-dev",
            }
        return packages

    def pip_depends(self) -> set[Union[PipPkg, str]]:
        return {PipPkg("Cython", self.cython_version),
                PipPkg("kivy", self.version, extras=['base'], no_binary=self.from_source)}

    def run(self) -> None:
        self.setup_config()
        version_tuple = tuple(int(v) for v in self.version.split('.')[:2])
        if (2, 0) <= version_tuple < (2, 2):
            try:
                self.vkeyboard_patch()
            except CalledProcessError:
                logging.error("Could not apply keyboard patch to kivy. "
                    "Maybe the patch is already applied or the file changed.")
            except (StopIteration, FileNotFoundError):
                logging.exception("Could not apply keyboard patch to kivy")

    @unprivileged
    def setup_config(self) -> None:
        config_dir = Path('~/.kivy/').expanduser()
        config_dir.mkdir(exist_ok=True)
        shutil.copy(self.general.setup_dir / 'config.ini', config_dir)

    def vkeyboard_patch(self) -> None:
        logging.info("Applying patch to kivy to fix custom VKeyboard")
        lib = self.general.venv / 'lib'
        # Use any python subversion that is found. Not very robust, but much
        # faster than 'pip show kivy'
        python_dir = next(iter(
            p for p in lib.iterdir() if p.name.startswith('python')))
        file = python_dir / 'site-packages/kivy/core/window/__init__.py'
        # Sometimes the file has CRLF line endings, the patch requires LF
        run(['dos2unix', file], check=True)
        run(['patch', '--force', '--forward',
             '--no-backup-if-mismatch', '--reject-file=-',
             file, self.general.setup_dir / 'kivy-vkeyboard.patch'], check=True)


class Graphics(Action):
    """Graphical environment: either Xorg or just SDL2"""

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self.build_path = self.general.build_dir / 'sdl2'
        self.parts = ['SDL2-' + self.config.get('sdl-version'),
                      'SDL2_image-' + self.config.get('sdl-image-version'),
                      'SDL2_mixer-' + self.config.get('sdl-mixer-version'),
                      'SDL2_ttf-' + self.config.get('sdl-ttf-version')]
        self.from_source = self.config.getboolean('sdl2-from-source')

    def apt_depends(self) -> set[str]:
        pkgs = set()
        if self.general.graphics_provider == 'xorg':
            pkgs.add('xorg')
        if self.from_source:
            pkgs |= {'gcc', 'make'}
        elif self.general.parser.get('kivy', 'from-source'):
            pkgs |= {'libsdl2-dev',
                     'libsdl2-image-dev',
                     'libsdl2-mixer-dev',
                     'libsdl2-ttf-dev'}
        return pkgs

    def pre_pip(self) -> None:
        if self.from_source:
            self.install_sdl2_kmsdrm()

    def run(self) -> None:
        if self.general.graphics_provider == 'xorg':
            self.configure_xorg()
        run(['adduser', username(), 'render'], check=True)

    DPMS_CONF = """Section "Monitor"
    Identifier "LVDS0"
    Option "DPMS" "true"
EndSection

Section "ServerLayout"
    Identifier "ServerLayout0"
    Option "StandbyTime" "10"
    Option "SuspendTime" "20"
    Option "OffTime"     "30"
    Option "BlankTime"   "0"
EndSection
"""

    def configure_xorg(self) -> None:
        """Change line in Xwrapper.config so xorg feels inclined to start when
        asked by systemd"""
        run(['sed', '-i', 's/allowed_users=console/allowed_users=anybody/',
             '/etc/X11/Xwrapper.config'], check=True)
        # Configure DPMS
        Path('/etc/X11/xorg.conf.d/10-dpms.conf').write_text(self.DPMS_CONF)

    def install_sdl2_kmsdrm(self) -> None:
        """The KMS/DRM video driver of SDL2 allows Kivy to run without X11. The
        packages in the Debian repository are compiled without support for that
        though, so to use that it is necessary to compile SDL2 from source, as
        well as Kivy so it binds to the correct libraries.
        """
        logging.info("Installing SDL2...")
        prev_wd = Path.cwd()
        main_url = 'https://libsdl.org/release/{part}.tar.gz'
        part_url = 'https://libsdl.org/projects/{name}/release/{part}.tar.gz'
        parts = self.parts
        urls = [main_url.format(part=parts[0]),
                part_url.format(name="SDL_image", part=parts[1]),
                part_url.format(name="SDL_mixer", part=parts[2]),
                part_url.format(name="SDL_ttf", part=parts[3])]
        for url, part in zip(urls, parts):
            if not self.test_sdl2_part(part):
                # Make sure to compile unprivileged
                self.compile_sdl2(url, part)
                run(['make', 'install'], check=True)
            else:
                logging.debug("%s already installed", part)
        run('ldconfig', check=True)
        os.chdir(prev_wd)

    def test_sdl2_part(self, part) -> bool:
        (name, _version) = part.split('-', 1)
        return Path(f"/usr/local/lib/lib{name}.so").is_file()

    @unprivileged
    def compile_sdl2(self, url: str, part: str):
        self.build_path.mkdir(parents=True, exist_ok=True)
        part_path = self.build_path / part
        if not part_path.is_dir():
            logging.debug("Downloading URL: %s", url)
            # Download to memory and extract
            with urlopen(url) as response:
                if response.status != 200:
                    logging.error("Error while downloading %s", part)
                    return
                buf = io.BytesIO(response.read())
                with tarfile.open(fileobj=buf) as tf:
                    tf.extractall(self.build_path)
        else:
            logging.debug("Using cached %s at %s", part, part_path)

        # Compile
        os.chdir(part_path)
        if part.startswith('SDL2-'):
            run(['./configure',
                 '--enable-video-kmsdrm', '--disable-video-opengl',
                 '--disable-video-x11', '--disable-video-rpi'],
                check=True)
        else:
            run('./configure', check=True)
        run(['make', '-j', str(os.cpu_count())], check=True)

    def cleanup(self) -> None:
        with Unprivileged():
            if self.build_path.is_dir():
                shutil.rmtree(self.build_path)

    def uninstall(self) -> None:
        """Uninstall self-compiled SDL2
        Note that this doesn't have any effect if the sources have already been cleaned
        up.
        """
        prev_wd = Path.cwd()
        for part in self.parts:
            part_path = self.build_path / part
            if part_path.is_dir():
                logging.debug("Uninstalling %s", part)
                os.chdir(part_path)
                run(['make', 'uninstall'], check=True)
        os.chdir(prev_wd)


class KlipperDepends(Action):
    """Dependency packages for Klipper"""

    def apt_depends(self) -> set[str]:
        return {
            'git',
            # Packages for python cffi
            'gcc',
            'python3-dev',
            'libffi-dev',
            'build-essential',
            # kconfig requirements
            'make',
            'libncurses-dev',
            # hub-ctrl
            'libusb-dev',
        }

    def pip_depends(self) -> set[Union[str, PipPkg]]:
        return {
            'cffi=='             + self.config['cffi-version'],
            'pyserial=='         + self.config['pyserial-version'],
            'greenlet=='         + self.config['greenlet-version'],
            'Jinja2=='           + self.config['jinja2-version'],
            'requests=='         + self.config['requests-version'],
            'websocket-client==' + self.config['websocket-client-version'],
            'packaging=='        + self.config['packaging-version'],
        }


class Install(Action):
    """Install Klippo to system"""

    SERVICE = """[Unit]
Description="Klipper with GUI"
{requires}
[Service]
Type=simple
User={user}
Environment=DISPLAY=:0
ExecStart={python} {srcdir}/klippy/klippy.py -v
Nice=-19
Restart=always
RestartSec=10
TimeoutSec=25

[Install]
WantedBy=multi-user.target
"""

    XORG_SERVICE = """[Unit]
Description="Start Xorg"
Requires=multi-user.target

[Service]
Type=simple
User={user}
ExecStart=startx

[Install]
WantedBy=multi-user.target
"""

    def __init__(self, config: Config):
        super().__init__(config)
        self.klipper_service = Path('/etc/systemd/system/klipper.service')
        self.xorg_service = Path('/etc/systemd/system/start_xorg.service')

    def run(self) -> None:
        #TODO self.install_klippy()
        self.install_services()
        if self.config.getboolean('autostart'):
            self.enable_service()

    def install_klippy(self) -> None:
        #TODO
        logging.info("Installing klippy")
        shutil.copytree(self.general.srcdir / 'klippy', '/opt/klippy',
                dirs_exist_ok=True)

    def install_services(self) -> None:
        logging.debug("Installing systemd services")
        if self.general.graphics_provider == "xorg":
            requires = "Requires=start_xorg.service\n"
            xorg_service = self.XORG_SERVICE.format(user=username())
            self.xorg_service.write_text(xorg_service)
        else:
            requires = ""
        service = self.SERVICE.format(
            requires=requires,
            user=username(),
            python=Pip(self.general).python,
            srcdir=self.general.srcdir)
        self.klipper_service.write_text(service)

    def enable_service(self) -> None:
        logging.debug("Enabling klipper systemd service")
        run(['systemctl', 'enable', 'klipper.service'], check=True)

    def uninstall(self) -> None:
        logging.debug("Removing systemd units")
        run(['systemctl', 'disable', 'klipper.service'])
        self.klipper_service.unlink(missing_ok=True)
        self.xorg_service.unlink(missing_ok=True)


class Wifi(Action):
    """Wifi menu in GUI"""

    def apt_depends(self) -> set[str]:
        return {
            "network-manager",
            # Dependencies for building PyGObject
            "libgirepository1.0-dev",
            "gcc",
            "libcairo2-dev",
            "pkg-config",
            "python3-dev",
        }

    def pip_depends(self) -> set[Union[str, PipPkg]]:
        return {
            "pydbus==" + self.config['pydbus-version'],
            "PyGObject== " + self.config['pygobject-version'],
        }

    def run(self) -> None:
        self.allow_wifi_scan()
        self.enable_nm()
        self.remove_dhcpcd5()

    def allow_wifi_scan(self) -> None:
        """Needed to allow wifi scanning to non-root users. Adds option
        'auth-polkit=false' in [main] section if it doesn't exist already.
        """
        file = Path('/etc/NetworkManager/NetworkManager.conf')
        if 'auth-polkit' not in file.read_text():
            run(['sed', '-i', r'/\[main\]/a auth-polkit=false', file],
                check=True)
    
    def enable_nm(self) -> None:
        """See do_netconf() in /usr/bin/raspi-config
        Ensure that NetworkManager will run (the service is enabled when
        installing network-manager, but not if it was already installed)
        """
        proc = run('systemctl -q is-enabled NetworkManager'.split(),
                   stdout=DEVNULL, stderr=DEVNULL)
        if proc.returncode != 0:
            run('systemctl -q enable NetworkManager'.split(), check=True)

    def remove_dhcpcd5(self) -> None:
        apt_uninstall(["dhcpcd5"])


class MonitorConf(Action):
    """Monitor configuration for certain 7" 1024x600 displays"""

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self.boot_cfg_file = Path('/boot/config.txt')
        self.mark_old = "#OLD_CFG "
        self.mark_new = " #LCD_CFG"

        self.rotation = self.config.getint('rotation')
        if self.rotation not in {0, 90, 180, 270}:
            raise ValueError(f"Invalid rotation value: {self.rotation}")
        self.set_modeline = self.config.getboolean('set-modeline')

    LIBINPUT_CONF = """Section "InputClass"
        Identifier "libinput pointer catchall"
        MatchIsPointer "on"
        MatchDevicePath "/dev/input/event*"
        Driver "libinput"
EndSection

Section "InputClass"
        Identifier "libinput keyboard catchall"
        MatchIsKeyboard "on"
        MatchDevicePath "/dev/input/event*"
        Driver "libinput"
EndSection

Section "InputClass"
        Identifier "libinput touchpad catchall"
        MatchIsTouchpad "on"
        MatchDevicePath "/dev/input/event*"
        Driver "libinput"
EndSection

Section "InputClass"
        Identifier "libinput touchscreen catchall"
        MatchIsTouchscreen "on"
        Option "CalibrationMatrix" "{matrix}"
        MatchDevicePath "/dev/input/event*"
        Driver "libinput"
EndSection

Section "InputClass"
        Identifier "libinput tablet catchall"
        MatchIsTablet "on"
        MatchDevicePath "/dev/input/event*"
        Driver "libinput"
EndSection
"""

    MODELINE_CONF = """Section "Monitor"
        Identifier "HDMI-1"
        Modeline "1024x600_60.00"   49.00  1024 1072 1168 1312  600 603 613 624 -hsync +vsync
        Option "PreferredMode" "1024x600_60.00"
EndSection

Section "Screen"
        Identifier "Screen0"
        Monitor "HDMI-1"
        DefaultDepth 24
        SubSection "Display"
                Modes "1024x600_60.00"
        EndSubSection
EndSection
"""

    def run(self) -> None:
        lcd_config = {
            'dtparam=i2c_arm': 'on',
            'dtparam=spi': 'on',
            'enable_uart': 1,
            'max_usb_current': 1,
            'hdmi_force_hotplug': 1,
            'config_hdmi_boost': 7,
            'hdmi_drive': 1,
            'hdmi_group': 2,
            'hdmi_mode': 87,
            'hdmi_cvt': '1024 600 60 6 0 0 0',
            'hdmi_blanking': 1,
        }
        if self.rotation == 0:
            input_matrix = '1 0 0 0 1 0 0 0 1'
        elif self.rotation == 90:
            input_matrix = '0 1 0 -1 0 1 0 0 1'
        elif self.rotation == 180:
            input_matrix = '1 0 1 0 -1 1 0 0 1'
        elif self.rotation == 270:
            input_matrix = '0 -1 1 1 0 0 0 0 1'
        else:
            raise ValueError(f"Invalid rotation value: {self.rotation}")

        #TODO: handle existing files better
        libinput_conf = self.LIBINPUT_CONF.format(matrix=input_matrix)
        conf_dir = Path("/etc/X11/xorg.conf.d")
        conf_dir.mkdir(parents=True, exist_ok=True)
        (conf_dir / '40-libinput.conf').write_text(libinput_conf)
        if self.set_modeline:
            (conf_dir / '00-monitor.conf').write_text(self.MODELINE_CONF)


        boot_cfg = self.boot_cfg_file.read_text().splitlines()
        to_remove = []
        for i, line in enumerate(boot_cfg):
            # Remove configuration from previous runs
            if line.endswith(self.mark_new):
                to_remove.append(i)
                continue
            # Disable conflicing configuration lines
            for k in lcd_config.keys():
                if line.startswith(k):
                    boot_cfg[i] = self.mark_old + line
                    break
        for i in reversed(to_remove):
            del boot_cfg[i]

        for k, v in lcd_config.items():
            boot_cfg.append(k + '=' + str(v) + self.mark_new)
        self.boot_cfg_file.write_text('\n'.join(boot_cfg) + '\n')

    def uninstall(self) -> None:
        boot_cfg = self.boot_cfg_file.read_text().splitlines()
        to_remove = []
        for i, line in enumerate(boot_cfg):
            # Restore previous configuration
            if line.startswith(self.mark_old):
                boot_cfg[i] = line[len(self.mark_old):]
            # Remove configuration added by setup
            elif line.endswith(self.mark_new):
                to_remove.append(i)

        for i in reversed(to_remove):
            del boot_cfg[i]


class Cura(Action):
    """Support direct connection with Cura"""

    IPTABLES_DEBCONF = \
"""iptables-persistent iptables-persistent/autosave_v4 boolean false
iptables-persistent iptables-persistent/autosave_v6 boolean false"""
    def setup(self) -> None:
        logging.debug("Setting iptables installation configuration")
        run('debconf-set-selections', input=self.IPTABLES_DEBCONF, text=True,
            check=True)

    def apt_depends(self) -> set[str]:
        return {'iptables-persistent'}

    def pip_depends(self) -> set[Union[str, PipPkg]]:
        return {'zeroconf==' + self.config.get('zeroconf-version')}

    def run(self) -> None:
        self.reroute_ports()

    IPTABLE = """*nat
:PREROUTING ACCEPT [0:0]
:INPUT ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
:POSTROUTING ACCEPT [0:0]
-A PREROUTING -p tcp -m tcp --dport 80 -j REDIRECT --to-ports 8008
COMMIT
"""

    def reroute_ports(self) -> None:
        """Redirect port 80 -> 8008"""
        logging.debug("Reroute TCP Port 80 to 8008")
        Path('/etc/iptables/rules.v4').write_text(self.IPTABLE)
        #TODO give the option to immediately apply rules
        """
        rule = "-p tcp --dport 80 -j REDIRECT --to-ports 8008".split()
        if run("iptables -C PREROUTING -t nat".split() + rule).returncode != 0:
            run("iptables -A PREROUTING -t nat".split() + rule, check=True)
            run("iptables-save -f /etc/iptables/rules.v4".split(), check=True)
        """

    def uninstall(self) -> None:
        logging.debug("Reset port rerouting")
        run("iptables -F PREROUTING -t nat".split(), check=True)
        run("iptables-save -f /etc/iptables/rules.v4".split(), check=True)


class MjpgStreamer(Action):
    """Webcam stream for Cura connection"""

    def __init__(self, config: Config):
        super().__init__(config)
        self.repo_path = self.general.build_dir / 'mjpg-streamer'
        self.service_path = Path('/etc/systemd/system/mjpg_streamer.service')

    def apt_depends(self) -> set[str]:
        return {'gcc', 'cmake', 'libjpeg-dev', 'git'}
    
    def run(self) -> None:
        if not self.test_mjpg_streamer():
            self.compile()
        else:
            logging.debug("mjpg-streamer already installed, skipping")
        self.enable()

    def test_mjpg_streamer(self) -> bool:
        """Return whether mjpg-streamer is already installed"""
        try:
            run(['mjpg_streamer', '-v'], stdout=DEVNULL, check=True)
        except (FileNotFoundError, CalledProcessError):
            return False
        return True

    MJPG_STREAMER_URL = "https://github.com/jacksonliam/mjpg-streamer.git"

    def compile(self) -> None:
        tag = self.config.get('tag')
        with Unprivileged():
            describe = run(['git', '-C', self.repo_path, 'describe', '--tags'],
                           capture_output=True, text=True)
            if describe.returncode != 0 or describe.stdout.rstrip() != tag:
                self.cleanup()
                logging.debug("Checking out mjpg-streamer at %s", self.repo_path)
                git_checkout(self.MJPG_STREAMER_URL, self.repo_path, branch='v1.0.0')
            else:
                logging.debug("Using cached mjpg-streamer repo at %s",
                              self.repo_path)
            logging.info("Compiling mjpg-streamer from source...")
            prev_wd = Path.cwd()
            os.chdir(self.repo_path / 'mjpg-streamer-experimental')
            run('make', check=True)
        run(['make', 'install'], check=True)
        os.chdir(prev_wd)

    MJPG_SERVICE = """[Unit]
Description=A server for streaming Motion-JPEG from a video capture device
After=network.target

[Service]
ExecStart=/usr/local/bin/mjpg_streamer -i 'input_raspicam.so -fps 24 -x 800 -y 600' -o 'output_http.so -p 8080'

[Install]
WantedBy=multi-user.target
"""

    def enable(self) -> None:
        self.service_path.write_text(self.MJPG_SERVICE)
        run(['systemctl', 'enable', '--now', 'mjpg_streamer.service'])

    def cleanup(self) -> None:
        with Unprivileged():
            if self.repo_path.is_dir():
                shutil.rmtree(self.repo_path)

    def uninstall(self) -> None:
        logging.debug("Uninstalling mjpg-streamer")
        run(['systemctl', 'disable', '--now', 'mjpg_streamer.service'])
        self.service_path.unlink(missing_ok=True)
        Path("/usr/local/bin/mjpg_streamer").unlink(missing_ok=True)
        shutil.rmtree(Path("/usr/local/share/mjpg-streamer"), ignore_errors=True)
        shutil.rmtree(Path("/usr/local/lib/mjpg-streamer"), ignore_errors=True)


class Usbmount(Action):
    """Install usbmount for automatic flash drive mounting"""
    def __init__(self, config: Config):
        super().__init__(config)
        self.build_dir = self.general.build_dir / 'usbmount'
        self.src_dir = self.build_dir / 'usbmount'

    def apt_depends(self) -> set[str]:
        if not self.test_usbmount():
            #TODO Install debhelper with --no-install-recommends
            return {'git', 'debhelper', 'build-essential'}
        return set()

    def run(self) -> None:
        if not self.test_usbmount():
            pkg = self.package()
            run(['dpkg', '--force-confold', '--force-depends', '-i', pkg])
            # Install dependencies using apt
            run(['apt-get', 'install', '--yes', '--fix-broken'], check=True)
        else:
            logging.debug("Usbmount already installed, skipping")
        shutil.copy(self.general.setup_dir / 'usbmount.conf', '/etc/usbmount/')

    URL = "https://github.com/moodlebox/usbmount.git"
    COMMIT = "616ac50e604bc9f8a04aa3ec7bcd7650cea68805"

    @unprivileged
    def package(self) -> Path:
        logging.info("Installing usbmount")
        shutil.rmtree(self.src_dir, ignore_errors=True)
        self.src_dir.mkdir(parents=True, exist_ok=True)
        git_checkout(self.URL, self.src_dir, shallow=False)
        prev_wd = Path.cwd()
        os.chdir(self.src_dir)
        run(['git', 'checkout', self.COMMIT], check=True)
        run(['dpkg-buildpackage', '-us', '-uc', '-b'], check=True)
        os.chdir(prev_wd)
        return self.build_dir / 'usbmount_0.0.26_all.deb'

    def test_usbmount(self) -> bool:
        return Path('/usr/share/usbmount/usbmount').is_file()

    def cleanup(self) -> None:
        with Unprivileged():
            shutil.rmtree(self.build_dir, ignore_errors=True)
        apt_uninstall(["debhelper"])

    def uninstall(self) -> None:
        apt_uninstall(["usbmount"])


class Swap(Action):
    """Increase swap size (necessary for some compilations on 1GB boards)"""
    def setup(self) -> None:
        size = self.config.getint('size')
        logging.info("Setting swap size to %dMB", size)
        run(['dphys-swapfile', 'swapoff'], check=True)
        run(['sed', '-i', f's/CONF_SWAPSIZE=[0-9]\+/CONF_SWAPSIZE={size}/',
             '/etc/dphys-swapfile'], check=True)
        run(['dphys-swapfile', 'setup'], check=True)
        run(['dphys-swapfile', 'swapon'], check=True)


class AVRChip(Action):
    """AVR chip installation and building"""
    def apt_depends(self) -> set[str]:
        return {'gcc-avr', 'binutils-avr', 'avr-libc', 'avrdude'}

class ARMChip(Action):
    """ARM chip installation and building"""
    def apt_depends(self) -> set[str]:
        return {
            'gcc-arm-none-eabi',
            'binutils-arm-none-eabi',
            'libnewlib-arm-none-eabi',
            'dfu-util',
            'stm32flash',
        }
