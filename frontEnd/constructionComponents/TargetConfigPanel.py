#!/usr/bin/env python

#*****************************************************************************
#
# TargetConfigPanel.py
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
import operator
import sys

# Common 3rd-party imports...
from PIL import Image, ImageDraw
from vitaToolbox.image.PilCompat import text_size
import wx

# Local imports...
from .ConfigPanel import ConfigPanel
from appCommon.CommonStrings import kTargetSettingToLabel, kTargetLabelToSetting, kTargetLabels
from backEnd.native.detector import DELIVERY_CARRIERS, carrier_display_name

# Constants...

_kTargetLabelStr = "Look for:"


_kWantMinSizeStr = "Ignore objects smaller than:"
_kMinSizeValues = [ 30, 40, 50, 60, 70 ]
_kMinSizeStrs = [
    "%d pixels" % (val) for val in _kMinSizeValues
]

_kShowMinSizeLabelStr = "Show how tall this is in the image above"

_kOverlayImageWidth = 25
_kOverlayBackgroundColor = (255, 255, 255, 127)
_kOverlayLineColor = (0, 204, 0, 127)
_kOverlayLineWidth = 3

_kPlateModes = ["", "equals", "matches", "watchlist"]
_kPlateModeLabels = ["Off", "Plate equals", "Plate matches", "Plate on watchlist"]
_kFaceModes = ["", "known", "unknown", "specific"]
_kFaceModeLabels = ["Off", "Known person", "Unknown person", "Specific person"]


##############################################################################
class TargetConfigPanel(ConfigPanel):
    """The block configuration panel for a camera."""

    ###########################################################
    def __init__(self, parent, videoWindow, targetBlockDataModel, queryDataModel=None):
        """TargetConfigPanel constructor.

        @param  parent                Our parent UI element.
        @param  videoWindow           The videoWindow.
        @param  targetBlockDataModel  The data model for this target block.
        @param  queryDataModel        The saved query, for plate/face/carrier rules.
        """
        # Call our super
        super(TargetConfigPanel, self).__init__(parent)

        # Keep track of params...
        self._videoWindow = videoWindow
        self._targetBlockDataModel = targetBlockDataModel
        self._queryDataModel = queryDataModel
        self._updating = False

        # Create our UI elements...

        # Create the target choice.  Curr select will be set from model later.
        targetLabel = wx.StaticText(self, -1, _kTargetLabelStr)
        self._targetChoice = wx.Choice(self, -1, choices=kTargetLabels)
        self._targetChoice.Bind(wx.EVT_CHOICE, self.OnTargetChoice)

        self._wantMinSizeCheckbox = wx.CheckBox(self, -1, _kWantMinSizeStr)
        self._wantMinSizeCheckbox.Bind(wx.EVT_CHECKBOX,
                                       self.OnWantMinSizeCheckbox)
        self._minSizeChoice = wx.Choice(self, -1, choices=_kMinSizeStrs)
        self._minSizeChoice.Bind(wx.EVT_CHOICE, self.OnMinSizeChoice)

        self._showMinSizeCheckbox = wx.CheckBox(self, -1, _kShowMinSizeLabelStr)
        self._showMinSizeCheckbox.Bind(wx.EVT_CHECKBOX, self.OnShowMinSizeCheckbox)

        # Throw our stuff into our sizer...
        targetSizer = wx.BoxSizer(wx.HORIZONTAL)
        targetSizer.Add(targetLabel, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        targetSizer.Add(self._targetChoice, 1, wx.ALIGN_CENTER_VERTICAL)

        minSizeSizer = wx.BoxSizer(wx.HORIZONTAL)
        minSizeSizer.Add(self._wantMinSizeCheckbox, 0,
                         wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        minSizeSizer.Add(self._minSizeChoice, 1, wx.ALIGN_CENTER_VERTICAL)

        showMinSizeSizer = wx.BoxSizer()
        showMinSizeSizer.Add(self._showMinSizeCheckbox, 0, wx.LEFT, 15)

        self._plateChoice = wx.Choice(self, -1, choices=_kPlateModeLabels)
        self._plateChoice.Bind(wx.EVT_CHOICE, self.OnPlateChoice)
        self._platePattern = wx.TextCtrl(self, -1, "")
        self._platePattern.Bind(wx.EVT_TEXT, self.OnPlatePattern)
        plateSizer = wx.BoxSizer(wx.HORIZONTAL)
        plateSizer.Add(wx.StaticText(self, -1, "Plate:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        plateSizer.Add(self._plateChoice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        plateSizer.Add(self._platePattern, 1, wx.ALIGN_CENTER_VERTICAL)

        self._faceChoice = wx.Choice(self, -1, choices=_kFaceModeLabels)
        self._faceChoice.Bind(wx.EVT_CHOICE, self.OnFaceChoice)
        self._faceName = wx.TextCtrl(self, -1, "")
        self._faceName.Bind(wx.EVT_TEXT, self.OnFaceName)
        faceSizer = wx.BoxSizer(wx.HORIZONTAL)
        faceSizer.Add(wx.StaticText(self, -1, "Face:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        faceSizer.Add(self._faceChoice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        faceSizer.Add(self._faceName, 1, wx.ALIGN_CENTER_VERTICAL)

        self._carrierBoxes = []
        carrierSizer = wx.BoxSizer(wx.HORIZONTAL)
        carrierSizer.Add(wx.StaticText(self, -1, "Truck type:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        for carrier in DELIVERY_CARRIERS:
            box = wx.CheckBox(self, -1, carrier_display_name(carrier))
            box.Bind(wx.EVT_CHECKBOX, self.OnCarrier)
            self._carrierBoxes.append(box)
            carrierSizer.Add(box, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._zoneCheckbox = wx.CheckBox(self, -1, "Only inside the where region")
        self._zoneCheckbox.Bind(wx.EVT_CHECKBOX, self.OnDeliveryZone)
        self._cooldownCtrl = wx.TextCtrl(self, -1, "300", size=(60, -1))
        self._cooldownCtrl.Bind(wx.EVT_TEXT, self.OnCooldown)
        cooldownSizer = wx.BoxSizer(wx.HORIZONTAL)
        cooldownSizer.Add(self._zoneCheckbox, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        cooldownSizer.Add(wx.StaticText(self, -1, "Cooldown (seconds):"), 0,
                          wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        cooldownSizer.Add(self._cooldownCtrl, 0, wx.ALIGN_CENTER_VERTICAL)

        if queryDataModel is None:
            for ctrl in (self._plateChoice, self._platePattern, self._faceChoice,
                         self._faceName, self._zoneCheckbox, self._cooldownCtrl):
                ctrl.Hide()
            for box in self._carrierBoxes:
                box.Hide()

        # Use a border sizer to give a little space
        borderSizer = wx.BoxSizer(wx.VERTICAL)
        borderSizer.Add(targetSizer, 0, wx.EXPAND | wx.TOP, 15)
        borderSizer.Add(minSizeSizer, 0, wx.EXPAND | wx.TOP, 10)
        borderSizer.Add(showMinSizeSizer, 0, wx.EXPAND | wx.TOP, 5)
        borderSizer.Add(plateSizer, 0, wx.EXPAND | wx.TOP, 12)
        borderSizer.Add(faceSizer, 0, wx.EXPAND | wx.TOP, 8)
        borderSizer.Add(carrierSizer, 0, wx.EXPAND | wx.TOP, 8)
        borderSizer.Add(cooldownSizer, 0, wx.EXPAND | wx.TOP, 6)
        self.SetSizer(borderSizer)

        # Listen for changes.
        self._targetBlockDataModel.addListener(self._updateFromModels)
        if self._queryDataModel is not None:
            self._queryDataModel.addListener(self._updateFromModels)

        # Update everything...
        self._updateFromModels()


    ###########################################################
    def getIcon(self):
        """Return the path to the bitmap associated with this panel.

        @return bmpPath  The path to the bitmap.
        """
        return "frontEnd/bmps/Block_Icon_Look_For.png"


    ###########################################################
    def getTitle(self):
        """Return the title associated with this panel.

        @return title  The title
        """
        return "Look for"


    ###########################################################
    def activate(self):
        """Set this panel as the active one."""
        self._updateFromModels(None)


    ###########################################################
    def deactivate(self):
        """Called before another panel gets activate."""
        self._videoWindow.setOverlayImage(None)


    ###########################################################
    def _updateFromModels(self, modelThatChanged=None):
        """Update all of our settings based on our data models.

        @param  modelThatChanged  The model that changed (ignored).
        """
        _ = modelThatChanged
        self._updating = True
        try:
            self._applyModel()
        finally:
            self._updating = False

    def _applyModel(self):
        target = self._targetBlockDataModel.getTargetName()
        target = kTargetSettingToLabel[target]
        self._targetChoice.SetStringSelection(target)

        minSize = self._targetBlockDataModel.getMinSize()
        try:
            minSizeSelection = _kMinSizeValues.index(minSize)
            self._minSizeChoice.SetSelection(minSizeSelection)
        except ValueError:
            pass

        wantMinSize = self._targetBlockDataModel.getWantMinSize()
        if not wantMinSize:
            self._wantMinSizeCheckbox.SetValue(0)
            self._minSizeChoice.Enable(False)
            self._showMinSizeCheckbox.SetValue(0)
            self._showMinSizeCheckbox.Enable(False)
            self._videoWindow.setOverlayImage(None)
        else:
            self._wantMinSizeCheckbox.SetValue(1)
            self._minSizeChoice.Enable(True)

            self._showMinSizeCheckbox.Enable(True)
            showMinSize = self._targetBlockDataModel.getShowMinSize()
            self._showMinSizeCheckbox.SetValue(int(showMinSize))
            if showMinSize:
                overlayImage = self._makeOverlayImage(minSize)
                self._videoWindow.setOverlayImage(overlayImage)
            else:
                self._videoWindow.setOverlayImage(None)

        query = self._queryDataModel
        if query is None:
            return
        plate_mode = query.getPlateMode()
        if plate_mode in _kPlateModes:
            self._plateChoice.SetSelection(_kPlateModes.index(plate_mode))
        self._platePattern.ChangeValue(query.getPlatePattern())
        face_mode = query.getFaceMode()
        if face_mode in _kFaceModes:
            self._faceChoice.SetSelection(_kFaceModes.index(face_mode))
        self._faceName.ChangeValue(query.getFaceName())
        selected = set(query.getDeliveryCarriers())
        for box, carrier in zip(self._carrierBoxes, DELIVERY_CARRIERS):
            box.SetValue(carrier in selected)
        self._zoneCheckbox.SetValue(bool(query.getDeliveryUseZone()))
        self._cooldownCtrl.ChangeValue(str(query.getDeliveryCooldownSec()))


    ###########################################################
    def _makeOverlayImage(self, height):
        """Make an image showing how tall the "height" is.

        @param  height  The height to show.
        @return img     A PIL Image.
        """
        img = Image.new('RGBA', (_kOverlayImageWidth, height), 0)
        imgDraw = ImageDraw.Draw(img)

        imgDraw.rectangle((_kOverlayLineWidth, _kOverlayLineWidth,
                           _kOverlayImageWidth - _kOverlayLineWidth,
                           height - _kOverlayLineWidth),
                          fill=_kOverlayBackgroundColor)
        for i in range(_kOverlayLineWidth):
            imgDraw.rectangle((i, i, _kOverlayImageWidth-i-1, height-i-1),
                              outline=_kOverlayLineColor)

        label = str(height)
        labelWidth, labelHeight = text_size(imgDraw, label)
        imgDraw.text(((_kOverlayImageWidth-labelWidth)/2,
                      (height-labelHeight)/2), label, fill="black")

        return img


    ###########################################################
    def OnTargetChoice(self, event=None):
        """Handle when the user changes targets.

        @param  event  The choice event (ignored)
        """
        target = self._targetChoice.GetStringSelection()
        target = kTargetLabelToSetting[target]
        self._targetBlockDataModel.setTargetName(target)


    ###########################################################
    def OnWantMinSizeCheckbox(self, event=None):
        """Handle when the user changes "want min size" checkbox.

        @param  event  The choice event (ignored)
        """
        wantMinSize = self._wantMinSizeCheckbox.GetValue()
        self._targetBlockDataModel.setWantMinSize(bool(wantMinSize))


    ###########################################################
    def OnMinSizeChoice(self, event=None):
        """Handle when the user changes min size.

        @param  event  The choice event (ignored)
        """
        minSizeIndex = self._minSizeChoice.GetSelection()
        if minSizeIndex != -1:
            self._targetBlockDataModel.setMinSize(_kMinSizeValues[minSizeIndex])
        else:
            assert False, "Bad min size choice"


    ###########################################################
    def OnShowMinSizeCheckbox(self, event=None):
        """Handle when the user changes "show min size" checkbox.

        @param  event  The choice event (ignored)
        """
        showMinSize = self._showMinSizeCheckbox.GetValue()
        self._targetBlockDataModel.setShowMinSize(bool(showMinSize))

    def OnPlateChoice(self, event=None):
        if self._updating or self._queryDataModel is None:
            return
        index = self._plateChoice.GetSelection()
        if index >= 0:
            self._queryDataModel.setPlateMode(_kPlateModes[index])

    def OnPlatePattern(self, event=None):
        if self._updating or self._queryDataModel is None:
            return
        self._queryDataModel.setPlatePattern(self._platePattern.GetValue())

    def OnFaceChoice(self, event=None):
        if self._updating or self._queryDataModel is None:
            return
        index = self._faceChoice.GetSelection()
        if index >= 0:
            self._queryDataModel.setFaceMode(_kFaceModes[index])

    def OnFaceName(self, event=None):
        if self._updating or self._queryDataModel is None:
            return
        self._queryDataModel.setFaceName(self._faceName.GetValue())

    def OnCarrier(self, event=None):
        if self._updating or self._queryDataModel is None:
            return
        chosen = []
        for box, carrier in zip(self._carrierBoxes, DELIVERY_CARRIERS):
            if box.GetValue():
                chosen.append(carrier)
        self._queryDataModel.setDeliveryCarriers(chosen)

    def OnDeliveryZone(self, event=None):
        if self._updating or self._queryDataModel is None:
            return
        self._queryDataModel.setDeliveryUseZone(bool(self._zoneCheckbox.GetValue()))

    def OnCooldown(self, event=None):
        if self._updating or self._queryDataModel is None:
            return
        text = self._cooldownCtrl.GetValue().strip()
        if not text:
            return
        try:
            seconds = int(text)
        except ValueError:
            return
        if seconds < 0:
            return
        self._queryDataModel.setDeliveryCooldownSec(seconds)


##############################################################################
def test_main():
    """OB_REDACT
       Contains various self-test code.
    """
    print("NO TESTS")


##############################################################################
if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_main()
    else:
        print("Try calling with 'test' as the argument.")
