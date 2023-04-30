#!/usr/bin/env python3
# Main code for host side printer firmware
#
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import sys, os, gc, optparse, logging, time, collections, importlib
import util, reactor, queuelogger, msgproto
import gcode, configfile, pins, mcu, toolhead, webhooks
import signal, traceback, multiprocessing, datetime
from os.path import join, exists, dirname

import location

message_ready = "Printer is ready"

message_startup = """
Printer is not ready
The klippy host software is attempting to connect.  Please
retry in a few moments.
"""

message_restart = """
Once the underlying issue is corrected, use the "RESTART"
command to reload the config and restart the host software.
Printer is halted
"""

message_protocol_error1 = """
This is frequently caused by running an older version of the
firmware on the MCU(s). Fix by recompiling and flashing the
firmware.
"""

message_protocol_error2 = """
Once the underlying issue is corrected, use the "RESTART"
command to reload the config and restart the host software.
"""

message_mcu_connect_error = """
Once the underlying issue is corrected, use the
"FIRMWARE_RESTART" command to reset the firmware, reload the
config, and restart the host software.
Error configuring printer
"""

message_shutdown = """
Once the underlying issue is corrected, use the
"FIRMWARE_RESTART" command to reset the firmware, reload the
config, and restart the host software.
Printer is shutdown
"""

class Printer:
    config_error = configfile.error
    command_error = gcode.CommandError

    def __init__(self, main_reactor, bglogger, start_args):
        signal.signal(signal.SIGTERM, lambda signum, frame: self.request_exit('exit'))
        self.bglogger = bglogger
        self.start_args = start_args
        self.reactor = main_reactor
        self.reactor.register_callback(self._connect)
        self.state_message = message_startup
        self.in_shutdown_state = False
        self.run_result = None
        self.objects = collections.OrderedDict()
        self.parallel_objects = {}
        self.parallel_queues = {}
        # Init printer components that must be setup prior to config
        for m in [gcode, webhooks]:
            m.add_early_printer_objects(self)
    def get_start_args(self):
        return self.start_args
    def get_reactor(self):
        return self.reactor
    def get_state_message(self):
        if self.state_message == message_ready:
            category = "ready"
        elif self.state_message == message_startup:
            category = "startup"
        elif self.in_shutdown_state:
            category = "shutdown"
        else:
            category = "error"
        return self.state_message, category
    def is_shutdown(self):
        return self.in_shutdown_state
    def _set_state(self, msg):
        if self.state_message in (message_ready, message_startup):
            self.state_message = msg
        if (msg != message_ready
            and self.start_args.get('debuginput') is not None):
            self.request_exit('error_exit')
    def add_object(self, name, obj):
        if name in self.objects:
            raise self.config_error(
                "Printer object '%s' already created" % (name,))
        self.objects[name] = obj
    def lookup_object(self, name, default=configfile.sentinel):
        if name in self.objects:
            return self.objects[name]
        if default is configfile.sentinel:
            raise self.config_error("Unknown config object '%s'" % (name,))
        return default
    def lookup_objects(self, module=None):
        if module is None:
            return list(self.objects.items())
        prefix = module + ' '
        objs = [(n, self.objects[n])
                for n in self.objects if n.startswith(prefix)]
        if module in self.objects:
            return [(module, self.objects[module])] + objs
        return objs
    def load_object(self, config, section, default=configfile.sentinel):
        if section in self.objects:
            return self.objects[section]
        module_parts = section.split()
        module_name = module_parts[0]
        init_func = 'load_config_prefix' if len(module_parts) > 1 else 'load_config'
        module           = join(dirname(__file__), 'extras', module_name + '.py')
        package          = join(dirname(__file__), 'extras', module_name, '__init__.py')
        parallel_module  = join(dirname(__file__), 'parallel_extras', module_name + '.py')
        parallel_package = join(dirname(__file__), 'parallel_extras', module_name, '__init__.py')
        if exists(module) or exists(package):
            mod = importlib.import_module('extras.' + module_name)
            init_func = getattr(mod, init_func, None)
            if init_func is None:
                if default is not configfile.sentinel:
                    return default
                raise self.config_error("Unable to load module '%s'" % (section,))
            self.objects[section] = init_func(config.getsection(section))
            return self.objects[section]
        elif exists(parallel_module) or exists(parallel_package):
            self.parallel_queues[section] = multiprocessing.Queue()
            self.parallel_objects[section] = ExtraProcess(
                section,
                config.getsection(section),
                init_func,
                self.reactor)
            return
        else:
            if default is not configfile.sentinel:
                return default
            raise self.config_error("Unable to load module '%s'" % (section,))
    def _read_config(self):
        self.objects['configfile'] = pconfig = configfile.PrinterConfig(self)
        config = pconfig.read_main_config()
        if self.bglogger is not None:
            pconfig.log_config(config)
        # Create printer components
        for m in [pins, mcu]:
            m.add_printer_objects(config)
        for section_config in config.get_prefix_sections(''):
            self.load_object(config, section_config.get_name(), None)
        self.parallel_queues['printer'] = multiprocessing.Queue()
        self.reactor.setup_mp_queues(self.parallel_queues)
        for proc in self.parallel_objects.values():
            proc.start(self.parallel_queues)

        # Wait for config access_tracking to be reported back
        for proc in self.parallel_objects.values():
            access_tracking = proc.completion.wait(waketime=self.reactor.monotonic() + 20)
            if access_tracking is None:
                raise TimeoutError(
                    f"{proc.name} did not return access tracking within 20 seconds!")
            else:
                config.access_tracking.update(access_tracking)

        for m in [toolhead]:
            m.add_printer_objects(config)
        # Validate that there are no undefined parameters in the config file
        pconfig.check_unused_options(config)
    def _build_protocol_error_message(self, e):
        host_version = self.start_args['software_version']
        msg_update = []
        msg_updated = []
        for mcu_name, mcu in self.lookup_objects('mcu'):
            try:
                mcu_version = mcu.get_status()['mcu_version']
            except:
                logging.exception("Unable to retrieve mcu_version from mcu")
                continue
            if mcu_version != host_version:
                msg_update.append("%s: Current version %s"
                                  % (mcu_name.split()[-1], mcu_version))
            else:
                msg_updated.append("%s: Current version %s"
                                   % (mcu_name.split()[-1], mcu_version))
        if not msg_update:
            msg_update.append("<none>")
        if not msg_updated:
            msg_updated.append("<none>")
        msg = ["MCU Protocol error",
               message_protocol_error1,
               "Your Klipper version is: %s" % (host_version,),
               "MCU(s) which should be updated:"]
        msg += msg_update + ["Up-to-date MCU(s):"] + msg_updated
        msg += [message_protocol_error2, str(e)]
        return "\n".join(msg)
    def _connect(self, eventtime):
        try:
            self._read_config()
            self.send_event("klippy:mcu_identify")
            self.reactor.send_event_wait("klippy:connect", check_status=message_startup)
        except (self.config_error, pins.error) as e:
            logging.exception("Config error")
            self.send_event("klippy:critical_error", "Config error")
            self._set_state("%s\n%s" % (str(e), message_restart))
            return
        except msgproto.error as e:
            logging.exception("Protocol error")
            self._set_state(self._build_protocol_error_message(e))
            util.dump_mcu_build()
            return
        except mcu.error as e:
            logging.exception("MCU error during connect")
            self.send_event("klippy:critical_error", "MCU error during connect")
            self._set_state("%s%s" % (str(e), message_mcu_connect_error))
            util.dump_mcu_build()
            return
        except Exception as e:
            logging.exception("Unhandled exception during connect")
            self.send_event("klippy:critical_error", "Unhandled exception during connect")
            self._set_state("Internal error during connect: %s\n%s" % (
                str(e), message_restart,))
            return
        try:
            self._set_state(message_ready)
            self.reactor.send_event_wait("klippy:ready", check_status=message_ready)
        except Exception as e:
            logging.exception("Unhandled exception during ready callback")
            self.invoke_shutdown("Internal error during ready callback: %s"
                                 % (str(e),))
    def run(self):
        systime = time.time()
        monotime = self.reactor.monotonic()
        logging.info("Start printer at %s (%.1f %.1f)",
                     time.asctime(time.localtime(systime)), systime, monotime)
        # Enter main reactor loop
        try:
            self.reactor.run()
        except Exception as e:
            msg = ''.join(traceback.format_tb(e.__traceback__)) + "\n\n" + repr(e)
            logging.exception(msg)
            # Exception from a reactor callback - try to shutdown
            try:
                self.reactor.register_callback((lambda e:
                                                self.invoke_shutdown(msg)))
                self.reactor.run()
            except:
                logging.exception("Repeat unhandled exception during run")
                # Another exception - try to exit
                self.run_result = "error_exit"
        # Check restart flags
        run_result = self.run_result
        try:
            if run_result == 'firmware_restart':
                self.send_event("klippy:firmware_restart")
            self.send_event("klippy:disconnect")
        except:
            logging.exception("Unhandled exception during post run")
        return run_result
    def set_rollover_info(self, name, info, log=True):
        if log:
            logging.info(info)
        if self.bglogger is not None:
            self.bglogger.set_rollover_info(name, info)
    def invoke_shutdown(self, msg):
        if self.in_shutdown_state:
            return
        logging.error("Transition to shutdown state: %s", msg)
        self.in_shutdown_state = True
        self._set_state("%s%s" % (msg, message_shutdown))
        self.send_event("klippy:critical_error", msg)
        for cb in self.reactor.event_handlers.get("klippy:shutdown", []):
            try:
                cb()
            except:
                logging.exception("Exception during shutdown handler")
        logging.info("Reactor garbage collection: %s",
                     self.reactor.get_gc_stats())
    def invoke_async_shutdown(self, msg):
        self.reactor.register_async_callback(
            (lambda e: self.invoke_shutdown(msg)))
    def register_event_handler(self, event, callback):
        self.reactor.register_event_handler(event, callback)
    def send_event(self, event, *params):
        return self.reactor.send_event(event, *params)
    def request_exit(self, result):
        if self.run_result is None:
            self.run_result = result
        self.reactor.end()


class ExtraProcess:

    def __init__(self, name, config, init_func, printer_reactor):
        self.name = name
        self.config = config
        self.init_func = init_func
        self.completion = printer_reactor.completion()
        self.proc = None

    def start(self, mp_queues):
        self.proc = multiprocessing.Process(
            target=self._target,
            args=(mp_queues,),
            name=self.name)
        self.proc.start()

    def ensure_stop(self):
        if self.proc is not None:
            logging.info("Joining %s", self.name)
            self.proc.join(10)
            if self.proc.exitcode is None:
                logging.error("Process termination timed out. Killing %s",
                    self.name)
                self.proc.kill()
            self.proc = None

    # Entry point for the new process
    def _target(self, mp_queues):
        self._setup()
        reactor = self.config.reactor
        # Avoid active imports changing environment - import in target process
        mod = importlib.import_module('parallel_extras.' + self.name.split()[0])
        init_func = getattr(mod, self.init_func, None)
        def start(e):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            logging.info(f"\nRestart {datetime.datetime.now()}\n")
            reactor.setup_mp_queues(mp_queues)
            reactor.root = init_func(self.config)
            reactor.cb(ExtraProcess._report_access_tracking,
                    self.config.section, self.config.access_tracking)
        try:
            reactor.register_callback(start)
            reactor.run()
        except Exception:
            logging.exception("Unhandled exception during run")
            # Stops the queuelogger thread
            reactor.end()
        finally:
            reactor.finalize()

    def _setup(self):
        config = self.config
        os.nice(config.getint("nice", 10))
        configfile.main_config = config
        # Delete all loaded klipper modules
        config.printer.objects.clear()
        # Reset logging module so that all processes can use the root logger
        logging.shutdown()
        importlib.reload(logging)
        config.reactor = reactor.Reactor(process=self.name, gc_checking=True)
        config.printer.reactor = config.reactor
        config.reactor.logger = queuelogger.setup_bg_logging(
                join(location.log_dir(), self.name.split()[-1] + ".log"),
                debuglevel)

    @staticmethod
    def _report_access_tracking(printer, section, access_tracking):
        logging.debug("Receiving config access tracking from %s", section)
        printer.parallel_objects[section].completion.complete(access_tracking)


def get_main_config():
    return configfile.main_config

######################################################################
# Startup
######################################################################

def import_test():
    # Import all optional modules (used as a build test)
    dname = os.path.dirname(__file__)
    for mname in ['extras', 'kinematics']:
        for fname in os.listdir(os.path.join(dname, mname)):
            if fname.endswith('.py') and fname != '__init__.py':
                module_name = fname[:-3]
            else:
                iname = os.path.join(dname, mname, fname, '__init__.py')
                if not os.path.exists(iname):
                    continue
                module_name = fname
            importlib.import_module(mname + '.' + module_name)
    sys.exit(0)

def arg_dictionary(option, opt_str, value, parser):
    key, fname = "dictionary", value
    if '=' in value:
        mcu_name, fname = value.split('=', 1)
        key = "dictionary_" + mcu_name
    if parser.values.dictionary is None:
        parser.values.dictionary = {}
    parser.values.dictionary[key] = fname

def main():
    usage = "%prog [options] <config file>"
    opts = optparse.OptionParser(usage)
    opts.add_option("-i", "--debuginput", dest="debuginput",
                    help="read commands from file instead of from tty port")
    opts.add_option("-I", "--input-tty", dest="inputtty",
                    default='/tmp/printer',
                    help="input tty name (default is /tmp/printer)")
    opts.add_option("-a", "--api-server", dest="apiserver",
                    help="api server unix domain socket filename")
    opts.add_option("-l", "--logfile", dest="logfile",
                    help="write log to file instead of stderr")
    opts.add_option("-v", action="store_true", dest="verbose",
                    help="enable debug messages")
    opts.add_option("-o", "--debugoutput", dest="debugoutput",
                    help="write output to file instead of to serial port")
    opts.add_option("-d", "--dictionary", dest="dictionary", type="string",
                    action="callback", callback=arg_dictionary,
                    help="file to read for mcu protocol dictionary")
    opts.add_option("--import-test", action="store_true",
                    help="perform an import module test")
    options, args = opts.parse_args()
    if options.import_test:
        import_test()
    start_args = {'apiserver': options.apiserver, 'start_reason': 'startup'}
    if len(args) == 1:
        start_args['config_file'] = args[0]
    elif len(args) > 1:
        opts.error("Incorrect number of arguments")

    global debuglevel
    debuglevel = logging.INFO
    if options.verbose:
        debuglevel = logging.DEBUG
    if options.debuginput:
        start_args['debuginput'] = options.debuginput
        debuginput = open(options.debuginput, 'rb')
        start_args['gcode_fd'] = debuginput.fileno()
    else:
        start_args['gcode_fd'] = util.create_pty(options.inputtty)
    if options.debugoutput:
        start_args['debugoutput'] = options.debugoutput
        start_args.update(options.dictionary)
    bglogger = None
    if options.logfile or not sys.stdout.isatty():
        logfile = options.logfile or join(location.log_dir(), 'klippy.log')
        start_args['log_file'] = logfile
        bglogger = queuelogger.setup_bg_logging(logfile, debuglevel)
    else:
        logging.getLogger().setLevel(debuglevel)
    logging.info(f"\nRestart {datetime.datetime.now()}\n")
    start_args['software_version'] = util.get_git_version()
    start_args['cpu_info'] = util.get_cpu_info()
    if bglogger is not None:
        versions = "\n".join([
            "Args: %s" % (sys.argv,),
            "Git version: %s" % (repr(start_args['software_version']),),
            "CPU: %s" % (start_args['cpu_info'],),
            "Python: %s" % (repr(sys.version),)])
        logging.info(versions)
    elif not options.debugoutput:
        logging.warning("No log file specified!"
                        " Severe timing issues may result!")
    gc.disable()

    # Start Printer() class
    while 1:
        if bglogger is not None:
            bglogger.clear_rollover_info()
            bglogger.set_rollover_info('versions', versions)
        gc.collect()
        main_reactor = reactor.Reactor(gc_checking=True)
        printer = Printer(main_reactor, bglogger, start_args)
        main_reactor.root = printer
        res = printer.run()
        time.sleep(1)
        for process in printer.parallel_objects.values():
            process.ensure_stop()
        logging.info("Joined all processes")
        main_reactor.finalize()
        if res in ('exit', 'error_exit'):
            break
        main_reactor = printer = None
        logging.info("Restarting printer")
        start_args['start_reason'] = res

    if bglogger is not None:
        bglogger.stop()

    if res == 'error_exit':
        sys.exit(-1)

if __name__ == '__main__':
    main()
