import os

xdg_data_home = os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
xdg_config_home = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
xdg_cache_home = os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache'))
xdg_state_home = os.environ.get('XDG_STATE_HOME', os.path.expanduser('~/.local/state'))

application_name = 'klippo'

def data_path():
    path = os.path.join(xdg_data_home, application_name)
    os.makedirs(path, exist_ok=True)
    return path

def config_path():
    path = os.path.join(xdg_config_home, application_name)
    os.makedirs(path, exist_ok=True)
    return path

def cache_path():
    path = os.path.join(xdg_cache_home, application_name)
    os.makedirs(path, exist_ok=True)
    return path

def state_path():
    path = os.path.join(xdg_state_home, application_name)
    os.makedirs(path, exist_ok=True)
    return path


def config():
    return os.path.join(config_path(), 'config')

def history():
    return os.path.join(state_path(), 'history.json')

def loaded_material():
    return os.path.join(state_path(), 'loaded_material.json')

def material_dir():
    path = os.path.join(data_path(), 'materials')
    os.makedirs(path, exist_ok=True)
    return path

def log_dir():
    path = os.path.join(data_path(), 'logs')
    os.makedirs(path, exist_ok=True)
    return path

#TODO: Thumbnail cache into cache_path()


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
