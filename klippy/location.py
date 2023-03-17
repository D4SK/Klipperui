import os

xdg_data_home = os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
xdg_config_home = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
xdg_cache_home = os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache'))
xdg_state_home = os.environ.get('XDG_STATE_HOME', os.path.expanduser('~/.local/state'))

application_name = 'klippo'

_data_path = os.path.join(xdg_data_home, application_name)
_config_path = os.path.join(xdg_config_home, application_name)
_cache_path = os.path.join(xdg_cache_home, application_name)
_state_path = os.path.join(xdg_state_home, application_name)

def data_path():
    os.makedirs(_data_path, exist_ok=True)
    return _data_path

def config_path():
    os.makedirs(_config_path, mode=0o700, exist_ok=True)
    return _config_path

def cache_path():
    os.makedirs(_cache_path, mode=0o700, exist_ok=True)
    return _cache_path

def state_path():
    os.makedirs(_state_path, exist_ok=True)
    return _state_path


def config():
    return os.path.join(config_path(), 'config')

def history():
    return os.path.join(state_path(), 'history.json')

def loaded_material():
    return os.path.join(state_path(), 'loaded_material.json')

def material_dir():
    path = os.path.join(_data_path, 'materials')
    os.makedirs(path, exist_ok=True)
    return path

def log_dir():
    path = os.path.join(_data_path, 'logs')
    os.makedirs(path, exist_ok=True)
    return path

def metadata_cache():
    path = os.path.join(cache_path(), 'metadata')
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


class Location:
    """Paths that may depend on configuration"""

    def __init__(self, config):
        self.config = config

    def print_files(self):
        path = self.config.getsection('virtual_sdcard').get('path', '~/Klippo')
        path = os.path.realpath(os.path.expanduser(path))
        os.makedirs(path, exist_ok=True)
        return path

    def usb_mountpoint(self):
        path = os.path.join(self.print_files(), 'USB-Device')
        try:
            os.symlink('/media/usb0', path)
        except FileExistsError:
            pass
        return path
