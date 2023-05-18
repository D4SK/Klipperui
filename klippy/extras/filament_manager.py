# Keeps track of loaded cura materials
#
# Copyright (C) 2020  Konstantin Vogel <konstantin.vogel@gmx.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from enum import Flag, auto
import functools
import os
import json
import logging

from math import pi
from xml.etree import ElementTree

import location


class FilamentManager:

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = config.get_reactor()
        self.material_condition = config.getchoice("material_condition",
                {"exact": "exact", "type": "type", "any": "any"}, "any")
        self.material_tolerance = config.getfloat("material_tolerance", 50)
        self.config_diameter = config.getsection("extruder").getfloat("filament_diameter", 1.75)
        self.filament_switch_sensor = bool(config.get_prefix_sections("filament_switch_sensor"))
        self.preselected_material = {}
        for i in range(1, 10):
            if not config.has_section(f"extruder{i}"):
                extruder_count = i
                break
        self.material_dir = location.material_dir()
        self.loaded_material_path = location.loaded_material()
        self.extruders = {}
        self.distance_trackers = {}
        self.printer.register_event_handler("klippy:ready", self.handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self.handle_shutdown)
        self.printer.register_event_handler("filament_switch_sensor:runout", self.handle_runout)
        self.parameter_callbacks = [self.update_loaded_material_amount]

        # [Type][Brand][Color] = guid, a dict tree for choosing filaments
        self.tbc_to_guid = {}
        self.guid_to_path = {}
        self.read_material_library_xml()

        # json object of loaded and unloaded material
        # {'loaded': [{'guid': None if nothing is loaded,
        #              'amount': amount in kg,
        #              'state': loading | loaded | unloading | no material,
        #              'parameters: dictionary of measured parameters}],
        # 'unloaded': [{'guid': None if nothing is loaded,
        #               'amount': amount in kg,
        #               'parameters': dictionary of measured parameters}]}
        self.material = {
            'loaded': [{'guid': None, 'state': "no material", 'amount': 0, 'parameters': {}} for _ in range(extruder_count)],
            'unloaded': []
        }
        self.read_loaded_material_json()

        # set materials to loaded if power was lost during loading or unloading
        for material in self.material['loaded']:
            if material['state'] in ('loading', 'unloading'):
                material['state'] = 'loaded'

    def register_parameter_callback(self, callback):
        self.parameter_callbacks.append(callback)

    def handle_ready(self):
        self.heater_manager = self.printer.lookup_object('heaters')
        self.toolhead = self.printer.lookup_object('toolhead')
        self.gcode = self.printer.lookup_object('gcode')
        for i in range(10):
            extruder_id = f"extruder{'' if i==0 else i}"
            extruder = self.printer.lookup_object(extruder_id, None)
            if extruder:
                self.extruders[extruder_id] = extruder
                self.distance_trackers[extruder_id] = [0]
                self.extruders[extruder_id].distance_tracker = self.distance_trackers[extruder_id]
        self.printer.send_event("filament_manager:material_changed", self.material)

    def handle_runout(self, extruder_id):
        virtual_sdcard = self.printer.objects['virtual_sdcard']
        while len(virtual_sdcard.jobs) and virtual_sdcard.jobs[0].state == 'pausing':
            self.reactor.pause(self.reactor.monotonic() + 0.05)
        self.unload(extruder_id)

    def handle_shutdown(self):
        self.run_parameter_callbacks()
        self.write_loaded_material_json()

    def set_config(self, material_condition):
        configfile = self.printer.lookup_object('configfile')
        if material_condition in {"exact", "type", "any"}:
            self.material_condition = material_condition
            configfile.set("filament_manager", "material_condition", material_condition)
            configfile.save_config(restart=False)

######################################################################
# manage cura-material xml files
######################################################################

    def read_material_library_xml(self):
        self.guid_to_path.clear()
        self.tbc_to_guid.clear()
        files = os.listdir(self.material_dir)
        for f in files:
            if f.endswith(".xml.fdm_material"):
                self.read_single_file(os.path.join(self.material_dir, f))

    def read_single_file(self, f_path):
        f_guid = self.get_info(f_path, './m:metadata/m:GUID')
        if not f_guid:
            logging.debug(f"Filament Manager: Couldn't get GUID from {f_path}")
            return -1
        # generate path lookup
        self.guid_to_path[f_guid] = f_path

        # only add to the tbc dict if the diameter is correct, use the same check as cura
        # cura/Settings/ExtruderStack.py -> getApproximateMaterialDiameter()
        f_diameter = self.get_info(f_path, './m:properties/m:diameter', -1)
        if round(self.config_diameter) != round(float(f_diameter)):
            return -1

        f_type  = self.get_info(f_path, './m:metadata/m:name/m:material')
        f_brand = self.get_info(f_path, './m:metadata/m:name/m:brand')
        f_color = self.get_info(f_path, './m:metadata/m:color_code')
        if not (f_type and f_brand and f_color):
            logging.debug(f"Filament Manager: Missing data in {f_path}")
            return -1

        tbc = self.tbc_to_guid
        if f_type in tbc:
            if f_brand in tbc[f_type]:
                # type and brand already there, add color entry
                tbc[f_type][f_brand][f_color] = f_guid
            else:
                # type already there, add dict for this brand with color entry
                tbc[f_type][f_brand] = {f_color: f_guid}
        else:
            # add dict for this type
            tbc[f_type] = {f_brand: {f_color: f_guid}}

    # Caches the 10 most recent calls to ElementTree.parse
    cached_parse = staticmethod(functools.lru_cache(maxsize=10)(ElementTree.parse))

    def get_info(self, material, xpath, default=None):
        """material can be either GUID or filepath"""
        fpath = self.guid_to_path.get(material) or material
        try:
            tree = self.cached_parse(fpath)
        except:
            logging.warning(f"Filament Manager: Failed to parse {fpath}, caught Exception:",
                    exc_info=True)
            return default
        else:
            ns = {'m': 'http://www.ultimaker.com/material'}
            return tree.findtext(xpath, default, ns)

    def get_material_match(self, md):
        loaded = self.get_status()["loaded"]

        loaded_materials = []
        needed_materials = []
        problems_per_extruder = []
        for extruder in range(md.get_extruder_count() or 1):
            problems = Problem.OK

            n_mat = Material(self, md.get_material_guid(extruder),
                             md.get_material_type(extruder),
                             md.get_material_brand(extruder),
                             md.get_material_color(extruder),
                             md.get_material_amount(extruder, "weight"))

            if extruder >= len(loaded):
                problems |= Problem.EXTRUDER_COUNT
            else:
                l_mat = Material(self, loaded[extruder]["guid"],
                                 amount=loaded[extruder]["amount"] * 1000, state=loaded[extruder]["state"])

                if n_mat.amount: # Ignore problems if amount is 0 or None

                    if (l_mat.amount - n_mat.amount) < self.material_tolerance:
                        problems |= Problem.AMOUNT

                    if self.material_condition != "any" and (
                        n_mat.guid is None or l_mat.guid != n_mat.guid):
                        if (n_mat.type is None or l_mat.type is None or
                            n_mat.type.lower() != l_mat.type.lower()):
                            problems |= Problem.TYPE

                        if self.material_condition != "type":
                            if (n_mat.brand is None or l_mat.brand is None or
                                n_mat.brand.lower() != l_mat.brand.lower()):
                                problems |= Problem.BRAND
                            if (n_mat.color is None or l_mat.color is None or
                                n_mat.color != n_mat.color):
                                problems |= Problem.COLOR

            loaded_materials.append(l_mat)
            needed_materials.append(n_mat)
            problems_per_extruder.append(problems)

        return loaded_materials, needed_materials, problems_per_extruder


######################################################################
# loading and unloading api
######################################################################

    def get_status(self):
        self.update_loaded_material_amount()
        return self.material

    def get_tbc(self):
        return self.tbc_to_guid

    def select_loading_material(self, extruder_id, material):
        if 'parameters' not in material:
            material['parameters'] = {}
        idx = self.idx(extruder_id)
        material['amount'] = material['amount'] or 1
        material['temp'] = float(self.get_info(material['guid'], "./m:settings/m:setting[@key='print temperature']", 200))
        if material['unloaded_idx'] is not None:
            unloaded = self.material['unloaded'].pop(material['unloaded_idx'])
            material['guid'] = material['guid'] or unloaded['guid']
        self.preselected_material[extruder_id] = material
        if self.material['loaded'][idx]['state'] == "loading":
            self._finalize_loading(extruder_id)
        elif not self.filament_switch_sensor:
            self.start_loading(extruder_id)

    def start_loading(self, extruder_id):
        idx = self.idx(extruder_id)
        if self.material['loaded'][idx]['state'] != "no material":
            return
        finalize = False
        if extruder_id in self.preselected_material:
            finalize = True
            material = self.preselected_material[extruder_id]
        else:
            material = {'guid': None, 'amount': 0, 'temp': 200, 'parameters': {}}
            self.printer.send_event('filament_manager:request_material_choice', extruder_id)

        self.material['loaded'][idx].update({
            'guid': material['guid'],
            'amount': material['amount'],
            'parameters': material['parameters'],
            'state': 'loading'})
        self.printer.send_event("filament_manager:material_changed", self.material)
        self.gcode.run_script(f"LOAD_FILAMENT TEMPERATURE={material['temp']} T={idx}")
        if finalize:
            self._finalize_loading(extruder_id)

    def _finalize_loading(self, extruder_id):
        idx = self.idx(extruder_id)
        material = self.preselected_material[extruder_id]
        self.material['loaded'][idx].update({
            'guid': material['guid'],
            'amount': material['amount'],
            'parameters': material['parameters'],
            'state': 'loading'})
        self.preselected_material.pop(extruder_id)
        self.printer.send_event("filament_manager:material_changed", self.material)
        self.gcode.run_script(f"PRIME_FILAMENT TEMPERATURE={material['temp']} T={idx}")
        self.material['loaded'][idx]['state'] = 'loaded'
        self.printer.send_event("filament_manager:material_changed", self.material)
        self.write_loaded_material_json()

    def unload(self, extruder_id):
        idx = self.idx(extruder_id)
        if self.material['loaded'][idx]['state'] == 'loaded':
            self.run_parameter_callbacks()
            temp = 200 # Default value
            if self.material['loaded'][idx]['state'] == 'loaded':
                self.material['loaded'][idx]['state'] = 'unloading'
                self.write_loaded_material_json()
                temp = self.get_info(self.material['loaded'][idx]['guid'],
                    "./m:settings/m:setting[@key='print temperature']", temp)
            self.gcode.run_script(f"UNLOAD_FILAMENT TEMPERATURE={temp} T={idx}")
            if self.material['loaded'][idx]['guid']:
                self.material['unloaded'].insert(0, {
                    'guid': self.material['loaded'][idx]['guid'],
                    'amount': self.material['loaded'][idx]['amount'],
                    'parameters': self.material['loaded'][idx]['parameters']})
            self.material['loaded'][idx].update({
                'guid': None,
                'amount': 0,
                'state': 'no material',
                'parameters': {}})
            self.material['unloaded'] = self.material['unloaded'][:15] # only store recent materials
            self.printer.send_event("filament_manager:material_changed", self.material)
            self.write_loaded_material_json()

    def idx(self, extruder_id):
        return 0 if extruder_id == 'extruder' else int(extruder_id[-1])

######################################################################
# store json with loaded and recently unloaded materials and their amount
######################################################################

    def read_loaded_material_json(self):
        """Read the material file and return it as a list object"""
        try:
            with open(self.loaded_material_path, "r") as f:
                material = json.load(f)
            if not self.verify_loaded_material_json(material):
                logging.info("Filament-Manager: Malformed material file at " + self.loaded_material_path)
            else:
                self.material = material
        except (IOError, ValueError): # No file or incorrect JSON
            logging.info("Filament-Manager: Couldn't read loaded-material-file at " + self.loaded_material_path)

    def verify_loaded_material_json(self, material):
        """Only return True when the entire file has a correct structure"""
        try:
            for mat in material['loaded']:
                if not (
                    mat['state'] in {'loaded', 'loading', 'unloading', 'no material'} and
                    isinstance(mat['guid'], (str, type(None))) and
                    isinstance(mat['amount'], (float, int))):
                    return False
            for mat in material['unloaded']:
                if not (
                    isinstance(mat['guid'], str) and
                    isinstance(mat['amount'], (float, int))):
                    return False
            return True
        except:
            return False

    def write_loaded_material_json(self):
        """Write the object to the material file"""
        try:
            with open(self.loaded_material_path, "w") as f:
                json.dump(self.material, f, indent=True)
        except IOError:
            logging.warning("Filament-Manager: Couldn't write loaded-material-file at "
                    + self.loaded_material_path, exc_info=True)
        self.printer.send_event("filament_manager:material_changed", self.material)

    def run_parameter_callbacks(self):
        for cb in self.parameter_callbacks:
            ret = cb()
            if ret:
                extruder_id, parameters = ret
                self.material['loaded'][self.idx(extruder_id)]['parameters'].update(parameters)

    def update_loaded_material_amount(self):
        for extruder_id in self.extruders:
            idx = self.idx(extruder_id)
            mat = self.material['loaded'][idx]
            extruded_length = self.distance_trackers[extruder_id][0]
            self.distance_trackers[extruder_id][0] = 0
            guid = mat['guid']
            if guid:
                density = float(self.get_info(guid, './m:properties/m:density', '1.24'))
                diameter = float(self.get_info(guid, './m:properties/m:diameter', '1.75'))
                area = pi * (diameter/2)**2
                extruded_weight = extruded_length*area*density/1e6 # [mm^2]*[g/cm^2] -> [kg]
                mat['amount'] -= extruded_weight

class Material:

    def __init__(self, fm=None, guid=None,
        type=None, brand=None, color=None, amount=None, state=None):
        self.guid = guid
        if isinstance(fm, FilamentManager) and guid in fm.guid_to_path:
            self.type  = fm.get_info(guid, './m:metadata/m:name/m:material')
            self.brand = fm.get_info(guid, './m:metadata/m:name/m:brand')
            self.color = fm.get_info(guid, './m:metadata/m:color_code')
        else:
            self.type = type
            self.brand = brand
            self.color = color # Hex color-code like "#1188ff"
        self.amount = amount  # [g]
        self.state = state


class Problem(Flag):
    OK = 0
    TYPE = auto()
    BRAND = auto()
    COLOR = auto()
    AMOUNT = auto()
    EXTRUDER_COUNT = auto()


def load_config(config):
    return FilamentManager(config)
