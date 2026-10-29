"""Dialogs for "Mark as Missed / Wrong Label" and "Training Samples & Retrain"."""

import threading
import time

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
    """Per-label New/Trained counts, retrain status, auto-retrain settings."""

    def __init__(self, parent):
        wx.Dialog.__init__(self, parent, -1, "Training Samples & Retrain",
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._frame = parent
        self._busy = False
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, -1, "Folder: %s" % ts.training_root()), 0, wx.ALL, 8)
        self._list = wx.ListCtrl(self, -1, size=(520, 220), style=wx.LC_REPORT)
        for col, (name, width) in enumerate((("Label folder", 230), ("New", 60),
                                             ("Trained", 70), ("Used by retrain", 140))):
            self._list.InsertColumn(col, name, width=width)
        sizer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        note = wx.StaticText(self, -1, _kNote + " Samples are kept after training: every "
                             "retrain uses all of them, so deleting would make the model forget.")
        note.Wrap(520)
        sizer.Add(note, 0, wx.ALL, 8)
        self._last = wx.StaticText(self, -1, "")
        sizer.Add(self._last, 0, wx.LEFT | wx.RIGHT, 8)
        self._status = wx.StaticText(self, -1, "")
        sizer.Add(self._status, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self._gauge = wx.Gauge(self, -1, 100, size=(520, 10))
        self._gauge.Hide()
        sizer.Add(self._gauge, 0, wx.EXPAND | wx.ALL, 8)

        settings = ts.get_settings()
        box = wx.StaticBoxSizer(wx.VERTICAL, self, "Automatic retrain")
        self._auto = wx.CheckBox(box.GetStaticBox(), -1, "Retrain automatically when there are new samples")
        self._auto.SetValue(bool(settings["auto_enabled"]))
        box.Add(self._auto, 0, wx.ALL, 4)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(box.GetStaticBox(), -1, "Nightly at"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._hour = wx.SpinCtrl(box.GetStaticBox(), -1, min=0, max=23, initial=int(settings["auto_hour"]), size=(60, -1))
        self._minute = wx.SpinCtrl(box.GetStaticBox(), -1, min=0, max=59, initial=int(settings["auto_minute"]), size=(60, -1))
        row.Add(self._hour, 0, wx.RIGHT, 2)
        row.Add(wx.StaticText(box.GetStaticBox(), -1, ":"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 2)
        row.Add(self._minute, 0, wx.RIGHT, 12)
        row.Add(wx.StaticText(box.GetStaticBox(), -1, "or soon after"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._threshold = wx.SpinCtrl(box.GetStaticBox(), -1, min=0, max=500, initial=int(settings["threshold"]), size=(60, -1))
        row.Add(self._threshold, 0, wx.RIGHT, 4)
        row.Add(wx.StaticText(box.GetStaticBox(), -1, "new samples (0 = off)"), 0, wx.ALIGN_CENTER_VERTICAL)
        box.Add(row, 0, wx.ALL, 4)
        self._rescanAfter = wx.CheckBox(box.GetStaticBox(), -1, "Re-scan the last 7 days of clips after an automatic retrain")
        self._rescanAfter.SetValue(bool(settings["rescan_after"]))
        box.Add(self._rescanAfter, 0, wx.ALL, 4)
        sizer.Add(box, 0, wx.EXPAND | wx.ALL, 8)
        for ctrl in (self._auto, self._rescanAfter):
            ctrl.Bind(wx.EVT_CHECKBOX, self._saveSettings)
        for ctrl in (self._hour, self._minute, self._threshold):
            ctrl.Bind(wx.EVT_SPINCTRL, self._saveSettings)
            ctrl.Bind(wx.EVT_TEXT, self._saveSettings)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self._delete = wx.Button(self, -1, "Delete sample(s)...")
        self._rescan = wx.Button(self, -1, "Re-scan Clips...")
        self._retrain = wx.Button(self, -1, "Retrain now")
        row.Add(self._delete, 0, wx.RIGHT, 8)
        row.Add(self._rescan, 0, wx.RIGHT, 8)
        row.Add(self._retrain, 0, wx.RIGHT, 8)
        row.Add(wx.Button(self, wx.ID_CLOSE, "Close"), 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizerAndFit(sizer)
        self.CenterOnParent()
        self._retrain.Bind(wx.EVT_BUTTON, self.OnRetrain)
        self._delete.Bind(wx.EVT_BUTTON, self.OnDelete)
        self._rescan.Bind(wx.EVT_BUTTON, self.OnRescan)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        self._pulse = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self._gauge.Pulse(), self._pulse)
        self._refresh()

    def _saveSettings(self, event=None):
        ts.save_settings({
            "auto_enabled": self._auto.GetValue(),
            "auto_hour": self._hour.GetValue(),
            "auto_minute": self._minute.GetValue(),
            "threshold": self._threshold.GetValue(),
            "rescan_after": self._rescanAfter.GetValue(),
        })
        if event is not None:
            event.Skip()

    def _refresh(self):
        self._list.DeleteAllItems()
        for name, new, trained, trainable in ts.label_status():
            index = self._list.InsertItem(self._list.GetItemCount(), name)
            self._list.SetItem(index, 1, str(new))
            self._list.SetItem(index, 2, str(trained) if trainable else "-")
            self._list.SetItem(index, 3, "truck-type classifier" if trainable else "stored only")
        state = ts.load_state()
        if state.get("last_trained"):
            text = "Last trained: %s - %s" % (_friendlyTime(state["last_trained"]), state.get("last_result", ""))
            if state.get("last_result", "").startswith("failed"):
                text = "Last successful train: %s. Last attempt %s %s" % (
                    _friendlyTime(state["last_trained"]), _friendlyTime(state.get("last_attempt", "")),
                    state.get("last_result", ""))
        elif state.get("last_attempt"):
            text = "Never trained successfully. Last attempt %s %s" % (
                _friendlyTime(state["last_attempt"]), state.get("last_result", ""))
        else:
            text = "Last trained: never"
        self._last.SetLabel(text)
        self._last.Wrap(520)
        ok, reason = ts.can_retrain()
        self._retrain.Enable(ok and not self._busy)
        self._retrain.SetToolTip(reason or "Train the truck-type classifier on all samples now.")
        if not ok and not self._busy and not self._status.GetLabel():
            self._setStatus(reason, wx.Colour(110, 110, 110))

    def _setStatus(self, text, colour):
        self._status.SetForegroundColour(colour)
        self._status.SetLabel(text)
        self._status.Wrap(520)
        self.Layout()

    def OnRetrain(self, event=None):
        self._busy = True
        self._retrain.Disable()
        self._setStatus("Training...", wx.Colour(0, 0, 160))
        self._gauge.Show()
        self._pulse.Start(80)
        self.Layout()

        def work():
            try:
                counts = ts.retrain_carrier_model()
                ok, message = True, "Retrained successfully at %s using %d samples; new model active (loaded within ~15 s)." % (
                    time.strftime("%I:%M %p").lstrip("0"), sum(counts.values()))
                try:
                    wx.CallAfter(self._setStatusIfAlive, message + " Re-scanning source clips...")
                    message += " " + rescanSampleSources(self._frame)
                except Exception as exc:
                    message += " Re-scan of source clips failed: %s" % exc
            except Exception as exc:
                ok, message = False, "Retrain failed: %s (previous model kept)" % exc
            wx.CallAfter(self._done, ok, message)

        threading.Thread(target=work, name="carrier-retrain", daemon=True).start()

    def _setStatusIfAlive(self, text):
        if self:
            self._setStatus(text, wx.Colour(0, 0, 160))

    def _done(self, ok, message):
        if not self:
            return
        self._busy = False
        self._pulse.Stop()
        self._gauge.Hide()
        self._setStatus(message, wx.Colour(0, 130, 0) if ok else wx.Colour(190, 0, 0))
        self._refresh()
        self.Fit()

    def OnDelete(self, event=None):
        index = self._list.GetFirstSelected()
        if index < 0:
            wx.MessageBox("Select a label folder in the list first.", "Delete samples", wx.OK, self)
            return
        folder = self._list.GetItemText(index, 0)
        files = ts.list_samples(folder)
        if not files:
            return
        dlg = wx.MultiChoiceDialog(self, "Choose bad samples to delete from %s:" % folder,
                                   "Delete samples", [f.split("/")[-1] for f in files])
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            chosen = [files[i] for i in dlg.GetSelections()]
        finally:
            dlg.Destroy()
        if chosen and wx.MessageBox("Delete %d sample(s)? This cannot be undone." % len(chosen),
                                    "Delete samples", wx.YES_NO | wx.ICON_WARNING, self) == wx.YES:
            ts.delete_samples(chosen)
            self._refresh()

    def OnRescan(self, event=None):
        showRescanDialog(self._frame, None, parent=self)


def _friendlyTime(iso):
    try:
        stamp = time.strptime(iso, "%Y-%m-%dT%H:%M:%S")
        return time.strftime("%b %d %I:%M %p", stamp).replace(" 0", " ")
    except (TypeError, ValueError):
        return str(iso or "")


def rescanPaths(frame):
    """(objDb, clipDb, videoDir) for the running app."""
    import os

    from appCommon.CommonStrings import kClipDbFile

    dataMgr = frame._dataManager
    objDb = dataMgr._curDbPath
    return objDb, os.path.join(os.path.dirname(objDb), kClipDbFile), dataMgr._vidStoragePath


def selectedObjectIds(frame):
    """Object ids for the selected search results (may be empty)."""
    try:
        model = frame._searchView._resultsModel
        clips = model.getMatchingClips()
        ids = []
        for num in model.getSelectedIds():
            for obj in getattr(clips[num], "objList", None) or []:
                ids.append(int(obj[0] if isinstance(obj, (tuple, list)) else obj))
        return ids
    except Exception:
        return []


def showRescanDialog(frame, objIds, parent=None):
    dlg = RescanDialog(parent or frame, frame, objIds)
    try:
        dlg.ShowModal()
    finally:
        dlg.Destroy()


def rescanSampleSources(frame):
    """After a successful retrain: re-scan the clips the samples came from.

    Returns a short result line (empty when there was nothing to scan).
    """
    from backEnd.rescan import rescan_sample_sources

    objDb, clipDb, videoDir = rescanPaths(frame)
    result = rescan_sample_sources(objDb, clipDb, videoDir, ts.read_samples(), pause_s=0.01)
    wx.CallAfter(refreshSearch, frame)
    return sourceRescanLine(result)


def sourceRescanLine(result):
    if result.error and not result.scanned:
        return "Re-scan of source clips failed: %s" % result.error
    return "Re-scanned %d source clip%s (%d label%s changed)." % (
        result.clips, "" if result.clips == 1 else "s", result.changed, "" if result.changed == 1 else "s")


def refreshSearch(frame):
    try:
        frame._searchView.OnRefresh()
    except Exception:
        pass


class RescanDialog(wx.Dialog):
    """Re-classify recorded vehicles with the current truck-type classifier."""

    def __init__(self, parent, frame, objIds=None):
        import datetime

        import wx.adv

        wx.Dialog.__init__(self, parent, -1, "Re-scan Clips")
        self._frame = frame
        self._objIds = list(objIds or [])
        self._cancel = threading.Event()
        self._running = False
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, -1,
                  "Re-run the truck-type classifier (carriers, garbage truck, bus, school bus)\n"
                  "on stored vehicle boxes. No full re-detection; Search refreshes when done."),
                  0, wx.ALL, 8)
        choices = ["Selected search results (%d objects)" % len(self._objIds), "One camera", "All cameras"]
        self._scope = wx.RadioBox(self, -1, "Scope", choices=choices, majorDimension=1)
        self._scope.SetSelection(0 if self._objIds else 2)
        self._scope.EnableItem(0, bool(self._objIds))
        sizer.Add(self._scope, 0, wx.EXPAND | wx.ALL, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self, -1, "Camera:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        cams = []
        try:
            cams = sorted(frame._dataManager.getCameraLocations()) if hasattr(
                frame._dataManager, "getCameraLocations") else []
        except Exception:
            cams = []
        if not cams:
            cams = self._camerasFromDb()
        self._camera = wx.Choice(self, -1, choices=cams)
        if cams:
            self._camera.SetSelection(0)
        row.Add(self._camera, 1)
        sizer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        today = wx.DateTime.Now()
        start = wx.DateTime.Now()
        start.Subtract(wx.DateSpan(days=7))
        row.Add(wx.StaticText(self, -1, "From"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._from = wx.adv.DatePickerCtrl(self, -1, start)
        row.Add(self._from, 0, wx.RIGHT, 8)
        row.Add(wx.StaticText(self, -1, "to"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._to = wx.adv.DatePickerCtrl(self, -1, today)
        row.Add(self._to, 0)
        sizer.Add(row, 0, wx.ALL, 8)
        self._dateHint = wx.StaticText(self, -1, "(Dates apply to camera / all-cameras scope.)")
        sizer.Add(self._dateHint, 0, wx.LEFT, 8)
        self._gauge = wx.Gauge(self, -1, 100, size=(420, 14))
        sizer.Add(self._gauge, 0, wx.EXPAND | wx.ALL, 8)
        self._status = wx.StaticText(self, -1, "")
        sizer.Add(self._status, 0, wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self._start = wx.Button(self, -1, "Start")
        self._stop = wx.Button(self, wx.ID_CANCEL, "Close")
        row.Add(self._start, 0, wx.RIGHT, 8)
        row.Add(self._stop, 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizerAndFit(sizer)
        self.CenterOnParent()
        self._start.Bind(wx.EVT_BUTTON, self.OnStart)
        self.Bind(wx.EVT_BUTTON, self.OnClose, id=wx.ID_CANCEL)
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self._datetime = datetime

    def _camerasFromDb(self):
        import sqlite3

        try:
            objDb, _clipDb, _vid = rescanPaths(self._frame)
            conn = sqlite3.connect(objDb, timeout=5)
            try:
                return [r[0] for r in conn.execute("SELECT DISTINCT camLoc FROM objects WHERE type='vehicle'")]
            finally:
                conn.close()
        except Exception:
            return []

    def _rangeMs(self):
        def ms(picker, endOfDay):
            value = picker.GetValue()
            dt = self._datetime.datetime(value.GetYear(), value.GetMonth() + 1, value.GetDay())
            if endOfDay:
                dt += self._datetime.timedelta(days=1)
            return int(time.mktime(dt.timetuple()) * 1000) - (1 if endOfDay else 0)
        return ms(self._from, False), ms(self._to, True)

    def OnStart(self, event=None):
        from backEnd.rescan import run_rescan

        scope = self._scope.GetSelection()
        objIds, cameras = None, None
        startMs, endMs = self._rangeMs()
        if scope == 0:
            objIds = self._objIds
            startMs, endMs = 0, 2 ** 62
        elif scope == 1:
            cameras = [self._camera.GetStringSelection()]
        objDb, clipDb, videoDir = rescanPaths(self._frame)
        self._cancel.clear()
        self._running = True
        self._start.Disable()
        self._stop.SetLabel("Cancel")
        self._status.SetLabel("Re-scanning...")

        def progress(done, total):
            wx.CallAfter(self._progress, done, total)

        def work():
            result = run_rescan(objDb, clipDb, videoDir, startMs, endMs, cameras, objIds,
                                progress=progress, cancel=self._cancel)
            wx.CallAfter(self._done, result)

        threading.Thread(target=work, name="vehicle-rescan", daemon=True).start()

    def _progress(self, done, total):
        if not self:
            return
        self._gauge.SetRange(max(1, total))
        self._gauge.SetValue(min(done, max(1, total)))
        self._status.SetLabel("Re-scanning... %d of %d vehicle objects" % (done, total))

    def _done(self, result):
        refreshSearch(self._frame)
        if not self:
            return
        self._running = False
        self._start.Enable()
        self._stop.SetLabel("Close")
        verb = "Cancelled" if result.cancelled else "Done"
        text = "%s: %d vehicle objects, %d re-classified, %d labels changed, %d skipped." % (
            verb, result.objects, result.scanned, result.changed, result.skipped)
        if result.error:
            text += " Error: %s" % result.error
        self._status.SetLabel(text)
        self.Fit()

    def OnClose(self, event=None):
        if self._running:
            self._cancel.set()
            self._status.SetLabel("Cancelling...")
            return
        self.EndModal(wx.ID_CANCEL)
