import collections
import logging
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.graphics.context_instructions import Color
from kivy.graphics.vertex_instructions import RoundedRectangle, Ellipse, Rectangle, BorderImage
from kivy.properties import StringProperty, NumericProperty
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from . import parameters as p
from . import printer_cmd


class StatusBar(BoxLayout):

    animation_pos = NumericProperty(0)

    def __init__(self, **kwargs):
        self.app = App.get_running_app()
        self.app.bind(state=self.update_animation, print_state=self.update_animation)
        self.scheduled_updating = None
        self.update_animation(None, self.app.state)
        super().__init__(**kwargs)

    def update_animation(self, instance, value):
        if self.app.state in ('startup', 'shutdown') or self.app.print_state in ('pausing', 'aborting'):
            if not self.scheduled_updating:
                self.animation_pos = 0
                self.scheduled_updating = Clock.schedule_interval(self.update_animation_pos, 0.02)
        else:
            if self.scheduled_updating:
                Clock.unschedule(self.scheduled_updating)
                self.scheduled_updating = None
            self.animation_pos = 0

    def update_animation_pos(self, dt):
        self.animation_pos += 1
        self.animation_pos = self.animation_pos % (p.screen_width + 300)


class TimeLabel(Label):

    time = StringProperty("--:--")

    def __init__(self, **kwargs):
        self.update_clock = None
        self.get_time_str()
        self.start_clock()
        super().__init__(**kwargs)

    def start_clock(self):
        # How many seconds are left to the next full minute
        offset = 60 - int(time.strftime("%S"))
        Clock.schedule_once(self.start_updates, offset+2)

    def start_updates(self, dt):
        self.update_clock = Clock.schedule_interval(self.get_time_str, 60)
        Clock.schedule_once(self.get_time_str, 1)

    def get_time_str(self, *args):
        self.time = time.strftime("%H:%M")


class ConnectionIcon(Widget):

    def __init__(self, **kwargs):
        self.network_manager = App.get_running_app().network_manager
        self.topright = []
        self.signal = 1
        self.icon_padding = 4
        self.signal_timer = None # Clock timer for requesting signal strength
        super().__init__(**kwargs)

        Clock.schedule_once(self.init_drawing, 0)

    def init_drawing(self, dt):
        with self.canvas:
            self.wifi_color = Color(rgba=(0,0,0,0))
            self.wifi = Ellipse(pos=(0, 0), size=(0, 0),
                                angle_start=315, angle_end=405)
            self.eth_color = Color(rgba=(0,0,0,0))
            self.eth = Rectangle(pos=(0, 0), size=(0, 0),
                                 source=p.kgui_dir + "/logos/ethernet.png")
        self.set_icon(None, self.network_manager.connection_type)
        self.network_manager.bind(connection_type=self.set_icon)

    def draw_wifi(self):
        padding = self.icon_padding
        r = self.height - 2*padding
        d = 2*r
        # cutoff = width of square h*h - width of cake slice (on one side)
        # 0.5**0.5 = cos(pi/4), avoid trigonometric functions
        cutoff = round(r*(1 - 0.5**0.5))
        # Position of the full circle
        full_pos = [self.topright[0] - (d - cutoff) - padding,
                    self.topright[1] - d - padding]

        # Size of the circle shrinked according to signal strength
        difference = r*(1 - self.signal)
        self.wifi.pos = [full_pos[0] + difference, full_pos[1] + difference]
        self.wifi.size = [d * self.signal, d * self.signal]

        self.width = d - 2*cutoff + padding
        self.wifi_color.rgba = p.status_bar
        self.eth_color.rgba = (0,0,0,0)

    def draw_eth(self):
        h = self.height - 2*self.icon_padding
        size = [h, h]
        self.width = size[0] + self.icon_padding
        self.eth.pos = [self.topright[0] - size[0] - self.icon_padding,
                        self.topright[1] - size[1] - self.icon_padding]
        self.eth.size = size
        self.eth_color.rgba = p.status_bar
        self.wifi_color.rgba = (0,0,0,0)

    def draw_nothing(self):
        self.width = 0
        self.eth_color.rgba = (0,0,0,0)
        self.wifi_color.rgba = (0,0,0,0)

    def set_icon(self, instance, value):
        if self.signal_timer is not None:
            self.signal_timer.cancel()
        if value == "ethernet":
            self.draw_eth()
        elif value == "wireless":
            self.signal_timer = Clock.schedule_interval(self.update_wifi, 4)
            self.update_wifi() # Takes care of drawing too
        else:
            self.draw_nothing()

    def update_wifi(self, *args):
        strength = self.network_manager.get_connection_strength()
        if strength:
            # Don't display signals lower than 30%, they would be too tiny
            self.signal = max(strength / 100, 0.3)
            self.draw_wifi()
        else: # strength can be None if WiFi disconnected
            self.draw_nothing()


class CuraConnectionIcon(Widget):
    """Icon indicating that there currently is a connection to Cura"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Clock.schedule_interval(self.update, 2)

    def update(self, dt):
        try:
            App.get_running_app().reactor.cb(printer_cmd.get_connected, process='cura_connection')
        except KeyError:
            self.connected = False


class Notifications(FloatLayout):

    def __init__(self):
        # Initialize update_clock as a ClockEvent in case it gets canceled first
        self.update_clock = Clock.schedule_once(lambda dt: 0, -1)
        self.active = False
        # Use a queue to save notifications to show after each other
        # Deletes oldest notifications when queuing more than ten
        self.queue = collections.deque(maxlen=10)
        self.initialized = False
        Clock.schedule_once(self.late_setup, 0)

    def late_setup(self, dt):
        super().__init__()
        self.root_widget = App.get_running_app().root
        self.size_hint = (None, None)
        self.size = self.root_widget.width - 2*p.notification_padding, 110
        self.x = self.root_widget.x + p.notification_padding
        self.top = self.root_widget.top - p.notification_padding
        with self.canvas:
            Color(rgba=p.notification_shadow)
            BorderImage(
                source=p.kgui_dir + '/logos/shadow.png',
                pos=(self.x-64, self.y-63),
                size=(self.width + 128, self.height + 126),
                border=(64, 64, 64, 64))
            self.bg_color = Color(rgba=p.red)
            RoundedRectangle(pos=self.pos, size=self.size, radius=(p.radius, p.radius))

        title = Label(
                size_hint = (None, None),
                font_size = p.normal_font,
                bold = True,)
        title.size = (self.width - 2*p.notification_text_padding, title.font_size)
        title.text_size = title.size
        title.x = self.x + p.notification_text_padding
        title.top = self.top - p.notification_text_padding
        self.add_widget(title)
        self.title_label = title

        message = Label(
                size_hint = (None, None),
                font_size = p.normal_font,)
        message.size = (self.width, message.font_size)
        message.text_size = message.size
        message.x = self.x + p.notification_text_padding
        message.top = title.y - p.notification_text_padding/2
        self.add_widget(message)
        self.message_label = message

        self.initialized = True
        if self.queue:
            self.show(**self.queue.popleft())

    def show(self, title="", message="", level="info", delay=-1, log=True, color=None):
        """
        Show a notification popup with the given parameters. If log is set,
        also write to the log file.

        Parameters:
        title   string      Title of the notification
        message string      Message body of the notification
        level   string      What log level preset to use.
        log     bool        Whether or not to write the notification in the logs.
        delay   int         Time until notification is automatically hidden in seconds.
                            Never automatically hide for any negative value.
        color   rgba list   Background color of the notification. Overwrites the
                or string   value set by the level preset. Can also be the name of
                            different preset than the specified log level.
        """
        if self.active or not self.initialized:
            self.queue.append({"title": title, "message": message,
                "level": level, "log": log, "delay": delay, "color": color})
            return

        color_presets = {
                "info": p.notify_info,
                "warning": p.notify_warning,
                "error": p.notify_error,
                "success": p.notify_success}
        if level not in color_presets.keys():
            raise ValueError("Unrecognized log level preset " + level)

        self.title_label.text = title
        self.message_label.text = message

        if isinstance(color, str) and color in color_presets.keys():
            self.bg_color.rgba = color_presets[color]
        elif isinstance(color, (list, tuple)):
            self.bg_color.rgba = color
        else:
            self.bg_color.rgba = color_presets[level]

        if log:
            if title:
                if level in("info", "success"):
                    logging.info("Notify: " + title)
                elif level == "warning":
                    logging.warning("Notify: " + title)
                elif level == "error":
                    logging.error("Notify: " + title)
            if message:
                if level in("info", "success"):
                    logging.info("Notify: " + message)
                elif level == "warning":
                    logging.warning("Notify: " + message)
                elif level == "error":
                    logging.error("Notify: " + message)

        window = self.root_widget.get_root_window()
        window.add_widget(self)
        self.active = True
        # Schedule automatic hiding
        # Never automatically hide for negative delay values
        if delay >= 0:
            self.update_clock = Clock.schedule_once(self.hide, delay)

    __call__ = show

    def hide(self, *args):
        self.update_clock.cancel()
        self.root_widget.get_root_window().remove_widget(self)
        self.active = False
        if self.queue:
            self.show(**self.queue.popleft())

    def redraw(self):
        # Redraw the notification on top of the window. Used in BasePopup.open()
        if self.active:
            window = self.root_widget.get_root_window()
            window.remove_widget(self)
            window.add_widget(self)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self.hide()
            return True
        return super().on_touch_down(touch)
