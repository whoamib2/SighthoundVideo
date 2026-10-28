#!/usr/bin/env python

#*****************************************************************************
#
# GetLaunchParameters.py
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
import os
import sys

# Local imports...
from appCommon.CommonStrings import kExeName
from appCommon.CommonStrings import kBackendExeName


#####################################################################
def getLaunchParameters(name=kBackendExeName):
    """Return parameters that will call the launcher.

    @param  name        A name to attempt to give to the launched process.
    @return openParams  A list of parameters to give to Popen.
    """
    frozen = getattr(sys, "frozen", None)

    # Launch the back end application
    if frozen is not None:
        # The packaged Windows app is VigilCam.exe. Re-launch that
        # executable. There is no separate "Sighthound Agent" binary.
        if sys.platform == 'darwin':
            exeDir = os.path.split(sys.executable)[0]
            openParams = [os.path.join(exeDir, kExeName)]
        else:
            openParams = [sys.executable]
    else:
        # Windows and OSX non-built. Use the absolute path so this still works
        # when the process was not started in the repository root.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        openParams = [sys.executable, os.path.join(root, "FrontEndLaunchpad.py")]

    return openParams


