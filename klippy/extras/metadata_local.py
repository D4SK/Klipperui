
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
        md = self.reactor.cb(self._obtain_md, path, wait=True, process="gcode_metadata")
        self._md_cache[path] = md
        return md
    @staticmethod
    def _obtain_md(e, gc_md, path):
        return gc_md.get_metadata(path)

    def get_material_info(self, material, xpath):
        self.reactor.cb(self._obtain_material_info, material, xpath, wait=True)
    @staticmethod
    def _obtain_material_info(e, printer, material, xpath):
        fm = printer.lookup_object('filament_manager', None)
        if fm:
            return fm.get_info(material, xpath)

    # The following functions operate on the cache in the metadata process.
    # The local cache is updated immediately as well instead of waiting for the
    # event, which gets handled later as well.
    def delete_cache_entry(self, path):
        self.reactor.cb(self._delete_cache_entry, path, process="gcode_metadata")
        self.flush_local_cache(path)
    @staticmethod
    def _delete_cache_entry(e, gc_md, path):
        gc_md.delete_cache_entry(path)

    def flush_cache(self):
        self.reactor.cb(self._flush_cache, process="gcode_metadata")
        self.flush_local_cache()
    @staticmethod
    def _flush_cache(e, gc_md):
        gc_md.flush_cache()


def load_config(config):
    module = MPMetadata(config)
    return module
