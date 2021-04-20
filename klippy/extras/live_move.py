# Simple module allowing for live-movement of axes, e.g. by pressing and holding a button
#
# Copyright (C) 2020  Konstantin Vogel <konstantin.vogel@gmx.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from reactor import ReactorCompletion

class LiveMove:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = config.get_reactor()
        self.move_completion = {'x': None, 'y': None, 'z': None, 'e': None}
        self.start_mcu_pos = {}
        self.speed = {'x': 5, 'y': 5, 'z': 3, 'e': 2}
        stepper_config = {'x': config.getsection('stepper_x'),
                          'y': config.getsection('stepper_y'),
                          'z': config.getsection('stepper_z')}
        self.pos_max = {i: stepper_config[i].getfloat('position_max', 200) for i in 'xyz'}
        self.pos_min = {i: stepper_config[i].getfloat('position_min', 0) for i in 'xyz'}
        self.printer.register_event_handler("klippy:connect", self.handle_connect)
        self.toolhead = None

    def handle_connect(self):
        self.toolhead = self.printer.objects['toolhead']

    def _fill_coord(self, new_pos):
        """ Fill in any None entries in 'new_pos' with current toolhead position """
        pos = list(self.toolhead.get_position())
        for i, new in enumerate(new_pos):
            if new is not None:
                pos[i] = new
        return pos

    def toolhead_busy(self):
        print_time, est_print_time, lookahead_empty = self.toolhead.check_busy(self.reactor.monotonic())
        idle_time = est_print_time - print_time
        return bool(not lookahead_empty or idle_time <= 0)

    def start_move(self, axis, direction):
        if self.toolhead and not self.toolhead_busy():
            i = 0
            extruder = self.printer.lookup_object(f"extruder{'' if i==0 else i}")
            kin_status = self.toolhead.kin.get_status(self.reactor.monotonic())
            self.move_completion[axis] = ReactorCompletion(self.reactor)
            pos = self.toolhead.get_position()
            idx = {'x': 0, 'y':1, 'z':2, 'e':3}[axis]
            if axis == 'e':
                pos[idx] += 40*direction
            elif axis in kin_status['homed_axes']:
                pos[idx] = (self.pos_max[axis] if direction == 1 else self.pos_min[axis])
            else:
                pos[idx] += (self.pos_max[axis] - self.pos_min[axis])*direction
            self.toolhead.flush_step_generation()
            steppers = self.toolhead.kin.get_steppers() + [extruder.stepper]

            for s in steppers:
                s.set_tag_position(s.get_commanded_position())

            self.start_mcu_pos[axis] = [(s, s.get_mcu_position()) for s in steppers]
            self.toolhead.dwell(0.050)
            self.toolhead.drip_move(pos, self.speed[axis], self.move_completion[axis], force=True)


    def stop_move(self, axis):
        if self.move_completion[axis] != None:
            i = 0
            extruder = self.printer.lookup_object(f"extruder{'' if i==0 else i}")
            # this works similar to homing.py
            self.move_completion[axis].complete(True)
            self.reactor.pause(self.reactor.NOW)
            self.toolhead.flush_step_generation()
            #                   v--start_pos     v--end_pos
            end_mcu_pos = [(s, spos, s.get_mcu_position()) for s, spos in self.start_mcu_pos[axis]]
            for s, spos, epos in end_mcu_pos:
                md = (epos - spos) * s.get_step_dist()
                s.set_tag_position(s.get_tag_position() + md)
            self.toolhead.set_position(
                self.toolhead.get_kinematics().calc_tag_position() + [extruder.stepper.get_tag_position()])
            self.toolhead.dwell(0.050)
            if axis == 'e':
                extruder.update_move_time(self.toolhead.print_time)

def load_config(config):
    return LiveMove(config)
