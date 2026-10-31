"""Minimal editor for the local face gallery and plate watchlist.

Embeddings and names stay in the object database on this machine. Deleting a
person removes the stored vector. Enrolling needs the face ONNX files from
``scripts/fetch_attribute_models.py``. The plate watchlist is the list plate
rules use in watchlist mode. ``*`` and ``?`` are wildcards.
"""

from __future__ import annotations

import wx


class AttributeLibraryDialog(wx.Dialog):
    """List, enroll, and delete gallery entries and watchlist patterns."""

    def __init__(self, parent, data_manager):
        super().__init__(parent, title="Face gallery and plate watchlist",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._data = data_manager
        self._name = wx.TextCtrl(self, size=(180, -1))
        self._faces = wx.ListBox(self, size=(360, 140))
        enroll = wx.Button(self, label="Enroll images...")
        enroll_track = wx.Button(self, label="Enroll object id")
        self._object_id = wx.TextCtrl(self, size=(80, -1))
        remove_face = wx.Button(self, label="Delete person")
        self._threshold = wx.TextCtrl(self, size=(60, -1))
        save_threshold = wx.Button(self, label="Save threshold")

        self._pattern = wx.TextCtrl(self, size=(180, -1))
        self._watch = wx.ListBox(self, size=(360, 100))
        add_pattern = wx.Button(self, label="Add pattern")
        remove_pattern = wx.Button(self, label="Remove pattern")
        close = wx.Button(self, wx.ID_CLOSE, "Close")

        note = wx.StaticText(
            self, label=(
                "Face embeddings are stored only in the local object database. "
                "They are not uploaded. Delete a name to remove its vector. "
                "A match uses cosine similarity. Unknown people have no identity."
            )
        )
        note.Wrap(420)

        face_row = wx.BoxSizer(wx.HORIZONTAL)
        face_row.Add(wx.StaticText(self, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        face_row.Add(self._name, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        face_row.Add(enroll, 0, wx.RIGHT, 6)
        face_row.Add(remove_face, 0)

        track_row = wx.BoxSizer(wx.HORIZONTAL)
        track_row.Add(wx.StaticText(self, label="Object id:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        track_row.Add(self._object_id, 0, wx.RIGHT, 6)
        track_row.Add(enroll_track, 0)

        threshold_row = wx.BoxSizer(wx.HORIZONTAL)
        threshold_row.Add(wx.StaticText(self, label="Cosine threshold:"), 0,
                          wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        threshold_row.Add(self._threshold, 0, wx.RIGHT, 6)
        threshold_row.Add(save_threshold, 0)

        plate_row = wx.BoxSizer(wx.HORIZONTAL)
        plate_row.Add(self._pattern, 1, wx.RIGHT, 6)
        plate_row.Add(add_pattern, 0, wx.RIGHT, 6)
        plate_row.Add(remove_pattern, 0)

        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(note, 0, wx.ALL | wx.EXPAND, 10)
        root.Add(wx.StaticText(self, label="Enrolled people"), 0, wx.LEFT | wx.RIGHT, 10)
        root.Add(self._faces, 1, wx.ALL | wx.EXPAND, 10)
        root.Add(face_row, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
        root.Add(track_row, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        root.Add(threshold_row, 0, wx.ALL, 10)
        root.Add(wx.StaticText(self, label="Plate watchlist"), 0, wx.LEFT | wx.RIGHT, 10)
        root.Add(self._watch, 1, wx.ALL | wx.EXPAND, 10)
        root.Add(plate_row, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
        root.Add(close, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        self.SetSizer(root)
        self.SetInitialSize((480, 560))

        enroll.Bind(wx.EVT_BUTTON, self._on_enroll_images)
        enroll_track.Bind(wx.EVT_BUTTON, self._on_enroll_track)
        remove_face.Bind(wx.EVT_BUTTON, self._on_delete_face)
        save_threshold.Bind(wx.EVT_BUTTON, self._on_threshold)
        add_pattern.Bind(wx.EVT_BUTTON, self._on_add_pattern)
        remove_pattern.Bind(wx.EVT_BUTTON, self._on_remove_pattern)
        close.Bind(wx.EVT_BUTTON, lambda event: self.Close())
        self._reload()

    def _reload(self):
        self._faces.Clear()
        try:
            for name, _dim, _when in self._data.listFaces():
                self._faces.Append(str(name))
            self._threshold.ChangeValue("%.2f" % float(self._data.getFaceThreshold()))
            self._watch.Clear()
            for pattern in self._data.listPlateWatchlist():
                self._watch.Append(str(pattern))
        except Exception as exc:
            wx.MessageBox(str(exc), "Gallery", wx.OK | wx.ICON_ERROR, self)

    def _selected_name(self):
        selection = self._faces.GetStringSelection()
        if selection:
            return selection
        return self._name.GetValue().strip()

    def _on_enroll_images(self, event):
        name = self._name.GetValue().strip()
        if not name:
            wx.MessageBox("Enter a name first.", "Gallery", wx.OK | wx.ICON_INFORMATION, self)
            return
        dialog = wx.FileDialog(
            self, "Face images", wildcard="Images|*.jpg;*.jpeg;*.png;*.bmp",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
        )
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        paths = dialog.GetPaths()
        dialog.Destroy()
        from backEnd.native.attribute_recognizers import enroll_image_file

        errors = []
        for path in paths:
            try:
                enroll_image_file(path, name, self._data)
            except Exception as exc:
                errors.append("%s: %s" % (path, exc))
        self._reload()
        if errors:
            wx.MessageBox("\n".join(errors), "Enrollment", wx.OK | wx.ICON_WARNING, self)

    def _on_enroll_track(self, event):
        name = self._name.GetValue().strip()
        text = self._object_id.GetValue().strip()
        if not name or not text:
            wx.MessageBox("Enter a name and an object id.", "Gallery", wx.OK | wx.ICON_INFORMATION, self)
            return
        try:
            obj_id = int(text)
        except ValueError:
            wx.MessageBox("Object id must be an integer.", "Gallery", wx.OK | wx.ICON_INFORMATION, self)
            return
        from backEnd.native.attribute_recognizers import enroll_track

        try:
            enroll_track(self._data, obj_id, name)
        except Exception as exc:
            wx.MessageBox(str(exc), "Enrollment", wx.OK | wx.ICON_ERROR, self)
            return
        self._reload()

    def _on_delete_face(self, event):
        name = self._selected_name()
        if not name:
            return
        self._data.deleteFace(name)
        self._reload()

    def _on_threshold(self, event):
        try:
            value = float(self._threshold.GetValue().strip())
        except ValueError:
            wx.MessageBox("Threshold must be a number, usually between 0.3 and 0.6.",
                          "Gallery", wx.OK | wx.ICON_INFORMATION, self)
            return
        self._data.setFaceThreshold(value)
        self._reload()

    def _on_add_pattern(self, event):
        pattern = self._pattern.GetValue().strip()
        if not pattern:
            return
        try:
            self._data.addPlateWatch(pattern)
        except Exception as exc:
            wx.MessageBox(str(exc), "Watchlist", wx.OK | wx.ICON_ERROR, self)
            return
        self._pattern.ChangeValue("")
        self._reload()

    def _on_remove_pattern(self, event):
        pattern = self._watch.GetStringSelection() or self._pattern.GetValue().strip()
        if not pattern:
            return
        self._data.removePlateWatch(pattern)
        self._reload()
