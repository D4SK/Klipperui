from argparse import ArgumentParser, SUPPRESS, Namespace
import configparser
from collections.abc import Callable
import logging
import os
from pathlib import Path
import pwd
from subprocess import run
import sys
from typing import Iterable, Union, Optional, Any

def unprivileged(uid, gid: Optional[int] = None) -> Callable:
    # Directly called: @unprivileged
    if callable(uid):
        func = uid
        def inner(*args, **kwargs):
            with Unprivileged():
                func(*args, **kwargs)
        return inner
    # Called with parameters: @unprivileged(uid, gid)
    def decorate(func):
        def inner(*args, **kwargs):
            with Unprivileged(uid, gid):
                func(*args, **kwargs)
        return inner
    return decorate


class Unprivileged:

    UID = 1000
    GID = 1000
    # To keep tracking of nested calls
    count = 0

    def __init__(self, uid: Optional[int] = None, gid: Optional[int] = None
    ) -> None:
        self.uid = uid or self.UID
        self.gid = gid or self.GID

    def __enter__(self):
        __class__.count += 1
        if os.geteuid() == self.uid and os.getegid() == self.gid:
            return
        os.setresgid(self.gid, self.gid, 0)
        os.setresuid(self.uid, self.uid, 0)

    def __exit__(self, *_args):
        __class__.count -= 1
        if __class__.count <= 0:
            os.setresgid(0, 0, 0)
            os.setresuid(0, 0, 0)


def username() -> str:
    return pwd.getpwuid(Unprivileged.UID).pw_name


class Config:

    DEFAULT_FILE = Path("default.cfg")

    def __init__(self, all_actions: list[Any]) -> None:
        self._setup_logging()
        conf_path, args = self._get_config_file()
        conf = self._read_conf(conf_path)
        self.parser = conf
        cli_args = self._get_cli_args(args)

        # Print available actions and exit
        if cli_args.list:
            for a in all_actions:
                print(f"{a.name()} - {a.description()}")
            sys.exit(0)

        actions = [a.name() for a in all_actions]
        # Apply configuration changes from command line
        for k, v in vars(cli_args).items():
            if '__' in k:
                sect, opt = k.split('__', 1)
                conf.set(sect, opt, v)
        
        path = self._path
        general = conf['general']
        self.verbose = conf.getboolean('general', 'verbose')
        if self.verbose:
            logger = logging.getLogger()
            logger.setLevel(logging.DEBUG)
        self.graphics_provider = self.get_graphics_provider()

        self.setup_dir = path(general['setup_dir'])
        self.build_dir = path(general['build_dir'])
        self.srcdir = path(general['srcdir'])
        self.python = path(general['python'])
        self.venv = path(general['venv'])

        self.actions = self._resolve_actions(
            general['include'].split(), cli_args, actions)

    def get_graphics_provider(self) -> str:
        provider = self.parser.get('graphics', 'provider')
        if provider.lower() == 'xorg':
            return 'xorg'
        elif provider.lower() == 'sdl2':
            return 'sdl2'
        else:
            logging.critical(f"Invalid provider: {provider}")
            sys.exit(10)

    def _setup_logging(self) -> None:
        logging.basicConfig(format='==> %(message)s', level=logging.INFO)

    def _resolve_actions(
        self, actions: list[str], args: Namespace, all_actions: list[str]
    ) -> list[str]:
        enable = set(args.enable)
        disable = set(args.disable)
        specified = enable | disable | set(args.include) | set(args.exclude)
        set_all = set(all_actions)
        if not specified <= set_all:
            logging.critical(
                f"Argument error: Unrecognized actions {specified - set_all}")
            sys.exit(10)

        if args.enable_all:
            if specified:
                logging.critical("Cannot use --enable-all together with "
                    "enable/disable or include/exclude")
                sys.exit(10)
            actions = all_actions
        elif enable or disable:
            if enable & disable:
                logging.critical(f"Argument error: {enable & disable} are "
                    "both enabled and disabled!")
                sys.exit(10)
            if args.include or args.exclude:
                logging.critical("Argument error: Cannot simultaneously use "
                    "enable/disable and include/exclude")
                sys.exit(10)
            for a in enable:
                if a not in actions:
                    actions.append(a)
            for a in disable:
                try:
                    actions.remove(a)
                except ValueError:
                    pass
        elif args.include and args.exclude:
            logging.critical(
                "Argument error: Cannot specify both include and exclude")
            sys.exit(10)
        elif args.include:
            actions = args.include
        elif args.exclude:
            actions = all_actions
            for a in args.exclude:
                actions.remove(a)
        logging.info("Enabled actions: %s", " ".join(actions))
        logging.debug("Disabled actions: %s", " ".join(set_all - set(actions)))
        return actions

    def _get_config_file(self) -> tuple[Optional[Path], list[str]]:
        """Get the location of the config file, either from command line
        arguments or from DEFAULT_FILE.
        """
        parser = ArgumentParser(add_help=False)
        parser.add_argument('-c', '--config', type=self._path,
                            help="Specify a config file to use. Default: default.cfg")
        cli_args, other = parser.parse_known_args()
        return cli_args.config, other

    def _read_conf(self, path: Optional[Path] = None) -> configparser.ConfigParser:
        if path is None:
            path = self.DEFAULT_FILE
        cur_path = path
        configs = []
        while True:
            # Interpolation is carried over from _dynamic_defaults()
            parser = configparser.ConfigParser(interpolation=None)
            try:
                # Use read_file instead of read for better error handling
                with open(cur_path, "r") as f:
                    parser.read_file(f)
            except (configparser.Error, OSError) as e:
                logging.critical(e)
                sys.exit(9)
            if not parser.has_section('general'):
                logging.critical("Missing section [general] in %s", path)
                sys.exit(9)

            configs.append(parser)
            if parser.has_option('general', 'inherit'):
                cur_path = Path(parser.get('general', 'inherit'))
                parser.remove_option('general', 'inherit')
            else:
                break
        # Merge configs in order
        configs.reverse()
        combined = self._dynamic_defaults()
        for c in configs:
            combined.read_dict(c)
        return combined

    def _dynamic_defaults(self) -> configparser.ConfigParser:
        """Default values that are usually determined at runtime but can be
        overriden in a config file if wanted.
        """
        default = configparser.ConfigParser(
            interpolation=configparser.ExtendedInterpolation())
        setup_dir = Path(__file__).resolve().parent
        default.read_dict({'general': {
            'home': pwd.getpwuid(Unprivileged.UID).pw_dir,
            'setup_dir': str(setup_dir),
            'build_dir': str(setup_dir / 'builds'),
            'srcdir': str(setup_dir.parent),
            'python': sys.executable,
            }
        })
        return default

    def _get_cli_args(self, args) -> Namespace:
        parser = ArgumentParser()
        parser.add_argument('-v', '--verbose', dest='general__verbose',
                            action='store_const', const='True',
                            default=SUPPRESS)
        parser.add_argument('-c', '--config', type=self._path,
                            help="Specify a config file to use. Default: default.cfg")
        parser.add_argument('-l', '--list', action='store_true',
                            help="List all available actions and exit")
        for section in self.parser.sections():
            if section == 'general':
                for opt, _v in self.parser.items(section, raw=True):
                    if opt not in {'verbose', 'include'}:
                        parser.add_argument(f"--{opt}",
                                            dest=f"general__{opt}",
                                            default=SUPPRESS,
                                            metavar=opt.upper())
            else:
                for opt, _v in self.parser.items(section, raw=True):
                    parser.add_argument(f"--{section}-{opt}",
                                        dest=f"{section}__{opt}",
                                        default=SUPPRESS,
                                        metavar=opt.upper())
        parser.add_argument('-e', '--enable', nargs='+',
                            default=[], action='extend',
                            help="Actions to enable additionally")
        parser.add_argument('-d', '--disable', nargs='+',
                            default=[], action='extend',
                            help="Actions to disable")
        parser.add_argument('--include', nargs='+', default=[], action='extend',
                            help="Explicitly set all actions to include")
        parser.add_argument('--exclude', nargs='+', default=[], action='extend',
                            help="Explicitly enable all actions except the specified ones")
        parser.add_argument('--enable-all', action='store_true',
                            help="Enable all actions")
        return parser.parse_args(args)

    @staticmethod
    def _path(string: str) -> Path:
        with Unprivileged():
            return Path(string).expanduser()


class Apt:

    def __init__(self, config: Config):
        self.config = config

    def install(self, packages: Iterable[str]):
        logging.info("Installing Debian packages using apt...")
        cmd = ["apt-get", "install", "--yes"]
        if not self.config.verbose:
            cmd.append("-qq")
        cmd.extend(packages)
        run(cmd, check=True)

    def uninstall(self, packages: Iterable[str]):
        cmd = ["apt-get", "purge", "--yes"]
        if not self.config.verbose:
            cmd.append("-qq")
        cmd.extend(packages)
        run(cmd, check=True)


class Pip:

    def __init__(self, config: Config):
        self.config = config
        self.venv = config.venv
        self.python = self.venv / 'bin/python3'
        self.pip_cmd: list[Union[str, Path]] = [self.python, '-m', 'pip']

    def verify_venv(self) -> None:
        if not self.venv.is_dir():
            self.create_venv(self.venv)

    @unprivileged
    def create_venv(self, path: Path):
        logging.info(f"Creating new virtual environment at {path}...")
        cmd: list[Union[str, Path]] = [self.config.python, "-m", "venv", path]
        run(cmd, check=True)

    @unprivileged
    def install(self, packages: Iterable[str]):
        self.verify_venv()
        if not packages:
            return
        logging.info("Installing Python packages using pip...")
        cmd = self.pip_cmd[:]
        if not self.config.verbose:
            cmd.append("--quiet")
        cmd.append("install")
        cmd.extend(packages)
        run(cmd, check=True)


class Git:

    def __init__(self, config: Config):
        self.config = config

    @unprivileged
    def checkout(
        self, url: str, directory: Optional[Path] = None,
        shallow: bool = True, branch: Optional[str] = None,
    ) -> None:
        cmd: list[Union[str, Path]] = ['git', 'clone']
        if shallow:
            cmd.extend(['--depth', '1'])
        if branch:
            cmd.extend(['--branch', branch])
        cmd.append(url)
        if directory:
            cmd.append(directory)
        run(cmd, check=True)
