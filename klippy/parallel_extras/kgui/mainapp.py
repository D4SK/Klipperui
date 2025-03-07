import logging
import threading
import os
import traceback
from os.path import join
from subprocess import Popen
from time import time

os.environ['KIVY_NO_CONSOLELOG'] = '1'  # Only use file logging
os.environ['KIVY_LOG_MODE'] = 'PYTHON'
os.environ['KIVY_NO_ARGS'] = '1'  # Disable kivy argument parsing

from kivy.config import Config

os.environ['KIVY_WINDOW'] = 'sdl2'
os.environ['KIVY_GL_BACKEND'] = 'sdl2'
os.environ['KIVY_METRICS_DENSITY'] = str(Config.getint('graphics', 'width')/600)

from kivy import kivy_data_dir
from kivy.app import App
from kivy.base import ExceptionHandler, ExceptionManager
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.properties import (OptionProperty, BooleanProperty, DictProperty,
                            NumericProperty, ListProperty, StringProperty)
from .elements import UltraKeyboard, CriticalErrorPopup, ErrorPopup, PrintPopup
from .home import FilamentChooserPopup, FilamentRunoutPopup
from .freedir import freedir
from .nm_dbus import NetworkManager
from .status import Notifications
from . import parameters as p
# Imports for KvLang Builder
from . import files, home, settings, status, timeline, update, printer_cmd


class MainApp(App, threading.Thread):
    state = OptionProperty("startup", options=[
        # Every string set has to be in this list
        "startup",
        "ready",
        "error",
        ])
    # State of first print job in virtual_sdcard.jobs, or "no print job"
    print_state = OptionProperty("no print job", options=[
        "no print job",
        "queued",
        "printing",
        "pausing",
        "paused",
        "aborting",
        "aborted",
        "finished",
        ])
    stats = StringProperty()
    plotjuggler_stats = DictProperty()
    factory_mode = BooleanProperty(False)
    homed = StringProperty("") # Updated by handle_home_end/start event handler
    temp = DictProperty() # {'heater_bed': [setpoint, current], 'extruder': ...}
    connected = BooleanProperty(False) # updated with handle_connect
    jobs = ListProperty()
    history = ListProperty()
    print_title = StringProperty()
    print_time = StringProperty()
    print_done_time = StringProperty()
    progress = NumericProperty(0)
    pos = ListProperty([0, 0, 0, 0])
    pos_min = ListProperty([0, 0, 0])
    pos_max = ListProperty([0, 0, 0])
    print_area_min = ListProperty([0, 0, 0])
    print_area_max = ListProperty([0, 0, 0])
    material = DictProperty()
    tbc_to_guid = DictProperty()
    cura_connected = BooleanProperty(False)
    thumbnail = StringProperty(p.kgui_dir + '/logos/transparent.png')
    led_brightness = NumericProperty()
    extruder_id = StringProperty("extruder")
    usage = DictProperty()
    gcode_output = StringProperty()
    # Tuning
    speed = NumericProperty(0)
    speed_factor = NumericProperty(100)
    flow = NumericProperty(0)
    flow_factor = NumericProperty(100)
    fan_speed = NumericProperty(0)
    chamber_fan_speed = NumericProperty(0)
    chamber_temp = ListProperty([0, 0])
    z_offset = NumericProperty(0)
    acceleration = NumericProperty(0)
    acceleration_factor = NumericProperty(100)
    pressure_advance = NumericProperty(0)
    force = ListProperty([0,0])
    moisture = ListProperty([0,0])
    # Config
    continuous_printing = BooleanProperty(False)
    reposition = BooleanProperty(False)
    material_condition = StringProperty("")
    material_tolerance = NumericProperty()

    def __init__(self, config, **kwargs):
        logging.info("Kivy app initializing...")
        self.network_manager = NetworkManager()
        self.notify = Notifications()
        self.gcode_metadata = config.get_printer().load_object(config, "gcode_metadata")
        self.temp = {'extruder': [0,0], 'extruder1': [0,0], 'heater_bed': [0,0]}
        self.kv_file = join(p.kgui_dir, "kv/main.kv") # Tell kivy where the root kv file is
        self.reactor = config.get_reactor()
        self.reactor.register_mp_callback_handler(kivy_callback)
        self.location = config.location
        # Read config
        self.xy_homing_controls = config.getboolean('xy_homing_controls', True)
        self.filament_diameter = config.getsection("extruder").getfloat("filament_diameter", 1.75)
        self.led_controls = config.get('led_controls', None)
        self.led_update_time = 0
        if self.led_controls:
            self.led_brightness = config.getsection(f'output_pin {self.led_controls}').getfloat('value')
        # Maintain this by keeping default the same as klipper
        self.min_extrude_temp = config.getsection("extruder").getfloat("min_extrude_temp", 170)
        # Count how many extruders exist
        for i in range(1, 10):
            if not config.has_section(f"extruder{i}"):
                self.extruder_count = i
                break
        self.factory_mode = config.has_section("factory_mode")
        # These are loaded a bit late
        self.reactor.cb(printer_cmd.load_object, "filament_manager")
        self.reactor.cb(printer_cmd.load_object, "print_history")
        super().__init__(**kwargs)
        self.reactor.cb(printer_cmd.request_event_history)

    def clean(self):
        ndel, freed = freedir(self.location.print_files())
        if ndel:
            self.notify.show("Disk space freed", f"Deleted {ndel} files, freeing {freed} MiB")
            self.reactor.cb(printer_cmd.trim_history, process='printer')

    def handle_connect(self):
        self.reactor.cb(printer_cmd.get_pos)
        self.connected = True
        self.clean() # print_history should exist at this point since it is created from a callback in init

    def handle_ready(self):
        self.state = "ready"
        self.reactor.cb(printer_cmd.update)
        self.reactor.cb(printer_cmd.get_material)
        self.reactor.cb(printer_cmd.get_tbc)
        self.reactor.cb(printer_cmd.get_collision_config)
        self.bind(print_state=self.handle_material_change)
        Clock.schedule_interval(lambda dt: self.reactor.cb(printer_cmd.update), 0.6)
        logging.info("Kivy app running")

    def handle_shutdown(self):
        """
        Is called when system shuts down all work, either
        to halt so the user can see what he did wrong
        or to fully exit afterwards
        """
        pass

    def handle_disconnect(self):
        """
        Is called when system disconnects from mcu, this is only done at
        the very end, when exiting or restarting
        """
        logging.info("Kivy app.handle_disconnect")
        self.connected = False
        self.reactor.register_async_callback(self.reactor.end)
        self.stop()

    def handle_critical_error(self, title=None, message="", is_exception=False):
        logging.info("Kivy app.handle_critical_error")
        if is_exception:
            title = title or "Unknown Error - Restart needed"
        else:
            title = title or "Error - Restart needed"
        self.state = "error"
        CriticalErrorPopup(message=message, title=title, is_exception=is_exception).open()

    def handle_error(self, message):
        if 'error' in self.state:
            logging.info(f"UI already in error state - suppress error {message}")
        else:
            ErrorPopup(message = message).open()

    def handle_home_end(self, homing_state, rails):
        self.reactor.cb(printer_cmd.get_homing_state)

    def handle_print_change(self, jobs):
        """ Update the configuration of print jobs and the state of 1. print job """
        if jobs:
            self.print_state = jobs[0].state
            if self.print_state == 'aborting':
                self.print_done_time = "Aborting..."
                self.print_time = ""
        else:
            self.print_state = "no print job"
        self.jobs = jobs

    def handle_print_added(self, jobs, job):
        self.handle_print_change(jobs)
        if len(self.jobs) > 1:
            self.notify.show("Added Print Job", f"Added {job.name} to print Queue", delay=4)

    def handle_print_start(self, jobs, job):
        self.handle_print_change(jobs)
        self.print_title = job.name
        self.thumbnail = self.gcode_metadata.get_metadata(job.path).get_thumbnail_path() or p.kgui_dir + '/logos/transparent.png'
        # This only works if we are in a printing state
        self.reactor.cb(printer_cmd.get_print_progress)

    def handle_print_end(self, jobs, job):
        self.handle_print_change(jobs)
        if job.state in ('finished', 'aborted'):
            self.thumbnail = p.kgui_dir + '/logos/transparent.png'
            self.progress = 0
            self.print_done_time = ""
            self.print_time = ""

    def hide_print(self):
        self.print_title = ""
        self.print_done_time = ""
        self.print_time = ""
        self.progress = 0
        if not self.jobs:
            # Tuning values are only reset once print_queue has run out
            self.reactor.cb(printer_cmd.reset_tuning)

    def handle_history_change(self, history):
        self.history = history

    def handle_material_change(self, *args):
        self.reactor.cb(printer_cmd.get_material)

    def handle_request_material_choice(self, extruder_id):
        FilamentChooserPopup(extruder_id, already_loaded=True).open()

    def handle_material_runout(self, extruder_id):
        FilamentRunoutPopup(extruder_id).open()

    def handle_material_mismatch(self, job):
        PrintPopup(job.path, job=job).open()

    def handle_notification(self, *args, **kwargs):
        self.notify.show(*args, **kwargs)

    def set_led_brightness(self, val):
        self.led_brightness = val
        now = self.reactor.monotonic()
        if now > self.led_update_time:
            self.led_update_time = max(self.led_update_time + 0.025, now + 0.025)
            Clock.schedule_once(self.apply_led_brightness, 0.025)

    def apply_led_brightness(self, dt):
        self.reactor.cb(printer_cmd.run_script_from_command, f"SET_PIN PIN={self.led_controls} VALUE={self.led_brightness}")

    def on_start(self, *args):
        if self.network_manager.available:
            self.network_manager.start()
        try:
            self.root_window.set_vkeyboard_class(UltraKeyboard)
        except:
            logging.warning("root_window wasnt available")

    def on_stop(self, *args):
        """Stop networking dbus event loop"""
        self.network_manager.stop()

    def firmware_restart(self):
        self.reactor.cb(printer_cmd.firmware_restart)

    def poweroff(self):
        Popen(['sudo','systemctl', 'poweroff'])

    def reboot(self):
        Popen(['sudo','systemctl', 'reboot'])


def run_callback(reactor, callback, completion_id, waiting_process, *args, **kwargs):
    res = callback(reactor.root, *args, **kwargs)
    if waiting_process:
        reactor.cb(reactor.mp_complete, completion_id, res, process=waiting_process, execute_in_reactor=True)

def kivy_callback(*args, **kwargs):
    Clock.schedule_del_safe(lambda: run_callback(*args, **kwargs))

# kgui exceptions
class PopupExceptionHandler(ExceptionHandler):
    def handle_exception(self, exception):
        tr = '\n'.join(traceback.format_tb(exception.__traceback__))
        message = tr + "\n\n" + repr(exception)
        App.get_running_app().handle_critical_error(message=message, is_exception=True)
        logging.exception("UI-Exception, popup invoked")
        return ExceptionManager.PASS
ExceptionManager.add_handler(PopupExceptionHandler())

# thread exceptions
def thread_exception_handler(exception):
    app = App.get_running_app()
    if app:
        Clock.schedule_del_safe(lambda: app.handle_critical_error(message=str(exception.exc_value), is_exception=True))
        logging.exception(f"Thread-Exception in {exception.thread.name}, popup invoked\n\n" + str(exception.exc_value))
    else:
        logging.exception(f"Exception occured in thread {exception.thread.name} while graphics are unavailable")
threading.excepthook = thread_exception_handler

# Load kv-files:
# load a custom style.kv with changes to popup and more
Builder.unload_file(join(kivy_data_dir, "style.kv"))
# All files to read (order is important), main.kv is read first, automatically
for fname in ("style.kv", "elements.kv", "home.kv", "timeline.kv", "files.kv", "settings.kv"):
    Builder.load_file(join(p.kgui_dir, "kv", fname))
