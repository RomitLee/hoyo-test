from __future__ import annotations

import json
from pathlib import Path

import pytest

from hoyo_analyzer import auto_update


def package(role: str) -> dict[str, object]:
    executable = "梦幻西游背包监控.exe" if role == "slave" else "梦幻西游告警主机.exe"
    archive_root = "梦幻西游背包监控" if role == "slave" else "梦幻西游告警主机"
    return {
        "filename": f"{archive_root}.zip",
        "sha256": ("a" if role == "slave" else "b") * 64,
        "size": 123,
        "download_path": f"/api/v1/updates/download/{role}/V1.3.0",
        "archive_root": archive_root,
        "executable": executable,
    }


def manifest() -> dict[str, object]:
    return {
        "schema_version": 2,
        "client_version": "V1.3.0",
        "packages": {"slave": package("slave"), "master": package("master")},
        "signature": {"algorithm": "RSA-3072-SHA256", "value": "invalid"},
    }


def test_parse_version_orders_semantic_versions() -> None:
    assert auto_update.parse_version("V1.10.0") > auto_update.parse_version("1.2.9")
    with pytest.raises(ValueError):
        auto_update.parse_version("V1.2")


def test_signature_payload_is_stable_and_covers_both_packages() -> None:
    document = manifest()
    payload = auto_update.manifest_signature_payload(json.loads(json.dumps(document)))
    assert payload.startswith(b"hoyo-update-v1\n2\nV1.3.0\nslave\n")
    assert b"master" in payload
    assert payload.endswith("梦幻西游告警主机.exe\n".encode())


def test_select_update_accepts_only_newer_signed_release(monkeypatch) -> None:
    monkeypatch.setattr(auto_update, "verify_manifest_signature", lambda _document: True)
    selected = auto_update.select_update(manifest(), "slave", "V1.2.0")
    assert selected is not None
    assert selected["client_version"] == "V1.3.0"
    assert auto_update.select_update(manifest(), "master", "V1.3.0") is None


def test_select_update_rejects_bad_signature(monkeypatch) -> None:
    monkeypatch.setattr(auto_update, "verify_manifest_signature", lambda _document: False)
    with pytest.raises(auto_update.UpdateError, match="签名"):
        auto_update.select_update(manifest(), "slave", "V1.2.0")


def test_mark_update_healthy_is_confined_to_runtime(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(auto_update, "application_root", lambda: tmp_path)
    health = tmp_path / "runtime" / "updates" / "health.ok"
    monkeypatch.setenv("HOYO_UPDATE_HEALTH_FILE", str(health))
    auto_update.mark_update_healthy()
    assert health.read_text(encoding="utf-8") == "V1.2.0"

    escaped = tmp_path / "outside.ok"
    monkeypatch.setenv("HOYO_UPDATE_HEALTH_FILE", str(escaped))
    auto_update.mark_update_healthy()
    assert not escaped.exists()
