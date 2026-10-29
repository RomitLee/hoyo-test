"""Multi-window desktop UI for inventory monitoring."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, ClassVar
from uuid import uuid4

import cv2
from PySide6.QtCore import Qt, QThread, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QCursor, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import SLAVE_VERSION as __version__
from .alarm_sound import play_alarm_loop, stop_alarm_loop
from .app_identity import SLAVE_APP_ID, application_icon, apply_application_icon, configure_windows_app_id
from .auto_update import install_auto_update, mark_update_healthy
from .cloud_config import CLOUD_RELAY_URL
from .config import load_config
from .desktop_theme import (
    DANGER,
    MUTED,
    PRIMARY,
    SUCCESS,
    WARNING,
    DarkTitleBar,
    apply_desktop_theme,
    pill_style,
)
from .device_auth import DeviceAuthClient
from .inventory_app import (
    APP_NAME,
    MAX_MONITORED_WINDOWS,
    AccountInfo,
    InventoryMonitorWorker,
    is_mhxy_game_window_title,
    parse_account_info,
)
from .models import Event
from .panels import PANEL_NAMES
from .paths import application_root
from .relay_client import SlaveRelayClient, make_event_payload
from .settings_dialog import SlaveSettingsDialog
from .slave_settings import load_slave_settings, save_slave_settings
from .wgc import (
    WindowOcclusionStatus,
    is_window_available,
    is_window_minimized,
    list_capturable_windows,
    measure_window_occlusion,
    minimize_window,
)


@dataclass(slots=True)
class MonitorSession:
    hwnd: int
    title: str
    account: AccountInfo
    thread: QThread
    worker: InventoryMonitorWorker
    output_panel: QFrame
    output_title: QLabel
    output_preview: QLabel
    output_status: QLabel
    output_detail: QLabel
    output_occlusion: QLabel
    monitor_session_id: str = field(default_factory=lambda: uuid4().hex)
    stall_mode: str = "unknown"
    last_image: Any | None = None
    last_signal: dict[str, Any] | None = None
    last_analysis_at: float | None = None
    last_stats: dict[str, Any] | None = None
    minimized: bool = False
    last_occlusion_percent: float = 0.0
    minimized_since: float | None = None
    minimized_alert_active: bool = False
    capacity_alert_active: bool = False
    capacity_candidate_since: float | None = None
    cash_alert_active: bool = False
    occlusion_alert_active: bool = False
    occlusion_candidate_since: float | None = None
    removing: bool = False


class InventoryMonitorWindow(QMainWindow):
    """Monitor up to eight game windows and show the selected live preview."""

    FULL_WIDTH = 560
    FULL_HEIGHT = 1100
    OUTPUT_COLUMNS = 3
    MINI_WIDTH = 300
    MINI_HEIGHT = 90

    STATUS_PRESENTATION: ClassVar[dict[str, tuple[str, str, str]]] = {
        "full": ("背包已满", "#B42335", "#FFF0F2"),
        "almost_full": ("背包接近满", "#986300", "#FFF7E3"),
        "not_full": ("背包未满", "#18734D", "#E8F7EF"),
        "inventory_closed": ("背包未打开", "#986300", "#FFF7E3"),
        "invalid": ("背包被遮挡", "#B42335", "#FFF0F2"),
    }

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(application_icon())
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setFixedSize(self.FULL_WIDTH, self.FULL_HEIGHT)
        root = application_root()
        path = config_path or root / "configs" / "default.toml"
        self.config = load_config(path if path.exists() else None)
        self.slave_settings = load_slave_settings()
        self._local_alert_enabled = False
        self._capacity_threshold = 0
        self._cash_threshold_wan = 500
        self._capacity_enabled = True
        self._cash_low_enabled = True
        self._capacity_modes = frozenset({"buy"})
        self._cash_low_modes = frozenset({"buy"})
        self._shown_local_alerts: set[int] = set()
        self.sessions: dict[int, MonitorSession] = {}
        self.alerts: set[QMessageBox] = set()
        self._sounding_alerts: set[QMessageBox] = set()
        self._market_toast: QFrame | None = None
        self._market_toast_title: QLabel | None = None
        self._market_toast_detail: QLabel | None = None
        self._market_toast_timer = QTimer(self)
        self._market_toast_timer.setSingleShot(True)
        self._market_toast_timer.timeout.connect(self._hide_market_toast)
        self._window_titles: dict[int, str] = {}
        self._minimize_targets: tuple[int, ...] = ()
        self.monitoring_paused = False
        self._mini_mode = False
        self._full_position = None
        self._closing = False
        self._authenticated = False
        self._build_ui()
        self.auth_client = DeviceAuthClient(self)
        self.auth_client.result.connect(self._on_auth_result)
        self.relay_client = SlaveRelayClient(self)
        self.relay_client.connection_changed.connect(self._on_relay_connection_changed)
        self.relay_client.event_result.connect(self._on_relay_event_result)
        self.relay_client.profile_received.connect(self._on_device_profile)
        self.relay_client.profile_result.connect(self._on_profile_result)
        self.relay_client.live_screenshots.control_received.connect(self._on_live_control)
        self.relay_client.live_screenshots.upload_failed.connect(self.log.appendPlainText)
        self._live_interval_minutes = 5
        self._last_live_capture: dict[int, float] = {}
        self._seen_live_requests: set[str] = set()
        self._live_capture_queue: list[int] = []
        self._queued_live_captures: set[int] = set()
        self._live_capture_queue_timer = QTimer(self)
        self._live_capture_queue_timer.setSingleShot(True)
        self._live_capture_queue_timer.timeout.connect(self._dispatch_next_periodic_snapshot)
        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.refresh_windows)
        self.scan_timer.setInterval(3000)
        self.window_state_timer = QTimer(self)
        self.window_state_timer.timeout.connect(self._poll_active_window_states)
        self.window_state_timer.setInterval(1000)
        self.occlusion_timer = QTimer(self)
        self.occlusion_timer.timeout.connect(self._poll_window_occlusion)
        self.occlusion_timer.setInterval(2000)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._report_window_status)
        self.status_timer.setInterval(5000)
        self.auth_timer = QTimer(self)
        self.auth_timer.setInterval(30000)
        self.auth_timer.timeout.connect(self._verify_saved_identity)
        self.minimize_verify_timer = QTimer(self)
        self.minimize_verify_timer.setSingleShot(True)
        self.minimize_verify_timer.timeout.connect(self._finish_minimize_all_game_windows)
        QTimer.singleShot(250, self._verify_saved_identity)
        mark_update_healthy("slave")
        self.update_controller = install_auto_update("slave", self)

    def _build_ui(self) -> None:
        apply_desktop_theme(self)
        root = QWidget()
        root.setObjectName("windowShell")
        self.setCentralWidget(root)
        shell_layout = QVBoxLayout(root)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.title_bar = DarkTitleBar(self, f"{APP_NAME} · 从机 · {__version__}")
        shell_layout.addWidget(self.title_bar)

        self.full_content = QWidget()
        self.full_content.setObjectName("appRoot")
        shell_layout.addWidget(self.full_content, 1)
        layout = QVBoxLayout(self.full_content)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(7)

        heading = QVBoxLayout()
        heading_text = QVBoxLayout()
        title = QLabel(self.slave_settings.display_name or "未命名从机")
        self.device_name_label = title
        self.device_name_label.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet("font-size:16px;font-weight:800;color:#243247;")
        self.subtitle_label = QLabel(f"最多监控 8 个窗口 · 客户端 {__version__}")
        self.subtitle_label.setStyleSheet(f"color:{MUTED};font-size:11px;")
        name_row = QHBoxLayout()
        name_row.addWidget(title)
        self.rename_button = QPushButton("编辑名称")
        self.rename_button.setFixedWidth(76)
        self.rename_button.clicked.connect(self._edit_device_name)
        name_row.addWidget(self.rename_button)
        name_row.addStretch(1)
        heading_text.addLayout(name_row)
        heading_text.addWidget(self.subtitle_label)
        title.setToolTip(self.subtitle_label.text())
        self.subtitle_label.hide()
        heading.addLayout(heading_text, 1)
        heading_controls = QGridLayout()
        heading_controls.setContentsMargins(0, 0, 0, 0)
        heading_controls.setHorizontalSpacing(6)
        heading_controls.setVerticalSpacing(2)
        heading.addLayout(heading_controls)
        self.add_window_button = QPushButton("＋ 增加窗口")
        self.add_window_button.setProperty("role", "primary")
        self.add_window_button.setFixedHeight(28)
        self.add_window_button.clicked.connect(self._show_add_window_menu)
        heading_controls.addWidget(self.add_window_button, 0, 0)
        self.monitoring_button = QPushButton("暂停监控")
        self.monitoring_button.setFixedHeight(28)
        self.monitoring_button.setToolTip("暂停所有窗口的背包分析和告警")
        self.monitoring_button.clicked.connect(self._toggle_monitoring)
        heading_controls.addWidget(self.monitoring_button, 0, 1)
        self.minimize_all_button = QPushButton("一键最小化")
        self.minimize_all_button.setProperty("role", "danger")
        self.minimize_all_button.setFixedHeight(28)
        self.minimize_all_button.setToolTip("暂停监控，并最小化电脑上的全部梦幻西游窗口（包括未监控窗口）")
        self.minimize_all_button.clicked.connect(self._minimize_all_game_windows)
        heading_controls.addWidget(self.minimize_all_button, 0, 2)
        self.settings_button = QPushButton("设置")
        self.settings_button.setToolTip("查看登录状态；告警方式请在后台统一设置")
        self.settings_button.clicked.connect(self._open_settings)
        heading_controls.addWidget(self.settings_button, 0, 3)
        self.logout_button = QPushButton("退出登录")
        self.logout_button.clicked.connect(self._logout)
        heading_controls.addWidget(self.logout_button, 2, 3)
        self.host_status_label = QLabel("云端：未连接")
        self.host_status_label.setStyleSheet(pill_style("#B42335", "#FFF0F2", "#F2BCC4"))
        heading_controls.addWidget(self.host_status_label, 1, 1, Qt.AlignmentFlag.AlignRight)
        self.host_connect_button = QPushButton("连接")
        self.host_connect_button.setProperty("role", "primary")
        self.host_connect_button.setToolTip("立即测试云端 HTTPS 连接")
        self.host_connect_button.clicked.connect(self._connect_relay)
        heading_controls.addWidget(self.host_connect_button, 1, 2)
        self.background_button = QPushButton("后台监控")
        self.background_button.setToolTip("最小化本程序，所有游戏窗口继续检测")
        self.background_button.clicked.connect(self._run_in_background)
        heading_controls.addWidget(self.background_button, 1, 3)
        self.mini_button = QPushButton("迷你版")
        self.mini_button.setToolTip("缩小界面；检测、云端连接和告警继续运行")
        self.mini_button.clicked.connect(self._enter_mini_mode)
        heading_controls.addWidget(self.mini_button, 1, 0)
        layout.addLayout(heading)
        self._refresh_host_status()

        self.outputs_group = QGroupBox("窗口输出（0）")
        outputs_layout = QVBoxLayout(self.outputs_group)
        self.output_scroll = QScrollArea()
        self.output_scroll.setWidgetResizable(True)
        self.output_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.output_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.output_container = QWidget()
        self.output_container.setMinimumWidth(488)
        self.output_grid = QGridLayout(self.output_container)
        self.output_grid.setContentsMargins(5, 5, 5, 5)
        self.output_grid.setHorizontalSpacing(5)
        self.output_grid.setVerticalSpacing(5)
        self.output_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.output_placeholder = QLabel("尚未监控窗口，请点击上方“增加窗口”")
        self.output_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.output_placeholder.setStyleSheet(
            f"color:{MUTED};background:#F8FAFD;border:1px dashed #D6DFEC;border-radius:10px;padding:40px;"
        )
        self.output_grid.addWidget(self.output_placeholder, 0, 0, 1, self.OUTPUT_COLUMNS)
        self.output_scroll.setWidget(self.output_container)
        outputs_layout.addWidget(self.output_scroll)
        layout.addWidget(self.outputs_group, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setFixedHeight(28)
        self.log.setPlaceholderText("所有窗口的提醒记录会显示在这里")
        layout.addWidget(self.log)

        self.mini_content = QWidget()
        self.mini_content.setObjectName("appRoot")
        mini_layout = QHBoxLayout(self.mini_content)
        mini_layout.setContentsMargins(8, 6, 8, 6)
        mini_layout.setSpacing(6)
        self.mini_status_label = QLabel("监控中")
        self.mini_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mini_status_label.setFixedHeight(30)
        self.mini_count_label = QLabel("监控窗口 0")
        self.mini_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mini_count_label.setFixedHeight(30)
        self.restore_button = QPushButton("恢复完整界面")
        self.restore_button.setProperty("role", "primary")
        self.restore_button.setFixedHeight(30)
        self.restore_button.clicked.connect(self._restore_full_mode)
        mini_layout.addWidget(self.mini_status_label)
        mini_layout.addWidget(self.mini_count_label)
        mini_layout.addWidget(self.restore_button, 1)
        shell_layout.addWidget(self.mini_content, 1)
        self.mini_content.hide()
        self._refresh_mini_status()

        self.login_content = QWidget()
        self.login_content.setObjectName("appRoot")
        login_layout = QVBoxLayout(self.login_content)
        login_layout.setContentsMargins(40, 35, 40, 35)
        login_layout.setSpacing(14)
        login_layout.addStretch(1)
        login_title = QLabel("登录后开始监控")
        login_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        login_title.setStyleSheet("font-size:22px;font-weight:800;color:#243247;")
        login_layout.addWidget(login_title)
        login_hint = QLabel("使用网页后台的账号和密码。首次登录会自动登记这台机器。")
        login_hint.setWordWrap(True)
        login_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        login_hint.setStyleSheet(f"color:{MUTED};font-size:12px;")
        login_layout.addWidget(login_hint)
        self.login_username = QLineEdit(self.slave_settings.account_username)
        self.login_username.setPlaceholderText("后台账号")
        self.login_username.setMaxLength(32)
        login_layout.addWidget(self.login_username)
        self.login_password = QLineEdit()
        self.login_password.setPlaceholderText("密码")
        self.login_password.setEchoMode(QLineEdit.EchoMode.Password)
        login_layout.addWidget(self.login_password)
        self.login_password.returnPressed.connect(self._submit_login)
        self.login_button = QPushButton("登录并开始使用")
        self.login_button.setProperty("role", "primary")
        self.login_button.clicked.connect(self._submit_login)
        login_layout.addWidget(self.login_button)
        self.retry_auth_button = QPushButton("重新验证本机登录")
        self.retry_auth_button.clicked.connect(self._verify_saved_identity)
        self.retry_auth_button.setVisible(bool(self.slave_settings.device_id and self.slave_settings.device_token))
        login_layout.addWidget(self.retry_auth_button)
        self.login_status = QLabel("请先登录账号")
        self.login_status.setWordWrap(True)
        self.login_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.login_status.setStyleSheet(f"color:{MUTED};font-size:12px;")
        login_layout.addWidget(self.login_status)
        login_layout.addStretch(1)
        shell_layout.addWidget(self.login_content, 1)
        self.full_content.hide()

    @Slot()
    def _enter_mini_mode(self) -> None:
        if self._mini_mode:
            return
        self._full_position = self.pos()
        self._mini_mode = True
        self.full_content.hide()
        self.mini_content.show()
        self.setFixedSize(self.MINI_WIDTH, self.MINI_HEIGHT)
        self.move(self._full_position)
        self._refresh_mini_status()

    @Slot()
    def _restore_full_mode(self) -> None:
        if not self._mini_mode:
            return
        full_position = self._full_position
        self._mini_mode = False
        self.mini_content.hide()
        self.full_content.show()
        self.setFixedSize(self.FULL_WIDTH, self.FULL_HEIGHT)
        if full_position is not None:
            self.move(full_position)

    def _refresh_mini_status(self) -> None:
        paused = self.monitoring_paused
        self.mini_status_label.setText("已暂停" if paused else "监控中")
        self.mini_status_label.setStyleSheet(
            pill_style("#986300", "#FFF7E3", "#EFD49A") if paused else pill_style("#18734D", "#E8F7EF", "#B6E1CA")
        )
        self.mini_count_label.setText(f"监控窗口 {len(self.sessions)}")
        self.mini_count_label.setStyleSheet(pill_style("#2563DB", "#EEF4FF", "#BBD2FA"))

    @Slot()
    def refresh_windows(self) -> None:
        if not self._authenticated:
            return
        try:
            windows = [item for item in list_capturable_windows() if is_mhxy_game_window_title(item.title)]
        except Exception as exc:  # noqa: BLE001 - native enumeration errors belong in the status area
            self.add_window_button.setEnabled(False)
            self.add_window_button.setToolTip(f"窗口检测失败：{exc}")
            return

        self._window_titles = {window.hwnd: window.title for window in windows}
        for hwnd, session in tuple(self.sessions.items()):
            if hwnd not in self._window_titles:
                self._request_remove_window(hwnd, "游戏窗口已经关闭")
                continue
            title = self._window_titles[hwnd]
            if title != session.title:
                self._update_session_identity(session, title)

        available = [window for window in windows if window.hwnd not in self.sessions]
        self.add_window_button.setEnabled(bool(available) and len(self.sessions) < MAX_MONITORED_WINDOWS)
        if not windows:
            self.add_window_button.setToolTip("未检测到梦幻西游窗口，请先启动游戏")
        elif not available:
            self.add_window_button.setToolTip("当前检测到的游戏窗口均已添加")
        else:
            self.add_window_button.setToolTip(
                f"检测到 {len(windows)} 个游戏窗口，已监控 {len(self.sessions)} 个，可继续增加 {len(available)} 个"
            )

    @Slot()
    def _run_in_background(self) -> None:
        self.log.appendPlainText("程序已转入后台监控；点击 Windows 任务栏图标可恢复界面。")
        self.showMinimized()

    @Slot()
    def _toggle_monitoring(self) -> None:
        if not self._authenticated:
            return
        self._set_monitoring_paused(not self.monitoring_paused)

    def _set_monitoring_paused(self, paused: bool) -> None:
        if self.monitoring_paused == paused:
            return
        self.monitoring_paused = paused
        self.relay_client.send_monitor_log(
            "monitoring_paused" if paused else "monitoring_started", "pause" if paused else "resume",
        )
        self._refresh_mini_status()
        if self.monitoring_paused:
            self.monitoring_button.setText("开始监控")
            self.monitoring_button.setToolTip("恢复所有窗口的背包分析和告警")
            self.monitoring_button.setStyleSheet(
                "QPushButton{background:#E8F7EF;color:#18734D;border:1px solid #B6E1CA;"
                "border-radius:7px;font-weight:700;padding:0 12px;}QPushButton:hover{background:#DAF0E4;}"
            )
            sounding = bool(self._sounding_alerts)
            self._sounding_alerts.clear()
            for alert in tuple(self.alerts):
                alert.close()
            if sounding:
                self._stop_alarm_sound()
            for session in self.sessions.values():
                session.worker.set_paused(True)
                session.minimized_since = None
                session.minimized_alert_active = False
                session.capacity_alert_active = False
                session.capacity_candidate_since = None
                session.occlusion_alert_active = False
                session.occlusion_candidate_since = None
                self._show_manual_pause_status(session)
            self.log.appendPlainText("监控已手动暂停：停止分析和告警。")
            return

        self.monitoring_button.setText("暂停监控")
        self.monitoring_button.setToolTip("暂停所有窗口的背包分析和告警")
        self.monitoring_button.setStyleSheet("")
        for session in self.sessions.values():
            session.monitor_session_id = uuid4().hex
            session.stall_mode = "unknown"
            session.capacity_alert_active = False
            session.capacity_candidate_since = None
            session.cash_alert_active = False
            session.occlusion_alert_active = False
            session.occlusion_candidate_since = None
            session.worker.set_paused(False)
            session.last_signal = None
            session.last_analysis_at = None
            if session.minimized:
                session.minimized_since = monotonic()
                session.minimized_alert_active = False
                self._show_minimized_status(session)
            else:
                session.minimized_since = None
                session.minimized_alert_active = False
                session.output_status.setText("监控已恢复，正在重新检测…")
                session.output_status.setStyleSheet(
                    "background:#EEF4FF;color:#2563DB;border:1px solid #BBD2FA;border-radius:6px;font-weight:800;"
                )
                session.output_detail.setText("空格 -｜已拥有 -｜置信度 -")
                session.output_preview.clear()
                session.output_preview.setText("等待新的游戏画面…")
                session.output_occlusion.setText("遮挡 检测中｜最小化 否")
        self.log.appendPlainText("监控已恢复：所有异常将重新计时确认。")
        self._poll_active_window_states()
        self._poll_window_occlusion()

    @Slot()
    def _minimize_all_game_windows(self) -> None:
        if not self._authenticated:
            return
        """Pause monitoring and minimize every game window, monitored or not."""
        self._set_monitoring_paused(True)
        try:
            windows = [item for item in list_capturable_windows() if is_mhxy_game_window_title(item.title)]
        except Exception as exc:  # noqa: BLE001 - native enumeration errors belong in the status area
            self.log.appendPlainText(f"一键最小化失败：无法扫描游戏窗口（{exc}）")
            return

        targets = tuple(dict.fromkeys(window.hwnd for window in windows))
        if not targets:
            self.log.appendPlainText("一键最小化：未检测到梦幻西游窗口，监控已暂停。")
            return

        accepted = 0
        for hwnd in targets:
            if is_window_minimized(hwnd):
                accepted += 1
                continue
            try:
                accepted += int(minimize_window(hwnd))
            except Exception as exc:  # noqa: BLE001 - one inaccessible window must not block the others
                self.log.appendPlainText(f"窗口 0x{hwnd:X} 的最小化请求失败：{exc}")

        self._minimize_targets = targets
        self.minimize_all_button.setEnabled(False)
        self.log.appendPlainText(
            f"正在一键最小化：共发现 {len(targets)} 个梦幻西游窗口，Windows 已接受 {accepted} 个请求。"
        )
        self.minimize_verify_timer.start(500)

    @Slot()
    def _finish_minimize_all_game_windows(self) -> None:
        targets = self._minimize_targets
        self._minimize_targets = ()
        self.minimize_all_button.setEnabled(True)
        minimized = {hwnd for hwnd in targets if is_window_available(hwnd) and is_window_minimized(hwnd)}
        for hwnd, session in self.sessions.items():
            if hwnd in minimized:
                session.minimized = True
                session.minimized_since = None
        failed = len(targets) - len(minimized)
        if failed:
            self.log.appendPlainText(
                f"一键最小化完成：已最小化 {len(minimized)}/{len(targets)} 个窗口，{failed} 个窗口未成功或已关闭。"
            )
        else:
            self.log.appendPlainText(f"一键最小化完成：{len(minimized)} 个梦幻西游窗口已全部最小化，监控保持暂停。")

    @staticmethod
    def _show_manual_pause_status(session: MonitorSession) -> None:
        session.output_status.setText("监控已手动暂停")
        session.output_status.setStyleSheet(
            "background:#FFF7E3;color:#986300;border:1px solid #EFD49A;border-radius:6px;font-weight:800;"
        )
        session.output_detail.setText("空格 -｜已拥有 -｜置信度 -")
        session.output_preview.clear()
        session.output_preview.setText("点击“开始监控”后继续")
        session.output_occlusion.setText("遮挡 --｜监控暂停")
        session.output_occlusion.setStyleSheet(f"color:{WARNING};font-size:11px;font-weight:700;")

    @Slot()
    def _open_settings(self) -> None:
        if not self._authenticated:
            return
        SlaveSettingsDialog(self.slave_settings, self).exec()

    def _refresh_host_status(self) -> None:
        online = hasattr(self, "relay_client") and self.relay_client.online
        self.host_status_label.setText("云端：已连接" if online else "云端：未连接")
        self.host_status_label.setStyleSheet(
            pill_style("#18734D", "#E8F7EF", "#B6E1CA") if online else pill_style("#B42335", "#FFF0F2", "#F2BCC4")
        )
        configured = bool(self.slave_settings.device_id and self.slave_settings.device_token)
        self.host_status_label.setToolTip(
            f"固定云端：{CLOUD_RELAY_URL}\n设备：{self.slave_settings.device_id or '未登录'}"
        )
        self.host_connect_button.setVisible(True)
        self.host_connect_button.setEnabled(configured)
        name = self.slave_settings.display_name or "未命名从机"
        self.device_name_label.setText(self.device_name_label.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 300))
        self.device_name_label.setToolTip(name)
        self.rename_button.setEnabled(configured)

    @Slot()
    def _edit_device_name(self) -> None:
        name, accepted = QInputDialog.getText(
            self, "编辑从机名称", "从机名称（最多 64 字）：", text=self.slave_settings.display_name,
        )
        if not accepted:
            return
        name = name.strip()
        if not 1 <= len(name) <= 64:
            QMessageBox.warning(self, "名称无效", "请填写 1～64 字的从机名称")
            return
        self.rename_button.setEnabled(False)
        self.relay_client.rename_device(name)

    @Slot(object)
    def _on_device_profile(self, data: dict[str, Any]) -> None:
        self._apply_alert_settings(data.get("alert_settings"))
        if self._authenticated and self._local_alert_enabled:
            for alert in data.get("local_alerts", data.get("local_stamina_alerts")) or []:
                if not isinstance(alert, dict) or not isinstance(alert.get("id"), int):
                    continue
                event_id = alert["id"]
                if event_id not in self._shown_local_alerts:
                    self._shown_local_alerts.add(event_id)
                    details = alert.get("details") or {}
                    account_label = f"{alert.get('server_name') or ''} / {alert.get('role_name') or ''}"
                    if alert.get("event_type") == "stall_sold":
                        sold = details.get("sold_count")
                        message = f"在售商品由 {details.get('previous_count')} 件减至 {details.get('current_count')} 件，减少 {sold} 件。"
                        self._show_alert(QMessageBox.Icon.Information, "商品售出", message,
                                         account_label, with_sound=True)
                    else:
                        source = "属性面板实测" if details.get("source") == "measured" else "在线恢复预测"
                        self._show_alert(QMessageBox.Icon.Warning, "体力已满", f"{source}：体力已满，请及时处理。",
                                         account_label, with_sound=True)
                self.relay_client.acknowledge_local_alert(event_id)
        name = str(data.get("display_name") or "").strip()
        if name and name != self.slave_settings.display_name:
            self.slave_settings.display_name = name
            try:
                save_slave_settings(self.slave_settings)
            except OSError as exc:
                self.log.appendPlainText(f"名称已同步，但本地缓存保存失败：{exc}")
        self._refresh_host_status()

    def _apply_alert_settings(self, raw: object) -> None:
        if not isinstance(raw, dict):
            return
        local = raw.get("local_alert_enabled") is True
        threshold = raw.get("capacity_threshold")
        if not isinstance(threshold, int) or isinstance(threshold, bool) or not 0 <= threshold <= 20:
            return
        cash_threshold = raw.get("cash_threshold_wan")
        if not isinstance(cash_threshold, int) or isinstance(cash_threshold, bool) or not 1 <= cash_threshold <= 99999999:
            return
        valid_modes = {"buy", "sell", "unknown"}
        capacity_modes = raw.get("capacity_modes", list(self._capacity_modes))
        cash_low_modes = raw.get("cash_low_modes", list(self._cash_low_modes))
        if (not isinstance(capacity_modes, list)
                or not isinstance(cash_low_modes, list)
                or any(not isinstance(mode, str) or mode not in valid_modes for mode in capacity_modes + cash_low_modes)):
            return
        capacity_enabled = raw.get("capacity_enabled", self._capacity_enabled) is True
        cash_low_enabled = raw.get("cash_low_enabled", self._cash_low_enabled) is True
        new_capacity_modes = frozenset(capacity_modes)
        new_cash_low_modes = frozenset(cash_low_modes)
        capacity_changed = (threshold, capacity_enabled, new_capacity_modes) != (
            self._capacity_threshold, self._capacity_enabled, self._capacity_modes)
        cash_changed = (cash_threshold, cash_low_enabled, new_cash_low_modes) != (
            self._cash_threshold_wan, self._cash_low_enabled, self._cash_low_modes)
        changed = local != self._local_alert_enabled or capacity_changed or cash_changed
        self._local_alert_enabled = local
        self._capacity_threshold = threshold
        self._cash_threshold_wan = cash_threshold
        self._capacity_enabled = capacity_enabled
        self._cash_low_enabled = cash_low_enabled
        self._capacity_modes = new_capacity_modes
        self._cash_low_modes = new_cash_low_modes
        if changed:
            for session in self.sessions.values():
                if capacity_changed:
                    session.capacity_alert_active = False
                    session.capacity_candidate_since = None
                if cash_changed:
                    session.cash_alert_active = False
            if not local:
                for alert in tuple(self.alerts):
                    alert.close()
                self._sounding_alerts.clear()
                self._stop_alarm_sound()
            self.log.appendPlainText(
                f"后台告警设置已同步：本机报警{'开启' if local else '关闭'}，"
                f"空格阈值 {threshold}，游戏币低于 {cash_threshold} 万提醒")

    @Slot(bool, str)
    def _on_profile_result(self, _success: bool, message: str) -> None:
        self.rename_button.setEnabled(self.relay_client.configured)
        self.log.appendPlainText(message)

    def _configure_relay(self) -> None:
        if not self._authenticated:
            return
        try:
            self.relay_client.configure(self.slave_settings.device_id, self.slave_settings.device_token)
        except ValueError as exc:
            self.host_status_label.setToolTip(str(exc))
            self.log.appendPlainText(f"云端设置无效：{exc}")
        self._refresh_host_status()

    @Slot()
    def _verify_saved_identity(self) -> None:
        if self._closing or not self.login_button.isEnabled():
            return
        if not self.slave_settings.device_id or not self.slave_settings.device_token:
            if not self._authenticated:
                self.login_status.setText("请输入后台账号和密码")
            return
        if not self._authenticated:
            self.login_status.setText("正在验证本机登录…")
        self.auth_client.verify(self.slave_settings.device_id, self.slave_settings.device_token)

    @Slot()
    def _submit_login(self) -> None:
        if self._closing or self._authenticated:
            return
        username = self.login_username.text().strip()
        password = self.login_password.text()
        if not username or not password:
            self.login_status.setText("请输入账号和密码")
            return
        self.login_button.setEnabled(False)
        self.login_status.setText("正在登录并绑定机器…")
        self.auth_client.login(username, password, self.slave_settings.installation_id)

    @Slot(str, bool, str, object)
    def _on_auth_result(self, kind: str, success: bool, message: str, data: dict[str, Any]) -> None:
        if self._closing:
            return
        if kind == "login":
            self.login_button.setEnabled(True)
        if success:
            self._apply_alert_settings(data.get("alert_settings"))
            previous = (self.slave_settings.device_id, self.slave_settings.device_token,
                        self.slave_settings.display_name, self.slave_settings.account_username)
            if data.get("device_id"):
                self.slave_settings.device_id = str(data["device_id"])
            if kind == "login":
                self.slave_settings.device_token = str(data.get("device_token") or "")
                self.slave_settings.pairing_code = ""
                self.login_password.clear()
            self.slave_settings.display_name = str(data.get("display_name") or self.slave_settings.display_name)
            self.slave_settings.account_username = str(data.get("account_username") or self.slave_settings.account_username)
            current = (self.slave_settings.device_id, self.slave_settings.device_token,
                       self.slave_settings.display_name, self.slave_settings.account_username)
            if current != previous:
                try:
                    save_slave_settings(self.slave_settings)
                except OSError as exc:
                    self._deactivate(f"无法保存本机登录状态：{exc}")
                    return
            if current[0] != previous[0]:
                self.relay_client.rebind_device(previous[0], current[0])
            if not self._authenticated:
                self._activate_authenticated()
            else:
                if current[0] != previous[0]:
                    self._configure_relay()
                self._refresh_host_status()
            return
        if kind == "verify" and int(data.get("http_status") or 0) in {401, 403}:
            self.slave_settings.device_id = ""
            self.slave_settings.device_token = ""
            try:
                save_slave_settings(self.slave_settings)
            except OSError:
                pass
        if kind == "verify" and self._authenticated:
            self._deactivate(message)
        else:
            self.login_status.setText(message)
            self.retry_auth_button.setVisible(bool(self.slave_settings.device_token))
            if self.slave_settings.device_token:
                self.auth_timer.start()

    def _activate_authenticated(self) -> None:
        self._authenticated = True
        self.login_content.hide()
        self.full_content.show()
        self._configure_relay()
        self._refresh_host_status()
        self.scan_timer.start()
        self.window_state_timer.start()
        self.occlusion_timer.start()
        self.status_timer.start()
        self.auth_timer.start()
        self.refresh_windows()
        self.relay_client.send_monitor_log("monitoring_started", "startup")
        self.relay_client.connect_now()

    def _deactivate(self, message: str) -> None:
        self._authenticated = False
        self._local_alert_enabled = False
        self._capacity_threshold = 0
        self._cash_threshold_wan = 500
        self._capacity_enabled = True
        self._cash_low_enabled = True
        self._capacity_modes = frozenset({"buy"})
        self._cash_low_modes = frozenset({"buy"})
        self._shown_local_alerts.clear()
        self.scan_timer.stop()
        self.window_state_timer.stop()
        self.occlusion_timer.stop()
        self.status_timer.stop()
        for hwnd in tuple(self.sessions):
            self._request_remove_window(hwnd)
        for alert in tuple(self.alerts):
            alert.close()
        self._sounding_alerts.clear()
        self._stop_alarm_sound()
        self.relay_client.configure("", "")
        if self._mini_mode:
            self._restore_full_mode()
        self.full_content.hide()
        self.mini_content.hide()
        self.login_content.show()
        self.setFixedSize(self.FULL_WIDTH, self.FULL_HEIGHT)
        self.retry_auth_button.setVisible(bool(self.slave_settings.device_token))
        self.login_status.setText(message)
        if self.slave_settings.device_token:
            self.auth_timer.start()
        else:
            self.auth_timer.stop()

    @Slot()
    def _logout(self) -> None:
        self.auth_client.cancel()
        self.slave_settings.device_id = ""
        self.slave_settings.device_token = ""
        self.slave_settings.display_name = ""
        try:
            save_slave_settings(self.slave_settings)
        except OSError as exc:
            QMessageBox.critical(self, "退出失败", f"无法清除本机登录状态：{exc}")
            return
        self._deactivate("已退出登录，请重新输入后台账号和密码")

    @Slot()
    def _connect_relay(self) -> None:
        self.host_connect_button.setEnabled(False)
        self.host_status_label.setText("云端：连接中…")
        self.host_status_label.setStyleSheet(pill_style("#986300", "#FFF7E3", "#EFD49A"))
        self.relay_client.connect_now()

    @Slot(bool, str)
    def _on_relay_connection_changed(self, online: bool, message: str) -> None:
        self.host_status_label.setText("云端：已连接" if online else "云端：未连接")
        self.host_status_label.setStyleSheet(
            pill_style("#18734D", "#E8F7EF", "#B6E1CA") if online else pill_style("#B42335", "#FFF0F2", "#F2BCC4")
        )
        self.host_status_label.setToolTip(message)
        self.host_connect_button.setEnabled(self.relay_client.configured)
        if not online and message not in {"尚未连接", "云端参数未配置"}:
            self.log.appendPlainText(f"云端连接失败：{message}")

    @Slot(bool, str)
    def _on_relay_event_result(self, _success: bool, message: str) -> None:
        self.log.appendPlainText(message)

    def _send_host_alert(
        self,
        session: MonitorSession,
        event_type: str,
        *,
        empty_slots: int | None = None,
        confidence: float | None = None,
        occlusion_percent: float | None = None,
        minimized: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not self._authenticated:
            return
        self.relay_client.send_event(
            make_event_payload(
                event_type,
                server_name=session.account.secondary_region,
                role_name=session.account.role_name,
                character_id=session.account.role_id or None,
                empty_slots=empty_slots,
                confidence=confidence,
                occlusion_percent=occlusion_percent,
                minimized=minimized,
                details=({**(details or {}), "stall_mode": session.stall_mode}
                         if event_type in {"inventory_full", "inventory_capacity", "cash_low"} else details),
            )
        )
        self.log.appendPlainText(f"正在发送云端告警：{session.account.secondary_region} / {session.account.role_name}")

    @Slot()
    def _show_add_window_menu(self) -> None:
        if not self._authenticated:
            return
        if len(self.sessions) >= MAX_MONITORED_WINDOWS:
            QMessageBox.information(self, "已达到上限", "一台电脑最多同时监控 8 个游戏窗口。")
            return
        available = [(hwnd, title) for hwnd, title in self._window_titles.items() if hwnd not in self.sessions]
        if not available:
            QMessageBox.information(self, "没有可增加的窗口", "没有检测到尚未监控的梦幻西游窗口。")
            return
        menu = QMenu(self)
        for hwnd, title in available:
            account = parse_account_info(title)
            action = menu.addAction(f"{account.secondary_region} / {account.role_name}  [{title}]")
            action.triggered.connect(lambda _checked=False, target=hwnd: self._add_window(target))
        menu.exec(self.add_window_button.mapToGlobal(self.add_window_button.rect().bottomLeft()))

    def _add_window(self, hwnd: int) -> None:
        if not self._authenticated:
            return
        if hwnd in self.sessions or len(self.sessions) >= MAX_MONITORED_WINDOWS:
            return
        title = self._window_titles.get(hwnd, "")
        if not title or not is_window_available(hwnd):
            self.refresh_windows()
            return

        account = parse_account_info(title)
        output_panel, output_title, output_preview, output_status, output_detail, output_occlusion = (
            self._make_output_panel(hwnd, account)
        )
        thread = QThread(self)
        thread.setProperty("window_hwnd", hwnd)
        worker = InventoryMonitorWorker(hwnd, self.config, title)
        worker.set_paused(self.monitoring_paused)
        worker.moveToThread(thread)
        session = MonitorSession(
            hwnd=hwnd,
            title=title,
            account=account,
            thread=thread,
            worker=worker,
            output_panel=output_panel,
            output_title=output_title,
            output_preview=output_preview,
            output_status=output_status,
            output_detail=output_detail,
            output_occlusion=output_occlusion,
        )
        self.sessions[hwnd] = session
        if account.role_id:
            # Persist first sight locally; the outbox retries even if this window closes immediately.
            self.relay_client.send_monitor_payload({
                "event_id": uuid4().hex, "event_type": "window_monitored", "reason": "startup",
                "occurred_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "window_id": f"hwnd:{hwnd}", "server_name": account.secondary_region,
                "role_name": account.role_name, "character_id": account.role_id,
            })

        thread.started.connect(worker.run)
        worker.preview_for_window.connect(self._on_preview)
        worker.analysis_for_window.connect(self._on_analysis)
        worker.event_for_window.connect(self._on_event)
        worker.stats_for_window.connect(self._on_stats)
        worker.live_snapshot_ready.connect(self._on_live_snapshot)
        worker.failed_for_window.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.finished_for_window.connect(self._on_worker_finished)
        thread.finished.connect(self._on_thread_finished)
        thread.start()

        if self.monitoring_paused:
            self._show_manual_pause_status(session)
            self.log.appendPlainText(f"已增加窗口（监控暂停中）：{account.secondary_region} / {account.role_name}")
        else:
            self.log.appendPlainText(f"开始监控：{account.secondary_region} / {account.role_name}")
        self._update_output_panels()
        self._poll_active_window_states()
        self._poll_window_occlusion()
        self.refresh_windows()

    def _make_output_panel(
        self, hwnd: int, account: AccountInfo
    ) -> tuple[QFrame, QLabel, QLabel, QLabel, QLabel, QLabel]:
        panel = QFrame()
        panel.setObjectName("outputCard")
        panel.setFixedSize(156, 230)
        panel.setStyleSheet(
            "QFrame#outputCard{background:#FFFFFF;border:1px solid #E2E8F0;border-radius:10px;}"
            "QFrame#outputCard:hover{border-color:#AAC6FA;background:#F5F9FF;}"
        )
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(3, 5, 3, 5)
        panel_layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel(f"{account.secondary_region} / {account.role_name}")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title.setStyleSheet("font-size:11px;font-weight:800;color:#243247;")
        title.setToolTip(
            f"二级区名：{account.secondary_region}\n角色：{account.role_name}"
            + (f"\n角色 ID：{account.role_id}" if account.role_id else "")
        )
        remove_button = QPushButton("移除")
        remove_button.setFixedSize(34, 20)
        remove_button.setStyleSheet(
            "QPushButton{min-height:18px;padding:0;border:0;color:#66768D;background:transparent;font-size:10px;}"
            "QPushButton:hover{color:#B42335;background:#FFE2E7;border-radius:5px;}"
        )
        remove_button.clicked.connect(lambda _checked=False, target=hwnd: self._request_remove_window(target))
        header.addWidget(title, 1)
        header.addWidget(remove_button)
        panel_layout.addLayout(header)

        preview = QLabel("等待游戏画面…")
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setFixedSize(148, 111)
        preview.setStyleSheet("background:#F1F5FB;color:#66768D;border:1px solid #E2E8F0;border-radius:7px;")
        panel_layout.addWidget(preview)

        status = QLabel("正在等待背包画面…")
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status.setFixedHeight(20)
        status.setStyleSheet(
            "background:#F1F5FB;color:#52647D;border:1px solid #D6DFEC;border-radius:6px;font-weight:800;"
        )
        detail = QLabel("空格 -｜已拥有 -｜置信度 -")
        occlusion = QLabel("遮挡 检测中｜最小化 否")
        for label in (detail, occlusion):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedHeight(19)
            label.setStyleSheet("font-size:11px;color:#66768D;")
        detail.setFixedHeight(34)
        occlusion.setFixedHeight(17)
        panel_layout.addWidget(status)
        panel_layout.addWidget(detail)
        panel_layout.addWidget(occlusion)
        return panel, title, preview, status, detail, occlusion

    def _update_session_identity(self, session: MonitorSession, title: str) -> None:
        account = parse_account_info(title)
        session.last_signal = None
        session.last_analysis_at = None
        session.title = title
        session.account = account
        session.worker.window_title = title
        session.output_title.setText(f"{session.account.secondary_region} / {session.account.role_name}")
        session.output_title.setToolTip(
            f"二级区名：{session.account.secondary_region}\n角色：{session.account.role_name}"
            + (f"\n角色 ID：{session.account.role_id}" if session.account.role_id else "")
        )

    def _update_output_panels(self) -> None:
        self.outputs_group.setTitle(f"窗口输出（{len(self.sessions)}）")
        self.output_placeholder.setVisible(not self.sessions)
        for index, session in enumerate(self.sessions.values()):
            self.output_grid.addWidget(session.output_panel, index // self.OUTPUT_COLUMNS, index % self.OUTPUT_COLUMNS)
        available = any(hwnd not in self.sessions for hwnd in self._window_titles)
        self.add_window_button.setEnabled(available and len(self.sessions) < MAX_MONITORED_WINDOWS)
        self._refresh_mini_status()

    def _request_remove_window(self, hwnd: int, reason: str = "") -> None:
        session = self.sessions.get(hwnd)
        if session is None or session.removing:
            return
        session.removing = True
        session.output_status.setText("正在移除…")
        session.worker.stop()
        if reason:
            self.log.appendPlainText(f"{reason}：{session.account.secondary_region} / {session.account.role_name}")

    @Slot(int, object)
    def _on_preview(self, hwnd: int, image: Any) -> None:
        if not self._authenticated:
            return
        session = self.sessions.get(hwnd)
        if session is None or session.minimized or self.monitoring_paused:
            return
        session.last_image = image
        self._display_preview(session.output_preview, image)

    @staticmethod
    def _display_preview(target: QLabel, image: Any) -> None:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        qimage = QImage(rgb.data, width, height, channels * width, QImage.Format.Format_RGB888).copy()
        target.setPixmap(
            QPixmap.fromImage(qimage).scaled(
                target.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    @Slot(int, object)
    def _on_analysis(self, hwnd: int, signal: dict[str, Any]) -> None:
        if not self._authenticated:
            return
        session = self.sessions.get(hwnd)
        if session is None or session.minimized or self.monitoring_paused:
            return
        if "character_id" in signal and signal["character_id"] != session.account.role_id:
            return
        session.last_signal = signal
        session.last_analysis_at = monotonic()
        observed_stall = signal.get("stall_panel_type")
        new_mode = "buy" if observed_stall == "stall_buy" else "sell" if observed_stall == "stall" else None
        if new_mode and new_mode != session.stall_mode:
            session.stall_mode = new_mode
            session.capacity_alert_active = False
            session.capacity_candidate_since = None
            session.cash_alert_active = False
        reason = str(signal.get("money_reason") or "等待金额识别")
        cash = signal.get("cash")
        session.output_detail.setToolTip(
            f"现金：{cash if cash is not None else '未识别'}\n"
            f"{reason}"
        )
        self._apply_signal(session, signal)
        self._update_capacity_alert(session, signal)
        self._update_cash_alert(session, signal)

    def _apply_signal(self, session: MonitorSession, signal: dict[str, Any]) -> None:
        status = str(signal.get("status", "invalid"))
        text, foreground, background = self.STATUS_PRESENTATION.get(status, ("未知状态", "#52647D", "#F1F5FB"))
        panel_type = signal.get("panel_type")
        if panel_type in PANEL_NAMES and panel_type != "inventory":
            text, foreground, background = PANEL_NAMES[panel_type], "#2563DB", "#EEF4FF"
        session.output_status.setText(text)
        session.output_status.setStyleSheet(
            f"background:{background};color:{foreground};border:1px solid {foreground};"
            "border-radius:6px;font-weight:800;"
        )
        cash = signal.get("cash")
        cash_text = "未识别" if cash is None else f"{int(cash):,}"
        qualifier = "上次 · " if cash is not None and not signal.get("cash_readable", signal.get("money_readable")) else ""
        session.output_detail.setText(
            f'<b style="color:#243247;">现金 {cash_text}</b><br>'
            f'<span style="font-size:10px;">{qualifier}空格 {signal.get("empty_count", 0)} · '
            f'置信度 {float(signal.get("confidence", 0.0)):.0%}</span>'
        )

    def _update_capacity_alert(
        self, session: MonitorSession, signal: dict[str, Any], observed_at: float | None = None
    ) -> None:
        if not self._capacity_enabled or session.stall_mode not in self._capacity_modes:
            session.capacity_alert_active = False
            session.capacity_candidate_since = None
            return
        if str(signal.get("status", "")) not in {"full", "almost_full", "not_full"}:
            session.capacity_alert_active = False
            session.capacity_candidate_since = None
            return
        empty_count = max(0, int(signal.get("empty_count", 0)))
        threshold = self._capacity_threshold
        reached = empty_count <= threshold
        if not reached:
            session.capacity_alert_active = False
            session.capacity_candidate_since = None
            return
        now = monotonic() if observed_at is None else observed_at
        if session.capacity_candidate_since is None:
            session.capacity_candidate_since = now
            return
        if now - session.capacity_candidate_since < self.config.inventory_monitor.alert_confirm_seconds:
            return
        if session.capacity_alert_active:
            return
        session.capacity_alert_active = True
        account_label = f"{session.account.secondary_region} / {session.account.role_name}"
        if threshold == 0:
            title = "背包已满"
            message = "检测到背包空格为 0，请及时处理。"
        else:
            title = "背包容量提醒"
            message = f"检测到背包剩余 {empty_count} 格，已达到提醒阈值（≤ {threshold} 格），请及时处理。"
        self.log.appendPlainText(f"{title}：{account_label}，正在上报云端")
        self._send_host_alert(
            session,
            "inventory_full" if threshold == 0 else "inventory_capacity",
            empty_slots=empty_count,
            confidence=float(signal.get("confidence", 0.0)),
            details={"threshold": threshold, "message": message},
        )
        if self._local_alert_enabled:
            self._show_alert(
                QMessageBox.Icon.Critical,
                title,
                message,
                account_label,
                with_sound=True,
            )

    def _update_cash_alert(self, session: MonitorSession, signal: dict[str, Any]) -> None:
        if not self._cash_low_enabled or session.stall_mode not in self._cash_low_modes:
            session.cash_alert_active = False
            return
        # MoneyTracker retains old confirmed values while the amount is unreadable.
        # Only a freshly readable value may start or clear a low-cash alert.
        cash = signal.get("cash")
        if signal.get("cash_readable") is not True or not isinstance(cash, int) or isinstance(cash, bool):
            return
        threshold_wan = self._cash_threshold_wan
        if cash >= threshold_wan * 10000:
            session.cash_alert_active = False
            return
        if session.cash_alert_active:
            return
        session.cash_alert_active = True
        account_label = f"{session.account.secondary_region} / {session.account.role_name}"
        message = f"当前游戏币 {cash:,}，低于设置阈值 {threshold_wan} 万，请及时补充。"
        self.log.appendPlainText(f"游戏币不足：{account_label}，{message}正在上报云端")
        self._send_host_alert(session, "cash_low", details={"cash": cash, "threshold_wan": threshold_wan})
        if self._local_alert_enabled:
            self._show_alert(QMessageBox.Icon.Warning, "游戏币不足", message, account_label, with_sound=True)

    @Slot(int, object)
    def _on_event(self, hwnd: int, event: Event) -> None:
        if not self._authenticated:
            return
        session = self.sessions.get(hwnd)
        if session is None or self.monitoring_paused:
            return
        if (event.type == "panel_opened" and event.payload.get("panel_type") == "stall_other_buy"
                or event.type == "market_stall_changed"):
            self._show_market_toast(session.account.secondary_region, event)
        if event.type == "market_gem_quotes":
            self.relay_client.send_market_quotes({
                "event_id": event.event_id, "occurred_at": event.payload["occurred_at"],
                "server_name": session.account.secondary_region,
                "observer_character_id": session.account.role_id or None,
                "stall_owner_id": event.payload["stall_owner_id"], "quotes": event.payload["quotes"],
            })
            return
        if session.minimized and (event.type not in {"panel_opened", "panel_closed", "game_default", "panel_target_changed"}
                                  or is_window_minimized(hwnd)):
            return
        account_label = f"{session.account.secondary_region} / {session.account.role_name}"
        self.log.appendPlainText(f"{event.display_time}  {account_label}  {event.display_name}")
        if event.type in {"panel_opened", "panel_closed", "game_default", "panel_target_changed", "market_stall_changed"}:
            payload = {
                "event_id": event.event_id, "event_type": event.type, "reason": "recognition",
                "occurred_at": event.payload["occurred_at"],
                "window_id": f"hwnd:{hwnd}", "server_name": session.account.secondary_region,
                "role_name": session.account.role_name, "character_id": session.account.role_id or None,
                "monitor_session_id": session.monitor_session_id,
                "panel_type": event.payload.get("panel_type"), "confidence": event.confidence,
                "message": event.display_name,
                **{key: event.payload[key] for key in ("target_name", "target_level", "target_confidence", "target_status")
                   if key in event.payload},
                **{key: event.payload[key] for key in ("stamina_current", "stamina_max", "character_level", "stamina_observed_at")
                   if key in event.payload},
            }
            if event.evidence_path:
                self.relay_client.send_monitor_payload(payload, screenshot_path=event.evidence_path)
            else:
                self.relay_client.send_monitor_payload(payload)

    def _show_market_toast(self, server_name: str, event: Event) -> None:
        if self._market_toast is None:
            toast = QFrame(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                           | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
            toast.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            toast.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            toast.setObjectName("marketDetectionToast")
            toast.setStyleSheet("""
                QFrame#marketDetectionToast { background: #17213B; border: 1px solid #33466F;
                    border-radius: 12px; }
                QLabel { color: #F8FAFC; background: transparent; }
                QLabel#toastTitle { font-size: 15px; font-weight: 800; }
                QLabel#toastDetail { color: #D8E2F4; font-size: 12px; }
            """)
            layout = QVBoxLayout(toast)
            layout.setContentsMargins(16, 12, 16, 12)
            layout.setSpacing(5)
            self._market_toast_title = QLabel("已检测到他人摊位", toast)
            self._market_toast_title.setObjectName("toastTitle")
            self._market_toast_detail = QLabel(toast)
            self._market_toast_detail.setObjectName("toastDetail")
            layout.addWidget(self._market_toast_title)
            layout.addWidget(self._market_toast_detail)
            self._market_toast = toast
        occurred = str(event.payload.get("occurred_at") or "")
        try:
            detected_at = datetime.fromisoformat(occurred).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            detected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        assert self._market_toast_detail is not None and self._market_toast is not None
        self._market_toast_detail.setText(f"区服：{server_name or '未知区服'}\n检测时间：{detected_at}")
        self._market_toast.adjustSize()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self._market_toast.move(area.right() - self._market_toast.width() - 18,
                                    area.bottom() - self._market_toast.height() - 18)
        self._market_toast.show()
        self._market_toast.raise_()
        self._market_toast_timer.start(2000)

    def _hide_market_toast(self) -> None:
        if self._market_toast is not None:
            self._market_toast.hide()

    def _show_alert(
        self,
        icon: QMessageBox.Icon,
        title: str,
        message: str,
        details: str = "",
        *,
        with_sound: bool = False,
    ) -> None:
        # There is deliberately only one desktop alert. New warnings replace
        # its content so repeated detections never leave hundreds to dismiss.
        alert = next(iter(self.alerts), None)
        if alert is None:
            alert = QMessageBox(None)
            apply_desktop_theme(alert)
            alert.setStandardButtons(QMessageBox.StandardButton.Close)
            alert.setWindowModality(Qt.WindowModality.NonModal)
            alert.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            alert.setWindowFlag(Qt.WindowType.Tool, True)
            alert.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            alert.finished.connect(lambda _result, current=alert: self._on_alert_finished(current))
            self.alerts.add(alert)
        alert.setIcon(icon)
        alert.setWindowTitle(title)
        alert.setText(message)
        alert.setInformativeText(details)
        close_button = alert.button(QMessageBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText("确认并停止声音" if with_sound else "确认")
        if with_sound:
            self._register_sounding_alert(alert)
        elif alert in self._sounding_alerts:
            self._sounding_alerts.discard(alert)
            self._stop_alarm_sound()
        alert.show()
        alert.raise_()
        alert.activateWindow()
        QTimer.singleShot(0, lambda current=alert: self._position_alert(current))

    def _register_sounding_alert(self, alert: QMessageBox) -> None:
        should_start = not self._sounding_alerts
        self._sounding_alerts.add(alert)
        if should_start:
            self._play_alarm_sound()

    def _on_alert_finished(self, alert: QMessageBox) -> None:
        self.alerts.discard(alert)
        if alert not in self._sounding_alerts:
            return
        self._sounding_alerts.discard(alert)
        if not self._sounding_alerts:
            self._stop_alarm_sound()

    def _play_alarm_sound(self) -> None:
        try:
            alarm_path = play_alarm_loop()
            self.log.appendPlainText(f"告警音乐开始循环播放：{alarm_path.name}")
        except OSError as exc:
            self.log.appendPlainText(f"告警音乐播放失败：{exc}")
            QApplication.beep()

    def _stop_alarm_sound(self) -> None:
        stop_alarm_loop()
        self.log.appendPlainText("告警音乐已停止。")

    def _position_alert(self, alert: QMessageBox) -> None:
        if alert not in self.alerts or not alert.isVisible():
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        margin = 16
        x = available.right() - alert.width() - margin + 1
        y = available.bottom() - alert.height() - margin + 1
        alert.move(max(available.left() + margin, x), y)

    @Slot()
    def _poll_active_window_states(self) -> None:
        if not self._authenticated or self.monitoring_paused:
            return
        now = monotonic()
        for hwnd, session in tuple(self.sessions.items()):
            if session.removing:
                continue
            if not is_window_available(hwnd):
                self._request_remove_window(hwnd, "游戏窗口已经关闭")
                continue
            minimized = is_window_minimized(hwnd)
            account_label = f"{session.account.secondary_region} / {session.account.role_name}"
            if minimized and not session.minimized:
                session.minimized = True
                session.minimized_since = now
                session.minimized_alert_active = False
                self.log.appendPlainText(f"游戏窗口已最小化，检测暂停：{account_label}")
                self._show_minimized_status(session)
            elif not minimized and session.minimized:
                session.minimized = False
                session.last_signal = None
                session.last_analysis_at = None
                session.minimized_since = None
                session.minimized_alert_active = False
                self.log.appendPlainText(f"游戏窗口已恢复，检测继续：{account_label}")
                session.output_status.setText("窗口已恢复，正在重新检测…")
                session.output_detail.setText("空格 -｜已拥有 -｜置信度 -")
                session.output_occlusion.setText("遮挡 检测中｜最小化 否")
                session.output_occlusion.setStyleSheet(f"color:{PRIMARY};font-size:11px;font-weight:700;")
                session.output_preview.clear()
                session.output_preview.setText("等待新的游戏画面…")

    def _show_minimized_status(self, session: MonitorSession) -> None:
        session.output_status.setText("检测已暂停")
        session.output_status.setStyleSheet(
            "background:#FFF7E3;color:#986300;border:1px solid #EFD49A;border-radius:6px;font-weight:800;"
        )
        session.output_detail.setText("空格 -｜已拥有 -｜置信度 -")
        session.output_preview.clear()
        session.output_preview.setText("窗口已最小化，检测暂停")
        session.output_occlusion.setText("遮挡 --｜最小化 是")
        session.output_occlusion.setStyleSheet(f"color:{WARNING};font-size:11px;font-weight:700;")

    @Slot()
    def _poll_window_occlusion(self) -> None:
        if not self._authenticated or self.monitoring_paused:
            return
        for session in tuple(self.sessions.values()):
            if session.removing or not is_window_available(session.hwnd):
                continue
            try:
                status = measure_window_occlusion(session.hwnd)
            except Exception as exc:  # noqa: BLE001 - native window-query failures belong in the UI
                session.output_occlusion.setText(f"遮挡 检测失败｜最小化 {'是' if session.minimized else '否'}")
                session.output_occlusion.setStyleSheet(f"color:{MUTED};font-size:11px;font-weight:700;")
                session.output_occlusion.setToolTip(str(exc))
                continue
            self._apply_occlusion(session, status)

    def _apply_occlusion(self, session: MonitorSession, status: WindowOcclusionStatus) -> None:
        session.last_occlusion_percent = 0.0 if status.minimized else status.ratio * 100
        if status.minimized:
            text = "遮挡 --｜最小化 是"
            color = WARNING
            session.occlusion_alert_active = False
            session.occlusion_candidate_since = None
        else:
            percentage = status.ratio * 100
            display = "<0.1" if 0 < percentage < 0.1 else f"{percentage:.1f}"
            text = f"遮挡 {display}%｜最小化 否"
            color = SUCCESS if status.occluded_area == 0 else DANGER
        session.output_occlusion.setText(text)
        session.output_occlusion.setToolTip("根据其他桌面窗口覆盖游戏窗口的面积计算")
        session.output_occlusion.setStyleSheet(f"color:{color};font-size:11px;font-weight:700;")

    @Slot(int, object)
    def _on_stats(self, hwnd: int, stats: dict[str, Any]) -> None:
        session = self.sessions.get(hwnd)
        if session is not None:
            session.last_stats = stats

    @Slot(int, str)
    def _on_failed(self, hwnd: int, message: str) -> None:
        if not self._authenticated:
            return
        session = self.sessions.get(hwnd)
        if session is None:
            return
        summary = message.strip().splitlines()[0] if message.strip() else "未知错误"
        session.output_status.setText("监控失败")
        session.output_status.setStyleSheet(
            "background:#FFF0F2;color:#B42335;border:1px solid #F2BCC4;border-radius:6px;font-weight:800;"
        )
        account_label = f"{session.account.secondary_region} / {session.account.role_name}"
        self.log.appendPlainText(f"监控失败：{account_label}：{summary}")
        if self._local_alert_enabled:
            self._show_alert(QMessageBox.Icon.Critical, "背包监控失败", summary, account_label)

    @Slot(int)
    def _on_worker_finished(self, hwnd: int) -> None:
        session = self.sessions.get(hwnd)
        if session is not None:
            session.output_status.setText("监控已停止")

    @Slot()
    def _on_thread_finished(self) -> None:
        thread = self.sender()
        if not isinstance(thread, QThread):
            return
        hwnd = int(thread.property("window_hwnd"))
        session = self.sessions.pop(hwnd, None)
        if session is None:
            return
        session.output_panel.setParent(None)
        session.output_panel.deleteLater()
        session.worker.deleteLater()
        session.thread.deleteLater()
        self._update_output_panels()
        if not self._closing:
            self.refresh_windows()

    @Slot()
    def _report_window_status(self) -> None:
        """Publish a replaceable snapshot so the master can show every game window."""
        if not self._authenticated:
            return
        windows: list[dict[str, Any]] = []
        now = datetime.now(UTC).isoformat(timespec="milliseconds")
        for session in tuple(self.sessions.values()):
            signal = session.last_signal or {}
            if session.last_analysis_at is None or monotonic() - session.last_analysis_at > 15:
                signal = {}
            money = {key: signal.get(key) for key in ("cash", "money_confirmed_at")}
            stamina = {key: signal.get(key) for key in
                       ("stamina_current", "stamina_max", "character_level", "stamina_observed_at")}
            money_current = bool(signal.get("money_readable"))
            try:
                money_current = money_current and (datetime.now(UTC) - datetime.fromisoformat(str(money["money_confirmed_at"]))).total_seconds() <= 5
            except (TypeError, ValueError):
                money_current = False
            # OS state remains measurable while inventory analysis is paused.
            # Never archive an old occlusion measurement as a fresh observation.
            minimized = is_window_minimized(session.hwnd)
            try:
                measured = measure_window_occlusion(session.hwnd)
                minimized = measured.minimized
                occlusion_percent = 0.0 if minimized else measured.ratio * 100
            except Exception:  # noqa: BLE001 - native query failure means unknown, never stale data
                occlusion_percent = None
            if session.removing:
                status = "removing"
                status_text = "正在移除"
            elif self.monitoring_paused:
                status = "paused"
                status_text = "监控已暂停"
            elif minimized:
                status = "minimized"
                status_text = "窗口已最小化"
            else:
                status = str(signal.get("status") or "waiting")
                status_text = self.STATUS_PRESENTATION.get(status, ("等待检测", "", ""))[0]
            if self.monitoring_paused or minimized or session.removing:
                signal = {}  # Do not present a stale inventory result as current.
            windows.append(
                {
                    **money,
                    **stamina,
                    "money_readable": money_current and not (self.monitoring_paused or minimized or session.removing) and occlusion_percent == 0,
                    "window_id": f"hwnd:{session.hwnd}",
                    "title": session.title,
                    "server_name": session.account.secondary_region,
                    "role_name": session.account.role_name,
                    "character_id": session.account.role_id or None,
                    "monitor_session_id": session.monitor_session_id,
                    "stall_panel_type": signal.get("stall_panel_type"),
                    "sell_stock_count": signal.get("sell_stock_count"),
                    "status": status,
                    "status_text": status_text,
                    "empty_slots": signal.get("empty_count"),
                    "occupied_slots": signal.get("occupied_count"),
                    "confidence": signal.get("confidence"),
                    "occlusion_percent": occlusion_percent,
                    "minimized": minimized,
                    "monitoring": not session.removing and not self.monitoring_paused,
                    "updated_at": now,
                }
            )
        self.relay_client.send_status(windows)

    @Slot(object)
    def _on_live_control(self, control: dict[str, Any]) -> None:
        if not self._authenticated:
            return
        try:
            interval = int(control.get("interval_minutes", 5))
            self._live_interval_minutes = max(1, min(60, interval))
        except (TypeError, ValueError):
            self._live_interval_minutes = 5
        for item in control.get("requests", []):
            request_id = str(item.get("request_id") or "")
            window_id = str(item.get("window_id") or "")
            if not request_id or request_id in self._seen_live_requests or not window_id.startswith("hwnd:"):
                continue
            try:
                hwnd = int(window_id[5:])
            except ValueError:
                continue
            session = self.sessions.get(hwnd)
            if session and not session.removing and not is_window_minimized(hwnd):
                self._seen_live_requests.add(request_id)
                if hwnd in self._queued_live_captures:
                    self._queued_live_captures.discard(hwnd)
                    self._live_capture_queue = [item for item in self._live_capture_queue if item != hwnd]
                session.worker.request_live_snapshot(request_id)
        now = monotonic()
        for hwnd, session in self.sessions.items():
            if session.removing or self.monitoring_paused or is_window_minimized(hwnd):
                continue
            if now - self._last_live_capture.get(hwnd, -3600.0) >= self._live_interval_minutes * 60:
                if hwnd not in self._queued_live_captures:
                    self._queued_live_captures.add(hwnd)
                    self._live_capture_queue.append(hwnd)
        if self._live_capture_queue and not self._live_capture_queue_timer.isActive():
            self._dispatch_next_periodic_snapshot()

    def _dispatch_next_periodic_snapshot(self) -> None:
        while self._live_capture_queue:
            hwnd = self._live_capture_queue.pop(0)
            self._queued_live_captures.discard(hwnd)
            session = self.sessions.get(hwnd)
            if (session is None or session.removing or self.monitoring_paused
                    or is_window_minimized(hwnd)):
                continue
            self._last_live_capture[hwnd] = monotonic()
            session.worker.request_live_snapshot()
            break
        if self._live_capture_queue:
            self._live_capture_queue_timer.start(10_000)

    @Slot(int, object, str, str)
    def _on_live_snapshot(self, hwnd: int, jpeg: bytes, captured_at: str, request_id: str) -> None:
        if not self._authenticated or hwnd not in self.sessions:
            return
        self._last_live_capture[hwnd] = monotonic()
        self.relay_client.live_screenshots.upload(f"hwnd:{hwnd}", jpeg, captured_at, request_id)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing:
            event.accept()
            return
        self._closing = True
        if self._authenticated:
            self.relay_client.send_monitor_log("monitoring_paused", "close")
        self.auth_timer.stop()
        self.auth_client.cancel()
        self.scan_timer.stop()
        self.window_state_timer.stop()
        self.occlusion_timer.stop()
        self.status_timer.stop()
        self.relay_client.flush_monitor_logs()
        self.relay_client.configure("", "")
        for alert in tuple(self.alerts):
            alert.close()
        self._sounding_alerts.clear()
        self._stop_alarm_sound()
        sessions = tuple(self.sessions.values())
        for session in sessions:
            session.worker.stop()
        for session in sessions:
            session.thread.quit()
            session.thread.wait(3000)
        event.accept()


def main(argv: list[str] | None = None) -> int:
    configure_windows_app_id(SLAVE_APP_ID)
    app = QApplication(argv or sys.argv)
    apply_application_icon(app)
    window = InventoryMonitorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
