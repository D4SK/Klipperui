import logging
import os
import re
import subprocess

from kivy.app import App
from kivy.clock import Clock
from kivy.properties import ObjectProperty, StringProperty, BooleanProperty, NumericProperty
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.recycleboxlayout import RecycleBoxLayout
from kivy.uix.recycleview import RecycleView
from kivy.uix.recycleview.layout import LayoutSelectionBehavior
from kivy.uix.recycleview.views import RecycleDataViewBehavior
from kivy.uix.screenmanager import Screen
from kivy.uix.tabbedpanel import TabbedPanelItem
from kivy.factory import Factory

from .elements import BasePopup, RectangleButton, UltraScreen
from . import printer_cmd


class SettingTab(TabbedPanelItem):

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self.ids.screen_man.current = "SettingScreen"
        return super().on_touch_down(touch)


class SetItem(FloatLayout, RectangleButton):
    left_title = StringProperty()
    right_title = StringProperty()


class SIWifi(SetItem):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.network_manager = App.get_running_app().network_manager
        self.network_manager.bind(connected_ssid=self.update)
        # Set default messages after everything is set up and running
        Clock.schedule_once(self.update, 0)

    def update(self, *args):
        if self.network_manager.available:
            self.right_title = self.network_manager.connected_ssid or "not connected"
        else:
            self.right_title = "not available"


class ConsoleScreen(UltraScreen):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = App.get_running_app()
        self.reactor = self.app.reactor
        Clock.schedule_once(self.init_drawing, 0)

    def init_drawing(self, *args):
        self.ids.console_input.bind(on_text_validate=self.confirm)
        self.app.bind(gcode_output=self.on_gcode_output)

    def on_pre_enter(self):
        self.ids.console_input.focus = True
        self.ids.console_scroll.scroll_y = 0.001
        self.app.reactor.cb(printer_cmd.get_gcode_output)
        self.ids.console_input.keep_focus = True

    def on_leave(self):
        self.app.reactor.cb(printer_cmd.stop_gcode_output)
        self.ids.console_input.keep_focus = False
        self.ids.console_input.focus = False

    def confirm(self, *args):
        cmd = self.ids.console_input.text
        self.ids.console_input.text = ""
        self.app.gcode_output += cmd + "\n"
        self.reactor.cb(printer_cmd.run_script, cmd)

    def on_gcode_output(self, *args):
        self.ids.console_scroll.scroll_y = 0.001

    def show_dropdown(self, button, *args):
        Factory.ConsoleDropDown().open(button)

class MoveScreen(Screen):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        Clock.schedule_once(self.add_text_inputs, 2)

    def add_text_inputs(self, dt):
        for i, axis, name in zip(range(4), ("x", "y", "z", "extruder"), "XYZE"):
            self.ids.box.add_widget(CoordinateInput(name=name, axis_id=axis, axis_idx=i))

    def on_pre_enter(self):
        self.updating = Clock.schedule_interval(self.update, 0.05)

    def update(self, dt):
        App.get_running_app().reactor.cb(printer_cmd.get_pos)

    def on_leave(self):
        Clock.unschedule(self.updating)

class AboutScreen(Screen):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def on_pre_enter(self):
        self.update()
        self.updating = Clock.schedule_interval(self.update, 1)

    def update(self, dt=None):
        App.get_running_app().reactor.cb(printer_cmd.get_usage)

    def on_leave(self):
        Clock.unschedule(self.updating)

class DebugScreen(Screen):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def on_pre_enter(self):
        App.get_running_app().reactor.cb(printer_cmd.start_stats)

    def on_leave(self):
        App.get_running_app().reactor.cb(printer_cmd.stop_stats)

class XyField(Widget):

    pressed = BooleanProperty(False)
    enabled = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.point_radius = 10
        self.app = App.get_running_app()
        self.app.bind(pos=self.update_with_mm)
        self.app.bind(pos_min=self.init_drawing)
        self.app.bind(pos_max=self.init_drawing)
        self.bind(x=self.init_drawing)
        self.bind(y=self.init_drawing)
        self.bind(top=self.init_drawing)
        self.bind(right=self.init_drawing)

    def init_drawing(self, *args):
        # Calculate bounds of actual field
        self.origin = [self.x + self.point_radius, self.y + self.point_radius]
        self.limits = [self.right - self.point_radius, self.top - self.point_radius]
        self.mm_to_px = [(self.limits[0] - self.origin[0]) / (0.00000001 + self.app.print_area_max[0] - self.app.print_area_min[0]),
                         (self.limits[1] - self.origin[1]) / (0.00000001 + self.app.print_area_max[1] - self.app.print_area_min[1])]
        self.overmove_min = [self.mm_to_px[0] * (pos_min - print_area_min)
                             for pos_min, print_area_min in zip(self.app.pos_min, self.app.print_area_min)]
        self.overmove_max = [self.mm_to_px[1] * (pos_max - print_area_min)
                             for pos_max, print_area_min in zip(self.app.pos_max, self.app.print_area_max)]
        self.px = self.origin

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos) and self.enabled:
            touch.grab(self)
            self.update_with_px(touch.pos)
            self.pressed = True
            return True
        return False

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            self.update_with_px(touch.pos)
            return True
        return False

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self.update_with_px(touch.pos)
            self.app.reactor.cb(printer_cmd.send_pos, x=self.mm[0], y=self.mm[1], speed=40)
            self.pressed = False
            return True
        return False

    def update_with_px(self, px_input):
        if self.enabled:
            px_input = (int(px_input[0]), int(px_input[1]))
            self.px = self.apply_bounds(px_input[0], px_input[1])
            self.set_mm_with_px(self.px)

    def update_with_mm(self, instance=None, mm=[0,0,0]):
        self.set_px_with_mm(mm)
        self.mm = mm[:3]

    def apply_bounds(self, x, y):
        if x < self.origin[0] + self.overmove_min[0]:
            x = self.origin[0] + self.overmove_min[0]
        elif x > self.limits[0] + self.overmove_max[0]:
            x = self.limits[0] + self.overmove_max[0]

        if y < self.origin[1] + self.overmove_min[1]:
            y = self.origin[1] + self.overmove_min[1]
        elif y > self.limits[1] + self.overmove_max[1]:
            y = self.limits[1] + self.overmove_max[1]
        return [x, y]

    def set_mm_with_px(self, px):
        self.mm[0] = float(px[0] - self.origin[0]) / self.mm_to_px[0]
        self.mm[1] = float(px[1] - self.origin[1]) / self.mm_to_px[1]

    def set_px_with_mm(self, mm):
        px = [float(mm[0]) * self.mm_to_px[0] + self.origin[0],
              float(mm[1]) * self.mm_to_px[1] + self.origin[1]]
        self.px = self.apply_bounds(*px)


class WifiScreen(UltraScreen):

    def on_pre_enter(self):
        """Update the recycleview and trigger frequent scanning"""
        wifi = self.ids.wifi
        network_manager = wifi.network_manager
        if not network_manager.available:
            # Sanity check, SIWifi Button should be disabled in this case
            self.manager.current = "SettingScreen"
            return
        network_manager.wifi_scan()
        network_manager.set_scan_frequency(10)
        wifi.update(None, network_manager.access_points)

    def on_leave(self):
        """Disable frequent scanning which slows down the wifi device"""
        if self.ids.wifi.network_manager.available:
            self.ids.wifi.network_manager.set_scan_frequency(0)


class Wifi(RecycleView):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.network_manager = App.get_running_app().network_manager
        self.network_manager.bind(on_access_points=self.update)

    def update(self, instance, value):
        # Repopulate the list of networks
        if value:
            self.message = ""
            self.data = [{'ap': None}] + [{'ap': ap} for ap in value]
            self.refresh_from_data()
        else:
            self.data = []
            self.refresh_from_data()
            self.message = "No Networks found"


class WifiBox(LayoutSelectionBehavior, RecycleBoxLayout):
    # Adds selection behaviour to the view
    pass


class WifiItem(RecycleDataViewBehavior, Label):
    ap = ObjectProperty(allownone=True)
    pressed = BooleanProperty(False)
    cake_radius = NumericProperty(0)
    index = None

    def refresh_view_attrs(self, rv, index, data):
        # Catch and handle the view changes
        self.index = index
        return super().refresh_view_attrs(rv, index, data)

    def on_touch_down(self, touch):
        if super().on_touch_down(touch):
            return True
        if self.collide_point(*touch.pos):
            self.pressed = True
            return True

    def on_touch_up(self, touch):
        was_pressed = self.pressed
        self.pressed = False
        if super().on_touch_up(touch):
            return True
        if self.collide_point(*touch.pos) and was_pressed:

            if self.ap.saved:
                ConnectionPopup(self.ap).open()
            elif not self.ap.encrypted:
                self.ap.connect()
            else:
                PasswordPopup(self.ap).open()

            return True
        return False


class PasswordPopup(BasePopup):

    txt_input = ObjectProperty(None)

    def __init__(self, ap, **kwargs):
        self.app = App.get_running_app()
        self.ap = ap
        self.title = self.ap.ssid
        super().__init__(**kwargs)
        self.network_manager = App.get_running_app().network_manager
        self.network_manager.bind(on_connect_failed=self.connect_failed)
        # If focus is set immediately, keyboard will be covered by popup
        Clock.schedule_once(self.set_focus_on, -1)

    def set_focus_on(self, *args):
        self.txt_input.focus = True

    def confirm(self, *args):
        self.password = self.txt_input.text
        try:
            self.ap.connect(self.password)
        except:
            self.app.notify.show("Connection Failed", "Find out why",
                    delay=4, level="warning")
        self.dismiss()

    def connect_failed(self, instance):
        # Avoid a network being stored with the wrong password
        self.ap.delete()
        self.open()
        self.set_focus_on()
        self.app.notify.show("connection failed", "Verify the password or try again later",
                level="warning", delay=4)
        return True


class ConnectionPopup(BasePopup):

    def __init__(self, ap, **kwargs):
        self.app = App.get_running_app()
        self.network_manager = self.app.network_manager
        self.ap = ap
        super().__init__(**kwargs)

    def toggle_connected(self):
        if self.ap.in_use:
            self.down()
        else:
            self.up()

    def up(self):
        try:
            self.ap.up()
        except:
            self.app.notify.show("Connection failed",
                    "Please try again later", delay=6, level="warning")
        self.dismiss()

    def down(self):
        try:
            self.ap.down()
        except:
            self.app.notify.show("Failed to disconnect",
                    "Please try again later", delay=6, level="warning")
        self.dismiss()

    def delete(self):
        try:
            self.ap.delete()
        except:
            self.app.notify.show("Failed to delete connection",
                    "Please try again later", delay=6, level="warning")
        self.dismiss()


class ContinuousPrintingScreen(UltraScreen):

    def on_pre_enter(self):
        self.app = App.get_running_app()
        self.reactor = self.app.reactor
        self.ids.enable_toggle.active = self.app.continuous_printing
        self.ids.reposition_toggle.active = self.app.reposition

    def update(self):
        # Wait for the switch to go into active state
        Clock.schedule_once(self.update_config, 0)

    def update_config(self, dt):
        self.app.continuous_printing = self.ids.enable_toggle.active
        self.app.reposition = self.ids.reposition_toggle.active
        self.reactor.cb(printer_cmd.set_collision_config,
                self.app.continuous_printing,
                self.app.reposition,
                self.app.material_condition)


class SITimezone(SetItem):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.update_timezone()

    def update_timezone(self):
        if os.path.exists("/etc/localtime"):
            self.right_title = os.path.basename(os.readlink("/etc/localtime"))
        else:
            self.right_title = "not available"


class TimezonePopup(BasePopup):
    setitem = ObjectProperty()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.region_selection = None

    def confirm(self):
        selection = self.ids.rv.data[self.ids.rv_box.selected_nodes[0]]
        self.ids.rv_box.selected_nodes = []
        if not self.region_selection: # 1. selection (region/continent) just done, fill rv with actual timezones
            self.region_selection = selection['text']
            timezone_pseudofiles = next(os.walk("/usr/share/zoneinfo/" + self.region_selection))[2]
            timezone_pseudofiles.sort()
            self.ids.rv.data = [{'text': timezone} for timezone in timezone_pseudofiles]
            self.ids.rv.refresh_from_data()
            self.ids.rv.scroll_y = 1
            self.title = "Choose Timezone"
        else: # 2. selection (timezone) just done
            os.system("sudo unlink /etc/localtime")
            os.system(f"sudo ln -s /usr/share/zoneinfo/{self.region_selection}/{selection['text']} /etc/localtime")
            # update Timezone shown in Settings and Time in Statusbar
            self.setitem.update_timezone()
            app = App.get_running_app()
            app.root.ids.status_bar.ids.time.get_time_str()
            self.dismiss()


class TimezoneRV(RecycleView):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # initally filled with all regions/continents
        region_folders = next(os.walk("/usr/share/zoneinfo/"))[1]
        region_folders.sort()
        # remove some folders we don't want to show
        for folder in ["SystemV", "Etc", "posix", "right"]:
            if folder in region_folders:
                region_folders.remove(folder)
            else:
                logging.warning(f"Please update Timezones: {folder} could not be removed from list")
        self.data = [{'text': region} for region in region_folders]


class TimezoneRVBox(LayoutSelectionBehavior, RecycleBoxLayout):
    # Adds selection behaviour to the view
    pass


class TimezoneRVItem(RecycleDataViewBehavior, Label):
    # Add selection support to the Label
    index = None
    selected = BooleanProperty(False)

    def refresh_view_attrs(self, rv, index, data):
        # Catch and handle the view changes
        self.index = index
        return super().refresh_view_attrs(rv, index, data)

    def on_touch_down(self, touch):
        # Add selection on touch down
        if super().on_touch_down(touch):
            return True
        if self.collide_point(*touch.pos):
            return self.parent.select_with_touch(self.index, touch)

    def apply_selection(self, rv, index, is_selected):
        # Respond to the selection of items in the view
        self.selected = is_selected

class HostnamePopup(BasePopup):
    """
    Popup for changing the hostname.
    """
    txt_input = ObjectProperty(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # If focus is set immediately, keyboard will be covered by popup
        Clock.schedule_once(self.set_focus_on, -1)

    def set_focus_on(self, *args):
        self.txt_input.focus = True

    def confirm(self, *args):
        text = self.txt_input.text
        cmd = ["sudo", "hostnamectl", "set-hostname", text]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            app = App.get_running_app()
            app.notify("Failed to set hostname",
                       "hostnamectl finished with exit-code "
                       + str(proc.returncode),
                       level="error",
                       delay=30)
            logging.warning("hostnamectl: " + proc.stdout + " " + proc.stderr)
        else:
            self.dismiss()
            App.get_running_app().reactor.cb(printer_cmd.restart)

# TextInput must be imported later, specifically in the kivy Thread,
# not in the klippy Thread. This is to prevent a segmentation fault
# if kivy.core.window is imported in a different Thread.
def late_define(dt):
    from kivy.uix.textinput import TextInput
    global HostnameTextInput
    global CoordinateInput
    class HostnameTextInput(TextInput):
        """
        Modify TextInput to only input lower- and uppercase ASCII letters,
        digits and hyphens, and not more than 64 characters in total.
        """

        pat = re.compile(r"[^-a-zA-Z0-9]")

        def insert_text(self, substring, from_undo=False):
            filtered = re.sub(self.pat, "", substring)
            max_len = 64 - len(self.text)
            limited = filtered[:max_len]
            return super().insert_text(limited, from_undo=from_undo)

    class CoordinateInput(Label):
        name = StringProperty('X')
        axis_id = StringProperty('x')
        axis_idx = NumericProperty(0)
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.app = App.get_running_app()
            self.reactor = self.app.reactor
            self.ids.txt_input.bind(on_text_validate=self.confirm)

        def confirm(self, *args):
            try:
                val = float(self.ids.txt_input.text)
            except:
                self.ids.txt_input.text = ""
                return
            self.reactor.cb(printer_cmd.send_pos, **{self.axis_id: val})
            self.ids.txt_input.text = ""

Clock.schedule_once(late_define, 0)
