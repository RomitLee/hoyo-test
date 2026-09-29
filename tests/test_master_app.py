import os
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hoyo_analyzer.master_app import MasterWindow
from hoyo_analyzer.master_settings import MasterSettings


@pytest.fixture
def window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("hoyo_analyzer.master_app.load_master_settings", MasterSettings)
    monkeypatch.setattr(MasterWindow, "_show_alarm", lambda self, event: None)
    widget = MasterWindow()
    widget.poll_timer.stop()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def event(event_id=1, acked_at=None):
    return {
        "server_event_id": event_id, "device_id": "slave-01",
        "server_name": "秦淮风光", "role_name": "清风知夏",
        "event_type": "inventory_full", "occurred_at": datetime.now(UTC).isoformat(),
        "acked_at": acked_at,
    }


def test_acknowledged_row_remains_and_button_disappears(window):
    window._on_events_received([event()])
    assert window.table.cellWidget(0, 6) is not None
    window._on_acknowledge_result(1, True, "")
    assert window.table.rowCount() == 1
    assert window.table.item(0, 5).text() == "已处理"
    assert window.table.cellWidget(0, 6) is None
    # An in-flight response must not undo an acknowledgement.
    window._on_events_received([event()])
    assert window.table.item(0, 5).text() == "已处理"
    assert "未处理 0 条" in window.summary.text()


def test_history_does_not_alarm_but_new_pending_event_does(window, monkeypatch):
    alarms = []
    monkeypatch.setattr(window, "_show_alarm", alarms.append)
    history = event(1, datetime.now(UTC).isoformat())
    window._on_events_received([history])
    assert alarms == []
    window._on_events_received([history, event(2)])
    assert len(alarms) == 1
    window._on_events_received([history, event(2)])
    assert len(alarms) == 1
    assert window.table.cellWidget(1, 6) is None


def test_status_renders_every_window_and_empty_offline_device(window):
    now = datetime.now(UTC)
    window._on_status_received([
        {"device_id": "slave-01", "last_seen_at": now.isoformat(), "windows": [
            {"server_name": "秦淮风光", "role_name": "清风知夏", "status_text": "背包已满",
             "empty_slots": 0, "confidence": 0.96, "occlusion_percent": 30,
             "minimized": False, "updated_at": now.isoformat()},
            {"role_name": "第二个角色", "status_text": "窗口已最小化", "minimized": True},
        ]},
        {"device_id": "slave-02", "last_seen_at": (now - timedelta(seconds=100)).isoformat(), "windows": []},
    ])
    assert window.status_table.rowCount() == 3
    assert window.status_table.item(0, 3).text() == "0"
    assert window.status_table.item(0, 4).text() == "96%"
    assert window.status_table.item(0, 5).text() == "30.0%"
    assert window.status_table.item(1, 6).text() == "是"
    assert window.status_table.item(2, 8).text() == "离线"
    assert window.status_table.item(2, 6).text() == "-"
