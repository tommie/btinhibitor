#!/usr/bin/python3
"""A program to disable the screensaver when some Bluetooth device is nearby.

Example, using the addresses of two BT devices:

  btinhibitor 11:22:33:44:55:66 12:34:56:78:90:12
"""
import argparse
import logging
import re
import sys

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

import btinhibitor


INHIBITOR_FLAGS = {
    'o': btinhibitor.InhibitMask.LOG_OUT,
    'u': btinhibitor.InhibitMask.SWITCH_USER,
    's': btinhibitor.InhibitMask.SUSPEND,
    'i': btinhibitor.InhibitMask.IDLE,
    'm': btinhibitor.InhibitMask.AUTO_MOUNT,
}
ADDR_RE = re.compile(r'[0-9a-fA-F]{2,2}(?::[0-9a-fA-F]{2,2}){5,5}')


log = logging.getLogger(__name__)


def main():
    argp = argparse.ArgumentParser(description=__doc__)
    argp.add_argument('--log-level', default='info', help='log level (debug, info, warning, error, critical) [%(default)s]')
    argp.add_argument('--inhibitors', metavar='imosu', default='i', help='a string of 1-character flags: o=log out, u=switch user, s=suspend, i=idle, m=auto mount [%(default)s]')
    argp.add_argument('--reason', default='Bluetooth device presence', help='the reason string provided to the GNOME session manager [%(default)s]')
    argp.add_argument('addrs', metavar='XX:XX:XX:XX:XX:XX', nargs='+', help='addresses of Bluetooth devices that will cause an inhibit')
    args = argp.parse_args()

    logging.basicConfig(stream=sys.stdout, level=args.log_level.upper())

    inh_mask = 0
    for c in args.inhibitors:
        m = INHIBITOR_FLAGS.get(c, None)
        if not m:
            raise ValueError('invalid inhibitor flag: {}'.format(c))
        inh_mask |= m.value

    for addr in args.addrs:
        if not ADDR_RE.match(addr):
            raise ValueError('invalid Bluetooth address syntax: {}'.format(addr))

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    mainloop = GLib.MainLoop()

    dd = btinhibitor.DeviceDiscoverer(dbus.SystemBus(), mainloop)
    si = btinhibitor.SessionInhibitor(dbus.SessionBus(), args.reason, inh_mask, 'btinhibitor')
    dpi = btinhibitor.DevicePresenceInhibitor(dd, si, [addr.upper() for addr in args.addrs])

    log.info('Inhibiting %r when any Bluetooth device of %r is present.', args.inhibitors, args.addrs)

    mainloop.run()


if __name__ == '__main__':
    main()
