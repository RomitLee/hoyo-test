from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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
    assert health.read_text(encoding="utf-8") == "V2.3.0"

    escaped = tmp_path / "outside.ok"
    monkeypatch.setenv("HOYO_UPDATE_HEALTH_FILE", str(escaped))
    auto_update.mark_update_healthy()
    assert not escaped.exists()


def test_role_manifest_is_bound_to_role_and_independent_version(monkeypatch):
    monkeypatch.setattr(auto_update, "verify_manifest_signature", lambda _: True)
    document = manifest()
    document.update(schema_version=3, release_role="master", client_version="V2.1.0")
    assert auto_update.select_update(document, "master", "V2.0.0")["client_version"] == "V2.1.0"
    with pytest.raises(auto_update.UpdateError, match="类型不匹配"):
        auto_update.select_update(document, "slave", "V2.0.0")
    signed = auto_update.manifest_signature_payload(document)
    document["release_role"] = "slave"
    assert signed != auto_update.manifest_signature_payload(document)
    document["client_version"] = "V2.0.0"
    assert auto_update.select_update(document, "slave", "V2.0.0") is None


def test_compatibility_uses_protocol_instead_of_equal_versions():
    from hoyo_analyzer import MASTER_VERSION, SLAVE_VERSION, client_version, slave_protocol
    assert client_version("master") == MASTER_VERSION == "V2.3.0"
    assert client_version("slave") == SLAVE_VERSION == "V2.18.17"
    assert slave_protocol({"app_version": "V2.0.0"}) == 1
    assert slave_protocol({"app_version": "V1.9.0"}) == 1
    assert slave_protocol({"app_version": "V9.0.0", "protocol_version": 1}) == 1
    assert slave_protocol({"protocol_version": 2}) == 2
    assert slave_protocol({"app_version": "V9.0.0"}) is None


def test_controller_uses_own_role_version(monkeypatch):
    monkeypatch.setattr(auto_update.QTimer, "singleShot", lambda *args: None)
    from PySide6.QtCore import QCoreApplication
    application = QCoreApplication.instance() or QCoreApplication([])
    assert application is not None
    slave = auto_update.UpdateController("slave")
    master = auto_update.UpdateController("master")
    slave.timer.stop()
    master.timer.stop()
    assert slave.current_version == "V2.18.17"
    assert master.current_version == "V2.3.0"
    slave.deleteLater()
    master.deleteLater()

def test_update_backs_off_after_repeated_install_failures(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(auto_update, "application_root", lambda: tmp_path)
    updates = tmp_path / "runtime" / "updates"
    updates.mkdir(parents=True)
    marker = updates / "install-failed-V2.18.1.json"
    now = datetime.now(UTC).isoformat(timespec="seconds")

    marker.write_text(json.dumps({"version": "V2.18.1", "attempts": 2, "at": now}), encoding="utf-8")
    assert auto_update.UpdateController._recent_install_failure("V2.18.1")
    assert not auto_update.UpdateController._recent_install_failure("V2.18.0")

    marker.write_text(json.dumps({"version": "V2.18.1", "attempts": 1, "at": now}), encoding="utf-8")
    assert not auto_update.UpdateController._recent_install_failure("V2.18.1")

    old = (datetime.now(UTC) - timedelta(hours=7)).isoformat(timespec="seconds")
    marker.write_text(json.dumps({"version": "V2.18.1", "attempts": 5, "at": old}), encoding="utf-8")
    assert not auto_update.UpdateController._recent_install_failure("V2.18.1")
    assert not marker.exists()


def test_failed_install_marker_counts_attempts(tmp_path) -> None:
    from hoyo_analyzer.updater import record_failed_install

    record_failed_install(tmp_path, "V2.18.1")
    record_failed_install(tmp_path, "V2.18.1")
    data = json.loads((tmp_path / "install-failed-V2.18.1.json").read_text(encoding="utf-8"))
    assert data["attempts"] == 2
    assert data["version"] == "V2.18.1"
