"""
Needs python-gobject (sometimes called python-gi)
and python-pydbus

NOTES:
In case no password is required, ap.Flags and ap.RsnFlags are both 0x0.
In case PSK is supported, at least ap.Flags has 0x1
and ap.RsnFlags must have 0x100.

eduroam with wpa-enterprise? has 0x200, but not 0x100 in RsnFlags.
"""
from threading import Thread

from gi.repository import GLib
from kivy.event import EventDispatcher
from kivy.properties import OptionProperty, StringProperty
from pydbus import SystemBus


_NM = "org.freedesktop.NetworkManager"

class NetworkManager(EventDispatcher, Thread):

    connected_ssid = StringProperty()
    connection_type = OptionProperty("none", options=["none", "ethernet", "wireless"])

    def __init__(self, **kwargs):
        self.available = False
        super().__init__(**kwargs)

        self.loop = GLib.MainLoop()
        self.bus = SystemBus()

        # ID used to cancel the scan timer and to find out whether it is running
        # Will be None whenever the timer isn't running
        self.scan_timer_id = None
        self.new_connection_subscription = None
        self.access_points = []
        self.saved_ssids = []

        # Register kivy events
        self.register_event_type('on_access_points')
        self.register_event_type('on_connect_failed')

        # Get proxy objects
        try:
            self.nm = self.bus.get(_NM, "/org/freedesktop/NetworkManager")
        except GLib.GError as e:
            # Occurs when NetworkManager was not installed
            if "org.freedesktop.DBus.Error.ServiceUnknown" in e.message:
                return # Leaves self.available = False
            raise

        self.settings = self.bus.get(_NM, "/org/freedesktop/NetworkManager/Settings")
        devices = self.nm.Devices # Type: ao
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
            return # Leaves self.available = False
        #UNUSED Does the wifi device support 5gGHz (flag 0x400)
        #self.freq_5ghz = bool(self.wifi_dev.WirelessCapabilities & 0x400)

        # Connect DBus signals to their callbacks
        # Pick out the .DBus.Properties interface because the .NetworkManager
        # interface overwrites that with a less functioning one.
        nm_prop = self.nm['org.freedesktop.DBus.Properties']
        nm_prop.PropertiesChanged.connect(self._handle_nm_props)
        wifi_prop = self.wifi_dev['org.freedesktop.DBus.Properties']
        wifi_prop.PropertiesChanged.connect(self._handle_wifi_dev_props)
        # Initiate the values handled by signal handlers by simply
        # sending all the properties that are being listened to.
        self._handle_nm_props(None, self.nm.GetAll('org.freedesktop.NetworkManager'), None)
        self._handle_wifi_dev_props(None, self.wifi_dev.GetAll(_NM + ".Device"), None)
        self._handle_wifi_dev_props(None, self.wifi_dev.GetAll(_NM + ".Device.Wireless"), None)

        self.available = True

    def run(self):
        """
        Executed by Thread.start(). This thread stops when this method finishes.
        Stop the loop by calling self.loop.quit().
        """
        self.loop.run()

    def stop(self):
        self.loop.quit()


    def _handle_nm_props(self, iface, props, inval):
        """Receives all property changes of self.nm"""
        if "PrimaryConnectionType" in props:
            # Connection Type changed
            con_type = props['PrimaryConnectionType']
            if con_type == '802-3-ethernet':
                self.connection_type = "ethernet"
            elif con_type == '802-11-wireless': # Wi-Fi connected
                self.connection_type = "wireless"
            else: # No active connection
                self.connection_type = "none"

    def _handle_wifi_dev_props(self, iface, props, inval):
        """
        Receives all property changes of self.wifi_dev and calls the
        appropriate methods.
        """
        if "LastScan" in props:
            self._handle_scan_complete()
        if "ActiveAccessPoint" in props:
            self._handle_connected_ssid(props['ActiveAccessPoint'])

    def _handle_new_connection(self, state, reason):
        """
        Receives state changes from newly added connections.
        Required to ensure everything went OK and to dispatch events
        in case it didn't.  The most important case would be a wrong
        password which isn't clearly identifiable from the reason.

        The signal subscription will be canceled when the connection
        was successfully activated.
        """
        if state > 2: # DEACTIVATING or DEACTIVATED
            self.dispatch('on_connect_failed')
        if state in (2, 4): # ACTIVATED or DEACTIVATED
            # done, no need to listen further
            self.new_connection_subscription.disconnect()

    def _handle_scan_complete(self):
        """
        Called on changes in wifi_dev.LastScan, which is changed whenever
        a scan completed.  Parses the access points into wrapper objects
        containing the relevant attributes and methods

        WARNING: Because of some DBus calls, this function can take
        from 0.5 up to 5 seconds to complete.
        """
        # Needed to accurately build AccessPoint objects
        self.saved_ssids = self.get_saved_ssids()
        access_points = []
        for path in self.wifi_dev.AccessPoints:
            try:
                ap = AccessPoint(self, path)
            except: # DBus sometimes throws a random error here
                continue
            # Ignore unnamed access points
            if ap.ssid:
                access_points.append(ap)
        # Sort by signal strength, frequency and then by 'in-use'
        access_points.sort(key=lambda x: x.signal, reverse=True)
        # We only really want to differentiate between 2.4 GHz and 5 GHz
        access_points.sort(key=lambda x: x.freq // 2000, reverse=True)
        access_points.sort(key=lambda x: x.in_use, reverse=True)

        # Filter out access points with duplicate ssids
        seen_ssids = set()
        unique_aps = []
        for ap in access_points:
            # Because access_points are already sorted most wanted first
            # we just add the first occurence of each ssid
            if ap.ssid not in seen_ssids:
                unique_aps.append(ap)
                seen_ssids.add(ap.ssid)
        self.access_points = unique_aps # update the property
        self.dispatch('on_access_points', self.access_points)

    def _handle_connected_ssid(self, active_path):
        """
        Called whenever the active wifi connection changes.
        Sets the ssid of the currently connected wifi connection.
        If no wifi connection currently exists, set "".

        active_path references the AccessPoint object currently in use.
        """
        if active_path == "/":
            # There is no wifi connection right now
            self.connected_ssid = ""
        else:
            active = self.bus.get(_NM, active_path)
            self.connected_ssid = _bytes_to_string(active.Ssid)


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

        When finished, self._handle_scan_complete() is called.  In case
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
            raise

    def wifi_connect(self, ap, password=None):
        """
        From AccessPoint and password as plaintext string get all the
        information needed to either create and connect or just connect
        the connection.

        This method is likely to raise a ValueError or GLib.GError in
        AddAndActivateConnection.  Exception catching is advised.

        Returns path to the new connection (in settings)
        """
        if ap._path not in self.wifi_dev.AccessPoints:
            # Network got out of view since previous scan
            raise ValueError("Network " + ap.ssid + " is not in view.")
        if ap.encrypted:
            if not ap.supports_psk:
                raise Exception("Access Point " + ap.ssid + " doesn't support PSK verification")
            if password is None:
                raise ValueError("No password provided")
            password = GLib.Variant('s', password)
            connection_info = {'802-11-wireless-security': {'psk': password}} # Type: a{sa{sv}}
            con, act_path = self.nm.AddAndActivateConnection(
                connection_info, self.wifi_dev._path, ap._path)
        else:
            # Open network, no password needed
            con, act_path = self.nm.AddAndActivateConnection(
                {}, self.wifi_dev._path, ap._path)
        active = self.bus.get(_NM, act_path)
        self.new_connection_subscription = active.StateChanged.connect(self._handle_new_connection)
        self.wifi_scan()
        return con

    def wifi_up(self, ap):
        """Activate a connection that is already stored"""
        if not (ap._path in self.wifi_dev.AccessPoints and ap.saved):
            raise Exception("Can't activate connection " + ap.ssid)
        active = self.nm.ActivateConnection("/", self.wifi_dev._path, ap._path)
        active = self.bus.get(_NM, active)
        self.new_connection_subscription = active.StateChanged.connect(self._handle_new_connection)
        self.wifi_scan()

    def wifi_down(self):
        """Deactivate the currently active wifi connection, if any"""
        active = self.wifi_dev.ActiveConnection
        if active == "/":
            return False
        self.nm.DeactivateConnection(active)
        self.wifi_scan()
        return True

    def wifi_delete(self, ap):
        """Delete a saved connection"""
        connection_paths = self.settings.Connections # Type: ao
        for path in connection_paths:
            con = self.bus.get(_NM, path)
            settings = con.GetSettings() # Type: a{sa{sv}}
            if '802-11-wireless' in settings: # Only check wifi connections
                if ap.b_ssid == settings['802-11-wireless']['ssid']:
                    con.Delete()
                    self.wifi_scan()
                    return True
        return False

    def get_saved_ssids(self):
        """Return list of ssid bytearrays of all stored Wi-Fi connections"""
        connection_paths = self.settings.Connections # Type: ao
        ssids = []
        for path in connection_paths:
            con = self.bus.get(_NM, path)
            settings = con.GetSettings() # Type: a{sa{sv}}
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
        return config.AddressData[0]['address'] # Type: aa{sv}

    def get_connection_strength(self):
        """
        Return the connection strength in percent of the currently connected
        wifi access point.  If no wifi connection currently exists, return None.
        """
        active_ap_path = self.wifi_dev.ActiveAccessPoint
        if active_ap_path == "/":
            # There is no wifi connection right now
            return None
        active_ap = self.bus.get(_NM, active_ap_path)
        return active_ap.Strength

    def on_access_points(self, aps):
        pass
    def on_connect_failed(self):
        pass


class AccessPoint:
    """Simpler wrapper class for dbus' AccessPoint proxy objects"""

    def __init__(self, network_manager, path):
        # the running NetworkManager instance
        self._network_manager = network_manager
        self._proxy = self._network_manager.bus.get(_NM, path)
        self._path = path

        self.b_ssid = self._proxy.Ssid # Type: ay
        self.ssid = _bytes_to_string(self.b_ssid)
        self.signal = self._proxy.Strength # Type: y, Signal strength
        # Type: u, Radio channel frequency in MHz
        self.freq = self._proxy.Frequency
        # whether the connection is known
        self.saved = self.b_ssid in self._network_manager.saved_ssids
        # whichever is not 0x0
        security_flags = self._proxy.RsnFlags or self._proxy.WpaFlags
        # False when no password is required, True otherwise
        self.encrypted = bool(self._proxy.Flags)
        # Pre-shared Key encryption is supported
        self.supports_psk = security_flags & 0x100

    @property
    def in_use(self):
        """Whether we are connected with this connection"""
        return self._path == self._network_manager.wifi_dev.ActiveAccessPoint

    def connect(self, password=None):
        call_async(self._network_manager.wifi_connect, self, password)

    def up(self):
        call_async(self._network_manager.wifi_up, self)

    def down(self):
        call_async(self._network_manager.wifi_down)

    def delete(self):
        call_async(self._network_manager.wifi_delete, self)


def call_async(callback, *args):
    """Call a function asynchronously with the dbus loop.
    Return values are not handled."""
    def call_and_stop(args):
        callback(*args)
        return False  # Return False, otherwise this is perpetually executed
    GLib.idle_add(call_and_stop, args, priority=GLib.PRIORITY_HIGH)

def _bytes_to_string(array):
    """Helper function to transform a bytearray to a string"""
    return bytearray(array).decode('utf-8')
