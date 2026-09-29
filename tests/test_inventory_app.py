import os
from itertools import pairwise
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from hoyo_analyzer.inventory_app import (
    APP_NAME,
    MAX_MONITORED_WINDOWS,
    _analysis_signal,
    is_mhxy_game_window_title,
    parse_account_info,
)
from hoyo_analyzer.inventory_fullness import InventoryAnalysis, InventoryStatus, SlotResult, SlotState
from hoyo_analyzer.multi_inventory_app import InventoryMonitorWindow
from hoyo_analyzer.slave_settings import ALERT_LOCAL, ALERT_NONE, SlaveSettings
from hoyo_analyzer.wgc import OccludingWindow, WindowOcclusionStatus


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
    try:
        assert window.windowTitle() == APP_NAME
        assert (window.width(), window.height()) == (800, 600)
        assert window.add_window_button.text() == "＋ 增加窗口"
        assert window.monitoring_button.text() == "暂停监控"
        assert window.settings_button.text() == "设置"
        assert window.background_button.text() == "后台监控"
        assert window.host_status_label.text() == "云端：未连接"
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
    finally:
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


def test_minimized_warning_setting_controls_popup(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.is_window_available", lambda _hwnd: True)
    minimized = True
    current_time = 0.0
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.is_window_minimized", lambda _hwnd: minimized)
    monkeypatch.setattr("hoyo_analyzer.multi_inventory_app.monotonic", lambda: current_time)

    window = InventoryMonitorWindow()
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
        window.slave_settings.minimize_alert = ALERT_NONE
        window._poll_active_window_states()

        assert session.minimized
        assert "检测已暂停" in output_status.text()
        assert alerts == []

        minimized = False
        current_time = 1.0
        window._poll_active_window_states()
        window.slave_settings.minimize_alert = ALERT_LOCAL
        minimized = True
        current_time = 2.0
        window._poll_active_window_states()
        current_time = 7.0
        window._poll_active_window_states()

        assert alerts == [("背包检测已暂停", "梦幻西游窗口已连续最小化 5 秒，请恢复窗口后继续检测。")]
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


def test_occlusion_warning_triggers_once_when_threshold_is_crossed(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    window.slave_settings.occlusion_threshold_percent = 30
    window.slave_settings.occlusion_alert = ALERT_LOCAL
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    session = SimpleNamespace(account=account, occlusion_alert_active=False, occlusion_candidate_since=None)
    alerts: list[str] = []
    monkeypatch.setattr(window, "_show_alert", lambda _icon, title, _message, _details="": alerts.append(title))
    try:
        window._update_occlusion_alert(session, 29.9, 0.0)
        window._update_occlusion_alert(session, 30.0, 1.0)
        window._update_occlusion_alert(session, 80.0, 6.0)
        window._update_occlusion_alert(session, 10.0, 7.0)
        window._update_occlusion_alert(session, 30.0, 8.0)
        window._update_occlusion_alert(session, 30.0, 13.0)

        assert alerts == ["游戏窗口被遮挡", "游戏窗口被遮挡"]
    finally:
        window.close()
        app.processEvents()


def test_inventory_warning_uses_configured_remaining_slot_threshold(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    window.slave_settings.inventory_empty_threshold = 3
    window.slave_settings.inventory_alert = ALERT_LOCAL
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    session = SimpleNamespace(account=account, capacity_alert_active=False, capacity_candidate_since=None)
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        window,
        "_show_alert",
        lambda _icon, title, message, _details="": alerts.append((title, message)),
    )
    try:
        window._update_capacity_alert(session, {"status": "not_full", "empty_count": 4}, 0.0)
        window._update_capacity_alert(session, {"status": "almost_full", "empty_count": 3}, 1.0)
        window._update_capacity_alert(session, {"status": "almost_full", "empty_count": 2}, 6.0)
        window._update_capacity_alert(session, {"status": "not_full", "empty_count": 5}, 7.0)
        window._update_capacity_alert(session, {"status": "almost_full", "empty_count": 3}, 8.0)
        window._update_capacity_alert(session, {"status": "almost_full", "empty_count": 3}, 13.0)

        assert [title for title, _message in alerts] == ["背包容量警告", "背包容量警告"]
        assert "只剩 2 个空格" in alerts[0][1]
    finally:
        window.close()
        app.processEvents()


def test_inventory_state_alerts_follow_their_own_settings(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
    account = parse_account_info("[梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])]")
    window.sessions[0x1234] = SimpleNamespace(account=account, minimized=False)
    alerts: list[str] = []
    monkeypatch.setattr(window, "_show_alert", lambda _icon, title, _message, _details="": alerts.append(title))
    try:
        window.slave_settings.inventory_not_open_alert = ALERT_NONE
        window.slave_settings.inventory_blocked_alert = ALERT_LOCAL
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

        assert alerts == ["背包检测被遮挡"]
    finally:
        window.sessions.clear()
        window.close()
        app.processEvents()


def test_output_panel_core_information_does_not_overlap(monkeypatch) -> None:
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

        assert detail.text() == "空格 6｜已拥有 14｜置信度 96%"
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
        assert window.output_grid.getItemPosition(window.output_grid.indexOf(panels[3]))[:2] == (0, 3)
        assert window.output_grid.getItemPosition(window.output_grid.indexOf(panels[4]))[:2] == (1, 0)
        assert window.output_grid.getItemPosition(window.output_grid.indexOf(panels[7]))[:2] == (1, 3)
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


def test_monitoring_button_pauses_alerts_and_resumes_from_fresh_state(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(InventoryMonitorWindow, "refresh_windows", lambda self: None)
    window = InventoryMonitorWindow()
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
    window.slave_settings.local_alert_style = "popup_sound"
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
        assert first.button(QMessageBox.StandardButton.Close).text() == "确认并停止声音"
        assert abs((available.right() - 16) - first.geometry().right()) <= 2
        assert abs((available.bottom() - 16) - first.geometry().bottom()) <= 2

        window._show_alert(QMessageBox.Icon.Warning, "告警二", "第二条告警")
        app.processEvents()
        assert played == [True]
        assert len(window.alerts) == 1
        assert next(iter(window.alerts)) is first
        assert first.windowTitle() == "告警二"
        assert first.text() == "第二条告警"

        first.close()
        app.processEvents()
        assert stopped == [True]
        assert window.alerts == set()
    finally:
        window.close()
        app.processEvents()
