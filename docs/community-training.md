# Community training

Phase 1 (this repo) is the **client side**. The server is not built yet; the
client treats it as unreachable and keeps work queued.

## Client (VigilCam app)

Tools > **Community Training...**

- **Opt-in, off by default:** "Share my labeled training samples to improve VigilCam
  for everyone", with the privacy text from `backEnd/community.py` (`PRIVACY_TEXT`).
- **What is sent.** When sharing is on, each new Mark as Missed / Wrong Label sample
  is queued in `%LOCALAPPDATA%\VigilCam\community\queue`.
  - By default only the crop is sent, re-encoded (so no EXIF). The metadata is just the
    label, the box, the image size, and a random install id.
  - Camera name and time are included only if "Remove camera name and time" is unchecked.
  - Samples labeled before opting in are never sent.
- **Preview:** the dialog lists the queue and previews each image. "Don't send
  selected" drops one.
- **Upload:** in the background every 15 minutes (and "Upload now") to the upload
  endpoint, default `https://api.vigilcam.app/v1/samples`. It sends one multipart POST
  per sample with these fields:
  - `install_id`
  - `metadata` (JSON)
  - `image` (JPEG)

  On any failure the sample stays queued and the error is shown in the dialog.
- **Revoke and request deletion:** turns sharing off, deletes the local queue, and
  POSTs `{"install_id": ...}` to `<upload endpoint>/delete`. It is retried until the
  server answers 2xx.
- **Model updates:**
  - **Manifest:** default `https://models.vigilcam.app/manifest.json`, checked daily.
    The "auto" mode installs updates automatically, "notify" logs them and shows them
    in the dialog, and "off" disables checking.
  - **Signing:** the manifest must be Ed25519-signed with the key embedded in
    `community.MANIFEST_PUBLIC_KEY_B64`. That key is a placeholder (empty) today, so
    nothing installs automatically until a release key is embedded. "Install available"
    in the dialog is an explicit user override that still verifies sha256.
  - **Install:** each model file is downloaded and its sha256 verified. The previous
    file is kept as `*.prev.onnx` ("Roll back classifier"), then swapped atomically.
- **Precedence:**
  - **Classifier:** the user's own retrained classifier (Training Samples & Retrain)
    always wins, because it is fitted to their cameras and corrections. The shared
    classifier is used only when there is no local one. It is hot-loaded within about
    15 seconds.
  - **Detector:** a shared detector, once installed, is preferred over the bundled
    YOLOX nano and applies on the next engine restart.

### Manifest format

```json
{
  "version": "2026.11.01",
  "models": [
    {"kind": "detector",   "version": "1.1.0", "url": "https://models.vigilcam.app/yolox_nano_vc_1.1.0.onnx", "sha256": "..."},
    {"kind": "classifier", "version": "1.3.0", "url": "https://models.vigilcam.app/carrier_1.3.0.onnx",      "sha256": "..."}
  ],
  "signature": "<base64 Ed25519 over canonical JSON of everything except 'signature' (sorted keys, no spaces)>"
}
```

## Server (to build)

1. **Upload API** (`api.vigilcam.app`)
   - `POST /v1/samples`: multipart with `install_id`, `metadata`, and `image`.
   - Validation:
     - Accept JPEG only, at most about 2 MB, and a label from the known label list.
     - Rate-limit per install id and per IP.
     - Re-encode images and strip metadata again server-side.
   - `POST /v1/samples/delete`: delete every sample and derived crop for an
     `install_id`, keep a tombstone so future training excludes them, and return 2xx.
   - Store nothing else about the client: no IP is stored past the rate-limit window.
2. **Storage:** object storage (images) plus a database (id, install_id, label, box,
   received_at, review state). Deletions also purge cached dataset snapshots.
3. **Review / labeling:**
   - A queue UI where reviewers accept, fix the label or box, or reject (privacy issues
     such as faces or plates in full frames get rejected or blurred).
   - Optional consensus from several reviewers.
   - Only accepted samples enter training.
4. **Training pipeline (periodic, e.g. weekly):**
   - Build a versioned dataset snapshot from accepted samples.
   - Fine-tune YOLOX nano from the current release (full frames, for the detector).
   - Retrain the truck-type classifier (crops, `CARRIER_LABELS` order must stay
     append-only).
   - Evaluate on a fixed held-out set plus a per-class regression gate, and publish
     only if the gate passes.
   - Export ONNX with the same input/output contract as the bundled models.
5. **Publishing:**
   - Upload the model files to `models.vigilcam.app` (immutable versioned file names).
   - Write the manifest and sign it with the offline Ed25519 release key.
   - Publish the manifest last, so clients never see a manifest for missing files.
   - Keep previous manifests for rollback.
6. **Ops:**
   - TLS everywhere and monitoring of upload volume per label.
   - A data-retention policy, and a privacy policy page on vigilcam.app that the
     opt-in text can link to.
