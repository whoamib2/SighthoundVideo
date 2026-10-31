"""Tools > Community Training...: opt-in sample sharing and shared-model updates."""

import threading

import wx

from backEnd import community


class CommunityDialog(wx.Dialog):
    def __init__(self, parent):
        wx.Dialog.__init__(self, parent, -1, "Community Training",
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        s = community.get_settings()
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._share = wx.CheckBox(self, -1, "Share my labeled training samples to improve VigilCam for everyone")
        self._share.SetValue(s["share_enabled"])
        sizer.Add(self._share, 0, wx.ALL, 8)
        privacy = wx.StaticText(self, -1, community.PRIVACY_TEXT)
        privacy.Wrap(560)
        sizer.Add(privacy, 0, wx.LEFT | wx.RIGHT, 8)
        self._crops = wx.CheckBox(self, -1, "Share the cropped object only (not the full frame)")
        self._crops.SetValue(s["crops_only"])
        self._strip = wx.CheckBox(self, -1, "Remove camera name and time (send only image, label, and box)")
        self._strip.SetValue(s["strip_metadata"])
        sizer.Add(self._crops, 0, wx.LEFT | wx.TOP, 8)
        sizer.Add(self._strip, 0, wx.LEFT | wx.TOP, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self._queue = wx.ListBox(self, -1, size=(260, 150))
        self._preview = wx.StaticBitmap(self, -1, wx.Bitmap(150, 150))
        row.Add(self._queue, 1, wx.EXPAND | wx.RIGHT, 8)
        row.Add(self._preview, 0)
        sizer.Add(wx.StaticText(self, -1, "Waiting to upload (exactly what will be sent):"), 0, wx.LEFT | wx.TOP, 8)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self._remove = wx.Button(self, -1, "Don't send selected")
        self._upload = wx.Button(self, -1, "Upload now")
        self._revoke = wx.Button(self, -1, "Revoke and request deletion...")
        for b in (self._remove, self._upload, self._revoke):
            row.Add(b, 0, wx.RIGHT, 6)
        sizer.Add(row, 0, wx.LEFT, 8)

        box = wx.StaticBoxSizer(wx.VERTICAL, self, "Shared model updates")
        self._mode = wx.RadioBox(box.GetStaticBox(), -1, "", choices=[
            "Install signed updates automatically", "Notify me only", "Off"], majorDimension=1)
        self._mode.SetSelection({"auto": 0, "notify": 1, "off": 2}.get(s["update_mode"], 1))
        box.Add(self._mode, 0, wx.ALL, 4)
        note = wx.StaticText(box.GetStaticBox(), -1,
                             "Your own retrained classifier always takes precedence over the shared one. "
                             "A shared detector applies after VigilCam restarts.")
        note.Wrap(540)
        box.Add(note, 0, wx.ALL, 4)
        self._modelStatus = wx.StaticText(box.GetStaticBox(), -1, "")
        box.Add(self._modelStatus, 0, wx.ALL, 4)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self._check = wx.Button(box.GetStaticBox(), -1, "Check now")
        self._install = wx.Button(box.GetStaticBox(), -1, "Install available")
        self._rollback = wx.Button(box.GetStaticBox(), -1, "Roll back classifier")
        for b in (self._check, self._install, self._rollback):
            row.Add(b, 0, wx.RIGHT, 6)
        box.Add(row, 0, wx.ALL, 4)
        sizer.Add(box, 0, wx.EXPAND | wx.ALL, 8)

        grid = wx.FlexGridSizer(0, 2, 4, 4)
        grid.AddGrowableCol(1)
        self._uploadUrl = wx.TextCtrl(self, -1, s["upload_url"], size=(380, -1))
        self._manifestUrl = wx.TextCtrl(self, -1, s["manifest_url"], size=(380, -1))
        grid.Add(wx.StaticText(self, -1, "Upload endpoint:"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self._uploadUrl, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, -1, "Model manifest:"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self._manifestUrl, 1, wx.EXPAND)
        sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 8)
        self._status = wx.StaticText(self, -1, "")
        sizer.Add(self._status, 0, wx.ALL, 8)
        sizer.Add(self.CreateButtonSizer(wx.CLOSE), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizerAndFit(sizer)
        self.CenterOnParent()

        for c in (self._share, self._crops, self._strip):
            c.Bind(wx.EVT_CHECKBOX, self._save)
        self._mode.Bind(wx.EVT_RADIOBOX, self._save)
        self._uploadUrl.Bind(wx.EVT_KILL_FOCUS, self._save)
        self._manifestUrl.Bind(wx.EVT_KILL_FOCUS, self._save)
        self._queue.Bind(wx.EVT_LISTBOX, self._showPreview)
        self._remove.Bind(wx.EVT_BUTTON, self.OnRemove)
        self._upload.Bind(wx.EVT_BUTTON, self.OnUpload)
        self._revoke.Bind(wx.EVT_BUTTON, self.OnRevoke)
        self._check.Bind(wx.EVT_BUTTON, lambda e: self._background(lambda: community.check_updates(), "Checking..."))
        self._install.Bind(wx.EVT_BUTTON, lambda e: self._background(
            lambda: community.apply_updates(force=True), "Installing..."))
        self._rollback.Bind(wx.EVT_BUTTON, self.OnRollback)
        self.Bind(wx.EVT_BUTTON, lambda e: (self._save(), self.EndModal(wx.ID_CLOSE)), id=wx.ID_CLOSE)
        self._refresh()

    def _save(self, event=None):
        community.save_settings({
            "share_enabled": self._share.GetValue(),
            "crops_only": self._crops.GetValue(),
            "strip_metadata": self._strip.GetValue(),
            "update_mode": ("auto", "notify", "off")[self._mode.GetSelection()],
            "upload_url": self._uploadUrl.GetValue().strip() or community.DEFAULT_UPLOAD_URL,
            "manifest_url": self._manifestUrl.GetValue().strip() or community.DEFAULT_MANIFEST_URL,
        })
        if event is not None:
            event.Skip()
        self._refresh()

    def _refresh(self):
        self._items = community.queued_samples()
        self._queue.Set(["%s  %s" % (m.get("label"), m["id"][:8]) for _i, m, _p in self._items])
        state = community.load_state()
        installed = state.get("installed") or {}
        check = state.get("last_check") or {}
        text = "Installed shared models: %s" % (", ".join(
            "%s %s" % (k, v.get("version")) for k, v in installed.items()) or "none")
        if check:
            text += "\nLast check %s: %s" % (check.get("at", ""), check.get("error") or (
                "available %s%s" % (check.get("available") or "none",
                                     "" if check.get("signed") else " (%s)" % check.get("reason", ""))))
        last = state.get("last_upload") or {}
        if last:
            text += "\nLast upload %s: %d sent, %d queued%s" % (
                last.get("at", ""), last.get("uploaded", 0), last.get("remaining", 0),
                (" - " + last["error"]) if last.get("error") else "")
        self._modelStatus.SetLabel(text)
        self._modelStatus.Wrap(540)
        self.Layout()

    def _showPreview(self, event=None):
        index = self._queue.GetSelection()
        if index < 0:
            return
        image = wx.Image(self._items[index][2])
        if image.IsOk():
            w, h = image.GetSize()
            scale = 150.0 / max(w, h, 1)
            image = image.Scale(max(1, int(w * scale)), max(1, int(h * scale)))
            self._preview.SetBitmap(wx.Bitmap(image))
            self.Layout()

    def OnRemove(self, event=None):
        index = self._queue.GetSelection()
        if index >= 0:
            community.remove_queued([self._items[index][0]])
            self._refresh()

    def OnUpload(self, event=None):
        self._background(lambda: community.upload_pending(), "Uploading...")

    def OnRevoke(self, event=None):
        if wx.MessageBox("Turn sharing off, discard queued samples, and ask vigilcam.app to delete "
                         "everything uploaded from this computer?", "Revoke sharing",
                         wx.YES_NO | wx.ICON_QUESTION, self) != wx.YES:
            return
        self._share.SetValue(False)
        self._background(lambda: community.revoke_and_request_deletion(), "Revoking...")

    def OnRollback(self, event=None):
        ok = community.rollback_model("classifier")
        self._status.SetLabel("Rolled back the shared classifier." if ok else "No previous shared classifier to roll back to.")
        self._refresh()

    def _background(self, work, label):
        self._save()
        self._status.SetLabel(label)

        def run():
            try:
                result = work()
            except Exception as exc:
                result = {"error": str(exc)}
            wx.CallAfter(self._finished, result)

        threading.Thread(target=run, name="community", daemon=True).start()

    def _finished(self, result):
        if not self:
            return
        error = (result or {}).get("error")
        if error:
            self._status.SetLabel("Not reachable yet, will retry automatically: %s" % error)
        elif (result or {}).get("deletion_sent") is False:
            self._status.SetLabel("Deletion request will be retried until the server is reachable.")
        else:
            self._status.SetLabel("Done.")
        self._refresh()
