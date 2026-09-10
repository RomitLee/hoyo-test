import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hoyo_analyzer.config import AppConfig
from hoyo_analyzer.gui import WINDOW_HEIGHT, WINDOW_WIDTH, MainWindow, RealtimeWorker
from hoyo_analyzer.wgc import OccludingWindow, WindowInfo, WindowOcclusionStatus


def test_capture_actions_are_disabled_without_game_window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_refresh_windows", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_occlusion_status_async", lambda self: None)

    window = MainWindow()
    try:
        assert not window.start_button.isEnabled()
        assert not window.stop_button.isEnabled()
        assert "未检测到梦幻西游" in window.start_button.toolTip()
    finally:
        window.close()
        app.processEvents()


def test_minimized_window_is_allowed_for_background_capture(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_refresh_windows", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_occlusion_status_async", lambda self: None)
    monkeypatch.setattr("hoyo_analyzer.gui.is_window_available", lambda _hwnd: True)
    monkeypatch.setattr("hoyo_analyzer.gui.is_window_minimized", lambda _hwnd: True)
    monkeypatch.setattr("hoyo_analyzer.gui.get_window_title", lambda _hwnd: "梦幻西游")

    window = MainWindow()
    try:
        window.selected_window_hwnd = 0x1234
        window.selected_window_name = "梦幻西游 [已最小化]"
        window._on_target_changed()

        assert window._validate_current_target(show_message=False)
        assert window.start_button.isEnabled()
        assert "保持采集会话" in window.target_label.text()
    finally:
        window.close()
        app.processEvents()


def test_unoccluded_status_does_not_override_active_capture(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_refresh_windows", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_occlusion_status_async", lambda self: None)
    monkeypatch.setattr("hoyo_analyzer.gui.is_window_available", lambda _hwnd: True)

    window = MainWindow()
    try:
        window.selected_window_hwnd = 0x1234
        window.selected_window_name = "梦幻西游"
        window._on_target_changed()
        window.running = True
        window._set_capture_state("captured")

        status = WindowOcclusionStatus(
            hwnd=0x1234,
            target_area=10_000,
            occluded_area=0,
            ratio=0.0,
            occluders=(),
        )
        window._on_occlusion_status_ready(status)

        assert "#e8f8ef" in window.captured_state_label.styleSheet()
        assert "#fff7e6" not in window.not_captured_state_label.styleSheet()
    finally:
        window.close()
        app.processEvents()


def test_one_percent_occlusion_shows_warning_banner(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_refresh_windows", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_occlusion_status_async", lambda self: None)
    monkeypatch.setattr("hoyo_analyzer.gui.is_window_available", lambda _hwnd: True)

    window = MainWindow()
    try:
        window.selected_window_hwnd = 0x1234
        window.selected_window_name = "梦幻西游"
        window._on_target_changed()
        status = WindowOcclusionStatus(
            hwnd=0x1234,
            target_area=10_000,
            occluded_area=100,
            ratio=0.01,
            occluders=(OccludingWindow(0x5678, "记事本", 200, "Notepad", 100),),
        )
        window._on_occlusion_status_ready(status)

        assert not hasattr(window, "occlusion_banner")
        assert "窗口被遮挡 1.0%" in window.occluded_state_label.text()
        assert "记事本" in window.occluded_state_label.toolTip()
        assert "检测到遮挡" in window.occlusion_label.text()
    finally:
        window.close()
        app.processEvents()


def test_window_list_only_contains_game_and_schedules_auto_start(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_refresh_windows", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_occlusion_status_async", lambda self: None)
    monkeypatch.setattr("hoyo_analyzer.gui.is_window_available", lambda _hwnd: True)
    monkeypatch.setattr("hoyo_analyzer.gui.get_window_title", lambda _hwnd: "梦幻西游")
    started = []
    monkeypatch.setattr(MainWindow, "_start_automatically", lambda self: started.append(self._current_window_hwnd()))

    window = MainWindow()
    window.timer.stop()
    try:
        window._on_windows_ready(
            [
                WindowInfo(0x1111, "Google Chrome", 10, "Chrome_WidgetWin_1"),
                WindowInfo(0x2222, "梦幻西游 ONLINE", 20, "GameWindow"),
            ]
        )
        app.processEvents()

        assert not hasattr(window, "source_combo")
        assert not hasattr(window, "window_combo")
        assert window._current_window_hwnd() == 0x2222
        assert "梦幻西游 ONLINE" in window.window_text_label.text()
        assert started == [0x2222]
        assert not window.game_status_banner.isVisibleTo(window)
    finally:
        window.close()
        app.processEvents()


def test_non_game_windows_do_not_enable_monitoring(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_refresh_windows", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_occlusion_status_async", lambda self: None)

    window = MainWindow()
    window.timer.stop()
    try:
        window._on_windows_ready([WindowInfo(0x1111, "Google Chrome", 10, "Chrome_WidgetWin_1")])

        assert window._current_window_hwnd() is None
        assert not window.start_button.isEnabled()
        assert not window.stop_button.isEnabled()
        assert "当前不能监控" in window.game_status_banner.text()
    finally:
        window.close()
        app.processEvents()


def test_fixed_product_layout_and_square_preview(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_refresh_windows", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_occlusion_status_async", lambda self: None)

    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        assert window.windowTitle() == "梦幻西游-希联文超助手"
        assert (window.width(), window.height()) == (WINDOW_WIDTH, WINDOW_HEIGHT)
        assert (window.minimumWidth(), window.minimumHeight()) == (WINDOW_WIDTH, WINDOW_HEIGHT)
        assert (window.maximumWidth(), window.maximumHeight()) == (WINDOW_WIDTH, WINDOW_HEIGHT)
        assert window.preview.width() == window.preview.height() == 300
        assert window.preview.width() == window.preview.parentWidget().width()
        assert window.events.parentWidget() is window.game_status_banner.parentWidget()
        assert window.events.width() == window.game_status_banner.width()
    finally:
        window.close()
        app.processEvents()


def test_login_panel_and_capture_states_are_visible_in_top_left(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_refresh_windows", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_occlusion_status_async", lambda self: None)

    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        assert window.login_state_label.text() == "未登录"
        assert window.login_button.isVisibleTo(window)
        assert window.login_button.width() <= 50
        assert window.captured_state_label.text() == "● 采集中"
        assert window.occluded_state_label.text() == "● 窗口被遮挡"
        assert window.not_captured_state_label.text() == "● 未采集"

        window.set_logged_in_user("子霖")
        assert window.login_state_label.text() == "子霖"
        assert not window.login_button.isVisibleTo(window)
        assert "已登录" in window.login_hint_label.text()

        window._set_capture_state("captured")
        assert "#e8f8ef" in window.captured_state_label.styleSheet()
        assert "#f1f4f8" in window.occluded_state_label.styleSheet()

        window._set_capture_state("occluded")
        assert "#fff0f0" in window.occluded_state_label.styleSheet()

        window._set_capture_state("not_captured")
        assert "#fff7e6" in window.not_captured_state_label.styleSheet()
    finally:
        window.close()
        app.processEvents()


def test_social_navigation_and_ai_analysis_default_page(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_refresh_windows", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_occlusion_status_async", lambda self: None)

    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        assert list(window.nav_buttons) == ["AI分析", "聊天室", "装备鉴赏", "希联商行", "个人中心"]
        assert window.nav_buttons["AI分析"].isChecked()
        assert window.ai_content.isVisibleTo(window)

        window.nav_buttons["聊天室"].click()
        assert not window.ai_content.isVisibleTo(window)
        assert window.page_status_label.isVisibleTo(window)
        assert "聊天室" in window.page_status_label.text()

        window.nav_buttons["AI分析"].click()
        assert window.ai_content.isVisibleTo(window)
        assert not window.page_status_label.isVisibleTo(window)
    finally:
        window.close()
        app.processEvents()


def test_capture_status_is_below_window_controls(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_refresh_windows", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_occlusion_status_async", lambda self: None)

    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        assert window.capture_status_card.geometry().top() >= window.controls.geometry().bottom()
        assert window.events.parentWidget() is window.capture_status_card.parentWidget()
        assert window.events.width() == window.capture_status_card.width()
    finally:
        window.close()
        app.processEvents()


def test_auto_capture_banner_is_above_expanded_runtime_status(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_refresh_windows", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_occlusion_status_async", lambda self: None)

    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        status = window.target_label.parentWidget()
        assert status.geometry().top() >= window.preview.geometry().bottom()
        assert window.game_status_banner.geometry().bottom() < window.events.geometry().top()
        assert status.minimumHeight() >= 116
        assert status.height() >= 116
    finally:
        window.close()
        app.processEvents()


def test_preview_mailbox_keeps_only_one_downscaled_frame():
    worker = RealtimeWorker("windows-graphics-capture", 0, None, AppConfig())
    worker._last_preview_emit_at = -1.0
    image = __import__("numpy").zeros((768, 1024, 3), dtype="uint8")

    worker._on_frame(type("Packet", (), {"image": image})())
    first = worker.take_latest_preview()

    worker._last_preview_emit_at = -1.0
    worker._on_frame(type("Packet", (), {"image": image})())
    second = worker.take_latest_preview()

    assert first is not None and second is not None
    assert max(first.shape[:2]) <= worker.MAX_PREVIEW_EDGE
    assert max(second.shape[:2]) <= worker.MAX_PREVIEW_EDGE
    assert worker.take_latest_preview() is None
