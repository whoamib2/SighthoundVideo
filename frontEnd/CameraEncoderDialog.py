"""Per-camera stream / bitrate dialog.

ONVIF cameras: shows main/sub codec, resolution, fps and bitrate from
GetVideoEncoderConfigurations and lets the user change bitrate / fps via
SetVideoEncoderConfiguration. Other cameras (Blue Iris restreams, plain
RTSP): explains where the bitrate is set. All cameras: measured bitrate of
the recorded stream and the estimated GB/day.
"""

import pickle
import threading

import wx

from backEnd.native.open_capture import gb_per_day
from frontEnd.DecoderBadge import read_decoder_info
from vitaToolbox.networking.Onvif import extractUuidFromOnvifUrl, isOnvifUrl
from vitaToolbox.networking.OnvifEncoder import (
    OnvifEncoderError,
    get_encoder_configs,
    onvif_target,
    set_encoder_config,
)


def measured_text(info):
    kbps = (info or {}).get("measured_kbps")
    if not kbps:
        return "Measured bitrate: not measured yet (needs about a minute of recording)."
    return "Measured bitrate of the recorded stream: %d kbps \u2248 %.1f GB/day if recorded continuously." % (
        kbps, gb_per_day(kbps))


def config_text(cfg):
    return "%s (%s): %s %sx%s, %s fps, %s kbps%s" % (
        cfg.get("name") or cfg.get("token"), cfg.get("token"), cfg.get("encoding"),
        cfg.get("width"), cfg.get("height"), cfg.get("fps"), cfg.get("bitrate_kbps"),
        (", GOP %s" % cfg["gov"]) if cfg.get("gov") else "")


class CameraEncoderDialog(wx.Dialog):
    def __init__(self, parent, cameraName, cameraUri, backEndClient, dataDir):
        super().__init__(parent, title="Stream && Bitrate \u2014 %s" % cameraName,
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._uri = cameraUri
        self._backEndClient = backEndClient
        self._configs = []
        self._target = None

        sizer = wx.BoxSizer(wx.VERTICAL)
        info = read_decoder_info(dataDir, cameraName)
        sizer.Add(wx.StaticText(self, -1, measured_text(info)), 0, wx.ALL, 8)

        self._isOnvif = isOnvifUrl(cameraUri)
        if not self._isOnvif:
            sizer.Add(wx.StaticText(self, -1,
                "This camera is not connected through ONVIF (for example a Blue Iris restream).\n"
                "Its bitrate, frame rate and codec are set in Blue Iris or in the camera's own web page;\n"
                "the measured bitrate above shows what VigilCam actually receives."), 0, wx.ALL, 8)
        else:
            self._list = wx.ListBox(self, -1, size=(560, 70))
            self._list.Bind(wx.EVT_LISTBOX, self._onSelect)
            sizer.Add(self._list, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
            grid = wx.FlexGridSizer(2, 4, 6, 6)
            grid.Add(wx.StaticText(self, -1, "Bitrate (kbps):"), 0, wx.ALIGN_CENTER_VERTICAL)
            self._bitrate = wx.SpinCtrl(self, -1, min=64, max=20000, initial=2048)
            self._bitrate.Bind(wx.EVT_SPINCTRL, self._onValues)
            grid.Add(self._bitrate)
            grid.Add(wx.StaticText(self, -1, "Frame rate:"), 0, wx.ALIGN_CENTER_VERTICAL)
            self._fps = wx.SpinCtrl(self, -1, min=1, max=60, initial=15)
            self._fps.Bind(wx.EVT_SPINCTRL, self._onValues)
            grid.Add(self._fps)
            sizer.Add(grid, 0, wx.ALL, 8)
            self._estimate = wx.StaticText(self, -1, "")
            sizer.Add(self._estimate, 0, wx.LEFT | wx.RIGHT, 8)
            sizer.Add(wx.StaticText(self, -1,
                "Resolution and H.265 are left as configured on the camera (ONVIF Media 1 only offers H.264)."),
                0, wx.ALL, 8)
            self._apply = wx.Button(self, -1, "Apply to camera")
            self._apply.Bind(wx.EVT_BUTTON, self._onApply)
            self._apply.Disable()
            sizer.Add(self._apply, 0, wx.LEFT, 8)
        self._status = wx.StaticText(self, -1, "Reading encoder settings from the camera..." if self._isOnvif else "")
        sizer.Add(self._status, 0, wx.ALL | wx.EXPAND, 8)
        sizer.Add(self.CreateButtonSizer(wx.CLOSE), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.SetEscapeId(wx.ID_CLOSE)
        self.SetSizerAndFit(sizer)
        if self._isOnvif:
            self._run(self._read)

    # Network work happens off the UI thread; results come back via CallAfter.
    def _run(self, fn, *args):
        def work():
            try:
                result = fn(*args)
                wx.CallAfter(self._done, result, None)
            except OnvifEncoderError as exc:
                wx.CallAfter(self._done, None, str(exc))
            except Exception as exc:
                wx.CallAfter(self._done, None, "Unexpected error: %s" % type(exc).__name__)
        threading.Thread(target=work, daemon=True).start()

    def _resolveTarget(self):
        if self._target is None:
            devices = pickle.loads(self._backEndClient.getOnvifDevices())
            self._target = onvif_target(devices.get(extractUuidFromOnvifUrl(self._uri)), self._uri)
        return self._target

    def _read(self):
        host, port, creds = self._resolveTarget()
        return ("read", get_encoder_configs(host, port, creds))

    def _write(self, cfg, bitrate, fps):
        host, port, creds = self._resolveTarget()
        set_encoder_config(host, port, creds, cfg, bitrate_kbps=bitrate, fps=fps)
        return ("written", get_encoder_configs(host, port, creds))

    def _done(self, result, error):
        if not self:
            return
        if error:
            self._status.SetLabel(error)
            self._status.SetForegroundColour(wx.RED)
            if self._configs:
                self._apply.Enable()
            if "Changing" in error:
                wx.MessageBox(error, "Could not change camera settings", wx.OK | wx.ICON_ERROR, self)
            self.Layout()
            return
        kind, configs = result
        self._configs = configs
        sel = max(self._list.GetSelection(), 0)
        self._list.Set([config_text(c) for c in configs])
        if configs:
            self._list.SetSelection(min(sel, len(configs) - 1))
            self._onSelect()
            self._apply.Enable()
        self._status.SetForegroundColour(wx.Colour(0, 120, 0))
        self._status.SetLabel("Saved on the camera. The stream restarts with the new settings."
                              if kind == "written" else "%d encoder configuration(s) read." % len(configs))
        self.Layout()

    def _onSelect(self, event=None):
        cfg = self._configs[self._list.GetSelection()]
        if cfg.get("bitrate_kbps"):
            self._bitrate.SetValue(cfg["bitrate_kbps"])
        if cfg.get("fps"):
            self._fps.SetValue(cfg["fps"])
        self._onValues()

    def _onValues(self, event=None):
        self._estimate.SetLabel("At %d kbps: \u2248 %.1f GB/day per camera stream if recorded continuously." % (
            self._bitrate.GetValue(), gb_per_day(self._bitrate.GetValue())))

    def _onApply(self, event):
        idx = self._list.GetSelection()
        if idx < 0:
            return
        self._apply.Disable()
        self._status.SetForegroundColour(wx.NullColour)
        self._status.SetLabel("Writing to the camera...")
        self._run(self._write, self._configs[idx], self._bitrate.GetValue(), self._fps.GetValue())
