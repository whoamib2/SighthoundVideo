#!/usr/bin/env python

#*****************************************************************************
#
# LookForOtherInstances.py
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

# Python imports...
import ctypes

# Common 3rd-party imports...
import wx

from appCommon.startuplog import instance_lock_name
from appCommon.startuplog import log_startup


# Window class names used by wx on Windows. Phoenix builds have used both.
_WINDOW_CLASSES = ("wxWindowClassNR", "wxWindowNR")


###########################################################
def _bring_existing_window_forward(mainWindowName):
    """Return True when an existing top-level window was found and raised."""
    if wx.Platform != '__WXMSW__':
        return False
    for className in _WINDOW_CLASSES:
        hwnd = ctypes.windll.user32.FindWindowW(className, mainWindowName)
        if hwnd:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            return True
    return False


###########################################################
def lookForOtherInstances(mainWindowName=None):
    """Check of other instances; if they're running, switch 'em to front.

    ...note that switching to front only happens on Windows.  On Mac, we don't
    really have this problem--the OS won't allow you to run the app twice.

    @param  mainWindowName        The window name to bring to the front, if
                                  we're not first.  If none, we'll try the app
                                  name.
    @return singleIntanceChecker  If non-None, keep a reference to this as long
                                  as you're running.  If None, this wasn't the
                                  first instance.
    """
    appName = wx.GetApp().GetAppName()

    if mainWindowName is None:
        mainWindowName = appName

    if isinstance(mainWindowName, (bytes, bytearray)):
        mainWindowName = mainWindowName.decode('utf-8')

    # Make an object to check for a single instance of apps.  We
    # temporarily disable logs so we never see a "stale lock file" error...
    lockName = instance_lock_name(appName, wx.GetUserId())
    log = wx.LogNull()
    singleInstanceChecker = wx.SingleInstanceChecker(lockName)
    del log

    if singleInstanceChecker.IsAnotherRunning():
        # A lock we cannot create also reports "another instance". Only stop
        # when a real window from that instance can be brought forward.
        if _bring_existing_window_forward(mainWindowName):
            log_startup("another instance is already running (%s)" % lockName)
            return None
        log_startup(
            "single-instance lock %s is set, but no window was found; continuing"
            % lockName
        )

    # Return the checker, for the caller to hold...
    return singleInstanceChecker


