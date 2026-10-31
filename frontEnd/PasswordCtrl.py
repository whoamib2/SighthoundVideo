"""Password entry asked once, with a "Show password" toggle.

Windows cannot switch ``wx.TE_PASSWORD`` on an existing text control, so
``PasswordCtrl`` holds a masked and a plain control and shows one of them,
keeping their text in sync. Text-control methods are forwarded to the
visible one, so it drops in where a ``wx.TextCtrl(style=wx.TE_PASSWORD)``
was used.
"""

import wx

kShowPasswordLabel = "Show password"


class PasswordCtrl(wx.Panel):
    def __init__(self, parent, id=-1, value="", size=wx.DefaultSize, style=0):
        wx.Panel.__init__(self, parent, id, size=size)
        self._masked = wx.TextCtrl(self, -1, value, size=size, style=style | wx.TE_PASSWORD)
        self._plain = wx.TextCtrl(self, -1, value, size=size, style=style)
        self._plain.Hide()
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(self._masked, 1, wx.EXPAND)
        sizer.Add(self._plain, 1, wx.EXPAND)
        self.SetSizer(sizer)
        if size != wx.DefaultSize:
            self.SetMinSize(size)
        self._shown = False

    def _active(self):
        return self._plain if self._shown else self._masked

    def IsShown_(self):
        return self._shown

    def SetShown(self, shown):
        """Unmask (True) or mask (False) the password."""
        shown = bool(shown)
        if shown == self._shown:
            return
        value = self._active().GetValue()
        self._shown = shown
        target, other = (self._plain, self._masked) if shown else (self._masked, self._plain)
        target.ChangeValue(value)
        other.Hide()
        target.Show()
        self.Layout()

    def GetValue(self):
        return self._active().GetValue()

    def SetValue(self, value):
        self._masked.SetValue(value)
        self._plain.ChangeValue(value)

    def ChangeValue(self, value):
        self._masked.ChangeValue(value)
        self._plain.ChangeValue(value)

    def SetFocus(self):
        self._active().SetFocus()

    def SetSelection(self, start, end):
        self._active().SetSelection(start, end)

    def SetMaxLength(self, length):
        self._masked.SetMaxLength(length)
        self._plain.SetMaxLength(length)

    def SetToolTip(self, tip):
        self._masked.SetToolTip(tip)
        self._plain.SetToolTip(tip)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._active(), name)


def makeShowPasswordToggle(parent, passwordCtrl):
    """A "Show password" checkbox wired to ``passwordCtrl``."""
    box = wx.CheckBox(parent, -1, kShowPasswordLabel)
    box.Bind(wx.EVT_CHECKBOX, lambda event: passwordCtrl.SetShown(event.IsChecked()))
    return box
