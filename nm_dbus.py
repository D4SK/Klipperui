# coding: utf-8

"""
Needs python-gobject or python-gi
and python-pydbus

TODO:
    account for wifi access points with no security
"""

from gi.repository import GLib
from kivy.event import EventDispatcher
from kivy.properties import OptionProperty, BooleanProperty, StringProperty
from pydbus import SystemBus

from threading import Thread


_NM = "org.freedesktop.NetworkManager"

class NetworkManager(EventDispatcher, Thread):

    available = BooleanProperty(True)
    connected_ssid = StringProperty()
    connection_type = OptionProperty("none", options=["none", "ethernet", "wireless"])

    def __init__(self, **kwargs):
        super(NetworkManager, self).__init__(**kwargs)

        self.loop = GLib.MainLoop()
        self.bus = SystemBus()

        # Get proxy objects
        try:
            self.nm = self.bus.get(_NM, "/org/freedesktop/NetworkManager")
        except GLib.GError as e:
            # Occurs when NetworkManager was not installed
            if "org.freedesktop.DBus.Error.ServiceUnknown" in e.message:
                self.available = False
                return
            else:
                raise

        self.settings = self.bus.get(_NM, "/org/freedesktop/NetworkManager/Settings")
        devices = self.nm.Devices # type: ao
        self.eth_dev = self.wifi_dev = None
        for dev in devices:
            dev_obj = self.bus.get(_NM, dev)
            if dev_obj.Capabilities & 0x1: # NetworkManager supports this device
                if dev_obj.DeviceType == 1: # a wired ethernet device
                    self.eth_dev = dev_obj
                elif dev_obj.DeviceType == 2: # an 802.11 Wi-Fi device
                    self.wifi_dev = dev_obj
        # For simplicity require both devices to be available
        if not(self.eth_dev and self.wifi_dev):
            self.available = False
            return
        # Does the wifi device support 5gGHz (flag 0x400)
        self.freq_5ghz = bool(self.wifi_dev.WirelessCapabilities & 0x400)

        # ID used to cancel the scan timer and to find out whether it is running
        # Will be None whenever the timer isn't running
        self.scan_timer_id = None
        self.access_points = []

        self.connect_signals()

        # Register kivy events
        self.register_event_type('on_access_points')
        self.register_event_type('on_wrong_password')
        self.register_event_type('on_connect_failed')

        # Initiate the values handled by signal handlers by simply
        # sending all the properties that are being listened to.
        self.handle_wifi_dev_props(None, self.wifi_dev.GetAll(_NM + ".Device"), None)
        self.handle_wifi_dev_props(None, self.wifi_dev.GetAll(_NM + ".Device.Wireless"), None)
        self.handle_nm_props(None, self.nm.GetAll('org.freedesktop.NetworkManager'), None)

    def connect_signals(self):
        """Connect DBus signals to their callbacks.  Called in __init__"""
        # Pick out the .DBus.Properties interface because the .NetworkManager
        # interface overwrites that with a less functioning one.
        nm_prop = self.nm['org.freedesktop.DBus.Properties']
        nm_prop.PropertiesChanged.connect(self.handle_nm_props)
        wifi_prop = self.wifi_dev['org.freedesktop.DBus.Properties']
        wifi_prop.PropertiesChanged.connect(self.handle_wifi_dev_props)

    def run(self):
        """Executed by Thread.start(). This thread stops when this method finishes."""
        self.loop.run()


    def handle_nm_props(self, iface, props, inval):
        """Receives all property changes of self.nm"""
        if "PrimaryConnectionType" in props:
            # Connection Type changed
            con_type = props['PrimaryConnectionType']
            if con_type == '': # No active connection
                self.connection_type = "none"
            elif con_type == '802-3-ethernet': # TODO verify this is the correct string
                self.connection_type = "ethernet"
            elif con_type == '802-11-wireless': # Wi-Fi connected
                self.connection_type = "wireless"

    def handle_wifi_dev_props(self, iface, props, inval):
        """
        Receives all property changes of self.wifi_dev and calls the
        appropriate methods.
        """
        if "LastScan" in props:
            self.handle_scan_complete()
        if "ActiveConnection" in props:
            self.handle_connected_ssid(props['ActiveConnection'])

    def handle_new_connection(self, state, reason):
        """
        Receives state changes from newly added connections.
        Required to ensure everything went OK and to dispatch events
        in case it didn't.  The most important case would be a wrong
        password which will be detected by the reason argument.

        The signal subscription will be canceled when the connection
        was successfully activated.
        """
        print(state, reason)
        #TODO: improve maybe
        if state > 2: # DEACTIVATING or DEACTIVATED
            if reason == 9: # NO_SECRETS
                self.dispatch('on_wrong_password')
            elif reason > 1: # not UNKNOWN or NONE
                self.dispatch('on_connect_failed')
        if state in (2, 4): # ACTIVATED or DEACTIVATED
            # done, no need to listen further
            self.new_connection_subscription.disconnect()

    def handle_scan_complete(self):
        """
        Called on changes in wifi_dev.LastScan, which is changed whenever
        a scan completed.  Parses the access points into dictionaries
        containing the relevant properties:
        ssid    name of wifi
        signal  signal strength in percent
        freq    radio channel frequency in MHz
        in-use  whether we are currently connected with the wifi
        saved   whether the connection is already known and saved
        path    the dbus object path of the access point.
        """
        access_points = []
        saved_ssids = self.get_saved_ssids()
        # Generate the entries
        for path in self.wifi_dev.AccessPoints:
            ap_obj = self.bus.get(_NM, path)
            ssid_b = ap_obj.Ssid # type: ay
            # convert to string
            #PYTHON3: ssid = str(bytes(ssid).decode('utf-8'))
            #PYTHON2:
            ssid = ""
            for c in ssid_b:
                # Will generate stupid things (e.g '\xc8') on unicode chars
                ssid += chr(c)
            signal = ap_obj.Strength # type: y
            freq = ap_obj.Frequency # type: u
            saved = (ssid_b in saved_ssids)
            in_use = (path == self.wifi_dev.ActiveAccessPoint)
            entry = {'signal': signal, 'ssid': ssid, 'in-use': in_use,
                'saved': saved, 'path': path, 'freq': freq}
            access_points.append(entry)
        # Sort by signal strength and then by 'in-use'
        access_points.sort(key=lambda x: x['signal'], reverse=True)
        access_points.sort(key=lambda x: x['in-use'], reverse=True)

        # Filter out access points with duplicate ssids
        unique_ssids = []
        to_remove = [] # Avoid removing while iterating
        for ap in access_points:
            if ap['ssid'] in unique_ssids:
                # Find previous occurence again
                for prev in access_points:
                    if ap['ssid'] == prev['ssid']:
                        break
                # Decide which to keep weighing in-use*4, freq*2, signal*1
                decision = (4*cmp(ap['in-use'], prev['in-use']) +
                    2*cmp(ap['freq'] // 2000, prev['freq'] // 2000) +
                    cmp(ap['signal'], prev['signal']))
                if decision > 0: # Decide for ap
                    to_remove.append(prev)
                else:
                    to_remove.append(ap)
            else:
                unique_ssids.append(ap['ssid'])
        for rm in to_remove:
            access_points.remove(rm)
        self.access_points = access_points
        self.dispatch('on_access_points', self.access_points)

    def handle_connected_ssid(self, active_path):
        """
        Called whenever the active wifi connection changes.
        Sets the ssid of the currently connected wifi connection.
        If no wifi connection currently exists, set "".
        """
        if active_path == "/" or active_path not in self.nm.ActiveConnections:
            # There is no wifi connection right now
            self.connected_ssid = ""
        else:
            active = self.bus.get(_NM, active_path)
            # The id which isn't guaranteed to be, but by default is the ssid
            self.connected_ssid = active.Id


    def set_scan_frequency(self, freq):
        """
        Set the frequency at which to scan for wifi networks.
        freq is the frequency in seconds and should be an int.
        If freq is 0, the rescan clock is cancelled.
        """
        if freq == 0:
            if self.scan_timer_id:
                GLib.source_remove(self.scan_timer_id)
                self.scan_timer_id = None
        else:
            self.scan_timer_id = GLib.timeout_add_seconds(freq, self.wifi_scan)

    def wifi_scan(self):
        """
        Request a rescan on the wifi device.
        
        When finished, self.handle_scan_complete() is called.  In case
        the previous scan is still running a new scan isn't allowed and
        this method returns False, otherwise True.
        """
        try:
            # Provide empty dict to scan for all ssids
            self.wifi_dev.RequestScan({})
            return True
        except GLib.GError as e:
            if "org.freedesktop.NetworkManager.Device.NotAllowed" in e.message:
                return False
            else:
                raise

    def wifi_connect(self, ssid, password):
        """
        From ssid and password (both as plaintext strings) get all the
        information needed to either create and connect or just connect
        the connection.  Relies on the data in self.access_points.

        This method is likely to raise a ValueError or GLib.GError in
        AddAndActivateConnection.  Exception catching is advised.

        Returns path to the new connection (in settings)
        """
        data = None
        for ap in self.access_points:
            if ssid == ap['ssid']:
                data = ap
                break
        if data is None or data['path'] not in self.wifi_dev.AccessPoints:
            # Network got out of view since previous scan
            raise ValueError("Network " + ssid + " is not in view.")
        password = GLib.Variant('s', password)
        #TODO this probably doesn't cover all cases
        connection_info = {'802-11-wireless-security': {'psk': password}} # type: a{sa{sv}}
        con, act_path = self.nm.AddAndActivateConnection(
            connection_info, self.wifi_dev._path, data['path'])
        active = self.bus.get(_NM, act_path)
        self.new_connection_subscription = active.StateChanged.connect(self.handle_new_connection)
        return con

    def wifi_up(self, ssid):
        """Activate a connection that is already stored"""
        data = None
        for ap in self.access_points:
            if ssid == ap['ssid']:
                data = ap
                break
        if data is None or not(data['path'] in self.wifi_dev.AccessPoints and data['saved']):
            raise Exception("Can't activate connection " + ssid)
        active = self.nm.ActivateConnection("/", self.wifi_dev._path, data['path'])
        active = self.bus.get(_NM, active)
        self.new_connection_subscription = active.StateChanged.connect(self.handle_new_connection)
        self.handle_scan_complete()

    def wifi_down(self):
        """Deactivate the currently active wifi connection, if any"""
        active = self.wifi_dev.ActiveConnection
        if active == "/":
            return False
        self.nm.DeactivateConnection(active)
        self.handle_scan_complete()
        return True

    def wifi_delete(self, ssid):
        """Delete a saved connection with the given ssid (string)"""
        ssid_b = [ord(c) for c in ssid]
        connection_paths = self.settings.Connections # type: ao
        for path in connection_paths:
            con = self.bus.get(_NM, path)
            settings = con.GetSettings() # type: a{sa{sv}}
            if '802-11-wireless' in settings: # Only check wifi connections
                if ssid_b == settings['802-11-wireless']['ssid']:
                    con.Delete()
                    return True
        return False

    def get_saved_ssids(self):
        """Return list of ssid bytearrays of all stored Wi-Fi connections"""
        connection_paths = self.settings.Connections # type: ao
        ssids = []
        for path in connection_paths:
            con = self.bus.get(_NM, path)
            settings = con.GetSettings() # type: a{sa{sv}}
            if '802-11-wireless' in settings: # Wired connections don't have ssids
                ssid_b = settings['802-11-wireless']['ssid']
                ssids.append(ssid_b)
        return ssids

    def get_ip4_address(self):
        """
        Return the IPv4 Address of the network device currently in use.
        Return None if there is no active connection.
        """
        active_path = self.nm.PrimaryConnection
        if active_path == "/":
            return None
        active = self.bus.get(_NM, active_path)
        config = self.bus.get(_NM, active.Ip4Config)
        return config.AddressData[0]['address'] # type: aa{sv}

    def get_connection_strength(self):
        """
        Return the connection strength in percent of the currently connected
        wifi access point.  If no wifi connection currently exists, return None.
        """
        active_path = self.wifi_dev.ActiveConnection
        if active_path == "/":
            # There is no wifi connection right now
            return None
        active = self.bus.get(_NM, active_path)
        ap = self.bus.get(_NM, active.SpecificObject)
        return ap.Strength

    def on_access_points(self, aps):
        pass
    def on_wrong_password(self):
        pass
    def on_connect_failed(self):
        pass
