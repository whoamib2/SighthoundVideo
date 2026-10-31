"""Community training (phase 1, client side).

Opt-in sharing of labeled training samples and signed shared-model updates.
Everything lives under ``%LOCALAPPDATA%/VigilCam/community``::

    state.json       settings, install id, installed model versions, status
    queue/           sanitized samples waiting to upload (<id>.jpg + <id>.json)
    sent.jsonl       ids of uploaded samples (for deletion requests)
    models/          shared models from the manifest (+ *.prev.onnx rollback)

Sharing is OFF by default. Nothing is queued or uploaded unless the user
turns it on. The upload server and manifest are not live yet: failures are
recorded and samples stay queued.

Model precedence: a classifier the user trained locally ("Retrain now") always
wins over the shared classifier, because it is fitted to their own cameras and
corrections; the shared classifier is used only when there is no local one. A
shared detector, once installed, is preferred over the bundled YOLOX nano and
takes effect when the recording engine restarts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
import uuid

DEFAULT_UPLOAD_URL = "https://api.vigilcam.app/v1/samples"
DEFAULT_MANIFEST_URL = "https://models.vigilcam.app/manifest.json"

# Ed25519 public key (raw 32 bytes, base64) that signs the model manifest.
# Placeholder until the release key exists: with no key, manifests are
# treated as unsigned and models are never installed automatically.
MANIFEST_PUBLIC_KEY_B64 = ""

PRIVACY_TEXT = (
    "When sharing is on, VigilCam uploads the training samples you label "
    "(Mark as Missed / Wrong Label) to vigilcam.app so future shared models can "
    "recognize more vehicles. By default only the cropped object is sent, never "
    "the full camera frame, and camera names, file names, and times are removed: "
    "each upload carries just the image, its label, the box, and a random install "
    "id (not tied to your account) so you can ask for deletion later. Samples "
    "labeled before you opt in are not sent. You can turn sharing off at any time; "
    "'Revoke and request deletion' clears anything still queued and asks the "
    "server to delete everything sent from this install."
)

DEFAULT_SETTINGS = {
    "share_enabled": False,
    "crops_only": True,
    "strip_metadata": True,
    "upload_url": DEFAULT_UPLOAD_URL,
    "manifest_url": DEFAULT_MANIFEST_URL,
    "update_mode": "notify",  # "auto", "notify", or "off"
    "allow_unsigned": False,
}

KIND_FILES = {"detector": "detector.onnx", "classifier": "carrier.onnx"}


def community_root() -> str:
    from backEnd.training_samples import training_root

    return os.path.join(os.path.dirname(training_root()), "community")


def _root(root):
    return root or community_root()


def load_state(root=None) -> dict:
    try:
        with open(os.path.join(_root(root), "state.json"), encoding="utf-8") as handle:
            state = json.load(handle)
            return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state, root=None):
    root = _root(root)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "state.json")
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=1)
    os.replace(path + ".tmp", path)


def get_settings(root=None) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    settings.update(load_state(root).get("settings") or {})
    return settings


def save_settings(changes, root=None) -> dict:
    state = load_state(root)
    settings = dict(DEFAULT_SETTINGS)
    settings.update(state.get("settings") or {})
    settings.update(changes)
    state["settings"] = settings
    save_state(state, root)
    return settings


def install_id(root=None) -> str:
    state = load_state(root)
    if not state.get("install_id"):
        state["install_id"] = uuid.uuid4().hex
        save_state(state, root)
    return state["install_id"]


def _set_status(key, value, root=None):
    state = load_state(root)
    state[key] = value
    save_state(state, root)


# ---------------------------------------------------------------- sharing


def queue_dir(root=None) -> str:
    return os.path.join(_root(root), "queue")


def queue_sample(frame_image, box, label, camera="", timestamp_ms=0, root=None):
    """Queue a sanitized copy of a newly labeled sample. Returns its id or None.

    ``frame_image`` is the full PIL frame; ``box`` the labeled box in it.
    Does nothing unless sharing is enabled.
    """
    settings = get_settings(root)
    if not settings["share_enabled"]:
        return None
    image = frame_image.convert("RGB")
    width, height = image.size
    box = [int(v) for v in (box or (0, 0, width, height))]
    if settings["crops_only"]:
        image = image.crop(box)
        shared_box = [0, 0, image.size[0], image.size[1]]
    else:
        shared_box = box
    sample_id = uuid.uuid4().hex
    meta = {"id": sample_id, "label": label, "box": shared_box,
            "image_size": list(image.size), "crop": bool(settings["crops_only"])}
    if not settings["strip_metadata"]:
        meta.update({"camera": camera, "timestamp_ms": int(timestamp_ms or 0)})
    folder = queue_dir(root)
    os.makedirs(folder, exist_ok=True)
    # Re-encoding drops EXIF and any other embedded metadata.
    image.save(os.path.join(folder, sample_id + ".jpg"), "JPEG", quality=90)
    with open(os.path.join(folder, sample_id + ".json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle)
    return sample_id


def queued_samples(root=None) -> list:
    """``[(id, meta, jpg_path), ...]`` oldest first (for the preview list)."""
    folder = queue_dir(root)
    items = []
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.endswith(".json"):
                path = os.path.join(folder, name)
                try:
                    with open(path, encoding="utf-8") as handle:
                        meta = json.load(handle)
                except (OSError, ValueError):
                    continue
                items.append((os.path.getmtime(path), meta["id"], meta, path[:-5] + ".jpg"))
    return [(i, m, p) for _t, i, m, p in sorted(items)]


def remove_queued(sample_ids, root=None) -> int:
    removed = 0
    for sample_id in sample_ids:
        for ext in (".jpg", ".json"):
            try:
                os.remove(os.path.join(queue_dir(root), os.path.basename(sample_id) + ext))
                removed += ext == ".json"
            except OSError:
                pass
    return removed


def _post(url, body, content_type, timeout=20):
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": content_type,
                                              "User-Agent": "VigilCam"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def _multipart(fields, image_bytes):
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                      % (boundary, key, value)).encode("utf-8"))
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"sample.jpg\"\r\n"
                  "Content-Type: image/jpeg\r\n\r\n" % boundary).encode("utf-8"))
    parts.append(image_bytes + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
    return b"".join(parts), "multipart/form-data; boundary=%s" % boundary


def upload_pending(root=None, post=None, limit=50) -> dict:
    """Upload queued samples. Failed uploads stay queued for the next try."""
    post = post or _post
    settings = get_settings(root)
    result = {"uploaded": 0, "remaining": 0, "error": ""}
    if not settings["share_enabled"]:
        result["remaining"] = len(queued_samples(root))
        return result
    ident = install_id(root)
    for sample_id, meta, jpg in queued_samples(root)[:limit]:
        try:
            with open(jpg, "rb") as handle:
                body, ctype = _multipart({"install_id": ident, "metadata": json.dumps(meta)}, handle.read())
            status = post(settings["upload_url"], body, ctype)
            if not 200 <= int(status) < 300:
                raise OSError("server returned HTTP %s" % status)
        except Exception as exc:  # offline, DNS failure, server not live yet...
            result["error"] = str(exc) or exc.__class__.__name__
            break
        with open(os.path.join(_root(root), "sent.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": sample_id, "sent_at": int(time.time())}) + "\n")
        remove_queued([sample_id], root)
        result["uploaded"] += 1
    result["remaining"] = len(queued_samples(root))
    _set_status("last_upload", {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **result}, root)
    return result


def revoke_and_request_deletion(root=None, post=None) -> dict:
    """Turn sharing off, drop the queue, and ask the server to delete our uploads.

    If the server cannot be reached the request is remembered and retried.
    """
    post = post or _post
    save_settings({"share_enabled": False}, root)
    dropped = remove_queued([i for i, _m, _p in queued_samples(root)], root)
    state = load_state(root)
    state["deletion_pending"] = True
    save_state(state, root)
    return {"dropped": dropped, **send_deletion_request(root, post)}


def send_deletion_request(root=None, post=None) -> dict:
    post = post or _post
    if not load_state(root).get("deletion_pending"):
        return {"deletion_sent": False, "error": ""}
    url = get_settings(root)["upload_url"].rstrip("/") + "/delete"
    body = json.dumps({"install_id": install_id(root)}).encode("utf-8")
    try:
        status = post(url, body, "application/json")
        if not 200 <= int(status) < 300:
            raise OSError("server returned HTTP %s" % status)
    except Exception as exc:
        return {"deletion_sent": False, "error": str(exc) or exc.__class__.__name__}
    state = load_state(root)
    state["deletion_pending"] = False
    state["deletion_sent_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_state(state, root)
    return {"deletion_sent": True, "error": ""}


# ---------------------------------------------------------------- models


def models_dir(root=None) -> str:
    return os.path.join(_root(root), "models")


def shared_model_path(kind, root=None) -> str:
    return os.path.join(models_dir(root), KIND_FILES[kind])


def active_carrier_model_path(root=None, training_root=None) -> str:
    """Local user-trained classifier first, then the shared one."""
    from backEnd.training_samples import user_carrier_model_path

    local = user_carrier_model_path(training_root)
    if os.path.isfile(local):
        return local
    return shared_model_path("classifier", root)


def _fetch(url, timeout=30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "VigilCam"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def canonical_manifest_bytes(manifest) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_manifest(manifest, public_key_b64=None) -> tuple:
    """``(ok, reason)``. Ed25519 over the canonical JSON without "signature"."""
    key = MANIFEST_PUBLIC_KEY_B64 if public_key_b64 is None else public_key_b64
    if not key:
        return False, "no manifest public key embedded yet (unsigned)"
    signature = manifest.get("signature")
    if not signature:
        return False, "manifest is not signed"
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(
            base64.b64decode(signature), canonical_manifest_bytes(manifest))
    except ImportError:
        return False, "signature check unavailable (cryptography not installed)"
    except Exception:
        return False, "bad manifest signature"
    return True, ""


def _version_key(text):
    return tuple(int(p) if p.isdigit() else p for p in str(text).replace("-", ".").split("."))


def check_updates(root=None, fetch=None, public_key_b64=None) -> dict:
    """Fetch the manifest and list models newer than what is installed."""
    fetch = fetch or _fetch
    settings = get_settings(root)
    result = {"available": [], "signed": False, "error": "", "reason": ""}
    try:
        manifest = json.loads(fetch(settings["manifest_url"]).decode("utf-8"))
    except Exception as exc:
        result["error"] = str(exc) or exc.__class__.__name__
        _set_status("last_check", {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "error": result["error"]}, root)
        return result
    result["signed"], result["reason"] = verify_manifest(manifest, public_key_b64)
    installed = load_state(root).get("installed") or {}
    for entry in manifest.get("models") or []:
        kind = entry.get("kind")
        if kind not in KIND_FILES or not entry.get("url") or not entry.get("sha256"):
            continue
        current = (installed.get(kind) or {}).get("version")
        if current is None or _version_key(entry.get("version", "0")) > _version_key(current):
            result["available"].append(entry)
    _set_status("last_check", {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "error": "",
                               "signed": result["signed"], "reason": result["reason"],
                               "available": [(e["kind"], e.get("version")) for e in result["available"]]},
                root)
    return result


def install_model(entry, root=None, fetch=None) -> str:
    """Download, verify sha256, keep the previous model, and swap atomically.

    Backend pipelines hot-load the classifier; the detector applies on the
    next engine restart. Raises ``ValueError`` on a hash mismatch.
    """
    fetch = fetch or _fetch
    kind = entry["kind"]
    data = fetch(entry["url"])
    digest = hashlib.sha256(data).hexdigest()
    if digest.lower() != str(entry["sha256"]).lower():
        raise ValueError("sha256 mismatch for %s: got %s" % (kind, digest))
    target = shared_model_path(kind, root)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    temp = target + ".download"
    with open(temp, "wb") as handle:
        handle.write(data)
    if os.path.isfile(target):
        shutil.copy2(target, target.replace(".onnx", ".prev.onnx"))
    os.replace(temp, target)
    state = load_state(root)
    installed = state.setdefault("installed", {})
    previous = installed.get(kind)
    installed[kind] = {"version": entry.get("version"), "sha256": digest,
                       "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "previous": previous}
    save_state(state, root)
    return target


def rollback_model(kind, root=None) -> bool:
    target = shared_model_path(kind, root)
    backup = target.replace(".onnx", ".prev.onnx")
    if not os.path.isfile(backup):
        return False
    os.replace(backup, target)
    state = load_state(root)
    installed = state.setdefault("installed", {})
    current = installed.get(kind) or {}
    installed[kind] = current.get("previous") or {"version": "rolled-back"}
    save_state(state, root)
    return True


def apply_updates(root=None, fetch=None, public_key_b64=None, force=False) -> dict:
    """Check, then install per the update mode. Unsigned manifests never auto-install."""
    settings = get_settings(root)
    result = check_updates(root, fetch, public_key_b64)
    result["installed"] = []
    if result["error"] or not result["available"]:
        return result
    trusted = result["signed"] or settings["allow_unsigned"]
    if not (force or settings["update_mode"] == "auto") or not trusted:
        return result
    for entry in result["available"]:
        try:
            install_model(entry, root, fetch)
            result["installed"].append(entry["kind"])
        except Exception as exc:
            result["error"] = str(exc)
    return result
