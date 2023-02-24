from abc import ABC
import configparser
import io
import logging
import os
from pathlib import Path
import shutil
from subprocess import run, DEVNULL, CalledProcessError
import tarfile
from urllib.request import urlopen

from util import Config, Apt, Pip, Git, unprivileged, Unprivileged, username


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

    def pip_depends(self) -> set[str]:
        return set()

    def run(self) -> None:
        pass

    def cleanup(self) -> None:
        pass


class Kivy(Action):
    """Install Kivy GUI Framework"""

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self.version = self.config.get('version')
        self.from_source = self.config.getboolean('from_source')
        self.cython_version = self.config.get('cython_version')

    def apt_depends(self) -> set[str]:
        # https://github.com/kivy/kivy/blob/master/doc/sources/installation/installation-rpi.rst
        return {
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
            "python3-pip",
            "python3-setuptools",
            "python3-venv",
            "tk-dev",
            "xclip",
            "xsel",
            # some stuff thats needed for installing gi",
            "libcairo2-dev",
            "libgif-dev",
            "libgirepository1.0-dev",
            "libjpeg-dev",

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
            "libjpeg-dev",
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

    def pip_depends(self) -> set[str]:
        depends = {"Cython==" + self.cython_version}
        if self.from_source:
            depends.add("kivy[base] @ https://github.com/kivy/kivy/archive/master.zip")
        else:
            depends.add("kivy[base]==" + self.version)
        return depends

    def run(self) -> None:
        self.setup_config()
        if self.version > "2.0.0":
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
        shutil.copy('config.ini', config_dir)

    def vkeyboard_patch(self) -> None:
        logging.info("Applying patch to kivy to fix custom VKeyboard")
        lib = self.general.venv / 'lib'
        # Use any python subversion that is found. Not very robust, but much
        # faster than 'pip show kivy'
        python_dir = next(iter(
            p for p in lib.iterdir() if p.name.startswith('python')))
        file = python_dir / 'site-packages/kivy/core/window/__init__.py'
        run(['patch', '--force', '--forward',
             '--no-backup-if-mismatch', '--reject-file=-',
             file, 'kivy-vkeyboard.patch'], check=True)


class Graphics(Action):
    """Graphical environment: either Xorg or just SDL2"""

    def apt_depends(self) -> set[str]:
        if self.general.graphics_provider == 'xorg':
            return {
                'xorg',
                'libsdl2-dev',
                'libsdl2-image-dev',
                'libsdl2-mixer-dev',
                'libsdl2-ttf-dev',
            }
        return set()

    def run(self) -> None:
        if self.general.graphics_provider == 'xorg':
            self.configure_xorg()
        else:
            self.install_sdl2_kmsdrm()
        run(['adduser', username(), 'render'], check=True)

    def configure_xorg(self) -> None:
        """Change line in Xwrapper.config so xorg feels inclined to start when
        asked by systemd"""
        run(['sed', '-i', 's/allowed_users=console/allowed_users=anybody/',
             '/etc/X11/Xwrapper.config'], check=True)
        # Configure DPMS
        shutil.copy('10-dpms.conf', '/etc/X11/xorg.conf.d')

    def install_sdl2_kmsdrm(self) -> None:
        """The KMS/DRM video driver of SDL2 allows Kivy to run without X11. The
        packages in the Debian repository are compiled without support for that
        though, so to use that it is necessary to compile SDL2 from source, as
        well as Kivy so it binds to the correct libraries.
        """
        logging.info("Installing SDL2...")
        prev_wd = Path.cwd()
        parts = ['SDL2-' + self.config.get('sdl_version'),
                 'SDL2_image-' + self.config.get('sdl_image_version'),
                 'SDL2_mixer-' + self.config.get('sdl_mixer_version'),
                 'SDL2_ttf-' + self.config.get('sdl_ttf_version')]
        main_url = 'https://libsdl.org/release/{part}.tar.gz'
        part_url = 'https://libsdl.org/projects/{name}/release/{part}.tar.gz'
        urls = [main_url.format(part=parts[0]),
                part_url.format(name="SDL_image", part=parts[1]),
                part_url.format(name="SDL_mixer", part=parts[2]),
                part_url.format(name="SDL_ttf", part=parts[3])]
        for url, part in zip(urls, parts):
            # Make sure to compile unprivileged
            self.compile_sdl2(url, part)
            run(['make', 'install'], check=True)
        run(['ldconfig', '-v'], check=True)
        os.chdir(prev_wd)

    @unprivileged
    def compile_sdl2(self, url, part):
        logging.debug("Downloading URL: %s", url)
        path = self.general.build_dir / 'sdl2'
        path.mkdir(parents=True, exist_ok=True)
        # Download to memory and extract
        with urlopen(url) as response:
            if response.status != 200:
                logging.error("Error while downloading %s", part)
                return
            buf = io.BytesIO(response.read())
            with tarfile.open(fileobj=buf) as tf:
                tf.extractall(path)

        # Compile
        os.chdir(path / part)
        if part.startswith('SDL2-'):
            run(['./configure',
                 '--enable-video-kmsdrm', '--disable-video-opengl',
                 '--disable-video-x11', '--disable-video-rpi'],
                check=True)
        else:
            run('./configure', check=True)
        run(['make', '-j', str(os.cpu_count())], check=True)


class KlipperDepends(Action):
    """Dependency packages for Klipper"""

    def apt_depends(self) -> set[str]:
        return {
            # Packages for python cffi
            'python3-dev',
            'libffi-dev',
            'build-essential',
            # kconfig requirements
            'libncurses-dev',
            # hub-ctrl
            'libusb-dev',
        }

    def pip_depends(self) -> set[str]:
        return {
            'cffi=='             + self.config['cffi_version'],
            'pyserial=='         + self.config['pyserial_version'],
            'greenlet=='         + self.config['greenlet_version'],
            'Jinja2=='           + self.config['jinja2_version'],
            'requests=='         + self.config['requests_version'],
            'websocket-client==' + self.config['websocket_client_version'],
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
ExecStart={python} {srcdir}/klippy/klippy.py -v -l /tmp/klippy.log
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

    def run(self) -> None:
        self.install_klippy()
        self.install_services()
        if self.config.getboolean('autostart'):
            self.enable_service()

    def install_klippy(self) -> None:
        logging.info("Installing klippy")
        shutil.copytree(self.general.srcdir / 'klippy', '/opt/klippy',
                dirs_exist_ok=True)

    def install_services(self) -> None:
        logging.debug("Installing systemd services")
        if self.general.graphics_provider == "xorg":
            requires = "Requires=start_xorg.service\n"
            xorg_service = self.XORG_SERVICE.format(user=username())
            Path('/etc/systemd/system/start_xorg.service').write_text(xorg_service)
        else:
            requires = ""
        service = self.SERVICE.format(
            requires=requires,
            user=username(),
            python=Pip(self.general).python,
            srcdir=self.general.srcdir)
        Path('/etc/systemd/system/klipper.service').write_text(service)

    def enable_service(self) -> None:
        logging.debug("Enabling klipper systemd service")
        run(['systemctl', 'enable', 'klipper.service'], check=True)


class Wifi(Action):
    """Wifi menu in GUI"""

    def apt_depends(self) -> set[str]:
        return {
            "network-manager",
            "python3-gi",  #TODO: Is this still needed?
        }

    def pip_depends(self) -> set[str]:
        return {
            "pydbus==" + self.config['pydbus_version'],
            "PyGObject",  #TODO: fix version
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
        Apt(self.general).uninstall(["dhcpcd5"])


class MonitorConf(Action):
    """Monitor configuration for certain 7" 1024x600 displays"""

    def run(self) -> None:
        run(['./LCDC7-better.sh', '-r', '90'], check=True)


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

    def pip_depends(self) -> set[str]:
        return {'zeroconf==' + self.config.get('zeroconf_version')}

    def run(self) -> None:
        self.reroute_ports()

    def reroute_ports(self) -> None:
        """Redirect port 80 -> 8008"""
        logging.debug("Reroute TCP Port 80 to 8008")
        run("iptables -A PREROUTING -t nat -p tcp --dport 80 -j REDIRECT --to-ports 8008".split(),
            check=True)
        run("iptables-save -f /etc/iptables/rules.v4".split(), check=True)


class MjpgStreamer(Action):
    """Webcam stream for Cura connection"""

    def apt_depends(self) -> set[str]:
        return {'gcc', 'cmake', 'libjpeg-dev'}
    
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
        with Unprivileged():
            logging.info("Compiling mjpg-streamer from source...")
            repo_path = self.general.build_dir / 'mjpg-streamer'
            Git(self.general).checkout(
                self.MJPG_STREAMER_URL, repo_path, branch='v1.0.0')
            logging.debug("Checked out mjpg-streamer at %s", repo_path)
            prev_wd = Path.cwd()
            os.chdir(repo_path / 'mjpg-streamer-experimental')
            run('make', check=True)
        run(['make', 'install'], check=True)
        os.chdir(prev_wd)

    def enable(self) -> None:
        shutil.copy('mjpg_streamer.service', '/etc/systemd/system/')
        run(['systemctl', 'enable', '--now', 'mjpg_streamer.service'])


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
