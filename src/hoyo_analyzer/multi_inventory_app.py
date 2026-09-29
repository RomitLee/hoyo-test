"""Multi-window desktop UI for inventory monitoring."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, ClassVar

import cv2
from PySide6.QtCore import Qt, QThread, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .alarm_sound import play_alarm_loop, stop_alarm_loop
from .config import load_config
from .inventory_app import (
    APP_NAME,
    MAX_MONITORED_WINDOWS,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
    AccountInfo,
    InventoryMonitorWorker,
    is_mhxy_game_window_title,
    parse_account_info,
)
from .models import Event
from .paths import application_root
from .relay_client import SlaveRelayClient, make_event_payload
from .settings_dialog import SlaveSettingsDialog
from .slave_settings import (
    ALERT_HOST,
    ALERT_LOCAL,
    ALERT_LOCAL_HOST,
    LOCAL_ALERT_POPUP_SOUND,
    load_slave_settings,
    save_slave_settings,
)
from .wgc import (
    WindowOcclusionStatus,
    is_window_available,
    is_window_minimized,
    list_capturable_windows,
    measure_window_occlusion,
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
    last_image: Any | None = None
    last_signal: dict[str, Any] | None = None
    last_stats: dict[str, Any] | None = None
    minimized: bool = False
    last_occlusion_percent: float = 0.0
    minimized_since: float | None = None
    minimized_alert_active: bool = False
    capacity_alert_active: bool = False
    capacity_candidate_since: float | None = None
    occlusion_alert_active: bool = False
    occlusion_candidate_since: float | None = None
    removing: bool = False


class InventoryMonitorWindow(QMainWindow):
    """Monitor up to eight game windows and show the selected live preview."""

    STATUS_PRESENTATION: ClassVar[dict[str, tuple[str, str, str]]] = {
        "full": ("背包已满", "#b91c1c", "#fee2e2"),
        "almost_full": ("背包接近满", "#b45309", "#fef3c7"),
        "not_full": ("背包未满", "#15803d", "#dcfce7"),
        "inventory_closed": ("背包未打开", "#b45309", "#fff7ed"),
        "invalid": ("背包被遮挡", "#9f1239", "#ffe4e6"),
    }

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        root = application_root()
        path = config_path or root / "configs" / "default.toml"
        self.config = load_config(path if path.exists() else None)
        self.slave_settings = load_slave_settings()
        self.sessions: dict[int, MonitorSession] = {}
        self.alerts: set[QMessageBox] = set()
        self._sounding_alerts: set[QMessageBox] = set()
        self._window_titles: dict[int, str] = {}
        self.monitoring_paused = False
        self._closing = False
        self._build_ui()
        self.relay_client = SlaveRelayClient(self)
        self.relay_client.connection_changed.connect(self._on_relay_connection_changed)
        self.relay_client.event_result.connect(self._on_relay_event_result)
        self._configure_relay()

        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.refresh_windows)
        self.scan_timer.start(3000)
        self.window_state_timer = QTimer(self)
        self.window_state_timer.timeout.connect(self._poll_active_window_states)
        self.window_state_timer.start(1000)
        self.occlusion_timer = QTimer(self)
        self.occlusion_timer.timeout.connect(self._poll_window_occlusion)
        self.occlusion_timer.start(2000)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._report_window_status)
        self.status_timer.start(5000)
        QTimer.singleShot(0, self.refresh_windows)
        QTimer.singleShot(250, self.relay_client.connect_now)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setStyleSheet(
            "QWidget{font-size:13px;} QGroupBox{font-weight:700;border:1px solid #dbe3ee;"
            "border-radius:8px;margin-top:8px;padding:10px;} QGroupBox::title{subcontrol-origin:margin;left:10px;}"
        )
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        heading = QHBoxLayout()
        heading_text = QVBoxLayout()
        title = QLabel("梦幻西游背包监控")
        title.setStyleSheet("font-size:24px;font-weight:800;color:#1e293b;")
        subtitle = QLabel("最多同时监控 8 个游戏窗口，只检测背包是否打开、是否已满")
        subtitle.setStyleSheet("color:#64748b;")
        heading_text.addWidget(title)
        heading_text.addWidget(subtitle)
        heading.addLayout(heading_text, 1)
        self.add_window_button = QPushButton("＋ 增加窗口")
        self.add_window_button.setFixedHeight(28)
        self.add_window_button.setStyleSheet(
            "QPushButton{border:1px solid #2563eb;border-radius:5px;color:#1d4ed8;font-weight:700;padding:0 10px;}"
            "QPushButton:hover{background:#eff6ff;}QPushButton:disabled{border-color:#cbd5e1;color:#94a3b8;}"
        )
        self.add_window_button.clicked.connect(self._show_add_window_menu)
        heading.addWidget(self.add_window_button, 0, Qt.AlignmentFlag.AlignTop)
        self.monitoring_button = QPushButton("暂停监控")
        self.monitoring_button.setFixedHeight(28)
        self.monitoring_button.setToolTip("暂停所有窗口的背包分析和告警")
        self.monitoring_button.clicked.connect(self._toggle_monitoring)
        heading.addWidget(self.monitoring_button, 0, Qt.AlignmentFlag.AlignTop)
        self.settings_button = QPushButton("设置")
        self.settings_button.setToolTip("设置从机告警策略和云端连接")
        self.settings_button.clicked.connect(self._open_settings)
        heading.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignTop)
        self.host_status_label = QLabel("云端：未连接")
        self.host_status_label.setStyleSheet("color:#b91c1c;font-weight:700;")
        heading.addWidget(self.host_status_label, 0, Qt.AlignmentFlag.AlignTop)
        self.host_connect_button = QPushButton("连接")
        self.host_connect_button.setToolTip("立即测试云端 HTTPS 连接")
        self.host_connect_button.clicked.connect(self._connect_relay)
        heading.addWidget(self.host_connect_button, 0, Qt.AlignmentFlag.AlignTop)
        self.background_button = QPushButton("后台监控")
        self.background_button.setToolTip("最小化本程序，所有游戏窗口继续检测")
        self.background_button.clicked.connect(self._run_in_background)
        heading.addWidget(self.background_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(heading)
        self._refresh_host_status()

        self.outputs_group = QGroupBox("窗口输出（0）")
        outputs_layout = QVBoxLayout(self.outputs_group)
        self.output_scroll = QScrollArea()
        self.output_scroll.setWidgetResizable(False)
        self.output_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.output_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.output_scroll.setFixedSize(731, 385)
        self.output_container = QWidget()
        self.output_container.setFixedSize(729, 383)
        self.output_grid = QGridLayout(self.output_container)
        self.output_grid.setContentsMargins(5, 5, 5, 5)
        self.output_grid.setHorizontalSpacing(5)
        self.output_grid.setVerticalSpacing(5)
        self.output_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.output_placeholder = QLabel("尚未监控窗口，请点击上方“增加窗口”")
        self.output_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.output_placeholder.setStyleSheet("color:#64748b;padding:40px;")
        self.output_grid.addWidget(self.output_placeholder, 0, 0, 1, 4)
        self.output_scroll.setWidget(self.output_container)
        outputs_layout.addWidget(self.output_scroll)
        layout.addWidget(self.outputs_group, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setFixedHeight(42)
        self.log.setPlaceholderText("所有窗口的提醒记录会显示在这里")
        layout.addWidget(self.log)

    @Slot()
    def refresh_windows(self) -> None:
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
        self.monitoring_paused = not self.monitoring_paused
        if self.monitoring_paused:
            self.monitoring_button.setText("开始监控")
            self.monitoring_button.setToolTip("恢复所有窗口的背包分析和告警")
            self.monitoring_button.setStyleSheet(
                "QPushButton{background:#15803d;color:white;border:0;border-radius:5px;font-weight:700;padding:0 10px;}"
                "QPushButton:hover{background:#166534;}"
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
            session.capacity_alert_active = False
            session.capacity_candidate_since = None
            session.occlusion_alert_active = False
            session.occlusion_candidate_since = None
            session.worker.set_paused(False)
            if session.minimized:
                session.minimized_since = monotonic()
                session.minimized_alert_active = False
                self._show_minimized_status(session)
            else:
                session.minimized_since = None
                session.minimized_alert_active = False
                session.output_status.setText("监控已恢复，正在重新检测…")
                session.output_status.setStyleSheet(
                    "background:#e0f2fe;color:#0369a1;border-radius:6px;font-weight:800;"
                )
                session.output_detail.setText("空格 -｜已拥有 -｜置信度 -")
                session.output_preview.clear()
                session.output_preview.setText("等待新的游戏画面…")
                session.output_occlusion.setText("遮挡 检测中｜最小化 否")
        self.log.appendPlainText("监控已恢复：所有异常将重新计时确认。")
        self._poll_active_window_states()
        self._poll_window_occlusion()

    @staticmethod
    def _show_manual_pause_status(session: MonitorSession) -> None:
        session.output_status.setText("监控已手动暂停")
        session.output_status.setStyleSheet("background:#fef3c7;color:#b45309;border-radius:6px;font-weight:800;")
        session.output_detail.setText("空格 -｜已拥有 -｜置信度 -")
        session.output_preview.clear()
        session.output_preview.setText("点击“开始监控”后继续")
        session.output_occlusion.setText("遮挡 --｜监控暂停")
        session.output_occlusion.setStyleSheet("color:#b45309;font-size:12px;font-weight:700;")

    @Slot()
    def _open_settings(self) -> None:
        dialog = SlaveSettingsDialog(self.slave_settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.settings()
        try:
            save_slave_settings(updated)
        except OSError as exc:
            QMessageBox.critical(self, "设置保存失败", f"无法保存从机设置：{exc}")
            return
        self.slave_settings = updated
        for session in self.sessions.values():
            session.capacity_alert_active = False
            session.capacity_candidate_since = None
            session.occlusion_alert_active = False
            session.occlusion_candidate_since = None
            session.minimized_alert_active = False
            session.minimized_since = monotonic() if session.minimized else None
        self._configure_relay()
        self._refresh_host_status()
        self.log.appendPlainText("从机设置已保存并生效。")
        self.relay_client.connect_now()
        self._poll_window_occlusion()

    def _refresh_host_status(self) -> None:
        online = hasattr(self, "relay_client") and self.relay_client.online
        self.host_status_label.setText("云端：已连接" if online else "云端：未连接")
        self.host_status_label.setStyleSheet(
            f"color:{'#15803d' if online else '#b91c1c'};font-weight:700;"
        )
        configured = bool(
            self.slave_settings.relay_url
            and self.slave_settings.device_id
            and self.slave_settings.device_token
        )
        endpoint = self.slave_settings.relay_url or "尚未设置云端地址"
        self.host_status_label.setToolTip(f"{endpoint}\n设备：{self.slave_settings.device_id or '未设置'}")
        self.host_connect_button.setVisible(True)
        self.host_connect_button.setEnabled(configured)

    def _configure_relay(self) -> None:
        try:
            self.relay_client.configure(
                self.slave_settings.relay_url,
                self.slave_settings.device_id,
                self.slave_settings.device_token,
            )
        except ValueError as exc:
            self.host_status_label.setToolTip(str(exc))
            self.log.appendPlainText(f"云端设置无效：{exc}")
        self._refresh_host_status()

    @Slot()
    def _connect_relay(self) -> None:
        self.host_connect_button.setEnabled(False)
        self.host_status_label.setText("云端：连接中…")
        self.relay_client.connect_now()

    @Slot(bool, str)
    def _on_relay_connection_changed(self, online: bool, message: str) -> None:
        self.host_status_label.setText("云端：已连接" if online else "云端：未连接")
        self.host_status_label.setStyleSheet(
            f"color:{'#15803d' if online else '#b91c1c'};font-weight:700;"
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
                details=details,
            )
        )
        self.log.appendPlainText(
            f"正在发送主机告警：{session.account.secondary_region} / {session.account.role_name}"
        )

    @Slot()
    def _show_add_window_menu(self) -> None:
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

        thread.started.connect(worker.run)
        worker.preview_for_window.connect(self._on_preview)
        worker.analysis_for_window.connect(self._on_analysis)
        worker.event_for_window.connect(self._on_event)
        worker.stats_for_window.connect(self._on_stats)
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
        panel.setFixedSize(176, 184)
        panel.setStyleSheet("QFrame#outputCard{background:#f8fafc;border:1px solid #dbe3ee;border-radius:8px;}")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(5, 5, 5, 5)
        panel_layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel(f"{account.secondary_region} / {account.role_name}")
        title.setStyleSheet("font-size:11px;font-weight:800;color:#1e293b;")
        title.setToolTip(
            f"二级区名：{account.secondary_region}\n角色：{account.role_name}"
            + (f"\n角色 ID：{account.role_id}" if account.role_id else "")
        )
        remove_button = QPushButton("移除")
        remove_button.setFixedSize(34, 20)
        remove_button.setStyleSheet(
            "QPushButton{border:0;color:#64748b;font-size:11px;}QPushButton:hover{color:#b91c1c;background:#fee2e2;}"
        )
        remove_button.clicked.connect(lambda _checked=False, target=hwnd: self._request_remove_window(target))
        header.addWidget(title, 1)
        header.addWidget(remove_button)
        panel_layout.addLayout(header)

        preview = QLabel("等待游戏画面…")
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setFixedSize(164, 68)
        preview.setStyleSheet("background:#111827;color:#cbd5e1;border-radius:6px;")
        panel_layout.addWidget(preview)

        status = QLabel("正在等待背包画面…")
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status.setFixedHeight(23)
        status.setStyleSheet("background:#f1f5f9;color:#475569;border-radius:6px;font-weight:800;")
        detail = QLabel("空格 -｜已拥有 -｜置信度 -")
        occlusion = QLabel("遮挡 检测中｜最小化 否")
        for label in (detail, occlusion):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedHeight(19)
            label.setStyleSheet("font-size:11px;color:#475569;")
        panel_layout.addWidget(status)
        panel_layout.addWidget(detail)
        panel_layout.addWidget(occlusion)
        return panel, title, preview, status, detail, occlusion

    def _update_session_identity(self, session: MonitorSession, title: str) -> None:
        session.title = title
        session.account = parse_account_info(title)
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
            self.output_grid.addWidget(session.output_panel, index // 4, index % 4)
        available = any(hwnd not in self.sessions for hwnd in self._window_titles)
        self.add_window_button.setEnabled(available and len(self.sessions) < MAX_MONITORED_WINDOWS)

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
        session = self.sessions.get(hwnd)
        if session is None or session.minimized or self.monitoring_paused:
            return
        session.last_signal = signal
        self._apply_signal(session, signal)
        self._update_capacity_alert(session, signal)

    def _apply_signal(self, session: MonitorSession, signal: dict[str, Any]) -> None:
        status = str(signal.get("status", "invalid"))
        text, foreground, background = self.STATUS_PRESENTATION.get(status, ("未知状态", "#475569", "#f1f5f9"))
        session.output_status.setText(text)
        session.output_status.setStyleSheet(
            f"background:{background};color:{foreground};border-radius:6px;font-weight:800;"
        )
        session.output_detail.setText(
            f"空格 {signal.get('empty_count', 0)}｜已拥有 {signal.get('occupied_count', 0)}｜"
            f"置信度 {float(signal.get('confidence', 0.0)):.0%}"
        )

    def _update_capacity_alert(
        self, session: MonitorSession, signal: dict[str, Any], observed_at: float | None = None
    ) -> None:
        if str(signal.get("status", "")) not in {"full", "almost_full", "not_full"}:
            session.capacity_alert_active = False
            session.capacity_candidate_since = None
            return
        empty_count = int(signal.get("empty_count", 0))
        threshold = self.slave_settings.inventory_empty_threshold
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
        title = "背包已满" if empty_count == 0 else "背包容量警告"
        message = (
            "检测到背包 20 个格子全部占用，请及时处理。"
            if empty_count == 0
            else f"检测到背包只剩 {empty_count} 个空格，已达到设定的 {threshold} 格警告线。"
        )
        self.log.appendPlainText(f"{title}：{account_label}，剩余 {empty_count} 格")
        mode = self.slave_settings.inventory_alert
        if mode in {ALERT_LOCAL, ALERT_LOCAL_HOST}:
            self._show_alert(QMessageBox.Icon.Warning, title, message, account_label)
        if mode in {ALERT_HOST, ALERT_LOCAL_HOST}:
            self._send_host_alert(
                session,
                "inventory_full" if empty_count == 0 else "inventory_capacity",
                empty_slots=empty_count,
                confidence=float(signal.get("confidence", 0.0)),
                details={"threshold": threshold, "message": message},
            )

    @Slot(int, object)
    def _on_event(self, hwnd: int, event: Event) -> None:
        session = self.sessions.get(hwnd)
        if session is None or session.minimized or self.monitoring_paused:
            return
        account_label = f"{session.account.secondary_region} / {session.account.role_name}"
        self.log.appendPlainText(f"{event.display_time}  {account_label}  {event.display_name}")
        messages = {
            "inventory_not_open": (
                self.slave_settings.inventory_not_open_alert,
                QMessageBox.Icon.Warning,
                "请打开背包",
                "当前没有打开背包，请让操作员工打开背包。",
            ),
            "inventory_detection_blocked": (
                self.slave_settings.inventory_blocked_alert,
                QMessageBox.Icon.Warning,
                "背包检测被遮挡",
                "请关闭游戏内遮挡窗口。",
            ),
        }
        details = messages.get(event.type)
        if details is None:
            return
        mode = details[0]
        if mode in {ALERT_LOCAL, ALERT_LOCAL_HOST}:
            self._show_alert(*details[1:], account_label)
        if mode in {ALERT_HOST, ALERT_LOCAL_HOST}:
            self._send_host_alert(
                session,
                event.type,
                confidence=float(event.confidence),
                details={"message": details[3], **event.payload},
            )

    def _show_alert(self, icon: QMessageBox.Icon, title: str, message: str, details: str = "") -> None:
        # There is deliberately only one desktop alert. New warnings replace
        # its content so repeated detections never leave hundreds to dismiss.
        with_sound = self.slave_settings.local_alert_style == LOCAL_ALERT_POPUP_SOUND
        alert = next(iter(self.alerts), None)
        if alert is None:
            alert = QMessageBox(None)
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
        if self.monitoring_paused:
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
                session.minimized_since = None
                session.minimized_alert_active = False
                self.log.appendPlainText(f"游戏窗口已恢复，检测继续：{account_label}")
                session.output_status.setText("窗口已恢复，正在重新检测…")
                session.output_detail.setText("空格 -｜已拥有 -｜置信度 -")
                session.output_occlusion.setText("遮挡 检测中｜最小化 否")
                session.output_occlusion.setStyleSheet("color:#0369a1;font-size:12px;font-weight:700;")
                session.output_preview.clear()
                session.output_preview.setText("等待新的游戏画面…")
            if (
                minimized
                and not session.minimized_alert_active
                and session.minimized_since is not None
                and now - session.minimized_since >= self.config.inventory_monitor.alert_confirm_seconds
            ):
                session.minimized_alert_active = True
                mode = self.slave_settings.minimize_alert
                if mode in {ALERT_LOCAL, ALERT_LOCAL_HOST}:
                    self._show_alert(
                        QMessageBox.Icon.Warning,
                        "背包检测已暂停",
                        "梦幻西游窗口已连续最小化 5 秒，请恢复窗口后继续检测。",
                        account_label,
                    )
                if mode in {ALERT_HOST, ALERT_LOCAL_HOST}:
                    self._send_host_alert(
                        session,
                        "window_minimized",
                        minimized=True,
                        details={"confirm_seconds": self.config.inventory_monitor.alert_confirm_seconds},
                    )

    def _show_minimized_status(self, session: MonitorSession) -> None:
        session.output_status.setText("检测已暂停")
        session.output_status.setStyleSheet("background:#fef3c7;color:#b45309;border-radius:6px;font-weight:800;")
        session.output_detail.setText("空格 -｜已拥有 -｜置信度 -")
        session.output_preview.clear()
        session.output_preview.setText("窗口已最小化，检测暂停")
        session.output_occlusion.setText("遮挡 --｜最小化 是")
        session.output_occlusion.setStyleSheet("color:#b45309;font-size:12px;font-weight:700;")

    @Slot()
    def _poll_window_occlusion(self) -> None:
        if self.monitoring_paused:
            return
        for session in tuple(self.sessions.values()):
            if session.removing or not is_window_available(session.hwnd):
                continue
            try:
                status = measure_window_occlusion(session.hwnd)
            except Exception as exc:  # noqa: BLE001 - native window-query failures belong in the UI
                session.output_occlusion.setText(f"遮挡 检测失败｜最小化 {'是' if session.minimized else '否'}")
                session.output_occlusion.setToolTip(str(exc))
                continue
            self._apply_occlusion(session, status)

    def _apply_occlusion(self, session: MonitorSession, status: WindowOcclusionStatus) -> None:
        session.last_occlusion_percent = 0.0 if status.minimized else round(status.ratio * 100, 1)
        if status.minimized:
            text = "遮挡 --｜最小化 是"
            color = "#b45309"
            session.occlusion_alert_active = False
            session.occlusion_candidate_since = None
        else:
            percentage = status.ratio * 100
            text = f"遮挡 {percentage:.1f}%｜最小化 否"
            color = "#15803d" if status.occluded_area == 0 else "#b91c1c"
        session.output_occlusion.setText(text)
        session.output_occlusion.setToolTip("根据其他桌面窗口覆盖游戏窗口的面积计算")
        session.output_occlusion.setStyleSheet(f"color:{color};font-size:12px;font-weight:700;")
        if not status.minimized:
            self._update_occlusion_alert(session, status.ratio * 100)

    def _update_occlusion_alert(
        self, session: MonitorSession, percentage: float, observed_at: float | None = None
    ) -> None:
        threshold = self.slave_settings.occlusion_threshold_percent
        reached = percentage >= threshold
        if not reached:
            session.occlusion_alert_active = False
            session.occlusion_candidate_since = None
            return
        now = monotonic() if observed_at is None else observed_at
        if session.occlusion_candidate_since is None:
            session.occlusion_candidate_since = now
            return
        if now - session.occlusion_candidate_since < self.config.inventory_monitor.alert_confirm_seconds:
            return
        if session.occlusion_alert_active:
            return
        session.occlusion_alert_active = True
        account_label = f"{session.account.secondary_region} / {session.account.role_name}"
        self.log.appendPlainText(f"窗口遮挡警告：{account_label}，遮挡 {percentage:.1f}%")
        mode = self.slave_settings.occlusion_alert
        if mode in {ALERT_LOCAL, ALERT_LOCAL_HOST}:
            self._show_alert(
                QMessageBox.Icon.Warning,
                "游戏窗口被遮挡",
                f"游戏窗口已被遮挡 {percentage:.1f}%，达到设定的 {threshold}% 警告线。",
                account_label,
            )
        if mode in {ALERT_HOST, ALERT_LOCAL_HOST}:
            self._send_host_alert(
                session,
                "window_occluded",
                occlusion_percent=round(percentage, 1),
                details={"threshold": threshold},
            )

    @Slot(int, object)
    def _on_stats(self, hwnd: int, stats: dict[str, Any]) -> None:
        session = self.sessions.get(hwnd)
        if session is not None:
            session.last_stats = stats

    @Slot(int, str)
    def _on_failed(self, hwnd: int, message: str) -> None:
        session = self.sessions.get(hwnd)
        if session is None:
            return
        summary = message.strip().splitlines()[0] if message.strip() else "未知错误"
        session.output_status.setText("监控失败")
        session.output_status.setStyleSheet("background:#fee2e2;color:#b91c1c;border-radius:6px;font-weight:800;")
        account_label = f"{session.account.secondary_region} / {session.account.role_name}"
        self.log.appendPlainText(f"监控失败：{account_label}：{summary}")
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
        windows: list[dict[str, Any]] = []
        now = datetime.now(UTC).isoformat(timespec="milliseconds")
        for session in tuple(self.sessions.values()):
            signal = session.last_signal or {}
            if session.removing:
                status = "removing"
                status_text = "正在移除"
            elif self.monitoring_paused:
                status = "paused"
                status_text = "监控已暂停"
            elif session.minimized:
                status = "minimized"
                status_text = "窗口已最小化"
            else:
                status = str(signal.get("status") or "waiting")
                status_text = self.STATUS_PRESENTATION.get(status, ("等待检测", "", ""))[0]
            if self.monitoring_paused or session.minimized or session.removing:
                signal = {}  # Do not present a stale inventory result as current.
            windows.append(
                {
                    "window_id": f"hwnd:{session.hwnd}",
                    "title": session.title,
                    "server_name": session.account.secondary_region,
                    "role_name": session.account.role_name,
                    "character_id": session.account.role_id or None,
                    "status": status,
                    "status_text": status_text,
                    "empty_slots": signal.get("empty_count"),
                    "occupied_slots": signal.get("occupied_count"),
                    "confidence": signal.get("confidence"),
                    "occlusion_percent": session.last_occlusion_percent,
                    "minimized": session.minimized,
                    "monitoring": not session.removing and not self.monitoring_paused,
                    "updated_at": now,
                }
            )
        self.relay_client.send_status(windows)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.scan_timer.stop()
        self.window_state_timer.stop()
        self.occlusion_timer.stop()
        self.status_timer.stop()
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
    app = QApplication(argv or sys.argv)
    window = InventoryMonitorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
