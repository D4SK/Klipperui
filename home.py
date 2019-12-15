# coding: utf-8
from kivy.properties import ListProperty, StringProperty
from kivy.clock import Clock
from kivy.uix.widget import Widget
from kivy.app import App
from kivy.graphics.vertex_instructions import RoundedRectangle, Ellipse, Line
from kivy.graphics.context_instructions import Color
import logging
from elements import *
import parameters as p


class XyField(Widget):

    pressed = BooleanProperty(False)
    enabled = BooleanProperty(False)

    def __init__(self, **kwargs):
        super(XyField, self).__init__(**kwargs)
        self.point_radius = 10
        self.app = App.get_running_app()
        self.printer_dimensions = (self.app.pos_max['x'] - self.app.pos_min['x'],\
                                   self.app.pos_max['y'] - self.app.pos_min['y'])
        self.app.bind(pos=self.update_with_mm)
        Clock.schedule_once(self.init_drawing, 0)
        

    def init_drawing(self, dt):
        #Calculate bounds of actual field
        self.origin = [self.x+self.point_radius, self.y+self.point_radius]
        self.limits = [self.right-self.point_radius, self.top-self.point_radius]
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
            self.app.send_pos(x=self.mm[0], y=self.mm[1], speed=40)
            self.pressed = False
            return True
        return False

    def update_with_px(self, px_input):
        if self.enabled:
            px_input = (int(px_input[0]), int(px_input[1]))
            self.px = self.apply_bounds(px_input[0], px_input[1])
            self.set_mm_with_px(self.px)

    def update_with_mm(self, instance=None, mm=(0,0)):
        self.set_px_with_mm(mm)
        self.mm[0] = mm[0]
        self.mm[1] = mm[1]

    def apply_bounds(self, x, y):
        logging.info("applying bounds to {}, {}".format(x, y))
        if x < self.origin[0]:
            x = self.origin[0]
        elif x > self.limits[0]:
            x = self.limits[0]

        if y < self.origin[1]:
            y = self.origin[1]
        elif y > self.limits[1]:
            y = self.limits[1]
        logging.info("gives {}, {}".format(x, y))
        return [x, y]

    def set_mm_with_px(self, px):
        ratio_x = float(px[0] - self.origin[0]) / (self.limits[0] - self.origin[0])
        ratio_y = float(px[1] - self.origin[1]) / (self.limits[1] - self.origin[1])

        self.mm[0] = self.printer_dimensions[0] * ratio_x
        self.mm[1] = self.printer_dimensions[1] * ratio_y

    def set_px_with_mm(self, mm):
        self.px = [(self.limits[0] - self.origin[0]) * float(mm[0]) / self.printer_dimensions[0] + self.origin[0],
                   (self.limits[1] - self.origin[1]) * float(mm[1]) / self.printer_dimensions[1] + self.origin[1]]



class TempSlider(UltraSlider):

    def __init__(self, **kwargs):
        super(ExtTempSlider, self).__init__(**kwargs)
        App.get_running_app().get_temp()
        self.buttons = []
    def get_val_from_px(self, x):
        v = int(((x-self.px_min)/self.px_width)*(280-40)+40)
        for b in self.buttons:
            if v >= b[0]-2 and v <= b[0]+2:
                v = b[0]
                self.px = self.get_px_from_val(v)
                break
        if v <= 42: v = 0
        return v
    def get_disp_from_val(self, val):
        if self.val == 0:
            s = "Off"
        else:
            s = "{}°C".format(self.val)
        return s
 

class BedTempSlider(UltraSlider):
    def __init__(self, **kwargs):
        super(BedTempSlider, self).__init__(**kwargs)
        App.get_running_app().get_temp()
        self.buttons = [[0,0,"Off",None],
                        [60,0,"PLA",None],
                        [90,0,"PETG",None],
                        [110,0,"ABS",None]]

    def get_val_from_px(self, x):
        v = int(((x-self.px_min)/self.px_width)*(140-30)+30)
        for b in self.buttons:
            if v >= b[0]-2 and v <= b[0]+2:
                v = b[0]
                self.px = self.get_px_from_val(v)
                break
        if v <= 42: v = 0
        return v
    def get_disp_from_val(self, val):
        if self.val == 0:
            s = "Off"
        else:
            s = "{}°C".format(self.val)
        return s
    def get_px_from_val(self, val):
        x = (float(val-30)/float(140-30))*self.px_width+self.px_min
        if x < self.px_min: x = self.px_min
        return x


class ExtTempSlider(UltraSlider):
    def __init__(self, **kwargs):
        super(ExtTempSlider, self).__init__(**kwargs)
        App.get_running_app().get_temp()
        self.buttons = [
            [0,14,"Off",None],
            [70,0,"PLA\ncold pull",None],
            [90,-68/2,"ABS/PETG\ncold pull",None],
            [210,68/2,"PLA",None],
            [230,0,"PETG",None],
            [250,-68/2,"ABS",None]]
    def get_val_from_px(self, x):
        v = int(((x-self.px_min)/self.px_width)*(280-40)+40)
        for b in self.buttons:
            if v >= b[0]-2 and v <= b[0]+2:
                v = b[0]
                self.px = self.get_px_from_val(v)
                break
        if v <= 42: v = 0
        return v
    def get_disp_from_val(self, val):
        if self.val == 0:
            s = "Off"
        else:
            s = "{}°C".format(self.val)
        return s
    def get_px_from_val(self, val):
        x = (float(val-40)/float(280-40))*self.px_width+self.px_min
        if x < self.px_min: x = self.px_min
        return x
"""
class ExtTempOffsetSlider(UltraOffsetSlider):
    def __init__(self, **kwargs:
        super(ExtTempSlider, self).__init__(**kwargs)
        App.get_running_app().get_temp()
        self.buttons = [
            [0,14,"Off",None],
            [210,68/2,"PLA",None],
            [230,0,"PETG",None],
            [250,-68/2,"ABS",None]]
    def get_val_from_px(self, x):
        v = int(((x-self.px_min)/self.px_width)*(280-40)+40)
        for b in self.buttons:
            if v >= b[0]-2 and v <= b[0]+2:
                v = b[0]
                self.px = self.get_px_from_val(v)
                break
        if v <= 42: v = 0
        return v
    def get_disp_from_val(self, val):
        offset = str(self.offset)
        if self.offset >= 0: offset = "+"+ offset
        if self.val == 0:  return "Off {}".format(offset)
        else:              return "{}°C {}".format(self.val, offset)

    def get_px_from_val(self, val):
        x = (float(val-40)/float(280-40))*self.px_width+self.px_min
        if x < self.px_min: x = self.px_min
        return x
"""