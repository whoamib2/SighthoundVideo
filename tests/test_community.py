"""Community training phase 1: opt-in sharing, uploads, signed model updates."""

import base64
import hashlib
import json
import os

import pytest
from PIL import Image

from backEnd import community as c
from backEnd import training_samples as ts


def _frame():
    image = Image.new("RGB", (200, 100), (10, 20, 30))
    image.paste((200, 50, 50), (50, 20, 120, 80))
    return image


def test_off_by_default_and_queues_nothing(tmp_path):
    root = str(tmp_path / "community")
    assert c.get_settings(root)["share_enabled"] is False
    assert c.queue_sample(_frame(), (50, 20, 120, 80), "UPS", "Driveway", 5, root=root) is None
    assert c.queued_samples(root) == []


def test_crop_only_and_metadata_stripped(tmp_path):
    root = str(tmp_path / "community")
    c.save_settings({"share_enabled": True}, root)
    sample_id = c.queue_sample(_frame(), (50, 20, 120, 80), "UPS", "Driveway", 5, root=root)
    [(qid, meta, jpg)] = c.queued_samples(root)
    assert qid == sample_id
    assert Image.open(jpg).size == (70, 60)
    assert set(meta) == {"id", "label", "box", "image_size", "crop"}
    assert "Driveway" not in json.dumps(meta)
    c.save_settings({"crops_only": False, "strip_metadata": False}, root)
    c.queue_sample(_frame(), (50, 20, 120, 80), "UPS", "Driveway", 5, root=root)
    meta = c.queued_samples(root)[-1][1]
    assert meta["box"] == [50, 20, 120, 80] and meta["camera"] == "Driveway"


def test_save_sample_hooks_into_queue(tmp_path):
    troot = str(tmp_path / "training")
    c.save_settings({"share_enabled": True}, os.path.join(troot, "community"))
    ts.save_sample(_frame(), "FedEx", (50, 20, 120, 80), "cam", 1, root=troot)
    assert len(c.queued_samples(os.path.join(troot, "community"))) == 1


def test_upload_failure_keeps_queue_then_success(tmp_path):
    root = str(tmp_path / "community")
    c.save_settings({"share_enabled": True}, root)
    c.queue_sample(_frame(), None, "USPS", root=root)

    def down(url, body, ctype):
        raise OSError("getaddrinfo failed")

    result = c.upload_pending(root, post=down)
    assert result["uploaded"] == 0 and result["remaining"] == 1 and "getaddrinfo" in result["error"]
    sent = []
    result = c.upload_pending(root, post=lambda url, body, ctype: sent.append((url, ctype)) or 201)
    assert result["uploaded"] == 1 and result["remaining"] == 0
    assert sent[0][0] == "https://api.vigilcam.app/v1/samples" and sent[0][1].startswith("multipart/form-data")


def test_revoke_clears_queue_and_retries_deletion(tmp_path):
    root = str(tmp_path / "community")
    c.save_settings({"share_enabled": True}, root)
    c.queue_sample(_frame(), None, "USPS", root=root)
    result = c.revoke_and_request_deletion(root, post=lambda *a: (_ for _ in ()).throw(OSError("offline")))
    assert result["dropped"] == 1 and not result["deletion_sent"]
    assert not c.get_settings(root)["share_enabled"] and c.load_state(root)["deletion_pending"]
    urls = []
    assert c.send_deletion_request(root, post=lambda url, body, ct: urls.append(url) or 200)["deletion_sent"]
    assert urls == ["https://api.vigilcam.app/v1/samples/delete"]


def _manifest(data, sign_with=None, version="1.0.0"):
    manifest = {"version": "x", "models": [{"kind": "classifier", "version": version,
                                             "url": "https://models.vigilcam.app/c.onnx",
                                             "sha256": hashlib.sha256(data).hexdigest()}]}
    if sign_with is not None:
        manifest["signature"] = base64.b64encode(sign_with.sign(c.canonical_manifest_bytes(manifest))).decode()
    return manifest


def test_unsigned_manifest_never_auto_installs(tmp_path):
    root = str(tmp_path / "community")
    data = b"model-bytes"
    files = {c.DEFAULT_MANIFEST_URL: json.dumps(_manifest(data)).encode(), "https://models.vigilcam.app/c.onnx": data}
    c.save_settings({"update_mode": "auto"}, root)
    result = c.apply_updates(root, fetch=files.__getitem__, public_key_b64="")
    assert result["available"] and not result["installed"] and not result["signed"]


def test_signed_manifest_installs_verifies_and_rolls_back(tmp_path):
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization

    key = ed.Ed25519PrivateKey.generate()
    pub = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    root = str(tmp_path / "community")
    c.save_settings({"update_mode": "auto"}, root)
    v1, v2 = b"model-v1", b"model-v2"
    files = {c.DEFAULT_MANIFEST_URL: json.dumps(_manifest(v1, key)).encode(), "https://models.vigilcam.app/c.onnx": v1}
    assert c.apply_updates(root, fetch=files.__getitem__, public_key_b64=pub)["installed"] == ["classifier"]
    files = {c.DEFAULT_MANIFEST_URL: json.dumps(_manifest(v2, key, "1.1.0")).encode(), "https://models.vigilcam.app/c.onnx": v2}
    c.apply_updates(root, fetch=files.__getitem__, public_key_b64=pub)
    path = c.shared_model_path("classifier", root)
    assert open(path, "rb").read() == v2
    assert c.rollback_model("classifier", root) and open(path, "rb").read() == v1
    # Tampered manifest signature is rejected; bad hash is refused.
    bad = _manifest(v2, key, "9.0.0")
    bad["models"][0]["version"] = "9.9.9"
    assert not c.verify_manifest(bad, pub)[0]
    with pytest.raises(ValueError):
        c.install_model({"kind": "classifier", "url": "u", "sha256": "0" * 64}, root, fetch=lambda u: v2)


def test_local_classifier_takes_precedence(tmp_path):
    troot = str(tmp_path / "training")
    root = str(tmp_path / "community")
    assert c.active_carrier_model_path(root, troot) == c.shared_model_path("classifier", root)
    os.makedirs(os.path.join(troot, "models"))
    open(ts.user_carrier_model_path(troot), "wb").close()
    assert c.active_carrier_model_path(root, troot) == ts.user_carrier_model_path(troot)


def test_domain_is_vigilcam_app():
    from appCommon import CommonStrings

    assert CommonStrings.kOemWebsite == "vigilcam.app"
    assert CommonStrings.kSupportEmail == "support@vigilcam.app"
