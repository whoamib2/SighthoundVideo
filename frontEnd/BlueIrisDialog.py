"""Dialog that connects to a Blue Iris server and picks one of its cameras."""

import threading

import wx

from frontEnd.PasswordCtrl import PasswordCtrl, makeShowPasswordToggle

from appCommon import BlueIris
from appCommon import BlueIrisServers


class BlueIrisDialog(wx.Dialog):
    """Asks for the Blue Iris address and login, lists its cameras and
    builds the stream URL. ``getResult()`` returns (url, cameraName)."""

    def __init__(self, parent, servers=None, backEndClient=None, cameras=None, storePath=None):
        wx.Dialog.__init__(self, parent, -1, "Add a Blue Iris camera",
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._result = None
        self._cams = []
        self._busy = False
        self._backEndClient = backEndClient
        self._cameras = list(cameras or [])   # (uri, extra) of configured cameras
        self._storePath = storePath
        self._servers = []
        self._extras = {}

        intro = wx.StaticText(self, -1,
            "Enter your Blue Iris web server address and a Blue Iris user. "
            "Use \"Find servers\" to search this network.")
        intro.Wrap(420)
        self._hostCtrl = wx.ComboBox(self, -1, "", style=wx.CB_DROPDOWN)
        self._portCtrl = wx.TextCtrl(self, -1, "81", size=(60, -1))
        self._findBtn = wx.Button(self, -1, "Find servers")
        self._forgetBtn = wx.Button(self, -1, "Forget this server")
        self._userCtrl = wx.TextCtrl(self, -1, "")
        self._passCtrl = PasswordCtrl(self, -1)
        self._showPass = makeShowPasswordToggle(self, self._passCtrl)
        self._connectBtn = wx.Button(self, -1, "Connect")
        self._status = wx.StaticText(self, -1, " ")
        self._camList = wx.ListBox(self, -1, size=(-1, 140))
        self._streamCtrl = wx.RadioBox(self, -1, "Stream",
            choices=["RTSP (H.264, recommended)", "MJPEG"],
            majorDimension=2, style=wx.RA_SPECIFY_COLS)

        grid = wx.FlexGridSizer(0, 2, 6, 6)
        grid.AddGrowableCol(1)
        hostRow = wx.BoxSizer(wx.HORIZONTAL)
        hostRow.Add(self._hostCtrl, 1, wx.EXPAND)
        hostRow.Add(wx.StaticText(self, -1, "Port:"), 0,
                    wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        hostRow.Add(self._portCtrl, 0, wx.LEFT, 4)
        hostRow.Add(self._findBtn, 0, wx.LEFT, 8)
        hostRow.Add(self._forgetBtn, 0, wx.LEFT, 4)
        grid.Add(wx.StaticText(self, -1, "Server:"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(hostRow, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, -1, "User name:"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self._userCtrl, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, -1, "Password:"), 0, wx.ALIGN_CENTER_VERTICAL)
        passRow = wx.BoxSizer(wx.HORIZONTAL)
        passRow.Add(self._passCtrl, 1, wx.EXPAND)
        passRow.Add(self._connectBtn, 0, wx.LEFT, 8)
        grid.Add(passRow, 1, wx.EXPAND)
        grid.AddSpacer(0)
        grid.Add(self._showPass, 0)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(intro, 0, wx.ALL, 10)
        sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(self._status, 0, wx.EXPAND | wx.ALL, 10)
        sizer.Add(wx.StaticText(self, -1, "Camera:"), 0, wx.LEFT, 10)
        sizer.Add(self._camList, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(self._streamCtrl, 0, wx.EXPAND | wx.ALL, 10)
        btns = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(btns, 0, wx.EXPAND | wx.ALL, 10)
        self.SetSizerAndFit(sizer)
        self.SetMinSize((480, -1))
        self._okBtn = self.FindWindowById(wx.ID_OK)
        self._okBtn.Enable(False)

        self._findBtn.Bind(wx.EVT_BUTTON, self.OnFind)
        self._connectBtn.Bind(wx.EVT_BUTTON, self.OnConnect)
        self._forgetBtn.Bind(wx.EVT_BUTTON, self.OnForget)
        self._hostCtrl.Bind(wx.EVT_COMBOBOX, self.OnPickServer)
        self._camList.Bind(wx.EVT_LISTBOX, self.OnPickCam)
        self._camList.Bind(wx.EVT_LISTBOX_DCLICK, self.OnOk)
        self._okBtn.Bind(wx.EVT_BUTTON, self.OnOk)

        saved = BlueIrisServers.load_servers(self._storePath)
        self._setServers([(sv["host"], sv["port"]) for sv in saved] + list(servers or []))
        self._updateForget()
        self.CenterOnParent()

    def getResult(self):
        return self._result

    def getExtras(self):
        """Camera extras referencing the saved server (for password updates)."""
        return dict(self._extras)

    # --- servers -----------------------------------------------------------
    def _setServers(self, servers):
        seen, merged = set(), []
        for host, port in list(servers):
            key = BlueIrisServers.server_id(host, port)
            if key not in seen:
                seen.add(key)
                merged.append((host, int(port)))
        self._servers = merged
        self._hostCtrl.Set(["%s:%d" % hp for hp in self._servers])
        if self._servers and not self._hostCtrl.GetValue().strip():
            self._hostCtrl.SetSelection(0)
            self.OnPickServer()

    def _savedFor(self, host, port):
        try:
            return BlueIrisServers.get_server(BlueIrisServers.server_id(host, port), self._storePath)
        except (TypeError, ValueError):
            return None

    def _updateForget(self):
        host, port, _u, _p = self._fields()
        self._forgetBtn.Enable(bool(host and port and self._savedFor(host, port)))

    def OnPickServer(self, event=None):
        value = self._hostCtrl.GetValue()
        if value.count(":") == 1:
            host, port = value.split(":")
            self._hostCtrl.SetValue(host)
            self._portCtrl.SetValue(port)
        host, port, _u, _p = self._fields()
        saved = self._savedFor(host, port) if host and port else None
        self._updateForget()
        if saved:
            self._userCtrl.SetValue(saved.get("user", ""))
            self._passCtrl.SetValue(BlueIrisServers.get_password(saved["id"], self._storePath))
            self._streamCtrl.SetSelection(1 if saved.get("stream") == BlueIris.kSchemeMjpeg else 0)
            wx.CallAfter(self.OnConnect)

    def OnForget(self, event=None):
        host, port, _u, _p = self._fields()
        saved = self._savedFor(host, port) if host and port else None
        if not saved:
            return
        if wx.MessageBox("Forget %s:%d and its saved password?" % (saved["host"], saved["port"]),
                         "Forget this server", wx.YES_NO | wx.ICON_QUESTION, self) != wx.YES:
            return
        BlueIrisServers.forget_server(saved["id"], self._storePath)
        self._passCtrl.SetValue("")
        self._servers = [hp for hp in self._servers
                         if BlueIrisServers.server_id(*hp) != saved["id"]]
        self._hostCtrl.Set(["%s:%d" % hp for hp in self._servers])
        self._hostCtrl.SetValue(host)
        self._status.SetLabel("Forgot %s:%d." % (saved["host"], saved["port"]))
        self._updateForget()

    def OnFind(self, event=None):
        if self._busy:
            return
        self._setBusy(True, "Searching this network for Blue Iris...")

        def work():
            try:
                found = BlueIris.discoverServers()
            except Exception:
                found = []
            wx.CallAfter(self._findDone, found)
        threading.Thread(target=work, daemon=True).start()

    def _findDone(self, found):
        if not self:
            return
        self._setBusy(False)
        if found:
            self._setServers(found)
            self._status.SetLabel("Found %d Blue Iris server(s)." % len(found))
        else:
            self._status.SetLabel("No Blue Iris server answered. Type its "
                                  "address and port (usually 81).")

    # --- login / cameras ---------------------------------------------------
    def _fields(self):
        host = self._hostCtrl.GetValue().strip()
        try:
            port = int(self._portCtrl.GetValue().strip())
        except ValueError:
            port = None
        return host, port, self._userCtrl.GetValue(), self._passCtrl.GetValue()

    def OnConnect(self, event=None):
        if self._busy:
            return
        host, port, user, password = self._fields()
        if not host or not port:
            self._status.SetLabel("Enter the server address and port.")
            return
        self._setBusy(True, "Connecting to Blue Iris...")

        def work():
            try:
                client = BlueIris.BlueIrisClient(host, port, user, password)
                name = client.login()
                cams = client.cameras()
                wx.CallAfter(self._connectDone, name, cams, None)
            except BlueIris.BlueIrisError as e:
                wx.CallAfter(self._connectDone, "", [], str(e))
            except Exception as e:  # never show credentials, only the type
                wx.CallAfter(self._connectDone, "", [],
                             "Blue Iris error (%s)." % type(e).__name__)
        threading.Thread(target=work, daemon=True).start()

    def _connectDone(self, name, cams, error):
        if not self:
            return
        self._setBusy(False)
        self._cams = cams
        host, port, user, password = self._fields()
        added = BlueIrisServers.added_short_names(self._cameras, host, port) if port else set()
        self._camList.Set(["%s  (%s)%s%s" % (c["name"], c["shortName"],
                           "" if c["enabled"] else "  - disabled",
                           "  - already added" if c["shortName"] in added else "")
                           for c in cams])
        if not error:
            self._saveServer(host, port, user, password)
        if error:
            self._status.SetLabel(error)
        elif not cams:
            self._status.SetLabel("Connected, but Blue Iris has no cameras.")
        else:
            self._status.SetLabel("Connected to %s. Pick a camera."
                                  % (name or "Blue Iris"))
            fresh = [i for i, c in enumerate(cams) if c["shortName"] not in added]
            self._camList.SetSelection(fresh[0] if fresh else 0)
        self.OnPickCam()

    def _stream(self):
        return BlueIris.kSchemeMjpeg if self._streamCtrl.GetSelection() == 1 else BlueIris.kSchemeRtsp

    def _saveServer(self, host, port, user, password):
        try:
            sid, changed = BlueIrisServers.save_server(host, port, user, password, self._stream(),
                                                       self._storePath)
        except Exception as e:  # never include the password
            self._status.SetLabel("Connected, but the server could not be saved (%s)." % type(e).__name__)
            return
        if (host, int(port)) not in self._servers:
            self._servers.insert(0, (host, int(port)))
        self._updateForget()
        if changed and self._backEndClient is not None:
            users = [x for x in self._cameras if (x[1] or {}).get(BlueIrisServers.EXTRA_SERVER) == sid]
            if users and wx.MessageBox(
                    "The password for %s changed. Update %d camera(s) added from this server?"
                    % (sid, len(users)), "Blue Iris", wx.YES_NO | wx.ICON_QUESTION, self) == wx.YES:
                try:
                    n = BlueIrisServers.update_cameras_for_server(self._backEndClient, sid, self._storePath)
                    self._status.SetLabel("Updated %d camera(s)." % n)
                except Exception as e:
                    self._status.SetLabel("Could not update cameras (%s)." % type(e).__name__)

    def OnPickCam(self, event=None):
        self._okBtn.Enable(self._camList.GetSelection() != wx.NOT_FOUND)

    def OnOk(self, event=None):
        sel = self._camList.GetSelection()
        if sel == wx.NOT_FOUND or sel >= len(self._cams):
            return
        host, port, user, password = self._fields()
        cam = self._cams[sel]
        scheme = self._stream()
        try:
            url = BlueIris.buildStreamUrl(scheme, host, port, cam["shortName"],
                                          user, password)
        except ValueError as e:
            self._status.SetLabel(str(e))
            return
        self._result = (url, cam["name"])
        self._extras = BlueIrisServers.camera_extras(BlueIrisServers.server_id(host, port),
                                                     cam["shortName"], scheme)
        try:  # remember the stream choice too
            BlueIrisServers.save_server(host, port, user, password, scheme, self._storePath)
        except Exception:
            pass
        self.EndModal(wx.ID_OK)

    def _setBusy(self, busy, message=None):
        self._busy = busy
        for c in (self._findBtn, self._connectBtn, self._forgetBtn):
            c.Enable(not busy)
        if message:
            self._status.SetLabel(message)
