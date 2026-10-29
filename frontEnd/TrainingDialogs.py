"""Dialogs for "Mark as Missed / Wrong Label" and "Training Samples & Retrain"."""

import threading

import wx

from backEnd import training_samples as ts

_kMaxW, _kMaxH = 800, 450
_kNote = ("Retraining covers the delivery-carrier / garbage-truck classifier "
          "(USPS, FedEx, UPS, Amazon, Garbage truck, Bus, School bus, and 'none' from \"Not a ...\" "
          "corrections). Person, Vehicle, Animal, generic Delivery vehicle and "
          "Other samples are saved for later but the main YOLOX detector is NOT "
          "retrained in the app.")


class _FramePanel(wx.Panel):
    """Shows a frame; drag to draw one box (image-pixel coords in ``box``)."""

    def __init__(self, parent, image):
        self._image = image
        w, h = image.GetWidth(), image.GetHeight()
        self._scale = min(1.0, float(_kMaxW) / max(1, w), float(_kMaxH) / max(1, h))
        size = (max(1, int(w * self._scale)), max(1, int(h * self._scale)))
        wx.Panel.__init__(self, parent, -1, size=size)
        self.SetMinSize(size)
        self._bitmap = wx.Bitmap(image.Scale(size[0], size[1], wx.IMAGE_QUALITY_HIGH))
        self._start = None
        self._end = None
        self.SetCursor(wx.Cursor(wx.CURSOR_CROSS))
        self.Bind(wx.EVT_PAINT, self._onPaint)
        self.Bind(wx.EVT_LEFT_DOWN, self._onDown)
        self.Bind(wx.EVT_MOTION, self._onMotion)
        self.Bind(wx.EVT_LEFT_UP, self._onUp)

    def _onDown(self, event):
        self._start = self._end = event.GetPosition()
        self.CaptureMouse()
        self.Refresh()

    def _onMotion(self, event):
        if self._start is not None and event.Dragging() and event.LeftIsDown():
            self._end = event.GetPosition()
            self.Refresh()

    def _onUp(self, event):
        if self.HasCapture():
            self.ReleaseMouse()
        if self._start is not None:
            self._end = event.GetPosition()
            self.Refresh()

    def _onPaint(self, event):
        dc = wx.PaintDC(self)
        dc.DrawBitmap(self._bitmap, 0, 0)
        if self._start is not None and self._end is not None:
            dc.SetPen(wx.Pen(wx.Colour(255, 40, 40), 2))
            dc.SetBrush(wx.TRANSPARENT_BRUSH)
            x1, y1 = self._start
            x2, y2 = self._end
            dc.DrawRectangle(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

    @property
    def box(self):
        if self._start is None or self._end is None:
            return None
        x1, y1 = self._start
        x2, y2 = self._end
        if abs(x2 - x1) < 4 or abs(y2 - y1) < 4:
            return None
        s = self._scale
        return (min(x1, x2) / s, min(y1, y2) / s, max(x1, x2) / s, max(y1, y2) / s)


class MarkMissedDialog(wx.Dialog):
    """Label a missed object (or correct a wrong label) on a paused frame."""

    def __init__(self, parent, framePath, cameraName, timestampMs):
        wx.Dialog.__init__(self, parent, -1, "Mark as Missed / Wrong Label",
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._framePath = framePath
        self._camera = cameraName
        self._timestamp = int(timestampMs)
        image = wx.Image(framePath)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, -1,
                  "Drag a box around the object. With no box the whole frame is saved."),
                  0, wx.ALL, 8)
        self._frame = _FramePanel(self, image)
        sizer.Add(self._frame, 0, wx.LEFT | wx.RIGHT, 8)
        self._kind = wx.RadioBox(self, -1, "What happened?",
                                 choices=["Missed: this should have been detected",
                                          "Wrong label: this is NOT a ..."],
                                 majorDimension=1)
        sizer.Add(self._kind, 0, wx.EXPAND | wx.ALL, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self, -1, "Object type:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self._label = wx.Choice(self, -1, choices=ts.label_choices())
        self._label.SetSelection(0)
        row.Add(self._label, 0, wx.RIGHT, 10)
        row.Add(wx.StaticText(self, -1, "Other label:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self._other = wx.TextCtrl(self, -1, size=(160, -1))
        row.Add(self._other, 1)
        sizer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        buttons = self.CreateSeparatedButtonSizer(wx.OK | wx.CANCEL)
        self.FindWindowById(wx.ID_OK).SetLabel("Save Sample")
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(sizer)
        self.CenterOnParent()
        self.Bind(wx.EVT_BUTTON, self.OnSave, id=wx.ID_OK)

    def OnSave(self, event=None):
        label = self._label.GetStringSelection()
        other = self._other.GetValue().strip()
        if label == ts.OTHER_LABEL and not other:
            wx.MessageBox("Type a label for 'Other'.", "Mark as Missed", wx.OK, self)
            return
        kind = ts.MISSED if self._kind.GetSelection() == 0 else ts.WRONG
        try:
            path = ts.save_sample(self._framePath, label, self._frame.box, self._camera,
                                  self._timestamp, kind, other)
        except Exception as exc:
            wx.MessageBox("Could not save the sample: %s" % exc, "Mark as Missed",
                          wx.ICON_ERROR | wx.OK, self)
            return
        trainable = "dataset" in path.replace("\\", "/").split("/")
        note = ("Use Tools > Training Samples & Retrain to update the classifier."
                if trainable else
                "Saved for later. The main person/vehicle/animal detector is not "
                "retrained in the app.")
        wx.MessageBox("Saved %s\n\n%s" % (path, note), "Sample saved", wx.OK, self)
        self.EndModal(wx.ID_OK)


class TrainingSamplesDialog(wx.Dialog):
    """Per-label sample counts and a background "Retrain now"."""

    def __init__(self, parent):
        wx.Dialog.__init__(self, parent, -1, "Training Samples & Retrain",
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, -1, "Folder: %s" % ts.training_root()), 0, wx.ALL, 8)
        self._list = wx.ListCtrl(self, -1, size=(420, 220), style=wx.LC_REPORT)
        self._list.InsertColumn(0, "Label folder", width=300)
        self._list.InsertColumn(1, "Samples", width=90)
        sizer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        note = wx.StaticText(self, -1, _kNote)
        note.Wrap(420)
        sizer.Add(note, 0, wx.ALL, 8)
        self._status = wx.StaticText(self, -1, "")
        sizer.Add(self._status, 0, wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self._retrain = wx.Button(self, -1, "Retrain now")
        row.Add(self._retrain, 0, wx.RIGHT, 8)
        row.Add(wx.Button(self, wx.ID_CLOSE, "Close"), 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizerAndFit(sizer)
        self.CenterOnParent()
        self._retrain.Bind(wx.EVT_BUTTON, self.OnRetrain)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        self._refresh()

    def _refresh(self):
        self._list.DeleteAllItems()
        for name, count in sorted(ts.sample_counts().items()):
            index = self._list.InsertItem(self._list.GetItemCount(), name)
            self._list.SetItem(index, 1, str(count))

    def OnRetrain(self, event=None):
        self._retrain.Disable()
        self._status.SetLabel("Training in the background...")

        def work():
            try:
                counts = ts.retrain_carrier_model()
                message = ("Model saved; the backend loads it within ~15 s. Used: %s"
                           % ", ".join("%s=%d" % kv for kv in counts.items() if kv[1]))
            except Exception as exc:
                message = "Retrain failed: %s" % exc
            wx.CallAfter(self._done, message)

        threading.Thread(target=work, name="carrier-retrain", daemon=True).start()

    def _done(self, message):
        if not self:
            return
        self._status.SetLabel(message)
        self._status.Wrap(420)
        self._retrain.Enable()
        self._refresh()
        self.Fit()
