#!/usr/bin/env python3

"""
IMPORTANT NOTES FOR MULTIPROCESSING:

Using this module in an extra process works mostly like in the main printer
process. Obtaining the module is done through printer.load_object(). The module
returned when doing that outside of the main process is slightly different
and any calls to get_metadata() get delegated to the main process so that all
processes can utilize the same cache. Additionally the module in extra processes
uses a local cache to minimize inter-process communication.

The returned metadata object is picklable and all of its methods are called
locally. The filament_manager calls are handled specially by MPMetadata.
"""

import logging
import os

from .ufp_reader import create_ufp_reader

from .base_parser import BaseParser
from .cura_marlin_parser import CuraMarlinParser
from .prusaslicer_parser import PrusaSlicerParser

class GCodeMetadata:

    _parsers = [CuraMarlinParser,
                PrusaSlicerParser,
    ]

    def __init__(self, config):
        # Map paths to metadata objects to cache already parsed files
        self._md_cache = {}

        self.filament_manager = None
        self.config = config
        self.reactor = config.reactor
        self.printer = config.get_printer()
        self.printer.register_event_handler(
                "klippy:connect", self._handle_connect)
        extruder_config = self.config.getsection("extruder")
        self.config_diameter = extruder_config.getfloat(
                "filament_diameter", None)

    def _handle_connect(self):
        self.filament_manager = self.printer.lookup_object(
                "filament_manager", None)

    def get_material_info(self, material, xpath):
        if self.filament_manager:
            return self.filament_manager.get_info(material, xpath)

    def delete_cache_entry(self, path):
        """Delete a single metadata object from the cache"""
        if path in self._md_cache:
            del self._md_cache[path]
            logging.debug("Deleted %s from metadata cache", path)
            self.reactor.send_event("gcode_metadata:invalidate_cache", path)

    def flush_cache(self):
        """
        Delete all cached metadata objects, forcing all files to be
        reparsed in the future.
        """
        self._md_cache.clear()
        logging.debug("Deleted metadata cache")
        self.reactor.send_event("gcode_metadata:invalidate_cache")

    def get_metadata(self, path):
        """
        This is the main method of the module that returns a metadata
        object for the given gcode path. UFP files are also accepted.
        """
        if path in self._md_cache:
            return self._md_cache[path]

        ext = os.path.splitext(path)[1]
        if ext in {".gco", ".gcode"}:
            metadata = self._parse_gcode(path)
        elif ext == ".ufp":
            metadata = create_ufp_reader(path, self)
        else:
            raise ValueError(f"File must be either gcode or ufp file, not {ext}")
        self._md_cache[path] = metadata
        return metadata

    def _parse_gcode(self, path):
        """
        Parse the Metadata for the G-Code and return an object describing
        the file.

        gcode_file can either be a path to the .gcode file or an open
        file pointer. If a stream is provided, be aware that it gets closed
        in this function.
        """
        with open(path, "rb") as gcode_file:
            head = self._get_head_md(gcode_file)
            tail = self._get_tail_md(gcode_file)
        ParserClass = self._find_parser(head + tail)
        return ParserClass(head, tail, path, self)

    def _find_parser(self, lines):
        """
        Return the correct GCode Parser class detect from the given lines.
        If no matching class is found, return BaseParser.
        """
        for p in self._parsers:
            if p._detect(lines):
                return p
        # Use BaseParser as fallback
        return BaseParser

    def _get_head_md(self, fp):
        """
        Retreave the relevant metadata lines from the gcode file,
        which must be given as an open file stream.

        This includes all fully commented lines (starting with ';') up until
        the first non-commented line.
        The leading semicolon is stripped.
        """
        bufsize = 1024
        head = []
        last_line = b""
        keep_reading = True
        fp.seek(0)

        while keep_reading:
            buf = fp.read(bufsize)
            if not buf:  # There are only comments in the file
                break
            new_lines = buf.split(b"\n")
            new_lines[0] = last_line + new_lines[0]
            last_line = new_lines.pop()
            for l in new_lines:
                l = l.strip()
                if l.startswith(b';'):
                    head.append(l[1:].decode())
                elif l == b'':
                    continue
                else:
                    keep_reading = False
                    break
        return head

    def _get_tail_md(self, fp):
        """
        Like _get_head_md but read from EOF backwards.
        """
        bufsize = 1024
        tail = []
        blocks_offset = -1
        last_line = b""
        keep_reading = True
        while keep_reading:
            try:
                fp.seek(blocks_offset * bufsize, 2)
            except OSError:  # Trying to go before BOF
                break
            blocks_offset -= 1
            buf = fp.read(bufsize)
            new_lines = buf.split(b'\n')
            new_lines[-1] += last_line
            last_line = new_lines.pop(0)
            for l in reversed(new_lines):
                l = l.strip()
                if l.startswith(b';'):
                    tail.append(l[1:].decode())
                elif l == b'':
                    continue
                else:
                    keep_reading = False
                    break
        tail.reverse()
        return tail


class MPMetadata:
    """Module class used in unpickled metadata objects that calls
    filament_manager.get_info in printer process.
    """

    def __init__(self, config):
        # Map paths to metadata objects to cache already parsed files for this
        # process specifically
        self._md_cache = {}

        self.reactor = config.reactor
        self.reactor.register_event_handler("gcode_metadata:invalidate_cache",
                self.flush_local_cache)

    def flush_local_cache(self, path=None):
        if path is not None:
            try:
                del self._md_cache[path]
            except KeyError:
                pass
        else:
            self._md_cache.clear()

    def get_metadata(self, path):
        if path in self._md_cache:
            return self._md_cache[path]
        md = self.reactor.cb(self._obtain_md, path, wait=True)
        self._md_cache[path] = md
        return md

    @staticmethod
    def _obtain_md(e, printer, path):
        gcode_metadata = printer.lookup_object('gcode_metadata')
        return gcode_metadata.get_metadata(path)

    def get_material_info(self, material, xpath):
        self.reactor.cb(self._obtain_material_info, material, xpath, wait=True)
    @staticmethod
    def _obtain_material_info(e, printer, material, xpath):
        fm = printer.lookup_object('filament_manager', None)
        if fm:
            return fm.get_info(material, xpath)

    # The following functions operate on the cache in the main process
    # The local cache is updated immediately as well instead of waiting for the
    # event, which gets handled later as well.
    def delete_cache_entry(self, path):
        self.reactor.cb(self._delete_cache_entry, path)
        self.flush_local_cache(path)
    @staticmethod
    def _delete_cache_entry(e, printer, path):
        gcode_metadata = printer.lookup_object('gcode_metadata')
        gcode_metadata.delete_cache_entry(path)

    def flush_cache(self):
        self.reactor.cb(self._flush_cache)
        self.flush_local_cache()
    @staticmethod
    def _flush_cache(e, printer):
        gcode_metadata = printer.lookup_object('gcode_metadata')
        gcode_metadata.flush_cache()


def load_config(config):
    if config.reactor.process_name == "printer":
        module = GCodeMetadata(config)
    else:
        module = MPMetadata(config)
    return module
