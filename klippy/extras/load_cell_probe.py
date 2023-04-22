# Load cell probes
#
# Copyright (C) 2023 Konstantin Vogel <konstantin.vogel@gmx.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from collections import deque
from math import sqrt
import logging
import numpy as np

PROBING_START_DELAY = 0.010

class LoadCellProbe:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')

        pin_name = config.get('adc_pin')
        ppins = self.printer.lookup_object('pins')
        ppins.register_chip('load_cell_probe', self)
        self._adc = ppins.setup_pin('load_cell_adc', pin_name)
        self._mcu = self._adc.mcu
        self._oid = self._mcu.create_oid()
        self._data_completion = None
        self._trigger_completion = None
        # How long to keep measuring after halting the stepper
        self._overshoot_sample_time = 0.03
        self._need_data_to = 0
        self.values = deque(maxlen=300)
        self.baseline = []
        self.steppers = []
        self.manual_query = False

        self.position_endstop    = config.getfloat('z_offset', 0)
        self.force_threshold     = config.getfloat('force_threshold', above=0.)
        self.noise_limit         = config.getfloat('noise_limit', self.force_threshold*0.15)
        self.stiffness_max       = config.getfloat('stiffness_max', self.force_threshold)
        self.stiffness_min       = config.getfloat('stiffness_min', 0)
        self.linear_noise_limit2 = config.getfloat('linear_noise_limit', self.force_threshold*0.2)**2
        self.baseline_sample_time = config.getfloat('baseline_sample_time', 1, above=0.2)

        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler('klippy:mcu_identify', self._handle_mcu_identify)
        self._mcu.register_config_callback(self._build_config)
        self.gcode.register_command('QUERY_LOAD_CELL', self.cmd_QUERY_LOAD_CELL)
        self.gcode.register_command('QUERY_LOAD_CELL_END', self.cmd_QUERY_LOAD_CELL_END)

    def _build_config(self):
        cmd_queue = self._mcu.alloc_command_queue()
        s = [-1, -1, -1]
        for i, st in enumerate(self.steppers):
            s[i] = st._oid
        self._mcu.add_config_cmd(
            f"config_load_cell_probe oid={self._oid} stepper1={s[0]} stepper2={s[1]} stepper3={s[2]}")
        self.enable_load_cell_trigger_cmd = self._mcu.lookup_command(
            "enable_load_cell_trigger oid=%c enable=%i limit=%i", cq=cmd_queue)
        self._mcu._serial.register_response(
            self.trigger, 'load_cell_probe_triggered', self._oid)

    def setup_pin(self, pin_type, pin_params):
        return self

    def _handle_ready(self):
        self._adc.setup_adc_callback(None, self._adc_callback)
        self.toolhead = self.printer.lookup_object('toolhead')
        self.bed_mesh = self.printer.lookup_object('bed_mesh')

    def calculate_baseline(self, stop_clock):
        full_data = np.array(self.values)
        data = full_data[:,1]
        # remove samples after stop, because the load cell will be under constant tension
        data = data[full_data[:,0] < stop_clock]
        dev = np.abs(data - np.median(data))
        mdev = np.median(dev)
        s = dev/mdev if mdev else 0.
        no_outliers = data[s<4]
        new_baseline = np.median(no_outliers)
        noise = np.std(no_outliers)
        if noise > self.noise_limit:
            self.gcode.respond_info(
                f"Noise limit for baseline measurement exceeded {noise} > {self.noise_limit}")
            return None
        logging.info(f"Load cell established baseline of {new_baseline} "
            f"rejected {100*(len(data)-len(no_outliers))/len(data):.0f}% of samples")
        self.baseline.append(new_baseline)
        if len(self.baseline) > 10:
            self.baseline = self.baseline[5:]
        normal = 0
        weight = 5
        avg = 0
        for b in reversed(self.baseline):
            normal += weight
            avg += weight*b
            weight -= 1
            if weight == 0:
                break
        return avg/normal

    def calculate_t0(self, stop_clock64):
        c1 = stop_clock64 + self._overshoot_sample_time
        if c1 > self.values[-1][1]:
            self._need_data_to = c1
            self._data_completion = self.reactor.completion()
            self._data_completion.wait()
        baseline = self.calculate_baseline(stop_clock64)
        if baseline is None:
            return None
        values = list(self.values)
        A = []
        y = []
        last_residual = np.inf
        skipped_samples = 0
        for i, v in enumerate(reversed(values)):
            if v[0] > c1:
                skipped_samples += 1
                continue
            A.append([self._mcu.clock_to_print_time(v[0]), 1])
            y.append(v[1])
            if len(y) > 50:
                self.gcode.respond_info(
                    "Linear fit could not distinguish baseline and contact phase")
                return None
            if len(y) > 5:
                # fit a linear function to the force where force = x[0]*t + x[1]
                x, residual, rank, s = np.linalg.lstsq(np.array(A), y, rcond=-1)
                assert len(residual), "Numeric error"
                # 1 if sample is peak, 0 if sample is baseline
                peakness = (v[1] - baseline)/(values[-(skipped_samples+1)][1] - baseline)
                added_residual = residual[0] - last_residual
                if added_residual >= (0.8 + 0.7*peakness)*last_residual/(len(y)-1):
                    break
                last_residual = residual[0]
                last_x = x
        noise = last_residual/len(y)
        if noise > self.linear_noise_limit2:
            self.gcode.respond_info(f"Noise limit for linear approximation exceeded "
                f"{sqrt(noise)} > {sqrt(self.linear_noise_limit2)}")
            return None
        t0 = (baseline - last_x[1]) / last_x[0]
        logging.info(f"Load cell established contact using {len(y)} samples "
            f"t0 {t0:.4f} t_stop {self._mcu.clock_to_print_time(stop_clock64):.4f} "
            f"t1 {self._mcu.clock_to_print_time(c1):.4f} "
            f"noise {100*sqrt(noise)/self.force_threshold:.1f}%")
        return t0

    def _adc_callback(self, clock32, value):
        clock64 = self._mcu.clock32_to_clock64(clock32)
        self.values.append((clock64, value))
        if self._data_completion and clock64 >= self._need_data_to:
            self._data_completion.complete(None)
        if self.manual_query:
            self.gcode.respond_info(f"Load cell value is {value} ({value:b})")

    def get_offsets(self):
        return 0, 0, 0

    def trigger(self, params):
        if self._trigger_completion:
            self._trigger_completion.complete(self._mcu.clock32_to_clock64(params['clock']))

    def _handle_mcu_identify(self):
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        for stepper in kin.get_steppers():
            if stepper.is_active_axis('z'):
                self.steppers.append(stepper)

    def cmd_QUERY_LOAD_CELL(self, gcmd):
        self.manual_query = True
        self._adc.query_adc_cmd.send([self._adc.oid, 1, self._oid])

    def cmd_QUERY_LOAD_CELL_END(self, gcmd):
        self.manual_query = False
        self._adc.query_adc_cmd.send([self._adc.oid, 0, -1])

   ######################################################################
   # Endstop interface
   ######################################################################

    def add_stepper(self, stepper):
        pass

    def get_steppers(self):
        return self.steppers

    def home_start(self, print_time, sample_time=None, sample_count=None,
                   rest_time=None, triggered=True, homing=True):
        if homing:
            self.baseline = []
        self._adc.query_adc_cmd.send([self._adc.oid, 1, self._oid])
        self.reactor.pause(self.reactor.monotonic() + self.baseline_sample_time)
        self.enable_load_cell_trigger_cmd.send([self._oid, 1, int(self.force_threshold)])
        self._trigger_completion = self.reactor.completion()
        return self._trigger_completion

    def home_wait(self, home_end_time, homing=True):
        self.reactor.register_callback(lambda e: self._trigger_completion.complete(0), home_end_time)
        stop_clock64 = self._trigger_completion.wait()
        if stop_clock64 != 0:
            t0 = self.calculate_t0(stop_clock64)
        if homing:
            for s in self.steppers:
                s.note_homing_end()
        self._adc.query_adc_cmd.send([self._adc.oid, 0, -1])
        if stop_clock64 == 0:
            return 0, True
        if t0 is None:
            return self._mcu.clock_to_print_time(stop_clock64), True
        return t0, False

    def query_endstop(self, print_time):
        return False

    def get_position_endstop(self):
        return self.position_endstop

class ProbePointsHelper:
    def __init__(self, config, finalize_callback, default_points=None):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')

        self.results = None
        self.retries = 0
        self.finalize_callback = finalize_callback
        self.probe_points = default_points

        self.horizontal_move_z = config.getfloat('horizontal_move_z', 6.)
        self.retract_dist      = config.getfloat('sample_retract_dist', 2., above=0.)
        self.probe_speed       = config.getfloat('probe_speed', 3, above=0.)
        self.max_probe_move    = config.getfloat('max_probe_move', 4)
        self.retract_speed     = config.getfloat('retract_speed', 10, above=0.)
        self.speed             = config.getfloat('speed', 100., above=0.)
        if default_points is None or config.get('points', None) is not None:
            self.probe_points = config.getlists('points', seps=(',', '\n'), parser=float, count=2)

    def minimum_points(self, n):
        pass

    def use_xy_offsets(self, use_offsets):
        pass

    def start_probe(self, gcmd):
        self.lcp = self.printer.lookup_object('load_cell_probe')
        self.lcp.baseline = []
        self.retries = 0
        self.results = []
        while 1:
            done = self._move_next()
            if done:
                break
            pos = self.probing_move()
            self.results.append(pos)

    def update_probe_points(self, points, min_points):
        self.probe_points = points

    def retry_probing_move(self):
        toolhead = self.printer.lookup_object('toolhead')
        self.retries += 1
        if self.retries > 5:
            raise self.printer.command_error("Load cell probing failed after 5 retries")
        # Lift toolhead
        toolhead.manual_move([None, None, self.retract_dist], self.retract_speed)
        return self.probing_move()

    def probing_move(self):
        toolhead = self.printer.lookup_object('toolhead')
        kin = toolhead.get_kinematics()
        gcode_move = self.printer.lookup_object('gcode_move')
        movepos = self._fill_coord([None, None, -self.max_probe_move, None])
        toolhead.flush_step_generation()
        # Start endstop checking
        print_time = toolhead.get_last_move_time()
        completion = self.lcp.home_start(print_time, homing=False)
        toolhead.dwell(PROBING_START_DELAY)
        # Issue move
        error = None
        try:
            toolhead.drip_move(movepos, self.probe_speed, completion)
        except self.printer.command_error as e:
            error = "Error during probing move: " + str(e)
        # Wait for endstops to trigger
        move_end_print_time = toolhead.get_last_move_time()
        trigger_time, retry = self.lcp.home_wait(move_end_print_time, homing=False)
        if trigger_time == 0:
            raise self.printer.command_error("Load cell probe not triggered after timeout")
        # Determine stepper halt positions
        toolhead.flush_step_generation()
        trig_kin_pos = {s.get_name(): s.get_commanded_position() for s in kin.get_steppers()}
        for s in self.lcp.steppers:
            s.note_load_cell_probing_end()
            trig_kin_pos[s.get_name()] = s.mcu_to_commanded_position(s.get_past_mcu_position(trigger_time))
        trig_pos = list(self.probe_points[len(self.results)]) + [kin.calc_position(trig_kin_pos)[2]]
        kin_pos = {s.get_name(): s.get_commanded_position() for s in kin.get_steppers()}
        position = list(kin.calc_position(kin_pos))[:3] + toolhead.get_position()[3:]
        toolhead.set_position(position)
        self.gcode.respond_info(f"Load cell probe triggered at {-position[2]:.3f}mm deflection")
        gcode_move.reset_last_position()
        if error is not None:
            raise self.printer.command_error(error)
        if retry:
            return self.retry_probing_move()
        return trig_pos

    def _move_next(self):
        toolhead = self.printer.lookup_object('toolhead')
        # Lift toolhead
        toolhead.manual_move([None, None, self.horizontal_move_z], self.retract_speed)
        # Check if done probing
        if len(self.results) >= len(self.probe_points):
            res = self.finalize_callback((0, 0, 0), self.results)
            if res != "retry":
                return True
            self.results = []
        # Move to next XY probe point
        nextpos = list(self.probe_points[len(self.results)])
        toolhead.manual_move(nextpos, self.speed)
        toolhead.wait_moves()
        return False

    def _fill_coord(self, coord):
        toolhead = self.printer.lookup_object('toolhead')
        # Fill in any None entries in 'coord' with current toolhead position
        thcoord = list(toolhead.get_position())
        for i in range(len(coord)):
            if coord[i] is not None:
                thcoord[i] = coord[i]
        return thcoord

def load_config(config):
    return LoadCellProbe(config)
