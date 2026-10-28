"""Local network interfaces via psutil.

netifaces has no Windows wheel for Python 3.14. psutil.net_if_addrs() is the
replacement. Callers get the same small dictionaries they used before:
``{"addr": ..., "netmask": ...}``.
"""

from __future__ import annotations

import socket

import psutil


def _records(family):
    found = []
    for name, addresses in psutil.net_if_addrs().items():
        rows = []
        for item in addresses:
            if item.family != family or not item.address:
                continue
            rows.append({"addr": item.address, "netmask": item.netmask})
        if rows:
            found.append((name, rows))
    return found


def ipv4_addresses():
    """IPv4 addresses on this machine, excluding 0.0.0.0."""
    addrs = []
    for _name, rows in _records(socket.AF_INET):
        for row in rows:
            if row["addr"] == "0.0.0.0":
                continue
            try:
                socket.inet_aton(row["addr"])
            except OSError:
                continue
            addrs.append(row)
    return addrs


def multicast_ipv4_text(ip):
    """Return a dotted IPv4 address that can host multicast, or None.

    0.0.0.0, broadcast, link-local, and non-IPv4 values are not valid
    multicast interfaces. Windows reports WinError 10049 when one of those
    is passed to IP_MULTICAST_IF or bind.
    """
    if isinstance(ip, (bytes, bytearray)):
        ip = bytes(ip).decode("ascii", "ignore")
    if not isinstance(ip, str):
        return None
    ip = ip.strip()
    if not ip or ip in ("0.0.0.0", "255.255.255.255"):
        return None
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    try:
        packed = socket.inet_aton(ip)
    except OSError:
        return None
    if packed[0] == 169 and packed[1] == 254:
        return None
    return ip


def mac_addresses():
    """``(interface name, [mac, ...])`` for universal (not locally administered) MACs."""
    found = []
    for name, rows in _records(psutil.AF_LINK):
        macs = []
        for row in rows:
            mac = row["addr"]
            hex_digits = "".join(ch for ch in mac if ch in "0123456789abcdefABCDEF")
            if len(hex_digits) < 2:
                continue
            if 0 == (2 & int(hex_digits[0:2], 16)):
                macs.append(mac)
        if macs:
            found.append((name, macs))
    return found
