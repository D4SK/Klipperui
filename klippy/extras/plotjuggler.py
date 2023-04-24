import logging
import json
import websocket # pip install websocket-client
import time
import queue
import chelper
from threading import Thread

class Plotjuggler:
    def __init__(self, config):
        self.host_address = config.get('host_address')
        self.data_queue = queue.Queue()
        self.printer = config.get_printer()
        self.trapq_interval = config.getfloat('trapq_interval', 0.1)
        self.reactor = self.printer.get_reactor()
        self.ws = None
        self.reconnect_time = 0
        self.last_print_time = 0
        self.last_monotonic = 0
        self.start_time = self.reactor.monotonic()
        logging.info(f"Plotjuggler print time offset is {self.start_time}")
        self.printer.register_event_handler("klippy:disconnect",
            self.handle_disconnect)
        self.printer.register_event_handler("klippy:ready", self.handle_ready)
        Thread(target=self.run_server, args=[]).start()

    def handle_ready(self):
        self.reactor.register_timer(self.plot_trapq,
            self.reactor.monotonic() + self.trapq_interval)

    def run_server(self):
        while self.reactor._process:
            data = json.dumps(self.data_queue.get())
            try:
                self.ws.send(data)
            except:
                now = time.time()
                if now > self.reconnect_time:
                    self.reconnect_time = now + 5
                    self.ws = websocket.WebSocket(skip_utf8_validation=True)
                    try:
                        self.ws.connect("ws://" + self.host_address)
                    except:
                        self.ws = None

    def send_data(self, name, data):
        monotonic = data.pop('monotonic', None)
        print_time = data.pop('print_time', None)
        if monotonic is None:
            monotonic = self.reactor.monotonic()
        if print_time is None:
            mcu = self.printer.lookup_object('mcu')
            print_time = mcu.estimated_print_time(monotonic)
        self.data_queue.put_nowait(
            {'monotonic': monotonic, 'print_time': print_time + self.start_time,
             'raw_print_time': print_time, name: data})
        if (abs(monotonic - self.last_monotonic) > 60 or
            abs(print_time - self.last_print_time) > 60):
            logging.info(f"Plotjuggler got a bad timestamp for {name}"
                f"print_time {print_time} monotonic {monotonic}"
                f"at {self.reactor.monotonic()}")
        self.last_print_time = print_time
        self.last_monotonic = monotonic
    def plot_trapq(self, eventtime):
        ffi_main, ffi_lib = chelper.get_ffi()
        print_time = self.printer.lookup_object('mcu').estimated_print_time(
            eventtime)
        th = self.printer.lookup_object('toolhead')
        xy = th.get_trapq()
        e = th.get_extruder().get_trapq()
        data = {'monotonic': eventtime, 'print_time': print_time}
        for tq in (xy, e):
            move = ffi_main.new('struct pull_move[1]')
            if ffi_lib.trapq_extract_old(tq, move, 1, 0., print_time):
                move = move[0]
                move_time = max(0.,
                    min(move.move_t, print_time - move.print_time))
                dist = (move.start_v + .5 * move.accel * move_time) * move_time
                pos = (move.start_x + move.x_r * dist, move.start_y +
                    move.y_r * dist, move.start_z + move.z_r * dist)
                velocity = move.start_v + move.accel * move_time
                if tq == xy:
                    data['x_pos'] = pos[0]
                    data['y_pos'] = pos[1]
                    data['velocity'] = velocity
                    data['z_pos'] = pos[2]
                else:
                    data['e_pos'] = pos[0]
                    data['e_velocity'] = velocity
        self.send_data('trapq', data)
        return eventtime + self.trapq_interval

    def handle_disconnect(self):
        self.data_queue.put_nowait({}) # Send dummy event to trigger the loop

def load_config(config):
    return Plotjuggler(config)
