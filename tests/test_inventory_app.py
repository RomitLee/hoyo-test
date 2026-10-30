import os
from itertools import pairwise
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from hoyo_analyzer import SLAVE_VERSION as __version__
from hoyo_analyzer.inventory_app import (
    APP_NAME,
    MAX_MONITORED_WINDOWS,
    _analysis_signal,
    is_mhxy_game_window_title,
    parse_account_info,
)
from hoyo_analyzer.inventory_fullness import InventoryAnalysis, InventoryStatus, SlotResult, SlotState
from hoyo_analyzer.multi_inventory_app import InventoryMonitorWindow
from hoyo_analyzer.models import Event
from hoyo_analyzer.screenshots import EVENT_SCREENSHOT_PANELS
from hoyo_analyzer.slave_settings import SlaveSettings
from hoyo_analyzer.wgc import OccludingWindow, WindowInfo, WindowOcclusionStatus


@pytest.fixture(autouse=True)
def use_isolated_slave_settings(monkeypatch) -> None:
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.load_slave_settings", SlaveSettings)


def test_analysis_signal_contains_only_inventory_result() -> None:
    slots = tuple(SlotResult(index // 5, index % 5, (0, 0, 10, 10), SlotState.OCCUPIED, 0.0) for index in range(20))
    analysis = InventoryAnalysis(
        status=InventoryStatus.FULL,
        confidence=0.97,
        reason="20 个格子均已占用",
        grid_bbox=(10, 20, 30, 40),
        location_method="template",
        slots=slots,
    )

    assert _analysis_signal(analysis) == {
        "active": True,
        "status": "full",
        "confidence": 0.97,
        "reason": "20 个格子均已占用",
        "empty_count": 0,
        "occupied_count": 20,
        "unknown_count": 0,
        "grid_bbox": [10, 20, 30, 40],
        "location_method": "template",
    }


def test_inventory_window_exposes_only_monitoring_controls(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)

    window = InventoryMonitorWindow()
    window.auth_timer.stop()
    try:
        assert window.windowTitle() == APP_NAME
        assert not window.windowIcon().isNull()
        assert (window.width(), window.height()) == (560, 1100)
        assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert window.title_bar.height() == 32
        assert window.add_window_button.text() == "＋ 增加窗口"
        assert window.add_window_button.property("role") == "primary"
        assert window.monitoring_button.text() == "暂停监控"
        assert window.minimize_all_button.text() == "一键最小化"
        assert window.minimize_all_button.property("role") == "danger"
        assert window.settings_button.text() == "设置"
        assert window.background_button.text() == "后台监控"
        assert window.mini_button.text() == "迷你版"
        assert window.host_status_label.text() == "云端：未连接"
        assert __version__.startswith("V2.")
        assert __version__ in window.subtitle_label.text()
        assert window.host_connect_button.text() == "连接"
        assert not window.host_connect_button.isEnabled()
        assert not hasattr(window, "minimize_warning_checkbox")
        assert window.sessions == {}
        assert window.outputs_group.title() == "窗口输出（0）"
        assert not hasattr(window, "windows_group")
        assert not hasattr(window, "window_strip")
        assert MAX_MONITORED_WINDOWS == 8
        assert set(window.STATUS_PRESENTATION) == {
            "full",
            "almost_full",
            "not_full",
            "inventory_closed",
            "invalid",
        }
        assert not hasattr(window, "equipment_button")
        assert not hasattr(window, "navigation")
        assert not window._authenticated
        assert not window.scan_timer.isActive()
        assert not window.status_timer.isActive()
        assert not window.relay_client.configured
    finally:
        window.sessions = {}
        window.close()


def test_market_detection_toast_shows_server_and_closes_after_two_seconds(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    try:
        event = Event("market", 100, "market_stall_changed",
                      payload={"occurred_at": "2026-10-29T16:30:00+08:00"})
        window._show_market_toast("百花村", event)
        assert window._market_toast is not None and window._market_toast.isVisible()
        assert window._market_toast_detail is not None
        assert "百花村" in window._market_toast_detail.text()
        assert "2026-10-29 16:30:00" in window._market_toast_detail.text()
        assert 0 < window._market_toast_timer.remainingTime() <= 2000
        window._hide_market_toast()
        assert not window._market_toast.isVisible()
    finally:
        window.close()


def test_market_panel_event_enters_toast_from_worker_event_path(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    window.auth_timer.stop()
    shown = []
    try:
        window._authenticated = True
        window.sessions = {
            123: SimpleNamespace(
                account=SimpleNamespace(secondary_region="秦淮风光", role_name="测试角色", role_id="123456"),
                minimized=False,
                monitor_session_id="session-1",
            )
        }
        monkeypatch.setattr(window, "_show_market_toast", lambda server, event: shown.append((server, event.type)))
        monkeypatch.setattr(window.relay_client, "send_monitor_payload", lambda *args, **kwargs: None)
        event = Event(
            "market-open", 100, "panel_opened", confidence=1.0,
            payload={"panel_type": "stall_other_buy", "message": "打开他人收货摊位",
                     "occurred_at": "2026-10-29T20:30:00+08:00"},
        )
        window._on_event(123, event)
        assert shown == [("秦淮风光", "panel_opened")]
    finally:
        window.sessions = {}
        window.close()


def test_periodic_screenshots_are_dispatched_one_window_at_a_time(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.is_window_minimized", lambda hwnd: False)
    window = InventoryMonitorWindow()
    window.auth_timer.stop()
    calls: list[int] = []
    try:
        window.sessions = {
            hwnd: SimpleNamespace(removing=False, worker=SimpleNamespace(
                request_live_snapshot=lambda current=hwnd: calls.append(current)))
            for hwnd in (1, 2, 3)
        }
        window._live_capture_queue = [1, 2, 3]
        window._queued_live_captures = {1, 2, 3}
        window._dispatch_next_periodic_snapshot()
        assert calls == [1]
        assert window._live_capture_queue == [2, 3]
        assert window._live_capture_queue_timer.isActive()
        window._live_capture_queue_timer.stop()
        window._dispatch_next_periodic_snapshot()
        assert calls == [1, 2]
    finally:
        window.sessions = {}
        window.close()
        app.processEvents()


def test_live_control_applies_event_screenshot_panels_to_all_workers(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    window.auth_timer.stop()
    received: list[frozenset[str]] = []
    try:
        window._authenticated = True
        window.monitoring_paused = True
        window.sessions = {
            first: SimpleNamespace(
                removing=False,
                worker=SimpleNamespace(set_event_screenshot_panels=received.append),
            )
            for first in (1, 2)
        }
        enabled = {"give": True, "stall_other_buy": True}

        window._on_live_control(
            {
                "interval_minutes": 12,
                "event_screenshots": {
                    panel: enabled.get(panel, False) for panel in EVENT_SCREENSHOT_PANELS
                },
            }
        )

        expected = frozenset({"give", "stall_other_buy"})
        assert window._event_screenshot_panels == expected
        assert received == [expected, expected]
    finally:
        window.sessions = {}
        window.close()
        app.processEvents()


def test_live_control_without_event_screenshot_settings_preserves_all_panels(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    window.auth_timer.stop()
    received: list[frozenset[str]] = []
    try:
        window._authenticated = True
        window.monitoring_paused = True
        window._event_screenshot_panels = frozenset()
        window.sessions = {
            1: SimpleNamespace(
                removing=False,
                worker=SimpleNamespace(set_event_screenshot_panels=received.append),
            )
        }

        window._on_live_control({"interval_minutes": 5})

        expected = frozenset(EVENT_SCREENSHOT_PANELS)
        assert window._event_screenshot_panels == expected
        assert received == [expected]
    finally:
        window.sessions = {}
        window.close()
        app.processEvents()


def test_login_gate_blocks_monitoring_until_verified_and_logout_stops_it(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    monkeypatch.setattr("hoyo_analyzer.relay_client.SlaveRelayClient._post", lambda *args: None)
    saved = []
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.save_slave_settings", lambda value: saved.append(value.device_id))
    window = InventoryMonitorWindow()
    try:
        window.show()
        app.processEvents()
        assert window.login_content.isVisible() and not window.full_content.isVisible()
        window._window_titles[123] = "game"
        window._add_window(123)
        assert not window.sessions
        sent = []
        monkeypatch.setattr(window.relay_client, "send_status", lambda rows: sent.append(rows))
        window._report_window_status()
        assert not sent
        window.login_password.setText("temporary-password")
        window._on_auth_result("login", True, "", {
            "device_id": "slave-001-001", "device_token": "test-token",
            "display_name": "收购电脑", "account_username": "merchant",
        })
        app.processEvents()
        assert window._authenticated and window.full_content.isVisible()
        assert all(timer.isActive() for timer in (
            window.scan_timer, window.window_state_timer, window.occlusion_timer, window.status_timer,
        ))
        assert window.relay_client.configured and window.login_password.text() == ""
        assert window.device_name_label.text() == "收购电脑"
        assert saved[-1] == "slave-001-001"
        window._logout()
        app.processEvents()
        assert not window._authenticated and window.login_content.isVisible()
        assert not window.relay_client.configured
        assert not window.scan_timer.isActive() and not window.status_timer.isActive()
        assert not window._event_screenshot_panels
        assert saved[-1] == ""
    finally:
        window.close()
        app.processEvents()


def test_saved_device_is_verified_before_monitoring_and_revocation_locks_screen(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    settings = SlaveSettings(device_id="slave-001-001", device_token="saved-token",
                             display_name="旧名称", installation_id="installation-1234567890")
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.load_slave_settings", lambda: settings)
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.save_slave_settings", lambda value: None)
    monkeypatch.setattr("hoyo_analyzer.relay_client.SlaveRelayClient._post", lambda *args: None)
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    seen = []
    monkeypatch.setattr(window.auth_client, "verify", lambda device_id, token: seen.append((device_id, token)))
    try:
        window.show()
        window._verify_saved_identity()
        assert seen == [("slave-001-001", "saved-token")]
        assert not window._authenticated and not window.scan_timer.isActive()
        window._on_auth_result("verify", True, "", {
            "device_id": "slave-001-001", "display_name": "后台改名", "account_username": "merchant",
        })
        app.processEvents()
        assert window._authenticated and window.full_content.isVisible()
        assert settings.display_name == "后台改名"
        assert window.login_password.text() == ""
        window._on_auth_result("verify", False, "设备已禁用", {"http_status": 401})
        app.processEvents()
        assert not window._authenticated and window.login_content.isVisible()
        assert settings.device_token == "" and not window.scan_timer.isActive()
        assert not window.relay_client.configured
    finally:
        window.close()
        app.processEvents()


def test_slave_name_edits_wait_for_server_confirmation_and_cache_new_name(monkeypatch):
    monkeypatch.setattr("hoyo_analyzer.relay_client.SlaveRelayClient._post", lambda *args: None)
    monkeypatch.setattr("hoyo_analyzer.relay_client.SlaveRelayClient.flush_monitor_logs", lambda *args: None)
    settings = SlaveSettings(device_id="device", device_token="token", display_name="旧名称")
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.load_slave_settings", lambda: settings)
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    saved, requested = [], []
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.save_slave_settings", lambda value: saved.append(value.display_name))
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.QInputDialog.getText", lambda *args, **kwargs: (" 收购电脑02 ", True))
    window = InventoryMonitorWindow()
    try:
        window._authenticated = True
        window.relay_client.configure(settings.device_id, settings.device_token)
        monkeypatch.setattr(window.relay_client, "rename_device", requested.append)
        window._edit_device_name()
        assert requested == ["收购电脑02"]
        assert settings.display_name == "旧名称"
        window._on_device_profile({"display_name": "收购电脑02"})
        window._on_profile_result(True, "已保存")
        assert settings.display_name == "收购电脑02"
        assert saved == ["收购电脑02"]
        assert window.device_name_label.text() == "收购电脑02"
        assert window.rename_button.isEnabled()
        window._on_device_profile({"display_name": "主机改名"})
        assert window.device_name_label.text() == "主机改名"
    finally:
        window.close()


def test_six_cards_fit_560_width_without_horizontal_or_vertical_scrolling(monkeypatch):
    app = QApplication.instance()
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    try:
        for index in range(6):
            account = parse_account_info(f"[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 角色{index}[5458823{index}])]")
            panel = window._make_output_panel(index + 1, account)[0]
            window.sessions[index + 1] = SimpleNamespace(output_panel=panel)
        window._update_output_panels()
        window.show()
        app.processEvents()
        assert (window.width(), window.height()) == (560, 1100)
        assert window.output_scroll.horizontalScrollBar().maximum() == 0
        assert window.output_scroll.verticalScrollBar().maximum() == 0
    finally:
        window.sessions.clear()
        window.close()
        app.processEvents()


def test_mini_mode_preserves_runtime_and_restores_geometry(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    window._authenticated = True
    window.login_content.hide()
    window.full_content.show()
    for timer in (window.scan_timer, window.window_state_timer, window.occlusion_timer, window.status_timer):
        timer.start()
    window.move(120, 80)
    window.show()
    app.processEvents()
    original_position = window.pos()
    timers = (
        window.scan_timer,
        window.window_state_timer,
        window.occlusion_timer,
        window.status_timer,
    )
    relay_client = window.relay_client
    try:
        window.mini_button.click()
        app.processEvents()

        assert window._mini_mode
        assert (window.width(), window.height()) == (300, 90)
        assert window.full_content.isHidden()
        assert window.mini_content.isVisible()
        assert window.mini_status_label.text() == "监控中"
        assert window.mini_count_label.text() == "监控窗口 0"
        assert window.relay_client is relay_client
        assert all(timer.isActive() for timer in timers)
        assert not window.monitoring_paused

        window.move(360, 240)
        window.restore_button.click()
        app.processEvents()

        assert not window._mini_mode
        assert (window.width(), window.height()) == (560, 1100)
        assert window.pos() == original_position
        assert window.full_content.isVisible()
        assert window.mini_content.isHidden()
        assert window.relay_client is relay_client
        assert all(timer.isActive() for timer in timers)
    finally:
        window.close()
        app.processEvents()


def test_mini_status_tracks_pause_and_monitored_window_count(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    panels = []
    try:
        window._enter_mini_mode()
        window._set_monitoring_paused(True)
        assert window.mini_status_label.text() == "已暂停"

        for index in range(2):
            account = parse_account_info(f"[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 角色{index}[5458823{index}])]")
            panel = window._make_output_panel(index + 1, account)[0]
            panels.append(panel)
            window.sessions[index + 1] = SimpleNamespace(output_panel=panel)
        window._update_output_panels()
        assert window.mini_count_label.text() == "监控窗口 2"

        window.sessions.pop(1)
        window._update_output_panels()
        assert window.mini_count_label.text() == "监控窗口 1"

        window.sessions.clear()
        window._set_monitoring_paused(False)
        assert window.mini_status_label.text() == "监控中"
    finally:
        window.sessions.clear()
        for panel in panels:
            panel.deleteLater()
        window.close()
        app.processEvents()


def test_account_information_is_parsed_from_window_title() -> None:
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")

    assert account.secondary_region == "秦淮风光"
    assert account.role_name == "清风知夏"
    assert account.role_id == "54588235"


def test_only_real_game_client_titles_are_accepted() -> None:
    assert is_mhxy_game_window_title("【梦幻西游ONLINE】江苏1区[秦淮风光]")
    assert is_mhxy_game_window_title("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    assert not is_mhxy_game_window_title("江苏1区[秦淮风光] - 清风知夏[54588235]")
    assert not is_mhxy_game_window_title("记事本")


def test_minimized_window_updates_status_without_alert(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.is_window_available", lambda _hwnd: True)
    minimized = True
    current_time = 0.0
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.is_window_minimized", lambda _hwnd: minimized)
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.monotonic", lambda: current_time)

    window = InventoryMonitorWindow()
    window._authenticated = True
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        window,
        "_show_alert",
        lambda _icon, title, message, _details="": alerts.append((title, message)),
    )
    try:
        account = parse_account_info("江苏1区【秦淮风光】清风知夏【54588235】")
        output_panel, _title, output_preview, output_status, output_detail, output_occlusion = (
            window._make_output_panel(0x1234, account)
        )
        session = SimpleNamespace(
            hwnd=0x1234,
            account=account,
            minimized=False,
            minimized_since=None,
            minimized_alert_active=False,
            removing=False,
            output_status=output_status,
            output_detail=output_detail,
            output_preview=output_preview,
            output_occlusion=output_occlusion,
        )
        window.sessions[0x1234] = session
        window._poll_active_window_states()

        assert session.minimized
        assert "检测已暂停" in output_status.text()
        assert alerts == []

        minimized = False
        current_time = 1.0
        window._poll_active_window_states()
        minimized = True
        current_time = 2.0
        window._poll_active_window_states()
        current_time = 7.0
        window._poll_active_window_states()

        assert alerts == []
    finally:
        window.sessions.clear()
        output_panel.deleteLater()
        window.close()
        app.processEvents()


def test_remove_button_belongs_to_corresponding_output_panel(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    removed: list[int] = []
    monkeypatch.setattr(window, "_request_remove_window", lambda hwnd: removed.append(hwnd))
    panel, title, *_ = window._make_output_panel(0x1234, account)
    try:
        assert title.text() == "秦淮风光 / 清风知夏"
        remove_buttons = [button for button in panel.findChildren(QPushButton) if button.text() == "移除"]
        assert len(remove_buttons) == 1
        remove_buttons[0].click()
        assert removed == [0x1234]
    finally:
        panel.deleteLater()
        window.close()
        app.processEvents()


def test_occlusion_percentage_is_displayed_without_source(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    output_panel, _title, _preview, _output_status, _detail, output_occlusion = window._make_output_panel(
        0x1234, account
    )
    session = SimpleNamespace(
        hwnd=0x1234,
        account=account,
        output_occlusion=output_occlusion,
        occlusion_alert_active=False,
    )
    status = WindowOcclusionStatus(
        hwnd=0x1234,
        target_area=10_000,
        occluded_area=2_500,
        ratio=0.25,
        occluders=(OccludingWindow(0x5678, "记事本", 100, "Notepad", 2_500),),
    )
    try:
        window._apply_occlusion(session, status)

        assert output_occlusion.text() == "遮挡 25.0%｜最小化 否"
        assert "记事本" not in output_occlusion.text()
    finally:
        output_panel.deleteLater()
        window.close()
        app.processEvents()


def test_occlusion_never_creates_an_alert_record(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    output_panel, _title, _preview, _status, _detail, output_occlusion = window._make_output_panel(0x1234, account)
    session = SimpleNamespace(
        account=account,
        output_occlusion=output_occlusion,
        occlusion_alert_active=False,
        occlusion_candidate_since=None,
    )
    sent: list[str] = []
    monkeypatch.setattr(window, "_send_host_alert", lambda *_args, **_kwargs: sent.append("sent"))
    try:
        window._apply_occlusion(
            session,
            WindowOcclusionStatus(0x1234, 10_000, 8_000, 0.8, ()),
        )

        assert output_occlusion.text() == "遮挡 80.0%｜最小化 否"
        assert sent == []
    finally:
        output_panel.deleteLater()
        window.close()
        app.processEvents()


def test_enabled_capacity_alert_reports_full_inventory_to_host(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    window._own_stall_character_ids = frozenset({account.role_id})
    session = SimpleNamespace(account=account, capacity_alert_active=False, capacity_candidate_since=None,
                              stall_mode="buy")
    sent: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(window, "_send_host_alert", lambda _session, kind, **data: sent.append((kind, data)))
    try:
        window._update_capacity_alert(session, {"status": "not_full", "empty_count": 1}, 0.0)
        window._update_capacity_alert(session, {"status": "full", "empty_count": 0}, 1.0)
        window._update_capacity_alert(session, {"status": "full", "empty_count": 0, "confidence": 0.96}, 6.0)
        window._update_capacity_alert(session, {"status": "full", "empty_count": 0}, 7.0)

        assert [kind for kind, _data in sent] == ["inventory_full"]
        assert sent[0][1]["empty_slots"] == 0
        assert sent[0][1]["confidence"] == 0.96
    finally:
        window.close()
        app.processEvents()


def test_capacity_alert_is_reported_even_when_local_sound_is_disabled(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    window._own_stall_character_ids = frozenset({account.role_id})
    session = SimpleNamespace(account=account, capacity_alert_active=False, capacity_candidate_since=None,
                              stall_mode="buy")
    sent: list[str] = []
    monkeypatch.setattr(window, "_send_host_alert", lambda *_args, **_kwargs: sent.append("sent"))
    try:
        window._update_capacity_alert(session, {"status": "full", "empty_count": 0}, 0.0)
        window._update_capacity_alert(session, {"status": "full", "empty_count": 0}, 10.0)

        assert sent == ["sent"]
        assert session.capacity_alert_active
    finally:
        window.close()
        app.processEvents()


def test_slave_applies_account_alert_settings_without_using_local_alert_mode(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    try:
        window.slave_settings.inventory_capacity_alert_enabled = False
        window._apply_alert_settings({"local_alert_enabled": True, "capacity_threshold": 4, "cash_threshold_wan": 500})
        assert window._local_alert_enabled is True
        assert window._capacity_threshold == 4
        window._apply_alert_settings({"local_alert_enabled": False, "capacity_threshold": 99, "cash_threshold_wan": 500})
        assert window._local_alert_enabled is True and window._capacity_threshold == 4
        window._apply_alert_settings({"local_alert_enabled": False, "capacity_threshold": 0, "cash_threshold_wan": 300,
                                      "own_stall_character_ids": ["54588235"]})
        assert window._local_alert_enabled is False and window._capacity_threshold == 0
        assert window._cash_threshold_wan == 300
        assert window._own_stall_character_ids == frozenset({"54588235"})
    finally:
        window.close()
        app.processEvents()


def test_cash_low_alert_uses_fresh_confirmed_cash_and_rearms_after_recovery(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    window._cash_threshold_wan = 500
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    window._own_stall_character_ids = frozenset({account.role_id})
    session = SimpleNamespace(account=account, cash_alert_active=False, stall_mode="buy")
    sent = []
    local = []
    monkeypatch.setattr(window, "_send_host_alert", lambda *args, **kwargs: sent.append((args, kwargs)))
    monkeypatch.setattr(window, "_show_alert", lambda *args, **kwargs: local.append((args, kwargs)))
    try:
        window._update_cash_alert(session, {"cash": 4999999, "cash_readable": False})
        assert sent == []
        window._update_cash_alert(session, {"cash": 5000000, "cash_readable": True})
        assert sent == []
        window._update_cash_alert(session, {"cash": 4999999, "cash_readable": True})
        window._update_cash_alert(session, {"cash": 4990000, "cash_readable": True})
        assert len(sent) == 1 and sent[0][0][1] == "cash_low"
        assert sent[0][1]["details"] == {"cash": 4999999, "threshold_wan": 500}
        assert local == []
        window._update_cash_alert(session, {"cash": 5000000, "cash_readable": True})
        window._local_alert_enabled = True
        window._update_cash_alert(session, {"cash": 4900000, "cash_readable": True})
        assert len(sent) == 2 and len(local) == 1
    finally:
        window.close()
        app.processEvents()


def test_capacity_and_cash_alerts_apply_only_to_my_stall_roles(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    session = SimpleNamespace(account=account, stall_mode="sell", cash_alert_active=False,
                              capacity_alert_active=False, capacity_candidate_since=None)
    sent = []
    monkeypatch.setattr(window, "_send_host_alert", lambda *args, **kwargs: sent.append(args[1]))
    try:
        capacity = {"status": "full", "empty_count": 0, "confidence": 0.99}
        cash = {"cash": 4_000_000, "cash_readable": True}
        window._update_capacity_alert(session, capacity, 10.0)
        window._update_capacity_alert(session, capacity, 20.0)
        window._update_cash_alert(session, cash)
        assert sent == []

        window._own_stall_character_ids = frozenset({account.role_id})
        window._update_capacity_alert(session, capacity, 30.0)
        window._update_capacity_alert(session, capacity, 35.0)
        window._update_cash_alert(session, cash)
        assert sent == ["inventory_full", "cash_low"]

        window._capacity_enabled = False
        window._cash_low_enabled = False
        session.capacity_alert_active = False
        session.cash_alert_active = False
        window._update_capacity_alert(session, capacity, 40.0)
        window._update_capacity_alert(session, capacity, 50.0)
        window._update_cash_alert(session, cash)
        assert sent == ["inventory_full", "cash_low"]
    finally:
        window.close()
        app.processEvents()


def test_server_stamina_alert_rings_current_device_once_and_acknowledges(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    shown = []
    acknowledged = []
    monkeypatch.setattr(window, "_show_alert", lambda *args, **kwargs: shown.append((args, kwargs)))
    monkeypatch.setattr(window.relay_client, "acknowledge_local_alert", acknowledged.append)
    window._authenticated = True
    profile = {"alert_settings": {"local_alert_enabled": True, "capacity_threshold": 0,
                                  "cash_threshold_wan": 500, "stamina_full_enabled": True},
               "local_stamina_alerts": [{"id": 42, "server_name": "秦淮风光",
                                         "role_name": "清风知夏", "details": {"source": "predicted"}}]}
    try:
        window._on_device_profile(profile)
        window._on_device_profile(profile)
        assert len(shown) == 1
        assert "体力已满" in shown[0][0][1]
        assert acknowledged == [42, 42]
    finally:
        window.close()
        app.processEvents()


def test_server_sold_alert_rings_current_device_once(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    shown = []
    acknowledged = []
    monkeypatch.setattr(window, "_show_alert", lambda *args, **kwargs: shown.append((args, kwargs)))
    monkeypatch.setattr(window.relay_client, "acknowledge_local_alert", acknowledged.append)
    window._authenticated = True
    profile = {"alert_settings": {"local_alert_enabled": True, "capacity_threshold": 0,
                                  "cash_threshold_wan": 500},
               "local_alerts": [{"id": 43, "event_type": "stall_sold", "server_name": "秦淮风光",
                                 "role_name": "清风知夏", "details": {"previous_count": 20,
                                                                       "current_count": 19, "sold_count": 1}}]}
    try:
        window._on_device_profile(profile)
        window._on_device_profile(profile)
        assert len(shown) == 1
        assert shown[0][0][1] == "商品售出"
        assert "20" in shown[0][0][2] and "19" in shown[0][0][2]
        assert acknowledged == [43, 43]
    finally:
        window.close()
        app.processEvents()


def test_account_local_switch_plays_alarm_after_five_seconds(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    window._local_alert_enabled = True
    window._capacity_threshold = 3
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    window._own_stall_character_ids = frozenset({account.role_id})
    session = SimpleNamespace(account=account, capacity_alert_active=False, capacity_candidate_since=None,
                              stall_mode="buy")
    sent: list[tuple[str, dict[str, object]]] = []
    local: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(window, "_send_host_alert", lambda _session, kind, **data: sent.append((kind, data)))
    monkeypatch.setattr(window, "_show_alert", lambda *args, **kwargs: local.append((args, kwargs)))
    try:
        window._update_capacity_alert(session, {"status": "not_full", "empty_count": 3}, 10.0)
        window._update_capacity_alert(session, {"status": "not_full", "empty_count": 3}, 14.9)
        assert sent == []
        assert local == []

        window._update_capacity_alert(
            session,
            {"status": "not_full", "empty_count": 2, "confidence": 0.95},
            15.0,
        )

        assert len(sent) == 1 and sent[0][0] == "inventory_capacity"
        assert len(local) == 1
        assert local[0][1]["with_sound"] is True
    finally:
        window.close()
        app.processEvents()


def test_non_capacity_inventory_states_do_not_create_alerts(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    window._authenticated = True
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    window.sessions[0x1234] = SimpleNamespace(account=account, minimized=False)
    alerts: list[str] = []
    monkeypatch.setattr(window, "_show_alert", lambda *_args, **_kwargs: alerts.append("local"))
    monkeypatch.setattr(window, "_send_host_alert", lambda *_args, **_kwargs: alerts.append("host"))
    try:
        window._on_event(
            0x1234,
            SimpleNamespace(type="inventory_not_open", display_time="12:00:00", display_name="背包未打开"),
        )
        window._on_event(
            0x1234,
            SimpleNamespace(
                type="inventory_detection_blocked",
                display_time="12:00:01",
                display_name="背包检测被遮挡",
            ),
        )

        assert alerts == []
        assert "背包未打开" in window.log.toPlainText()
        assert "背包检测被遮挡" in window.log.toPlainText()
    finally:
        window.sessions.clear()
        window.close()
        app.processEvents()


def test_output_panel_core_information_does_not_overlap(monkeypatch) -> None:
    import numpy as np

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    panel, title, preview, status, detail, window_state = window._make_output_panel(0x1234, account)
    panel.setParent(window.output_container)
    panel.show()
    app.processEvents()
    try:
        session = SimpleNamespace(output_status=status, output_detail=detail)
        window._apply_signal(
            session,
            {"status": "not_full", "empty_count": 6, "occupied_count": 14, "confidence": 0.96},
        )

        assert "现金 未识别" in detail.text()
        assert "空格 6" in detail.text() and "置信度 96%" in detail.text()
        assert (preview.width(), preview.height()) == (148, 111)
        window._display_preview(preview, np.zeros((480, 640, 3), dtype=np.uint8))
        assert (preview.pixmap().width(), preview.pixmap().height()) == (148, 111)
        widgets = (title, preview, status, detail, window_state)
        assert all(first.geometry().bottom() < second.geometry().top() for first, second in pairwise(widgets))
    finally:
        panel.deleteLater()
        window.close()
        app.processEvents()


def test_output_panel_count_matches_monitored_window_count(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    accounts = [
        parse_account_info(f"[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 角色{index}[5458823{index}])]")
        for index in range(1, 9)
    ]
    panels = [window._make_output_panel(index, account)[0] for index, account in enumerate(accounts, start=1)]
    window.sessions = {
        index: SimpleNamespace(output_panel=panel, account=account)
        for index, (panel, account) in enumerate(zip(panels, accounts, strict=True), start=1)
    }
    try:
        window._update_output_panels()
        window.show()
        app.processEvents()

        assert window.outputs_group.title() == "窗口输出（8）"
        assert window.output_grid.getItemPosition(window.output_grid.indexOf(panels[0]))[:2] == (0, 0)
        assert window.output_grid.getItemPosition(window.output_grid.indexOf(panels[3]))[:2] == (1, 0)
        assert window.output_grid.getItemPosition(window.output_grid.indexOf(panels[4]))[:2] == (1, 1)
        assert window.output_grid.getItemPosition(window.output_grid.indexOf(panels[7]))[:2] == (2, 1)
        assert window.output_scroll.verticalScrollBar().maximum() == 0
        assert window.output_scroll.horizontalScrollBar().maximum() == 0
    finally:
        window.sessions.clear()
        for panel in panels:
            panel.deleteLater()
        window.close()
        app.processEvents()


def test_background_monitoring_button_minimizes_main_window(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    minimized: list[bool] = []
    monkeypatch.setattr(window, "showMinimized", lambda: minimized.append(True))
    try:
        window.background_button.click()

        assert minimized == [True]
    finally:
        window.close()
        app.processEvents()


def test_one_click_minimize_pauses_and_includes_unmonitored_game_windows(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    window._authenticated = True
    windows = [WindowInfo(index, f"【梦幻西游 ONLINE】角色{index}", 100 + index, "GameWindow") for index in range(1, 5)]
    minimized: set[int] = set()
    paused: list[bool] = []
    window.sessions = {1: object(), 2: object(), 3: object()}
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.list_capturable_windows", lambda: windows)
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.is_window_available", lambda hwnd: hwnd in range(1, 5))
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.is_window_minimized", lambda hwnd: hwnd in minimized)
    monkeypatch.setattr(
        "hoyo_analyzer.multi_inventory_app.minimize_window",
        lambda hwnd: minimized.add(hwnd) is None,
    )
    monkeypatch.setattr(window, "_set_monitoring_paused", lambda value: paused.append(value))
    try:
        window.minimize_all_button.click()
        window.minimize_verify_timer.stop()

        assert paused == [True]
        assert minimized == {1, 2, 3, 4}
        assert not window.minimize_all_button.isEnabled()

        window.sessions.clear()
        window._finish_minimize_all_game_windows()
        assert window.minimize_all_button.isEnabled()
        assert "4 个梦幻西游窗口已全部最小化" in window.log.toPlainText()
    finally:
        window.sessions.clear()
        window.close()
        app.processEvents()


def test_monitoring_button_pauses_alerts_and_resumes_from_fresh_state(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    window._authenticated = True
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    output_panel, title, preview, status, detail, occlusion = window._make_output_panel(0x1234, account)
    pause_states: list[bool] = []
    session = SimpleNamespace(
        hwnd=0x1234,
        account=account,
        worker=SimpleNamespace(set_paused=lambda paused: pause_states.append(paused)),
        output_panel=output_panel,
        output_title=title,
        output_preview=preview,
        output_status=status,
        output_detail=detail,
        output_occlusion=occlusion,
        minimized=False,
        minimized_since=None,
        minimized_alert_active=False,
        removing=False,
        capacity_alert_active=True,
        capacity_candidate_since=1.0,
        occlusion_alert_active=True,
        occlusion_candidate_since=1.0,
    )
    window.sessions[0x1234] = session
    applied: list[dict[str, object]] = []
    monkeypatch.setattr(window, "_apply_signal", lambda _session, signal: applied.append(signal))
    monkeypatch.setattr(window, "_poll_active_window_states", lambda: None)
    monkeypatch.setattr(window, "_poll_window_occlusion", lambda: None)
    try:
        window.monitoring_button.click()

        assert window.monitoring_paused
        assert window.monitoring_button.text() == "开始监控"
        assert pause_states == [True]
        assert status.text() == "监控已手动暂停"
        assert not session.capacity_alert_active
        assert not session.occlusion_alert_active
        window._on_analysis(0x1234, {"status": "full"})
        assert applied == []

        window.monitoring_button.click()

        assert not window.monitoring_paused
        assert window.monitoring_button.text() == "暂停监控"
        assert pause_states == [True, False]
        assert status.text() == "监控已恢复，正在重新检测…"
    finally:
        window.sessions.clear()
        output_panel.deleteLater()
        window.close()
        app.processEvents()


def test_new_desktop_alert_replaces_existing_alert(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    played: list[bool] = []
    stopped: list[bool] = []
    monkeypatch.setattr(window, "_play_alarm_sound", lambda: played.append(True))
    monkeypatch.setattr(window, "_stop_alarm_sound", lambda: stopped.append(True))
    try:
        window._show_alert(QMessageBox.Icon.Warning, "告警一", "第一条告警")
        app.processEvents()
        first = next(iter(window.alerts))
        screen = window.screen() or QApplication.primaryScreen()
        assert screen is not None
        available = screen.availableGeometry()

        assert first.isVisible()
        assert first.button(QMessageBox.StandardButton.Close).text() == "确认"
        assert abs((available.right() - 16) - first.geometry().right()) <= 2
        assert abs((available.bottom() - 16) - first.geometry().bottom()) <= 2

        window._show_alert(QMessageBox.Icon.Warning, "告警二", "第二条告警")
        app.processEvents()
        assert played == []
        assert len(window.alerts) == 1
        assert next(iter(window.alerts)) is first
        assert first.windowTitle() == "告警二"
        assert first.text() == "第二条告警"

        first.close()
        app.processEvents()
        assert stopped == []
        assert window.alerts == set()
    finally:
        window.close()
        app.processEvents()


def test_sounding_desktop_alert_loops_until_user_closes_it(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    played: list[bool] = []
    stopped: list[bool] = []
    monkeypatch.setattr(window, "_play_alarm_sound", lambda: played.append(True))
    monkeypatch.setattr(window, "_stop_alarm_sound", lambda: stopped.append(True))
    try:
        window._show_alert(
            QMessageBox.Icon.Critical,
            "背包容量提醒",
            "达到空格阈值",
            with_sound=True,
        )
        app.processEvents()
        alert = next(iter(window.alerts))

        assert played == [True]
        assert stopped == []
        assert alert.button(QMessageBox.StandardButton.Close).text() == "确认并停止声音"

        alert.close()
        app.processEvents()
        assert stopped == [True]
        assert window.alerts == set()
    finally:
        window.close()
        app.processEvents()
