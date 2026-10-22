from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication

from hoyo_analyzer.multi_inventory_app import InventoryMonitorWindow
from hoyo_analyzer.relay_client import SlaveRelayClient
from hoyo_analyzer.slave_settings import SlaveSettings
from hoyo_analyzer.wgc import WindowOcclusionStatus


def log_reply(event_id, success):
    properties = {"relay_kind": "monitor_log", "log_event_id": event_id}
    return SimpleNamespace(
        property=properties.get, attribute=lambda key: 202 if success else 503,
        error=lambda: QNetworkReply.NetworkError.NoError if success else QNetworkReply.NetworkError.ConnectionRefusedError,
        errorString=lambda: "unavailable", readAll=lambda: b'{"accepted":true}', deleteLater=lambda: None,
    )


def test_locked_outbox_keeps_uploading_and_retries_latest_queue(monkeypatch, tmp_path):
    client = SlaveRelayClient(QApplication.instance())
    client.configure("device", "token")
    requests = []
    monkeypatch.setattr(client, "_post", lambda path, payload, kind: requests.append(payload))
    client.send_cash_observation({"event_id": "first", "cash": 100})
    original_replace = Path.replace

    def locked(source, destination):
        raise PermissionError(5, "access denied")

    monkeypatch.setattr(Path, "replace", locked)
    # Both a new observation and an acknowledgement can hit the same locked file.
    client.send_cash_observation({"event_id": "second", "cash": 80})
    client._on_finished(log_reply("first", True))
    assert requests[-1]["event_id"] == "second"
    client._on_finished(log_reply("second", True))
    assert client._log_dirty
    assert not client._log_outbox
    assert json.loads(client._log_path.read_text())[0]["payload"]["event_id"] == "first"
    # Retry even when there is nothing left to send or the device is unconfigured.
    client.configure("", "")
    monkeypatch.setattr(Path, "replace", original_replace)
    client._retry_log_outbox()
    assert not client._log_dirty
    assert json.loads(client._log_path.read_text()) == []
    assert not list(client._log_path.parent.glob("*.tmp"))
    client._log_timer.stop()
    client._heartbeat_timer.stop()


def test_outbox_write_failure_preserves_pending_observations_for_restart(monkeypatch):
    client = SlaveRelayClient(QApplication.instance())
    original_write = Path.write_text

    def locked(*args, **kwargs):
        raise PermissionError(5, "access denied")

    monkeypatch.setattr(Path, "write_text", locked)
    client.send_cash_observation({"event_id": "cash", "cash": 100})
    client.send_monitor_payload({"event_id": "pause", "event_type": "monitoring_paused"})
    client._retry_log_outbox()
    assert client._log_dirty
    assert len(client._log_outbox) == 2
    monkeypatch.setattr(Path, "write_text", original_write)
    client.flush_monitor_logs()
    restored = SlaveRelayClient(QApplication.instance())
    assert [item["payload"]["event_id"] for item in restored._log_outbox] == ["cash", "pause"]
    assert restored._log_outbox[0]["route"] == "cash"
    for current in (client, restored):
        current._log_timer.stop()
        current._heartbeat_timer.stop()


def test_cash_outbox_persists_routes_and_retries_without_duplicate(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("hoyo_analyzer.relay_client.application_root", lambda: tmp_path)
    requests = []
    monkeypatch.setattr(SlaveRelayClient, "_post", lambda self, path, payload, kind: requests.append((path, payload)))
    first = SlaveRelayClient(app)
    first.configure("device-cash", "token")
    payload = {"event_id": "cash-1", "character_id": "123", "cash": 1000}
    first.send_cash_observation(payload)
    assert requests[-1][0] == "/api/v1/slave/cash-observations"
    first._on_finished(log_reply("cash-1", False))
    first._log_timer.stop()
    first._heartbeat_timer.stop()
    second = SlaveRelayClient(app)
    second.configure("device-cash", "token")
    second._send_next_log()
    assert requests[-1] == ("/api/v1/slave/cash-observations", payload)
    second._on_finished(log_reply("cash-1", True))
    assert json.loads(second._log_path.read_text(encoding="utf-8")) == []
    second._log_timer.stop()
    second._heartbeat_timer.stop()


def test_lifecycle_outbox_survives_failure_restart_and_keeps_device_ownership(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("hoyo_analyzer.relay_client.application_root", lambda: tmp_path)
    requests = []
    monkeypatch.setattr(SlaveRelayClient, "_post", lambda self, path, payload, kind: requests.append(payload))
    first = SlaveRelayClient(app)
    first.configure("device-one", "token")
    first.send_monitor_log("monitoring_started", "startup")
    event_id = requests[-1]["event_id"]
    first._on_finished(log_reply(event_id, False))
    first.send_monitor_log("monitoring_paused", "close")
    first._log_timer.stop()
    first._heartbeat_timer.stop()
    assert len(json.loads(first._log_path.read_text(encoding="utf-8"))) == 2

    second = SlaveRelayClient(app)
    second.configure("device-two", "token")
    count = len(requests)
    second._send_next_log()
    assert len(requests) == count
    second.configure("device-one", "new-token")
    second._send_next_log()
    assert requests[-1]["event_id"] == event_id
    second._on_finished(log_reply(event_id, True))
    assert requests[-1]["reason"] == "close"
    second._on_finished(log_reply(requests[-1]["event_id"], True))
    assert json.loads(second._log_path.read_text(encoding="utf-8")) == []
    second._log_timer.stop()
    second._heartbeat_timer.stop()


def test_window_records_start_pause_resume_close_even_without_game_windows(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.load_slave_settings", SlaveSettings)
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    calls = []
    monkeypatch.setattr(SlaveRelayClient, "send_monitor_log", lambda self, kind, reason: calls.append((kind, reason)))
    monkeypatch.setattr(SlaveRelayClient, "flush_monitor_logs", lambda self: None)
    window = InventoryMonitorWindow()
    window._set_monitoring_paused(True)
    window._set_monitoring_paused(True)  # No second click/state transition.
    window._set_monitoring_paused(False)
    window.close()
    assert calls == [
        ("monitoring_started", "startup"), ("monitoring_paused", "pause"),
        ("monitoring_started", "resume"), ("monitoring_paused", "close"),
    ]
    app.processEvents()


def test_paused_snapshot_reads_current_os_state_and_preserves_small_occlusion(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.load_slave_settings", SlaveSettings)
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    monkeypatch.setattr(SlaveRelayClient, "send_monitor_log", lambda *args: None)
    monkeypatch.setattr(SlaveRelayClient, "flush_monitor_logs", lambda self: None)
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.is_window_minimized", lambda hwnd: False)
    measured = WindowOcclusionStatus(123, 480000, 100, 100 / 480000)
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.measure_window_occlusion", lambda hwnd: measured)
    window = InventoryMonitorWindow()
    session = SimpleNamespace(
        hwnd=123, title="游戏", account=SimpleNamespace(secondary_region="区服", role_name="角色", role_id="1234"),
        last_signal={"status": "full", "empty_count": 0, "confidence": 0.99},
        minimized=True, last_occlusion_percent=80, removing=False,
    )
    window.sessions[123] = session
    snapshots = []
    monkeypatch.setattr(window.relay_client, "send_status", snapshots.append)
    try:
        window.monitoring_paused = True
        window._report_window_status()
        item = snapshots[-1][0]
        assert item["occlusion_percent"] == measured.ratio * 100 > 0
        assert not item["minimized"]
        assert item["empty_slots"] is None
        assert not item["monitoring"]
        monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.measure_window_occlusion",
                            lambda hwnd: (_ for _ in ()).throw(OSError("failed")))
        window._report_window_status()
        assert snapshots[-1][0]["occlusion_percent"] is None
    finally:
        window.sessions.clear()
        window.close()
        app.processEvents()
