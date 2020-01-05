# Virtual sdcard support (print files directly from a host g-code file)
#
# Copyright (C) 2018  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, logging, re

class VirtualSD:
    def __init__(self, config):
        printer = config.get_printer()
        printer.register_event_handler("klippy:shutdown", self.handle_shutdown)
        printer.register_event_handler("gcode:read_metadata", self.handle_gcode_metadata)
        self.printjob = None
        # sdcard state
        sd = config.get('path')
        self.sdcard_dirname = os.path.normpath(os.path.expanduser(sd))
        # Work timer
        self.reactor = printer.get_reactor()
        self.must_pause_work = False
        self.cmd_from_sd = False
        self.work_timer = None
        # Register commands
        self.gcode = printer.lookup_object('gcode')
        self.gcode.register_command('M21', None)
        for cmd in ['M20', 'M21', 'M23', 'M24', 'M25', 'M26', 'M27']:
            self.gcode.register_command(cmd, getattr(self, 'cmd_' + cmd))
        for cmd in ['M28', 'M29', 'M30']:
            self.gcode.register_command(cmd, self.cmd_error)
    def handle_shutdown(self):
        if self.work_timer and self.printjob:
            self.must_pause_work = True
            try:
                readpos = max(self.printjob.file_position - 1024, 0)
                readcount = self.printjob.file_position - readpos
                self.printjob.file.seek(readpos)
                data = self.printjob.file.read(readcount + 128)
            except:
                logging.exception("virtual_sdcard shutdown read")
                return
            logging.info("Virtual sdcard (%d): %s\nUpcoming (%d): %s",
                         readpos, repr(data[:readcount]),
                         self.printjob.file_position, repr(data[readcount:]))
    def stats(self, eventtime):
        if self.work_timer and self.printjob:
            return True, "sd_pos=%d" % (self.printjob.file_position,)
        return False, ""
    def get_file_list(self):
        dname = self.sdcard_dirname
        try:
            filenames = os.listdir(self.sdcard_dirname)
            return [(fname, os.path.getsize(os.path.join(dname, fname)))
                    for fname in sorted(filenames, key=str.lower)
                    if not fname.startswith('.')
                    and os.path.isfile((os.path.join(dname, fname)))]
        except:
            logging.exception("virtual_sdcard get_file_list")
            raise self.gcode.error("Unable to get file list")
    def handle_gcode_metadata(self, eventtime, params):
        if self.printjob:
            line = params['#original']
            # recieves all gcode-comment-lines as they are printed, and searches for print-time estimations
            print_time_estimate = [
                r'\s\s\d*\.\d*\sminutes' , 						#	Kisslicer
                r'; estimated printing time' ,					#	Slic3r
                r';\s+Build time:.*' ,							#	S3d
                r'\d+h?\s?\d+m\s\d+s' ,							#	Slic3r PE
                r';TIME:\d+' ,									#	Cura
                r';Print Time:\s\d+\.?\d+',						#	ideamaker
                r'\d+h?\s?\d+m\s\d+s'							#	PrusaSlicer
                ]
            print_time_elapsed = [
                #r'\s\s\d*\.\d*\sminutes' , 					#	Kisslicer
                #                               				#	Slic3r
                #r';\s+Build time:.*' ,							#	S3d
                #r'\d+h?\s?\d+m\s\d+s' ,						#	Slic3r PE
                r';TIME_ELAPSED:\d+' ,							#	Cura
                #r';Print Time:\s\d+\.?\d+',					#	ideamaker
                #                                    			#	PrusaSlicer
                ]
            def get_seconds(in_):
                if in_ == -1: return in_
                h_str = re.search(re.compile('(\d+(\s)?hours|\d+(\s)?h)'),in_)
                m_str = re.search(re.compile('(([0-9]*\.[0-9]+)\sminutes|\d+(\s)?m)'),in_)
                s_str = re.search(re.compile('(\d+(\s)?seconds|\d+(\s)?s)'),in_)
                dursecs = 0
                if h_str:
                    dursecs += float( max( re.findall('([0-9]*\.?[0-9]+)' , ''.join(h_str.group()) ) ) ) *3600 
                if m_str:
                    dursecs += float( max( re.findall('([0-9]*\.?[0-9]+)' , ''.join(m_str.group()) ) ) ) *60 
                if s_str:
                    dursecs += float( max( re.findall('([0-9]*\.?[0-9]+)' , ''.join(s_str.group()) ) ) )
                if dursecs == 0:
                    dursecs = float( max( re.findall('([0-9]*\.?[0-9]+)' , in_) ) )
                return dursecs
            for regex in print_time_estimate:
                match = re.search(regex, line)
                if match:
                    self.printjob.slicer_estimated_time = get_seconds(match.group())
                    return
            for regex in print_time_elapsed:
                match = re.search(regex, line)
                if match:
                    self.printjob.slicer_elapsed_times.append((self.printjob.get_printed_time(self.reactor.monotonic()), get_seconds(match.group())))
                    return
    def get_status(self, eventtime):
        state = 'no printjob'
        progress = est_remaining = None
        if self.printjob:
            progress, est_remaining = self.printjob.estimate_remaining_time()
            state = self.printjob.state
        return {'progress': progress, 'estimated_remaining_time': est_remaining, 'state': state }
    def is_active(self):
        return self.work_timer is not None
    def do_pause(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            while self.work_timer is not None and not self.cmd_from_sd:
                self.reactor.pause(self.reactor.monotonic() + .001)
        # G-Code commands
    def cmd_error(self, params):
        raise self.gcode.error("SD write not supported")
    def cmd_M20(self, params):
        # List SD card
        files = self.get_file_list()
        self.gcode.respond("Begin file list")
        for fname, fsize in files:
            self.gcode.respond("%s %d" % (fname, fsize))
        self.gcode.respond("End file list")
    def cmd_M21(self, params):
        # Initialize SD card
        self.gcode.respond("SD card ok")
    def cmd_M23(self, params):
        # Select SD file
        if self.work_timer is not None:
            raise self.gcode.error("SD busy")
        if self.printjob:
            self.printjob.done()
        # parse filename
        try:
            orig = params['#original']
            filename = orig[orig.find("M23")+4 : max(orig.find(".gco")+4, orig.find(".gcode")+6)].strip()
            if '*' in filename:
                filename = filename[:filename.find('*')].strip()
        except:
            raise self.gcode.error("Unable to extract filename")
        if filename.startswith('/'):
            filename = filename[1:]
        filename = os.path.join(self.sdcard_dirname, filename)
        # create new printjob
        self.printjob = PrintJob(filename, self.reactor, self.gcode)
        self.gcode.respond("File opened:%s Size:%d" % (filename, self.printjob.file_size))
        self.gcode.respond("File selected")
    def cmd_M24(self, params):
        # Start/resume SD print
        if self.work_timer is not None:
            raise self.gcode.error("SD busy")
        self.must_pause_work = False
        self.work_timer = self.reactor.register_timer(
            self.work_handler, self.reactor.NOW)
        if self.printjob:
            self.printjob.start()
    def cmd_M25(self, params):
        # Pause SD print
        if self.printjob:
            self.do_pause()
            self.printjob.pause()
    def cmd_M26(self, params):
        # Set SD position
        if self.work_timer is not None:
            raise self.gcode.error("SD busy")
        pos = self.gcode.get_int('S', params, minval=0)
        self.printjob.file_position = pos
    def cmd_M27(self, params):
        # Report SD print status
        if self.printjob:
            self.gcode.respond("SD printing byte %d/%d" % (self.printjob.file_position, self.printjob.file_size))
        else:
            self.gcode.respond("Not SD printing.")
    # Background work timer
    def work_handler(self, eventtime):
        logging.info("Starting SD card print (position %d)", self.printjob.file_position)
        self.reactor.unregister_timer(self.work_timer)
        try:
            self.printjob.file.seek(self.printjob.file_position)
        except:
            logging.exception("virtual_sdcard seek")
            self.gcode.respond_error("Unable to seek file")
            self.work_timer = None
            return self.reactor.NEVER
        gcode_mutex = self.gcode.get_mutex()
        partial_input = ""
        lines = []
        while not self.must_pause_work:
            if not lines:
                # Read more data
                try:
                    data = self.printjob.file.read(8192)
                except:
                    logging.exception("virtual_sdcard read")
                    self.gcode.respond_error("Error on virtual sdcard read")
                    break
                if not data:
                    # End of file
                    self.printjob.done()
                    logging.info("Finished SD card print")
                    self.gcode.respond("Done printing file")
                    break
                lines = data.split('\n')
                lines[0] = partial_input + lines[0]
                partial_input = lines.pop()
                lines.reverse()
                self.reactor.pause(self.reactor.NOW)
                continue
            # Pause if any other request is pending in the gcode class
            if gcode_mutex.test():
                self.reactor.pause(self.reactor.monotonic() + 0.100)
                continue
            # Dispatch command
            self.cmd_from_sd = True
            try:
                self.gcode.run_script(lines[-1])
            except self.gcode.error as e:
                break
            except:
                logging.exception("virtual_sdcard dispatch")
                break
            self.cmd_from_sd = False
            self.printjob.file_position += len(lines.pop()) + 1
        logging.info("Exiting SD card print (position %d)", self.printjob.file_position)
        self.work_timer = None
        self.cmd_from_sd = False
        return self.reactor.NEVER

class PrintJob: # A class that provides a file with file_position that can be printed
    def __init__(self, filename, reactor, gcode):
        self.gcode = gcode
        self.reactor = reactor
        try:
            f = open(filename, 'rb')
            f.seek(0, os.SEEK_END)
            self.file_size = f.tell()
            f.seek(0)
        except:
            logging.exception("virtual_sdcard file open")
            raise self.gcode.error("Unable to open file")
        self.state = 'initialized'
        self.file = f
        self.file_position = 0
        # print time estimation
        self.start_stop_times = [] # [[start1, pause1], [resume, pause2]....]
        self.slicer_elapsed_times = [] # [[time actually printed, ELAPSED_TIME shown by slicer], ...]
        self.slicer_estimated_time = None
    def start(self):
        if self.state == 'done':
            self.__init__(self.filename, self.reactor, self.gcode)
        if self.state == 'paused':
            self.gcode.run_script_from_command("RESTORE_GCODE_STATE STATE=PAUSE_STATE MOVE=1")
        self.state = 'printing'
        self.start_stop_times.append([self.reactor.monotonic(), None])
    def pause(self):
        self.state = 'paused'
        self.gcode.run_script_from_command("SAVE_GCODE_STATE STATE=PAUSE_STATE")
        self.start_stop_times[-1][1] = self.reactor.monotonic()
    def done(self):
        self.state = 'done'
        self.file.close()
        self.start_stop_times[-1][1] = self.reactor.monotonic()
    def estimate_remaining_time(self):
        # we have at least one silcer estimate
        if self.state == 'done':
            return 1, 0
        if self.slicer_estimated_time:
            printed_time = self.get_printed_time(self.reactor.monotonic())
            if len(self.slicer_elapsed_times):
                time_since_slicer_est = printed_time - self.slicer_elapsed_times[-1][0]
                est_remaining  = max(self.slicer_estimated_time - self.slicer_elapsed_times[-1][1] - time_since_slicer_est, 0)
                # now apply factor based on how wrong previous estimations were
                # we ignore the first estimation block since it contains heatup where high variance is expected
                if len(self.slicer_elapsed_times) > 1:
                    est_remaining  *= (self.slicer_elapsed_times[-1][1] - self.slicer_elapsed_times[0][1])\
                                     /(self.slicer_elapsed_times[-1][0] - self.slicer_elapsed_times[0][0])
            else: #we dont have elapsed times
                est_remaining = max(self.slicer_estimated_time - printed_time, 0)
            # time estimation done, get progress
            if printed_time <= 0:#avoid zero division
                progress = 0
            else:
                progress = printed_time/float(printed_time + est_remaining)
            return progress, est_remaining
        return None, None
    def get_printed_time(self, currenttime):
        printed_time = 0
        for time in self.start_stop_times:
            printed_time += - time[0] + (time[1] if time[1] else currenttime)
        return printed_time

def load_config(config):
    return VirtualSD(config)

