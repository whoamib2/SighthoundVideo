#!/usr/bin/env python

#*****************************************************************************
#
# GetProcessorInfo.py
#
#
#
#*****************************************************************************
#
#
# Copyright 2013-2022 Sighthound, Inc.
#
# Licensed under the GNU GPLv3 license found at
# https://www.gnu.org/licenses/gpl-3.0.txt
#
# Alternative licensing available from Sighthound, Inc.
# by emailing opensource@sighthound.com
#
# This file is part of the Sighthound Video project which can be found at
# https://github.com/sighthoundinc/SighthoundVideo
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; using version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02111, USA.
#
#
#*****************************************************************************


import os
import sys


###############################################################
def getProcessorInfo():
    """Return a string describing the system processor."""
    if sys.platform == "darwin":
        return _getProcessorInfoMac()
    if sys.platform == "win32":
        return _getProcessorInfoWin()
    return sys.platform


###############################################################
def _getProcessorInfoMac():
    """Return a string describing the system processor."""
    model = os.popen('sysctl -n hw.model').read()
    cpu = os.popen('sysctl -n machdep.cpu.brand_string').read()
    return "%s - %s" % (model.strip(), cpu.strip())


###############################################################
def _registry_text(value):
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def _getProcessorInfoWin():
    """Return a string describing the system processor."""
    import winreg

    identifier = ""
    procName = ""
    numKeys = 0

    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        try:
            identifier = _registry_text(winreg.QueryValueEx(key, "Identifier")[0])
        except Exception:
            pass
        try:
            procName = _registry_text(winreg.QueryValueEx(key, "ProcessorNameString")[0])
        except Exception:
            pass
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"HARDWARE\DESCRIPTION\System\CentralProcessor")
            while True:
                # This eventually raises when the processor keys run out.
                winreg.EnumKey(key, numKeys)
                numKeys += 1
        except Exception:
            pass

    except Exception:
        pass

    return "%s - %s - Num procs: %s" % (identifier, procName, str(numKeys))

