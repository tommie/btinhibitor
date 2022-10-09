# Bluetooth GNOME Session Inhibitor

A program to disable the screensaver when some Bluetooth device is nearby.

This will not unlock an already locked computer, just stop idling from
triggering the screenlock.

## Example

Using the addresses of two BT devices:

```shell
btinhibitor 11:22:33:44:55:66 12:34:56:78:90:12
```

Inhibiting suspend instead of the screensaver:

```shell
btinhibitor --inhibitors s 11:22:33:44:55:66 12:34:56:78:90:12
```

## Inhibitors

These `--inhibitor` flags are supported, defaulting to "i":

* **i** idle
* **m** auto mount
* **o** log out
* **s** suspend
* **u** switch user
