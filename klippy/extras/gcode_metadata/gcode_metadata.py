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

import hashlib
import logging
import os
import pickle

import location

from .ufp_reader import create_ufp_reader

from .base_parser import BaseParser
from .cura_marlin_parser import CuraMarlinParser
from .prusaslicer_parser import PrusaSlicerParser


class MetadataBase:

    def get_cached(self, path):
        #TODO: Compare mtimes
        cfile = self._cache_file(path)
        if os.path.isfile(cfile):
            try:
                fp = open(cfile, 'rb')
                md = pickle.load(fp)
                return md
            except:
                logging.exception("Error while reading metadata cache at %s", cfile)
                # Delete the file as it is probably an invalid cache
                try:
                    os.remove(path)
                except:
                    pass
                return None
            finally:
                fp.close()

    def write_cache(self, md, path):
        cfile = self._cache_file(path)
        try:
            fp = open(cfile, 'wb')
            pickle.dump(md, fp)
        except:
            logging.exception("Could not write metadata cache")
        finally:
            fp.close()

    def delete_cache_entry(self, path):
        cfile = self._cache_file(path)
        try:
            os.remove(cfile)
            logging.debug("Deleted metadata cache for %s at %s", path, cfile)
            return True
        except FileNotFoundError:
            logging.info("Trying to delete non-existing cache file %s", cfile)
        except OSError:
            logging.exception("Could not delete cache file at %s", cfile)
        return False

    def _cache_file(self, path, ext='pickle'):
        key = self._cache_key(path)
        return os.path.join(location.metadata_cache(), key + '.' + ext)

    def _cache_key(self, path):
        """Use the hashed filepath as a cache key"""
        path = os.path.abspath(path)
        hasher = hashlib.sha1()
        hasher.update(path.encode())
        return hasher.hexdigest()


class GCodeMetadata(MetadataBase):

    _parsers = [CuraMarlinParser,
                PrusaSlicerParser,
    ]

    def __init__(self, config):
        self.filament_manager = None
        self.config = config
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

    def get_metadata(self, path):
        """
        This is the main method of the module that returns a metadata
        object for the given gcode path. UFP files are also accepted.
        """
        cached = self.get_cached(path)
        if cached is not None:
            return cached

        ext = os.path.splitext(path)[1]
        if ext in {".gco", ".gcode"}:
            metadata = self._parse_gcode(path)
        elif ext == ".ufp":
            metadata = create_ufp_reader(path, self)
        else:
            raise ValueError(f"File must be either gcode or ufp file, not {ext}")
        self.write_cache(metadata, path)
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


class MPMetadata(MetadataBase):
    """Module class used in unpickled metadata objects that calls
    filament_manager.get_info in printer process.
    """

    def __init__(self, config):
        self.reactor = config.reactor

    def get_metadata(self, path):
        cached = self.get_cached(path)
        if cached is not None:
            return cached
        # Remote process takes care of writing the cache
        md = self.reactor.cb(self._obtain_md, path, wait=True)
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


def load_config(config):
    if config.reactor.process_name == "printer":
        module = GCodeMetadata(config)
    else:
        module = MPMetadata(config)
    return module
