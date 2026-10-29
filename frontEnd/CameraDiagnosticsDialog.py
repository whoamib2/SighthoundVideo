"""Tools > Camera Diagnostics...: per-camera decoder (hardware/software), codec,
resolution, fps and today's reconnect count."""

import glob
import json
import os
import time

import wx

_COLUMNS = (("Camera", 150), ("Decoder", 140), ("Codec", 60), ("Resolution", 90),
            ("Pixel format", 80), ("FPS", 50), ("Reconnects today", 110), ("Stream started", 130))


def _reconnects_today(logPath, today):
    count = 0
    try:
        with open(logPath, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith(today) and "Beginning stream open" in line:
                    count += 1
    except OSError:
        pass
    return max(0, count - 1)


def collect_diagnostics(dataDir, today=None):
    """Rows of dicts, one per camera that has written decoder info or a log."""
    today = today or time.strftime("%Y-%m-%d")
    rows = {}
    for path in glob.glob(os.path.join(dataDir, "live", "*.decoder.json")):
        name = os.path.basename(path)[:-len(".decoder.json")]
        try:
            with open(path, encoding="utf-8") as handle:
                rows[name] = dict(json.load(handle))
        except (OSError, ValueError):
            rows[name] = {}
    for path in glob.glob(os.path.join(dataDir, "logs", "cameras", "*.log")):
        name = os.path.splitext(os.path.basename(path))[0]
        rows.setdefault(name, {})["reconnects"] = _reconnects_today(path, today)
    out = []
    for name in sorted(rows):
        info = rows[name]
        out.append({
            "camera": name,
            "decoder": info.get("decoder", "Unknown (not started on this build)"),
            "codec": info.get("codec", ""),
            "resolution": "%sx%s" % (info["width"], info["height"]) if info.get("width") else "",
            "pix_fmt": info.get("pix_fmt", ""),
            "fps": info.get("fps", ""),
            "reconnects": info.get("reconnects", 0),
            "started": info.get("started", ""),
        })
    return out


class CameraDiagnosticsDialog(wx.Dialog):
    def __init__(self, parent, dataDir):
        wx.Dialog.__init__(self, parent, -1, "Camera Diagnostics",
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._dataDir = dataDir
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._list = wx.ListCtrl(self, -1, size=(860, 200), style=wx.LC_REPORT)
        for col, (name, width) in enumerate(_COLUMNS):
            self._list.InsertColumn(col, name, width=width)
        sizer.Add(self._list, 1, wx.EXPAND | wx.ALL, 8)
        note = wx.StaticText(self, -1,
            "Decoder shows 'Hardware (D3D11VA)' when the GPU decodes the stream. "
            "Task Manager > Performance > GPU > 'Video Decode' also rises when hardware "
            "decoding is active.")
        note.Wrap(840)
        sizer.Add(note, 0, wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        refresh = wx.Button(self, -1, "Refresh")
        row.Add(refresh, 0, wx.RIGHT, 8)
        row.Add(wx.Button(self, wx.ID_OK, "Close"), 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizerAndFit(sizer)
        refresh.Bind(wx.EVT_BUTTON, lambda e: self.refresh())
        self.refresh()
        self.CenterOnParent()

    def refresh(self):
        self._list.DeleteAllItems()
        for row in collect_diagnostics(self._dataDir):
            index = self._list.InsertItem(self._list.GetItemCount(), row["camera"])
            for col, key in enumerate(("decoder", "codec", "resolution", "pix_fmt", "fps",
                                       "reconnects", "started"), start=1):
                self._list.SetItem(index, col, str(row[key]))
