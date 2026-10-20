import os
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from hoyo_analyzer import __version__
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
    assert "#070B14" in window.styleSheet()
    assert "QTabWidget::pane" in window.styleSheet()
    assert "QTabBar::tab:selected" in window.styleSheet()
    assert not window.windowIcon().isNull()
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.title_bar.height() == 32
    assert __version__ == "V1.7.0"
    assert __version__ in window.subtitle_label.text()
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


def test_popup_switch_controls_new_event_alarm(window, monkeypatch):
    alarms = []
    monkeypatch.setattr(window, "_show_alarm", alarms.append)
    history = event(1, datetime.now(UTC).isoformat())
    window._on_events_received([history])
    assert alarms == []
    window._on_events_received([history, event(2)])
    assert alarms == []

    window.settings.popup_alert_enabled = True
    window._on_events_received([history, event(2), event(3)])
    assert len(alarms) == 1
    window._on_events_received([history, event(2), event(3)])
    assert len(alarms) == 1
    assert window.table.cellWidget(2, 6) is None


def test_master_settings_dialog_exposes_popup_checkbox(window):
    from hoyo_analyzer.master_app import MasterSettingsDialog

    dialog = MasterSettingsDialog(MasterSettings(popup_alert_enabled=True), window)
    try:
        assert dialog.popup_alert_checkbox.isChecked()
        dialog.popup_alert_checkbox.setChecked(False)
        assert not dialog.settings().popup_alert_enabled
    finally:
        dialog.close()


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
    assert window.status_table.item(0, 5).text() == "0"
    assert window.status_table.item(0, 6).text() == "96%"
    assert window.status_table.item(0, 7).text() == "30.0%"
    assert window.status_table.item(1, 8).text() == "是"
    assert window.status_table.item(2, 10).text() == "离线"
    assert window.status_table.item(2, 8).text() == "-"


def test_money_is_visible_per_role_and_marks_last_confirmation(window):
    now = datetime.now(UTC).isoformat()
    rows = [
        {"role_name": "角色甲", "cash": 1450000, "bank_money": 0,
         "money_readable": True, "money_confirmed_at": now},
        {"role_name": "角色乙", "cash": 4995810, "bank_money": 0,
         "money_readable": False, "money_confirmed_at": now},
        {"role_name": "角色丙"},
    ]
    window._on_status_received([{"device_id": "slave-01", "last_seen_at": now, "windows": rows}])
    assert window.status_table.item(0, 2).text() == "1,450,000"
    assert window.status_table.item(0, 3).text() == "0"
    assert window.status_table.item(1, 2).text() == "4,995,810（上次确认）"
    assert window.status_table.item(2, 2).text() == "未识别"
    assert window.status_table.item(0, 11).text() != "-"
    assert MasterWindow._format_money(rows[0], "cash", False).endswith("（上次确认）")


def test_device_management_page_renders_devices_and_pairing_code(window):
    now = datetime.now(UTC)
    window._on_devices_received([
        {
            "device_id": "slave-001-001",
            "display_name": "工作室电脑",
            "note": "二楼",
            "app_version": __version__,
            "last_seen_at": now.isoformat(),
            "disabled": False,
        },
        {
            "device_id": "slave-001-002",
            "display_name": "家里电脑",
            "last_seen_at": now.isoformat(),
            "disabled": True,
        },
    ])
    assert window.tabs.count() == 3
    assert window.device_table.rowCount() == 2
    assert window.device_table.item(0, 0).text() == "工作室电脑"
    assert window.device_table.item(0, 2).text() == "在线"
    assert window.device_table.item(0, 4).text() == __version__
    assert window.device_table.item(0, 6).text() == "正常"
    assert window.device_table.item(1, 6).text() == "已禁用 · 版本不匹配"
    assert window.device_table.cellWidget(0, 7) is not None

    window._on_pairing_code_received(
        True, "", {"pairing_code": "123456", "target_device_id": None}
    )
    assert "123456" in window.pairing_code_label.text()
    assert window.tabs.currentIndex() == 1


def test_device_management_warns_when_slave_version_differs(window):
    window._on_devices_received([
        {
            "device_id": "slave-old",
            "display_name": "旧版从机",
            "app_version": "V0.9.0",
            "last_seen_at": datetime.now(UTC).isoformat(),
            "disabled": False,
        }
    ])

    assert window.device_table.item(0, 4).text() == "V0.9.0"
    assert window.device_table.item(0, 6).text() == "版本不匹配"
    assert f"主机版本：{__version__}" in window.device_table.item(0, 6).toolTip()


def test_monitor_log_page_renders_history_without_alarms(window, monkeypatch):
    alarms = []
    monkeypatch.setattr(window, "_show_alarm", alarms.append)
    requested = []
    monkeypatch.setattr(window.client, "fetch_monitor_logs", requested.append)
    window._on_monitor_logs_received({"logs": [{
        "id": 9, "device_id": "slave-01", "display_name": "一号电脑",
        "event_type": "window_occluded", "occlusion_percent": 0.021, "minimized": False,
        "server_name": "秦淮风光", "role_name": "角色一",
        "occurred_at": datetime.now(UTC).isoformat(),
    }], "has_more": True})
    assert window.tabs.tabText(2) == "监控日志"
    assert window.monitor_log_table.item(0, 1).text() == "一号电脑"
    assert window.monitor_log_table.item(0, 3).text() == "遮挡"
    assert window.monitor_log_table.item(0, 4).text() == "<0.1%"
    assert window.monitor_log_table.item(0, 5).text() == "否"
    assert window.older_logs_button.isEnabled()
    window.older_logs_button.click()
    assert requested == [9]
    window._on_monitor_logs_received({"logs": [], "has_more": False})
    assert window.monitor_log_table.rowCount() == 0
    assert not window.older_logs_button.isEnabled()
    assert alarms == []


def test_panel_monitor_log_renders_exact_chinese_timestamp(window):
    window._on_monitor_logs_received({"logs": [{
        "id": 10, "device_id": "slave-01", "event_type": "panel_opened",
        "panel_type": "trade", "reason": "recognition",
        "occurred_at": "2026-10-20T02:31:38+00:00", "confidence": 0.99,
    }], "has_more": False})
    assert window.monitor_log_table.item(0, 0).text() == "2026年10月20日10:31:38"
    assert window.monitor_log_table.item(0, 3).text() == "打开交易面板"
    assert window.monitor_log_table.item(0, 6).text() == "面板识别"
