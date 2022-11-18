import logging
import json
import websocket # pip install websocket-client
import time
import queue
from threading import Thread

class Plotjuggler:
    def __init__(self, config):
        self.host_adress = config.get('host_adress')
        self.data_queue = queue.Queue()
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.ws = None
        self.reconnect_time = 0
        self.start_time = self.reactor.monotonic()
        logging.info(f"Plotjuggler print time offset is {self.start_time}")
        self.reactor.register_event_handler("klippy:disconnect", self.handle_disconnect)
        Thread(target=self.run_server, args=[]).start()

    def run_server(self):
        while self.reactor._process:
            data = json.dumps(self.data_queue.get())
            try:
                self.ws.send(data)
            except:
                # logging.info(f"failed sending {data}")
                now = time.time()
                if now > self.reconnect_time:
                    self.reconnect_time = now + 5
                    self.ws = websocket.WebSocket(skip_utf8_validation=True)
                    try:
                        self.ws.connect("ws://" + self.host_adress)
                    except:
                        self.ws = None

    def send_data(self, name, data):
        mcu = self.printer.lookup_object('mcu')
        monotonic = data.pop('monotonic', self.reactor.monotonic())
        print_time = data.pop('print_time', mcu.estimated_print_time(monotonic))
        print_time += self.start_time
        self.data_queue.put_nowait({'monotonic': monotonic, 'print_time': print_time, name: data})

    def handle_disconnect(self):
        self.data_queue.put_nowait({}) # Send dummy event to trigger the loop

def load_config(config):
    return Plotjuggler(config)
