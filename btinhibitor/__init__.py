from dataclasses import dataclass
from enum import Enum
import logging
import time
from typing import Dict, Iterable, Set

import dbus
import dbus.exceptions
from gi.repository import GLib


OBJECT_MANAGER_IFACE = 'org.freedesktop.DBus.ObjectManager'

BLUEZ_SERVICE = 'org.bluez'
BLUEZ_ADAPTER_IFACE = 'org.bluez.Adapter1'
BLUEZ_DEVICE_IFACE = 'org.bluez.Device1'

GNOME_SESSION_MANAGER_SERVICE = 'org.gnome.SessionManager'
GNOME_SESSION_MANAGER_IFACE = 'org.gnome.SessionManager'

DISCOVERY_FILTER = dict(
    Transport='le')
INFINITE_FUTURE = 2 * int(time.time())


log = logging.getLogger(__name__)


@dataclass
class DeviceRecord:
    """The record of a device available in the system."""

    address: str
    props: dbus.Interface
    propsChangedSig: any


class DeviceDiscoverer:
    """A Bluetooth device discovery manager.

    `on_done(addrs)` is called whenever a new discovery has been made.

    The class listens to both connections, and runs active discovery
    on a regular interval. For battery powered devices, increasing the
    discovery interval may be worthwhile.
    """

    def __init__(self, bus: dbus.Bus, mainloop: GLib.MainLoop, interval: float=60, timeout: float=1):
        self.bus = bus
        self.om = dbus.Interface(bus.get_object(BLUEZ_SERVICE, '/'), OBJECT_MANAGER_IFACE)
        self.mainloop = mainloop
        self.interval = interval
        self.timeout = timeout

        self.on_done = None

        self._adps = {}
        self._devs = {}
        self._present_devs = {}  # addr -> expiration
        self._latest = time.time()
        self._done_latest = 0
        self._rsig = self.om.connect_to_signal('InterfacesRemoved', self._on_interfaces_removed)
        self._asig = self.om.connect_to_signal('InterfacesAdded', self._on_interfaces_added)

        self._discovering = False
        self._stopto = None
        self._discoverto = GLib.timeout_add_seconds(interval, self._on_discover)

        log.debug('Enumerating initial adapters and devices...')
        for path, ifaces in self.om.GetManagedObjects().items():
            self._on_interfaces_added(path, list(ifaces.keys()))

    def close(self):
        """Close stops all discovery work."""

        if self._stopto:
            GLib.source_remove(self._stopto)

        if self._discovering:
            for adp in self._adps.values():
                adp.StopDiscovery()

        if self._discoverto:
            GLib.source_remove(self._discoverto)

        self._asig.remove()
        self._rsig.remove()

    def _on_discover(self):
        """Called periodically to start discovery.

        A stop timer is started so that _on_stop is called if no
        changes to current devices has been seen within the timeout.
        """
        if self._discovering:
            return True

        log.debug('Discovering devices...')
        for adp in self._adps.values():
            try:
                adp.SetDiscoveryFilter(DISCOVERY_FILTER)
                adp.StartDiscovery()
            except dbus.exceptions.DBusException as ex:
                if ex.get_dbus_name() != 'org.freedesktop.DBus.Error.UnknownObject':
                    raise
                log.debug('Exception ignored: %s', ex)

        self._discovering = True
        self._stopto = GLib.timeout_add_seconds(self.timeout, self._on_stop)
        return True

    def _on_stop(self):
        """Called when a discovery has timed out."""

        if self._latest >= time.time() - self.timeout:
            return True

        for adp in self._adps.values():
            try:
                adp.StopDiscovery()
            except dbus.exceptions.DBusException as ex:
                if ex.get_dbus_name() not in ('org.bluez.Error.Failed', 'org.freedesktop.DBus.Error.UnknownObject'):
                    raise
                # If an adapter was added, but discovery failed to start.
                log.debug('Exception ignored: %s', ex)
        self._discovering = False
        log.debug('Discovery done.')

        self._expire()

        return self._on_done()

    def _on_done(self):
        """Called when a new set of devices is available."""

        if self.on_done and self._done_latest < self._latest:
            now = time.time()
            present = set(addr
                          for addr, exp in self._present_devs.items()
                          if exp >= now)
            log.debug('Present devices: %s', present)
            self.on_done(present)
            self._done_latest = self._latest

        self._stopto = None
        return False

    def _on_interfaces_added(self, path: str, ifaces: [str]):
        """Called when the Bluez ObjectManager has seen a new interface.

        For adapters, we start discovery as needed.

        For devices, we see if they are present, and subscribe to property changes.
        """
        if BLUEZ_ADAPTER_IFACE in ifaces:
            log.debug('Found new BT adapter: %s', path)
            adp = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE, path), BLUEZ_ADAPTER_IFACE)
            self._adps[path] = adp
            if self._discovering:
                try:
                    adp.SetDiscoveryFilter(DISCOVERY_FILTER)
                    adp.StartDiscovery()
                except dbus.exceptions.DBusException as ex:
                    # Discovery happens regularly; no need to retry.
                    if ex.get_dbus_name() not in ('org.bluez.Error.NotReady', 'org.freedesktop.DBus.Error.UnknownObject'):
                        raise
                    log.debug('Exception ignored: %s', ex)
        elif BLUEZ_DEVICE_IFACE in ifaces and path not in self._devs:
            log.debug('Found new BT device: %s', path)
            dev_props = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE, path), dbus.PROPERTIES_IFACE)

            try:
                props = dev_props.GetAll(BLUEZ_DEVICE_IFACE)
            except dbus.exceptions.DBusException as ex:
                if ex.get_dbus_name() == 'org.freedesktop.DBus.Error.UnknownObject':
                    log.debug('Exception ignored (device skipped): %s', ex)
                    return
                raise

            dev = DeviceRecord(
                str(props['Address']),
                dev_props,
                dev_props.connect_to_signal(
                    'PropertiesChanged',
                    lambda iface, ch, inv: self._on_dev_props_changed(path, iface, ch, inv)))
            self._devs[path] = dev

            if _is_device_present(props):
                self._on_dev_present(dev.address, props['Connected'])

    def _on_interfaces_removed(self, path: str, ifaces: [str]):
        """Called when the Bluez ObjectManager has seen a removed interface.

        For adapters, we stop discovery as needed.

        For devices, we remove them and unsubscribe from property changes.
        """
        if BLUEZ_ADAPTER_IFACE in ifaces:
            log.debug('BT adapter removed: %s', path)
            adp = self._adps.pop(path, None)
            if adp and self._discovering:
                try:
                    adp.StopDiscovery()
                except dbus.exceptions.DBusException as ex:
                    if ex.get_dbus_name() not in ('org.bluez.Error.Failed', 'org.freedesktop.DBus.Error.UnknownObject'):
                        raise
                    # If discovery failed to start, or the adapter is already gone (very likely).
                    log.debug('Exception ignored: %s', ex)
        elif BLUEZ_DEVICE_IFACE in ifaces and path in self._devs:
            log.debug('BT device removed: %s', path)
            dev = self._devs.pop(path)
            dev.propsChangedSig.remove()

            self._on_dev_absent(dev.address)

    def _on_dev_props_changed(self, path: str, iface: str, changed: Dict[str, any], invalidated: [str]):
        """Called when a device's properties have changed."""

        if not (IMPORTANT_DEV_PROPS & (set(changed) | set(invalidated))):
            return

        dev = self._devs[path]
        props = dev.props.GetAll(BLUEZ_DEVICE_IFACE)
        if _is_device_present(props):
            log.debug('BT device properties changed, now present: %s, %s', path, changed)
            self._on_dev_present(dev.address, props['Connected'])
        else:
            log.debug('BT device properties changed, now absent: %s, %s', path, changed)
            self._on_dev_absent(dev.address)

    def _on_dev_present(self, addr: str, connected: bool):
        """Called when a device seems to be present."""

        now = time.time()
        exp = self._present_devs.get(addr, None)
        if connected:
            self._present_devs[addr] = INFINITE_FUTURE
        else:
            self._present_devs[addr] = now + self.interval + self.timeout
        if exp and exp >= now:
            return

        log.debug('Marked device %r present.', addr)
        self._latest = now
        if not self._stopto:
            self._stopto = GLib.idle_add(self._on_done)

    def _on_dev_absent(self, addr: str):
        """Called when a device seems to be absent."""

        now = time.time()
        exp = self._present_devs.get(addr, None)
        if not exp or exp < now:
            return

        log.debug('Marked device %r absent.', addr)
        if exp == INFINITE_FUTURE:
            del self._present_devs[addr]
        self._latest = now
        if not self._stopto:
            self._stopto = GLib.idle_add(self._on_done)

    def _expire(self):
        """Removes devices that have not been present for some time."""

        now = time.time()
        present_devs = {addr: exp
                              for addr, exp in self._present_devs.items()
                              if exp >= now}
        if len(present_devs) != len(self._present_devs):
            log.debug('Expired devices: %r', set(self._present_devs) - set(present_devs))
            self._present_devs = present_devs
            self._latest = now


def _is_device_present(props: Dict[str, any]):
    """Returns whether the device properties indicate the device is present."""

    if props['Blocked']:
        # No matter what, we don't care about this device.
        return False

    if props['Paired'] and not props['Connected']:
        # The device object exists because it is configured, not present.
        return False

    # The device is connected, or it's a device known because it is present.
    return True


IMPORTANT_DEV_PROPS = set(['Blocked', 'Paired', 'Connected'])

class InhibitMask(Enum):
    """The GNOME session manager inhibit value masks.

    See https://gitlab.gnome.org/GNOME/gnome-session/-/blob/main/gnome-session/org.gnome.SessionManager.xml
    """
    LOG_OUT = 1 << 0
    SWITCH_USER = 1 << 1
    SUSPEND = 1 << 2
    IDLE = 1 << 3
    AUTO_MOUNT = 1 << 4


class SessionInhibitor:
    """A helper to issue inhibits in the GNOME session manager."""

    def __init__(self, bus: dbus.Bus, reason: str, flags: int, client_name: str):
        self.sm = dbus.Interface(bus.get_object(GNOME_SESSION_MANAGER_SERVICE, '/org/gnome/SessionManager'), GNOME_SESSION_MANAGER_IFACE)
        self.reason = reason
        self.flags = flags

        self._client_id = self.sm.RegisterClient(client_name, client_name)
        self._inhibit_cookie = None

    def close(self):
        """Unregisters any current inhibit and the client."""

        if self._inhibit_cookie:
            self.sm.Uninhibit(self._inhibit_cookie)

        self.sm.UnregisterClient(self._client_id)

    def inhibit(self):
        """Inhibits the preconfigured flags, unless it is already active."""

        if not self._inhibit_cookie:
            self._inhibit_cookie = self.sm.Inhibit(self._client_id, 0, self.reason, self.flags)

    def uninhibit(self):
        """Removes any inhibit created by this class."""

        if self._inhibit_cookie:
            self.sm.Uninhibit(self._inhibit_cookie)
            self._inhibit_cookie = None


class DevicePresenceInhibitor:
    """Looks at devices from a device discoverer and issues inhibits."""

    def __init__(self, discoverer: DeviceDiscoverer, inhibitor: SessionInhibitor, anyOfAddrs: Iterable[str]):
        self.discoverer = discoverer
        discoverer.on_done = self._on_devices
        self.inhibitor = inhibitor
        self.anyOfAddrs = set(anyOfAddrs)

        self._prev = set()

    def _on_devices(self, dev_addrs: Set[str]):
        current = dev_addrs & self.anyOfAddrs
        prev = self._prev
        if current == prev:
            return

        self._prev = current

        if current:
            log.info('Inhibiting devices present: %s', current)
            self.inhibitor.inhibit()
        else:
            log.info('No inhibiting devices present.')
            self.inhibitor.uninhibit()
