from datetime import datetime, timedelta

def set_attribute(e, root, property_name, val):
    setattr(root, property_name, val)

def update_dict(e, root, dict_name, val):
    getattr(root, dict_name).update(val)

def load_object(e, printer, object_name): # config objects can't be pickled
    klipper_config = printer.objects['configfile'].read_main_config()
    printer.load_object(klipper_config, object_name)

######################################################################
# Tuning
######################################################################

def reset_tuning(e, printer):
    send_flow(e, printer, 100)
    send_speed(e, printer, 100)
    send_z_offset(e, printer, 0)
    send_fan(e, printer, 0)
    send_chamber_fan(e, printer, 0)
    send_acceleration(e, 100)
    reset_pressure_advance(e, printer)
    update(e, printer)


def clear_buildplate(e, printer):
    printer.lookup_object('virtual_sdcard').clear_buildplate()

def get_collision_config(e, printer):
    continuous_printing, reposition = printer.lookup_object('collision').get_config()
    printer.reactor.cb(set_attribute, 'continuous_printing', continuous_printing, process='kgui')
    printer.reactor.cb(set_attribute, 'reposition', reposition, process='kgui')

    condition = printer.lookup_object('filament_manager').material_condition
    printer.reactor.cb(set_attribute, 'material_condition', condition, process='kgui')

def set_collision_config(e, printer, continuous, reposition, condition):
    printer.lookup_object('collision').set_config(continuous, reposition)
    printer.lookup_object('filament_manager').set_config(material_condition=condition)


def get_z_offset(e, printer):
    z_offset = printer.objects['gcode_move'].homing_position[2]
    printer.reactor.cb(set_attribute, 'z_offset', z_offset, process='kgui')
def send_z_offset(e, printer, z_offset):
    printer.objects['gcode'].run_script(f"SET_GCODE_OFFSET Z={z_offset} MOVE=1 MOVE_SPEED=5")
    get_z_offset(e, printer)

def get_speed(e, printer):
    motion_status = printer.objects['motion_report'].get_status(e)
    status = printer.objects['gcode_move'].get_status(e)
    printer.reactor.cb(set_attribute, 'speed_factor', status['speed_factor']*100, process='kgui')
    printer.reactor.cb(set_attribute, 'speed', motion_status['live_velocity'], process='kgui')
def send_speed(e, printer, val):
    val = val/(60.*100.)
    printer.objects['gcode_move'].speed = printer.objects['gcode_move']._get_gcode_speed() * val
    printer.objects['gcode_move'].speed_factor = val
    get_speed(e, printer)

def get_flow(e, printer):
    flow_factor = printer.objects['gcode_move'].extrude_factor*100
    status = printer.objects['motion_report'].get_status(e)
    printer.reactor.cb(set_attribute, 'flow_factor', flow_factor, process='kgui')
    printer.reactor.cb(set_attribute, 'flow', status['live_extruder_velocity'], process='kgui')
def send_flow(e, printer, val):
    new_extrude_factor = val/100.
    gcode_move = printer.objects['gcode_move']
    last_e_pos = gcode_move.last_position[3]
    e_value = (last_e_pos - gcode_move.base_position[3]) / gcode_move.extrude_factor
    gcode_move.base_position[3] = last_e_pos - e_value * new_extrude_factor
    gcode_move.extrude_factor = new_extrude_factor
    get_flow(e, printer)

def get_fan(e, printer):
    if 'fan' in printer.objects:
        fan_speed = printer.objects['fan'].fan.last_fan_value * 100 / printer.objects['fan'].fan.max_power
        printer.reactor.cb(set_attribute, 'fan_speed', fan_speed, process='kgui')
def send_fan(e, printer, speed):
    if 'fan' in printer.objects:
        printer.objects['fan'].fan.set_speed_from_command(speed/100)
        get_fan(e, printer)

def get_chamber_fan(e, printer):
    if "temperature_fan chamber_fan" in printer.objects:
        state = printer.objects['temperature_fan chamber_fan'].get_status(e)
        speed = state['speed']*100/printer.objects['temperature_fan chamber_fan'].fan.max_power
        printer.reactor.cb(set_attribute, 'chamber_fan_speed', speed, process='kgui')
        printer.reactor.cb(set_attribute, 'chamber_temp', [state['target'], state['temperature']], process='kgui')
def send_chamber_fan(e, printer, val):
    if "temperature_fan chamber_fan" in printer.objects:
        printer.objects['gcode'].run_script(f"SET_TEMPERATURE_FAN_TARGET TEMPERATURE_FAN=chamber_fan TARGET={val}")
        get_chamber_fan(e, printer)

def get_pressure_advance(e, printer): # gives pressure_advance value of 1. extruder
    pressure_advance = printer.objects['extruder'].get_status(e)['pressure_advance']
    printer.reactor.cb(set_attribute, 'pressure_advance', pressure_advance, process='kgui')
def send_pressure_advance(e, printer, val):
    for i in range(10):
        extruder_id = f"extruder{'' if i==0 else i}"
        if extruder_id in printer.objects:
            printer.objects[extruder_id].extruder_stepper._set_pressure_advance(
                val, printer.objects[extruder_id].extruder_stepper.pressure_advance_smooth_time)
        else:
            break
    get_pressure_advance(e, printer)
def reset_pressure_advance(e, printer):
    for i in range(10):
        extruder_id = f"extruder{'' if i==0 else i}"
        if extruder_id in printer.objects:
            extruder = printer.objects[extruder_id]
            klipper_config = printer.objects['configfile'].read_main_config()
            pa = klipper_config.getsection(extruder.name).getfloat('pressure_advance', 0., minval=0.)
            extruder.extruder_stepper._set_pressure_advance(pa, extruder.extruder_stepper.pressure_advance_smooth_time)

def get_acceleration(e, printer):
    acceleration = printer.objects['toolhead'].max_accel
    acceleration_factor = printer.objects['toolhead'].accel_factor*100
    printer.reactor.cb(set_attribute, 'acceleration', acceleration, process='kgui')
    printer.reactor.cb(set_attribute, 'acceleration_factor', acceleration_factor, process='kgui')
def send_acceleration(e, printer, val):
    val /= 100
    printer.objects['toolhead'].max_accel = printer.objects['toolhead'].max_accel * val/printer.objects['toolhead'].accel_factor
    printer.objects['toolhead'].accel_factor = val
    printer.objects['toolhead']._calc_junction_deviation()
    get_acceleration(e, printer)

######################################################################
# Other Commands
######################################################################

def update(e, printer):
    get_homing_state(e, printer)
    get_print_progress(e, printer)
    get_pressure_advance(e, printer)
    get_acceleration(e, printer)
    get_z_offset(e, printer)
    get_speed(e, printer)
    get_flow(e, printer)
    get_temp(e, printer)
    get_fan(e, printer)
    get_chamber_fan(e, printer)

def save_config(e, printer):
    printer.objects['configfile'].cmd_SAVE_CONFIG(None)

def write_config(e, printer, section, option, value):
    printer.objects['configfile'].set(section, option, value)
    printer.objects['configfile'].cmd_SAVE_CONFIG(None)

def write_pressure_advance(e, printer, value, extruder_count):
    for i in range(extruder_count):
        printer.objects['configfile'].set(f"extruder{'' if i==0 else i}", "pressure_advance", value)
    printer.objects['configfile'].cmd_SAVE_CONFIG(None)

def get_temp(e, printer):
    if 'heaters' in printer.objects:
        temp = {}
        for name, heater in printer.objects['heaters'].heaters.items():
            current, target = heater.get_temp(e)
            temp[name] = [target, current]
        printer.reactor.cb(update_dict, 'temp', temp, process='kgui')
def send_temp(e, printer, temp, extruder_id):
    printer.objects['heaters'].heaters[extruder_id].set_temp(temp)
    get_temp(e, printer)

def get_homing_state(e, printer):
    status = printer.objects['toolhead'].kin.get_status(e)
    printer.reactor.cb(set_attribute, 'homed', status['homed_axes'], process='kgui')
def send_home(e, printer, axis):
    printer.objects['gcode'].run_script("G28" + axis.upper())

def send_motors_off(e, printer):
    printer.objects['gcode'].run_script("M18")
    get_homing_state(e, printer)

def get_usage(e, printer):
    usage = printer.lookup_object('usage', None)
    if usage:
        printer.reactor.cb(set_attribute, 'usage', usage.get_status(), process='kgui')

def get_pos(e, printer):
    status = printer.objects['motion_report'].get_status(e)
    printer.reactor.cb(set_attribute, 'pos', status['live_position'], process='kgui')
def send_pos(e, printer, x=None, y=None, z=None, extruder=None, speed=15):
    new_pos = [x,y,z]
    homed_axes = printer.objects['toolhead'].get_status(e)['homed_axes']
    # check whether axes are still homed
    mv = ""
    for new, name in zip(new_pos, 'xyz'):
        if new != None and name in homed_axes:
            mv += f"{name}{new} "
    if extruder:
        mv += f"e{extruder}"
    printer.objects['gcode'].run_script(
        f"""
        SAVE_GCODE_STATE NAME=MOVE_STATE
        M83
        G1 {mv} F{speed*60}
        RESTORE_GCODE_STATE NAME=MOVE_STATE
        """)
    get_pos(e, printer)

def get_pos_limits(e, printer):
    rails = printer.objects['toolhead'].kin.rails
    printer.reactor.cb(set_attribute, 'pos_min', [rail.position_min for rail in rails], process='kgui')
    printer.reactor.cb(set_attribute, 'pos_max', [rail.position_max for rail in rails], process='kgui')

def get_print_progress(e, printer):
    est_remaining, progress = printer.objects['print_stats'].get_print_time_prediction()
    printer.reactor.cb(set_print_progress, est_remaining, progress, process='kgui')
def set_print_progress(e, kgui, est_remaining, progress):
    if kgui.print_state in ('printing', 'pausing', 'paused'):
        if progress is None: # no prediction could be made yet
            kgui.progress = 0
            kgui.print_time = ""
            kgui.print_done_time = ""
        else:
            remaining = timedelta(seconds=est_remaining)
            done = datetime.now() + remaining
            tomorrow = datetime.now() + timedelta(days=1)
            kgui.progress = progress
            kgui.print_time = format_time(remaining.total_seconds()) + " remaining"
            if done.day == datetime.now().day:
                kgui.print_done_time = done.strftime("%-H:%M")
            elif done.day == tomorrow.day:
                kgui.print_done_time = done.strftime("tomorrow %-H:%M")
            else:
                kgui.print_done_time = done.strftime("%a %-H:%M")

def get_material(e, printer):
    fm = printer.lookup_object('filament_manager', None)
    if not fm:
        return
    material = fm.get_status()
    for m in material['unloaded']:
        m.update({
            'material_type': fm.get_info(m['guid'], "./m:metadata/m:name/m:material", ""),
            'hex_color': fm.get_info(m['guid'], "./m:metadata/m:color_code", None),
            'brand': fm.get_info(m['guid'], './m:metadata/m:name/m:brand', "")})
    for m in material['loaded']:
        if m['guid']:
            m.update({
            'material_type': fm.get_info(m['guid'], "./m:metadata/m:name/m:material", ""),
            'hex_color': fm.get_info(m['guid'], "./m:metadata/m:color_code", None),
            'brand': fm.get_info(m['guid'], './m:metadata/m:name/m:brand', ""),
            'print_temp': fm.get_info(m['guid'], "./m:settings/m:setting[@key='print temperature']", 0),
            'bed_temp': fm.get_info(m['guid'], "./m:settings/m:setting[@key='heated bed temperature']", 0)})
        else:
            m.update({
            'material_type': "",
            'hex_color': None,
            'brand': ""})
    printer.reactor.cb(set_attribute, 'material', material, process='kgui')

def get_tbc(e, printer):
    fm = printer.lookup_object('filament_manager', None)
    if not fm:
        return
    printer.reactor.cb(set_attribute, 'tbc_to_guid', fm.get_tbc(), process='kgui')


def send_print(e, printer, filepath):
    printer.objects['virtual_sdcard'].add_print(filepath, assume_clear_after=0)

def send_stop(e, printer):
    printer.objects['virtual_sdcard'].stop_print()

def send_pause(e, printer):
    printer.objects['virtual_sdcard'].pause_print()

def send_resume(e, printer):
    printer.objects['virtual_sdcard'].resume_print()

def restart(e, printer):
    printer.request_exit('restart')

def firmware_restart(e, printer):
    printer.request_exit('firmware_restart')

def format_time(seconds):
    seconds = int(seconds)
    days = seconds // 86400
    seconds %= 86400
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    if days:
        return f"{days} days {hours} {'hr' if hours==1 else 'hrs'} {minutes} min"
    if hours:
        return f"{hours} {'hr' if hours==1 else 'hrs'} {minutes} min"
    if minutes:
        return f"{minutes} min"
    return f"{seconds} sec"

def calculate_filament_color(c):
    """ Calculate filament color thats not to light for text.
        Also the lightness of an rgb color.
        This is equal to the average between the minimum and
        maximum value."""
    #lightness = 0.5*(max(filament_color) + min(filament_color))
    return [c[0]*0.6, c[1]*0.6, c[2]*0.6, c[3]]

def hex_to_rgba(h):
    """ Converts hex color to rgba float format
        accepts strings like #ffffff or #FFFFFF"""
    if not h:
        return (0,0,0,0)
    return [int(h[i:i + 2], 16) / 255. for i in (1, 3, 5)] + [1]

def trim_history(e, printer):
    printer.objects['print_history'].trim_history()

def request_event_history(e, printer):
    events = printer.reactor.get_event_history()
    printer.reactor.cb(receive_event_history, events, process='kgui')

def receive_event_history(e, kgui, events):
    # Register event handlers
    kgui.reactor.register_event_handler("klippy:connect", kgui.handle_connect) # printer_objects available
    kgui.reactor.register_event_handler("klippy:ready", kgui.handle_ready) # connect handlers have run
    kgui.reactor.register_event_handler("klippy:disconnect", kgui.handle_disconnect)
    kgui.reactor.register_event_handler("klippy:shutdown", kgui.handle_shutdown)
    kgui.reactor.register_event_handler("klippy:critical_error", kgui.handle_critical_error)
    kgui.reactor.register_event_handler("klippy:error", kgui.handle_error)
    kgui.reactor.register_event_handler("homing:home_rails_end", kgui.handle_home_end)
    kgui.reactor.register_event_handler("virtual_sdcard:print_start", kgui.handle_print_start)
    kgui.reactor.register_event_handler("virtual_sdcard:print_end", kgui.handle_print_end)
    kgui.reactor.register_event_handler("virtual_sdcard:print_change", kgui.handle_print_change)
    kgui.reactor.register_event_handler("virtual_sdcard:print_added", kgui.handle_print_added)
    kgui.reactor.register_event_handler("print_history:change", kgui.handle_history_change)
    kgui.reactor.register_event_handler("filament_manager:material_changed", kgui.handle_material_change)
    kgui.reactor.register_event_handler("filament_manager:request_material_choice", kgui.handle_request_material_choice)
    kgui.reactor.register_event_handler("filament_switch_sensor:runout", kgui.handle_material_runout)
    kgui.reactor.register_event_handler("virtual_sdcard:material_mismatch", kgui.handle_material_mismatch)
    for event, params in events:
        kgui.reactor.run_event(e, kgui, event, params)

def setup_commands(e, printer):
    def cmd_SHOW_STATS(gcmd):
        statistics = printer.lookup_object('statistics')
        statistics.subscribers['kgui'] = lambda stats: printer.reactor.cb(set_attribute, 'stats', '\n'.join([s[1] for s in stats]), process='kgui')
    def cmd_HIDE_STATS(gcmd):
        statistics = printer.lookup_object('statistics')
        statistics.subscribers.pop("kgui", None)
        printer.reactor.cb(set_attribute, 'stats', "", process='kgui')
    gcode = printer.lookup_object('gcode')
    gcode.register_command('SHOW_STATS', cmd_SHOW_STATS)
    gcode.register_command('HIDE_STATS', cmd_HIDE_STATS)

def move_print(e, printer, idx, uuid, move):
    printer.objects['virtual_sdcard'].move_print(idx, uuid, move)

def remove_print(e, printer, idx, uuid):
    printer.objects['virtual_sdcard'].remove_print(idx, uuid)

def load(e, printer, extruder_id, material):
    printer.objects['filament_manager'].select_loading_material(extruder_id, material)

def unload(e, printer, *args, **kwargs):
    printer.objects['filament_manager'].unload(*args, **kwargs)

def get_connected(e, curaconnection):
    connected = curaconnection.is_connected()
    curaconnection.reactor.cb(set_attribute, "cura_connected", connected, process='kgui')

def run_script(e, printer, gcode):
    printer.objects['gcode'].run_script(gcode)

def run_script_from_command(e, printer, gcode):
    printer.objects['gcode'].run_script_from_command(gcode)

def set_config(e, printer, section, key, value):
    configfile = printer.lookup_object('configfile')
    configfile.set(section, key, value)
    configfile.save_config(restart=False)
