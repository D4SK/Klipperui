"""
IMPORTANT NOTES FOR MULTIPROCESSING:

Using this module in an extra process works mostly like in the main printer
process. Obtaining the module is done through printer.load_object(). The module
returned when doing that outside of the main process is slightly different.
A filesystem cache is always used first. On cache misses the metadata is
created in the main process but can then be used locally.

Metadata objects are picklable and all their methods are called locally
except for calls to filament_manager, which are handled in the main process.
"""

import hashlib
import itertools
import logging
import os
import pickle
import threading
import time

import location

from .ufp_reader import create_ufp_reader

from .base_parser import BaseParser
from .cura_marlin_parser import CuraMarlinParser
from .prusaslicer_parser import PrusaSlicerParser


class MetadataBase:

    def get_cached(self, path):
        cfile = self._cache_file(path)
        if os.path.isfile(cfile):
            try:
                if os.path.getmtime(path) > os.path.getmtime(cfile):
                    # Ignore if file is newer than metadata cache
                    return None
                with open(cfile, 'rb') as fp:
                    md = pickle.load(fp)
                cur_version = type(md)._VERSION + type(md)._SUBCLASS_VERSION
                if md.__version__ >= cur_version:
                    return md
                else:
                    logging.info("Ignoring cache %s with outdated version %d",
                                 cfile, md.__version__)
            except:
                logging.exception("Error while reading metadata cache at %s", cfile)

    def write_cache(self, md, path):
        cfile = self._cache_file(path)
        try:
            fp = open(cfile, 'wb')
            md.__version__ = md._VERSION + md._SUBCLASS_VERSION
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

    def _cache_file(self, path):
        key = self._cache_key(path)
        return os.path.join(location.metadata_cache(), key + '.pickle')

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
        self.max_cache_age = self.config.getfloat("max_cache_age", 180)  # Days
        self.max_cache_size = self.config.getfloat("max_cache_size", 128) # MiB
        extruder_config = self.config.getsection("extruder")
        self.config_diameter = extruder_config.getfloat(
                "filament_diameter", None)
        # Timestamp of last time the prune_cache was called
        self.last_cache_check = 0

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
        self.prune_cache()
        return metadata

    def prune_cache(self):
        # Prune at at most once an hour
        now = time.time()
        if now - self.last_cache_check < 60 * 60:
            return
        self.last_cache_check = now
        thread = threading.Thread(target=self._prune_cache_thread,
                                  name="Prune-Cache-Thread")
        thread.start()

    def _prune_cache_thread(self):
        os.nice(30)
        logging.info("Pruning cache files...")
        max_age = 60 * 60 * 24 * self.max_cache_age
        max_size = 1024 * 1024 * self.max_cache_size
        with os.scandir(location.metadata_cache()) as dir_md, \
             os.scandir(location.thumbnails()) as dir_tn:
            files = [dirent for dirent in itertools.chain(dir_md, dir_tn)
                     if dirent.is_file(follow_symlinks=False)]
        # Sort by newest first
        files.sort(key=lambda e: e.stat().st_mtime, reverse=True)
        size = 0
        min_time = time.time() - max_age
        delete_from = len(files)
        for i, dirent in enumerate(files):
            stat = dirent.stat()
            size += stat.st_size
            if size > max_size or stat.st_mtime < min_time:
                delete_from = i
                break
        for dirent in files[delete_from:]:
            path = dirent.path
            try:
                os.remove(path)
                logging.debug("Pruned cache file %s", path)
            except OSError:
                logging.exception("Could not delete cache file %s", path)

    def _parse_gcode(self, path):
        """
        Parse the Metadata for the G-Code and return an object describing
        the file.
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
    def _obtain_md(printer, path):
        gcode_metadata = printer.lookup_object('gcode_metadata')
        return gcode_metadata.get_metadata(path)

    def get_material_info(self, material, xpath):
        self.reactor.cb(self._obtain_material_info, material, xpath, wait=True)

    @staticmethod
    def _obtain_material_info(printer, material, xpath):
        fm = printer.lookup_object('filament_manager', None)
        if fm:
            return fm.get_info(material, xpath)


def load_config(config):
    if config.reactor.process_name == "printer":
        module = GCodeMetadata(config)
    else:
        module = MPMetadata(config)
    return module
