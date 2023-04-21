import logging
import shutil
from os.path import join, basename
from os import remove
from time import time
from math import log10

from kivy.app import App
from kivy.clock import Clock
from kivy.properties import (NumericProperty, BooleanProperty, StringProperty,
                             ListProperty, ObjectProperty)
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.vkeyboard import VKeyboard
from kivy.uix.widget import Widget
from kivy.graphics.context_instructions import Color
from kivy.graphics.vertex_instructions import RoundedRectangle

from . import parameters as p
from . import printer_cmd


class Divider(Widget):
    pass

class BaseButton(Label):
    """ Lightweight adaptation of the kivy button class, with disable functionality """
    pressed = BooleanProperty(False)
    enabled = BooleanProperty(True)
    def __init__(self, **kwargs):
        self.register_event_type('on_press')
        self.register_event_type('on_release')
        super().__init__(**kwargs)

    def on_touch_down(self, touch):
        if super().on_touch_down(touch):
            return True
        if touch.is_mouse_scrolling:
            return False
        if not self.collide_point(touch.x, touch.y):
            return False
        if self in touch.ud:
            return False
        # A button with enabled=False can be placed above other buttons and they keep working
        if not self.enabled:
            return False
        self.pressed = True
        self.dispatch('on_press')
        touch.grab(self)
        touch.ud[self] = True
        # Set pressed=True for at least this time to allow GPU to render
        # highlighting of the button. Choose lower for faster GPU.
        self.pressed_at_least_till = time() + 0.08
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            return True
        if super().on_touch_move(touch):
            return True
        return self in touch.ud

    def on_touch_up(self, touch):
        res = False
        if touch.grab_current is not self:
            return super().on_touch_up(touch)
        assert(self in touch.ud)
        touch.ungrab(self)
        if self.collide_point(*touch.pos) or not self.enabled:
            self.dispatch('on_release')
            res = True
        t = time()
        if t < self.pressed_at_least_till:
            Clock.schedule_once(self.do_release, self.pressed_at_least_till - t)
        else: self.pressed = False
        return res

    def do_release(self, arg):
        self.pressed = False
    def on_press(self):
        pass
    def on_release(self):
        pass

class RoundButton(BaseButton):
    pass

class RectangleButton(BaseButton):
    pass

class BtnSlider(BaseButton):
    val = NumericProperty()
    px = NumericProperty()
    s_title = StringProperty()
    offset = NumericProperty()

class MenuItem(RoundButton):
    pass

class MICheckbox(MenuItem):
    active = BooleanProperty(False)
    def on_release(self):
        self.active = not self.active
        return super().on_release()

class BasePopup(Popup):
    def __init__(self, creator=None, val=None, **kwargs):
        # makes this Popup recieve the instance of the calling button to
        # access its methods and e.g. heater_id
        self.creator = creator
        # a popup holds a value that can be passed to a slider, this
        # avoids the value being updated, and the slider reseting
        self.val = val
        super().__init__(**kwargs)

    def open(self, animation=False, **kwargs):
        super().open(animation=animation, **kwargs)
        app = App.get_running_app()
        app.notify.redraw()

    def dismiss(self, animation=False, **kwargs):
        super().dismiss(animation=animation, **kwargs)

class CriticalErrorPopup(BasePopup):
    message = StringProperty()

class ErrorPopup(BasePopup):
    message = StringProperty()

class StopPopup(BasePopup):
    pass

class PrintPopup(BasePopup):

    detailsbox = ObjectProperty(None)

    def __init__(self, path, filechooser=None, **kwargs):
        self.app = App.get_running_app()
        try:
            self.md = self.app.gcode_metadata.get_metadata(path)
        except (ValueError, AttributeError):
            self.md = None
        self.path = path
        self.filechooser = filechooser
        super().__init__(**kwargs)
        self.populate_details()
        Clock.schedule_once(self._align, 0)

    def populate_details(self):
        md = self.md
        if md is None:
            self.add_detail("Invalid File", "")
            return

        weight = md.get_material_amount(measure='weight')
        if weight is not None:
            precision = max(1-int(log10(weight)), 0) # show up to 2 decimal places for small values
            self.add_detail("Filament:", f"{weight:.{precision}f}g")

        time = md.get_time()
        if time is not None:
            self.add_detail("Print Time:", printer_cmd.format_time(time))

        material_type = md.get_material_type()
        if material_type:
            self.add_detail("Material:", material_type)

        n_extruders = md.get_extruder_count()
        if n_extruders is not None:
            self.add_detail("Extruder Count:", str(n_extruders))

        slicer = md.get_slicer()
        if slicer is not None:
            self.add_detail("Sliced by:", slicer)

        size = md.get_file_size()
        if size is not None:
            for ext in ("B", "KiB", "MiB", "GiB", "TiB"):
                if size < 1024:
                    break
                size /= 1024
            precision = max(1-int(log10(size)), 0)
            self.add_detail("G-Code Size:", f"{size:.{precision}f} {ext}")

    def add_detail(self, key, value):
        detail = PrintPopupDetail(key=key, value=value)
        self.detailsbox.add_widget(detail)

    def _align(self, *args):
        """
        Detailsbox is anchored at its bottom, so it needs to be
        readjusted after adding elements to always start right below
        the filename label.
        """
        self.detailsbox.top = self.ids.thumbnail.y - p.padding

    def confirm(self):
        self.dismiss()
        new_path = self.path
        if p.usb_mount_dir in self.path:
            new_path = join(p.sdcard_path, basename(self.path))
            self.app.notify.show(f"Copying {basename(self.path)} to Printer...", delay=3)
            shutil.copy(self.path, new_path)

        self.app.reactor.cb(printer_cmd.send_print, new_path)
        tabs = self.app.root.ids.tabs
        tabs.switch_to(tabs.ids.home_tab)


class PrintPopupDetail(Label):
    key = StringProperty()
    value = StringProperty()


class DeletePopup(BasePopup):
    """ Popup to confirm file deletion """
    def __init__(self, path, filechooser=None, **kwargs):
        self.path = path
        self.filechooser = filechooser
        super().__init__(**kwargs)

    def confirm(self):
        """Deletes the file and closes the popup"""
        remove(self.path)
        app = App.get_running_app()
        # Update the filechooser and print_history
        app.reactor.cb(printer_cmd.trim_history)
        if self.filechooser:
            self.filechooser.load_files(in_background=True)
        # Clear file form the metadata cache
        if app.gcode_metadata:
            app.gcode_metadata.delete_cache_entry(self.path)
        self.dismiss()
        app.notify.show("File deleted", "Deleted " + basename(self.path), delay=4)


class WarningPopup(BasePopup):
    def __init__(self, confirm_callback, **kwargs):
        self.confirm_callback = confirm_callback
        super().__init__(**kwargs)


def warn_if_printing(confirm_callback):
    app = App.get_running_app()
    if app.jobs and app.jobs[0].state not in ('finished', 'aborting', 'aborted'):
        WarningPopup(confirm_callback=confirm_callback).open()
    else:
        confirm_callback()


class UltraSlider(Widget):
    """
    Simple slider widget

    kwargs:
    val_min, val_max    Minimum and Maximum for the output value,
                        used for px <-> val conversion.
                        Defaults to 0 and 100
    unit                Unit string, appended to display value.
                        Defaults to "" (no unit)
    round_to            How many decimals to round val to, is passed to round().
    round_style         5 rounds lowest decimal place to multiples of 5... normally 1
    attributes:
    buttons             list of lists: e.g. [[val,offset,"name",the instance]]
    val                 value, passed to printer, not in px

    The conversion methods get_px_from_val() and get_val_from_px()
    can be safely overwritten by inheritors for nonlinear conversion.
    """
    buttons = ListProperty() # list of lists: e.g. [[val,offset,"name",the instance]]
    val = NumericProperty() # value, passed to printer, not in px
    val_min = NumericProperty(0)
    val_max = NumericProperty(100)
    unit = StringProperty()
    round_to = NumericProperty()
    round_style = NumericProperty(1)
    px = NumericProperty() # absolute position of dot in px
    disp = StringProperty() # value displayed by label
    pressed = BooleanProperty(False)
    changed = BooleanProperty(False)
    initialized = BooleanProperty(False)

    def __init__(self, **kwargs):
        self.btn_last_active = None
        self.initialized = False
        super().__init__(**kwargs)
        Clock.schedule_once(self.init_drawing, 0)

    def init_drawing(self, *args):
        self.px = self.get_px_from_val(self.val)
        self.disp = self.get_disp_from_val(self.val)
        for b in self.buttons:
            b[3] = BtnSlider(y=self.center_y-95, px=self.get_px_from_val(b[0]),
                    val=b[0], offset=b[1], s_title=b[2])
            b[3].bind(on_press=self.on_button)
            self.add_widget(b[3])
        self.highlight_button()
        self.initialized = True

    def on_touch_down(self, touch):
        if (touch.pos[0] > self.x - 30 and touch.pos[0] < self.right + 30 and
                touch.pos[1] > self.y - 18 and touch.pos[1] < self.top + 18 and
                self.initialized):
            self.pressed = True
            touch.grab(self)
            self.on_touch_move(touch)
            self.changed = True
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            self.px = self.apply_bounds(touch.pos[0])
            self.val = self.get_val_from_px(self.px)
            self.disp = self.get_disp_from_val(self.val)
            self.highlight_button()
            return True

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            self.on_touch_move(touch)
            self.pressed = False
            touch.ungrab(self)
            return True
        return super().on_touch_up(touch)

    def apply_bounds(self, x):
        x = max(self.x, x)
        x = min(self.right, x)
        return x

    def highlight_button(self):
        if self.btn_last_active is not None:
            self.btn_last_active[3].active = False
        for b in self.buttons:
            if b[0] == self.val:
                b[3].active = True
                self.btn_last_active = b
                break

    def on_button(self, instance):
        self.val = instance.val
        self.px = self.get_px_from_val(instance.val)
        self.highlight_button()
        self.disp = self.get_disp_from_val(instance.val)
        self.changed = True

    def get_px_from_val(self, val):
        """
        Function that converts values between val_min and val_max
        linearly, returning absolute pixel values within the widget
        boundaries.  If val is outside val_min and val_max, the
        returned pixel value is still cast within the slider.
        """
        val = max(self.val_min, val)
        val = min(self.val_max, val)
        px_per_unit = self.width/(self.val_max - self.val_min)
        px = self.x + px_per_unit*(val - self.val_min)
        return int(px)

    def get_val_from_px(self, px):
        """
        Inverse function of get_px_from_val(),
        returns val rounded according to self.round_to and self.round_style
        """
        units_per_px = (self.val_max - self.val_min)/self.width
        val = self.val_min + units_per_px*(px - self.x)
        return round(val/self.round_style, self.round_to)*self.round_style

    def get_disp_from_val(self, val):
        """ Returns string of the value and the given unit string """
        dec = max(0, self.round_to)
        return f"{val:.{dec}f}{self.unit}"


class UltraKeyboard(VKeyboard):
    # Copy of VKeyboard, only overwrite these methods
    # Changed parts marked with <<<<<<<<>>>>>>>>>

    def process_key_on(self, touch):
        if not touch:
            return
        x, y = self.to_local(*touch.pos)
        key = self.get_key_at_pos(x, y)
        if not key:
            return

        key_data = key[0]
        displayed_char, internal, special_char, size = key_data
        line_nb, key_index = key[1]

        # save pressed key on the touch
        ud = touch.ud[self.uid] = {}
        ud['key'] = key

        # for caps lock or shift only:
        uid = touch.uid
        if special_char is not None:
            # Do not repeat special keys
            if special_char in ('capslock', 'shift', 'layout', 'special'):
                if self._start_repeat_key_ev is not None:
                    self._start_repeat_key_ev.cancel()
                    self._start_repeat_key_ev = None
                self.repeat_touch = None
            if special_char == 'capslock':
                self.have_capslock = not self.have_capslock
                uid = -1
            elif special_char == 'shift':
                self.have_shift = True
            elif special_char == 'special':
                #<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
                self.have_special = not self.have_special
                uid = -2
                if self.have_capslock:
                    self.active_keys.pop(-1, None)
                    self.have_capslock = False
                #>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
            elif special_char == 'layout':
                self.change_layout()

        # send info to the bus
        b_keycode = special_char
        b_modifiers = self._get_modifiers()
        if self.get_parent_window().__class__.__module__ == \
            'kivy.core.window.window_sdl2' and internal:
            self.dispatch('on_textinput', internal)
        else:
            self.dispatch('on_key_down', b_keycode, internal, b_modifiers)

        # save key as an active key for drawing
        self.active_keys[uid] = key[1]
        self.refresh_active_keys_layer()

    def process_key_up(self, touch):
        uid = touch.uid
        if self.uid not in touch.ud:
            return

        # save pressed key on the touch
        key_data, key = touch.ud[self.uid]['key']
        displayed_char, internal, special_char, size = key_data

        # send info to the bus
        b_keycode = special_char
        b_modifiers = self._get_modifiers()
        self.dispatch('on_key_up', b_keycode, internal, b_modifiers)

        if special_char == 'capslock':
            uid = -1
        #<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
        elif special_char == 'special':
            uid = -2
        #>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

        if uid in self.active_keys:
            self.active_keys.pop(uid, None)
            if special_char == 'shift':
                self.have_shift = False
            #<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
            elif special_char == 'special' and self.have_special:
                self.active_keys[-2] = key
            #>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
            if special_char == 'capslock' and self.have_capslock:
                self.active_keys[-1] = key
            self.refresh_active_keys_layer()

    def draw_keys(self):
        layout = self.available_layouts[self.layout]
        layout_rows = layout['rows']
        layout_geometry = self.layout_geometry
        layout_mode = self.layout_mode

        #<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
        self.background_key_layer.clear()
        with self.background_key_layer:
            Color(*p.background)
            RoundedRectangle(size=self.size, radius=(p.key_radius,))

        with self.background_key_layer:
            Color(*p.key)
            for line_nb in range(1, layout_rows + 1):
                for pos, size in layout_geometry['LINE_%d' % line_nb]:
                    RoundedRectangle(pos=pos, size=size, radius=(p.key_radius,))
        #>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

        # then draw the text
        for line_nb in range(1, layout_rows + 1):
            key_nb = 0
            for pos, size in layout_geometry['LINE_%d' % line_nb]:
                # retrieve the relative text
                text = layout[layout_mode + '_' + str(line_nb)][key_nb][0]
                z = Label(text=text, font_size=self.font_size, pos=pos,
                           size=size, font_name=self.font_name)
                self.add_widget(z)
                key_nb += 1

    def refresh_active_keys_layer(self):
        self.active_keys_layer.clear()

        active_keys = self.active_keys
        layout_geometry = self.layout_geometry
        #<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
        with self.active_keys_layer:
            Color(*p.key_down)
            for line_nb, index in active_keys.values():
                pos, size = layout_geometry['LINE_%d' % line_nb][index]
                RoundedRectangle(pos=pos, size=size, radius=(p.key_radius,))
        #>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
