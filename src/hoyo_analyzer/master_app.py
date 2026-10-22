"""Desktop master that receives and acknowledges slave alarms via HTTPS."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, ClassVar

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .alarm_sound import play_alarm_loop, stop_alarm_loop
from .app_identity import MASTER_APP_ID, application_icon, apply_application_icon, configure_windows_app_id
from .auto_update import install_auto_update, mark_update_healthy
from .desktop_theme import MUTED, DarkTitleBar, apply_desktop_theme, banner_style, pill_style
from .master_settings import MasterSettings, load_master_settings, save_master_settings
from .panel_types import panel_event_name
from .relay_client import MasterRelayClient

EVENT_NAMES: dict[str, str] = {
    "inventory_full": "背包已满",
    "inventory_capacity": "背包容量不足",
    "inventory_not_open": "背包未打开",
    "inventory_detection_blocked": "背包被遮挡",
    "window_minimized": "游戏窗口最小化",
    "window_occluded": "游戏窗口被遮挡",
}
BEIJING_TZ = timezone(timedelta(hours=8))


class MasterSettingsDialog(QDialog):
    def __init__(self, settings: MasterSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("主机设置")
        self.setFixedWidth(560)
        apply_desktop_theme(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        title = QLabel("主机控制中心")
        title.setStyleSheet("font-size:20px;font-weight:800;color:#F3F7FD;")
        subtitle = QLabel("配置数据刷新频率和主机提醒")
        subtitle.setStyleSheet(f"color:{MUTED};font-size:11px;")
        root.addWidget(title)
        root.addWidget(subtitle)
        connection_group = QGroupBox("主机偏好")
        form = QFormLayout(connection_group)
        self.poll_spin = QSpinBox()
        self.poll_spin.setRange(2, 60)
        self.poll_spin.setSuffix(" 秒")
        self.poll_spin.setValue(settings.poll_seconds)
        self.popup_alert_checkbox = QCheckBox("收到新告警时弹窗并播放声音")
        self.popup_alert_checkbox.setChecked(settings.popup_alert_enabled)
        form.addRow("拉取间隔：", self.poll_spin)
        form.addRow("弹窗提醒：", self.popup_alert_checkbox)
        root.addWidget(connection_group)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("role", "primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def settings(self) -> MasterSettings:
        return MasterSettings(
            poll_seconds=self.poll_spin.value(),
            popup_alert_enabled=self.popup_alert_checkbox.isChecked(),
            username=self._settings.username,
            access_token=self._settings.access_token,
            master_token=self._settings.master_token,
        )


class AccountDialog(QDialog):
    """Login/register dialog; the cloud address is intentionally not editable."""

    def __init__(self, username: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("主机账号")
        self.setFixedWidth(420)
        apply_desktop_theme(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        title = QLabel("登录主机账号")
        title.setStyleSheet("font-size:20px;font-weight:800;color:#F3F7FD;")
        root.addWidget(title)
        note = QLabel("登录后只显示当前账号绑定的从机和告警记录。")
        note.setStyleSheet(f"color:{MUTED};")
        root.addWidget(note)
        form = QFormLayout()
        self.username_edit = QLineEdit(username)
        self.username_edit.setPlaceholderText("3～32 位字母、数字、点、横线或下划线")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("至少 8 位")
        form.addRow("账号：", self.username_edit)
        form.addRow("密码：", self.password_edit)
        root.addLayout(form)
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("color:#FFB7C5;")
        root.addWidget(self.message_label)
        buttons = QHBoxLayout()
        self.register_button = QPushButton("注册新账号")
        self.login_button = QPushButton("登录")
        self.login_button.setProperty("role", "primary")
        buttons.addWidget(self.register_button)
        buttons.addStretch(1)
        buttons.addWidget(self.login_button)
        root.addLayout(buttons)

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.login_button.setEnabled(not busy)
        self.register_button.setEnabled(not busy)
        self.message_label.setText(message)

    def credentials(self) -> tuple[str, str]:
        return self.username_edit.text().strip(), self.password_edit.text()


class MasterWindow(QMainWindow):
    """Receive pending alarms from all configured slave computers."""

    COLUMNS: ClassVar[tuple[str, ...]] = ("编号", "从机", "区服 / 角色", "告警", "发生时间", "状态", "操作")
    STATUS_COLUMNS: ClassVar[tuple[str, ...]] = (
        "从机编号", "窗口 / 角色", "现金", "当前状态", "空格", "置信度", "遮挡", "最小化", "在线"
    )

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("梦幻西游背包监控 - 主机")
        self.setWindowIcon(application_icon())
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setFixedSize(800, 632)
        self.settings = load_master_settings()
        self.events: dict[int, dict[str, Any]] = {}
        self.devices: list[dict[str, Any]] = []
        self.window_status: list[dict[str, Any]] = []
        self._monitor_logs_before_id = 0
        self._monitor_logs_next_id = 0
        self.alert: QMessageBox | None = None
        self.account_dialog: AccountDialog | None = None
        self._build_ui()
        self.client = MasterRelayClient(self)
        self.client.connection_changed.connect(self._on_connection_changed)
        self.client.events_received.connect(self._on_events_received)
        self.client.devices_received.connect(self._on_devices_received)
        self.client.status_received.connect(self._on_status_received)
        self.client.monitor_logs_received.connect(self._on_monitor_logs_received)
        self.client.acknowledge_result.connect(self._on_acknowledge_result)
        self.client.authentication_result.connect(self._on_authentication_result)
        self.client.pairing_code_received.connect(self._on_pairing_code_received)
        self.client.device_action_result.connect(self._on_device_action_result)
        self._configure_client()
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_master)
        self.poll_timer.start(self.settings.poll_seconds * 1000)
        if self.settings.access_token or self.settings.master_token:
            QTimer.singleShot(250, self.client.poll)
        else:
            QTimer.singleShot(250, self._open_account_dialog)
        mark_update_healthy()
        self.update_controller = install_auto_update("master", self)

    def _build_ui(self) -> None:
        apply_desktop_theme(self)
        root = QWidget()
        root.setObjectName("windowShell")
        self.setCentralWidget(root)
        shell_layout = QVBoxLayout(root)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.title_bar = DarkTitleBar(self, f"梦幻西游背包监控 · 主机 · {__version__}")
        shell_layout.addWidget(self.title_bar)

        content = QWidget()
        content.setObjectName("appRoot")
        shell_layout.addWidget(content, 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(7)
        header = QHBoxLayout()
        header_text = QVBoxLayout()
        title = QLabel("主机告警中心")
        title.setStyleSheet("font-size:22px;font-weight:800;color:#F3F7FD;")
        self.subtitle_label = QLabel(f"多从机状态汇总 · 实时告警处置 · 客户端 {__version__}")
        self.subtitle_label.setStyleSheet(f"color:{MUTED};font-size:11px;")
        header_text.addWidget(title)
        header_text.addWidget(self.subtitle_label)
        header.addLayout(header_text)
        header.addStretch(1)
        self.device_label = QLabel("从机：0 台")
        self.device_label.setStyleSheet(pill_style("#C9D6E8", "#17233A", "#30415D"))
        header.addWidget(self.device_label)
        self.status_label = QLabel("云端：未连接")
        self.status_label.setStyleSheet(pill_style("#FFD8DF", "#351824", "#6E2941"))
        header.addWidget(self.status_label)
        connect_button = QPushButton("连接")
        connect_button.setProperty("role", "primary")
        connect_button.clicked.connect(self._connect_now)
        header.addWidget(connect_button)
        self.account_button = QPushButton(self.settings.username or "账号登录")
        self.account_button.clicked.connect(self._open_account_dialog)
        header.addWidget(self.account_button)
        settings_button = QPushButton("设置")
        settings_button.clicked.connect(self._open_settings)
        header.addWidget(settings_button)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        monitor_page = QWidget()
        monitor_layout = QVBoxLayout(monitor_page)
        monitor_layout.setContentsMargins(0, 6, 0, 0)
        monitor_layout.setSpacing(7)
        self.tabs.addTab(monitor_page, "监控")

        monitor_header = QHBoxLayout()
        self.window_summary = QLabel("从机状态 · 等待同步")
        self.window_summary.setStyleSheet(banner_style("#B9D8FF", "#122C4D", "#245284"))
        monitor_header.addWidget(self.window_summary, 1)
        self.monitor_updated_label = QLabel("更新时间：--")
        self.monitor_updated_label.setStyleSheet(f"color:{MUTED};font-size:11px;")
        monitor_header.addWidget(self.monitor_updated_label)
        monitor_layout.addLayout(monitor_header)
        self.status_table = QTableWidget(0, len(self.STATUS_COLUMNS))
        self.status_table.setHorizontalHeaderLabels(self.STATUS_COLUMNS)
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.setAlternatingRowColors(True)
        self.status_table.setShowGrid(False)
        self.status_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.status_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.status_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.status_table.verticalHeader().setDefaultSectionSize(46)
        for column, width in enumerate((70, 140, 132, 90, 38, 50, 52, 50, 46)):
            self.status_table.setColumnWidth(column, width)
        self.status_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        monitor_layout.addWidget(self.status_table, 1)

        alert_page = QWidget()
        alert_layout = QVBoxLayout(alert_page)
        alert_layout.setContentsMargins(0, 6, 0, 0)
        alert_layout.setSpacing(7)
        self.tabs.addTab(alert_page, "告警")

        self.summary = QLabel("告警记录 · 暂无未处理告警")
        self.summary.setStyleSheet(banner_style("#C7FBE8", "#0A3B31", "#176A58"))
        alert_layout.addWidget(self.summary)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(29)
        widths = (48, 75, 158, 130, 116, 65, 65)
        for column, width in enumerate(widths):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        alert_layout.addWidget(self.table, 1)
        note = QLabel("确认后记录保留为“已处理”；从机 90 秒无上报显示离线。网络中断不影响从机本地检测。")
        note.setStyleSheet(f"color:{MUTED};font-size:11px;")
        alert_layout.addWidget(note)

        device_page = QWidget()
        device_layout = QVBoxLayout(device_page)
        device_layout.setContentsMargins(0, 8, 0, 0)
        device_layout.setSpacing(8)
        device_toolbar = QHBoxLayout()
        self.generate_pairing_button = QPushButton("+ 添加从机")
        self.generate_pairing_button.setProperty("role", "primary")
        self.generate_pairing_button.clicked.connect(lambda: self.client.create_pairing_code())
        device_toolbar.addWidget(self.generate_pairing_button)
        self.pairing_code_label = QLabel("点击“添加从机”生成一次性配对码")
        self.pairing_code_label.setStyleSheet(f"color:{MUTED};font-weight:700;")
        device_toolbar.addWidget(self.pairing_code_label)
        device_toolbar.addStretch(1)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(lambda: self.client.poll())
        device_toolbar.addWidget(refresh_button)
        device_layout.addLayout(device_toolbar)
        self.device_table = QTableWidget(0, 8)
        self.device_table.setHorizontalHeaderLabels(
            ("从机名称", "设备编号", "在线", "最后连接", "版本", "备注", "状态", "操作")
        )
        self.device_table.verticalHeader().setVisible(False)
        self.device_table.setAlternatingRowColors(True)
        self.device_table.setShowGrid(False)
        self.device_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.device_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for column, width in enumerate((95, 90, 45, 110, 72, 80, 98, 190)):
            self.device_table.setColumnWidth(column, width)
        self.device_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        device_layout.addWidget(self.device_table, 1)
        device_note = QLabel(
            "配对码 5 分钟内有效且只能使用一次；重新配对会立即让旧凭据失效。"
        )
        device_note.setStyleSheet(f"color:{MUTED};font-size:11px;")
        device_layout.addWidget(device_note)
        self.tabs.addTab(device_page, "从机管理")

        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        toolbar = QHBoxLayout()
        self.monitor_log_summary = QLabel("监控日志 · 等待同步")
        toolbar.addWidget(self.monitor_log_summary, 1)
        latest_button = QPushButton("最新记录")
        latest_button.clicked.connect(lambda: self._load_monitor_logs(0))
        toolbar.addWidget(latest_button)
        self.older_logs_button = QPushButton("更早记录")
        self.older_logs_button.setEnabled(False)
        self.older_logs_button.clicked.connect(lambda: self._load_monitor_logs(self._monitor_logs_next_id))
        toolbar.addWidget(self.older_logs_button)
        log_layout.addLayout(toolbar)
        self.monitor_log_table = QTableWidget(0, 7)
        self.monitor_log_table.setHorizontalHeaderLabels(("发生时间", "从机", "区服 / 角色", "类型", "遮挡", "最小化", "原因"))
        self.monitor_log_table.verticalHeader().setVisible(False)
        self.monitor_log_table.setAlternatingRowColors(True)
        self.monitor_log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.monitor_log_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for column, width in enumerate((185, 90, 140, 145, 55, 50, 80)):
            self.monitor_log_table.setColumnWidth(column, width)
        self.monitor_log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        log_layout.addWidget(self.monitor_log_table)
        log_layout.addWidget(QLabel("每次上报的遮挡、最小化状态均保留；开始和暂停按操作记录。"))
        self.tabs.addTab(log_page, "监控日志")

    def _poll_master(self) -> None:
        self.client.poll()

    def _load_monitor_logs(self, before_id: int = 0) -> None:
        self._monitor_logs_before_id = before_id
        self.client.fetch_monitor_logs(before_id)

    @Slot(object)
    def _on_monitor_logs_received(self, data: dict[str, Any]) -> None:
        logs = data.get("logs", [])
        self.monitor_log_table.setRowCount(len(logs))
        labels = {"window_occluded": "遮挡", "window_minimized": "最小化",
                  "monitoring_started": "开始监控", "monitoring_paused": "暂停监控"}
        reasons = {"startup": "启动程序", "resume": "点击开始", "pause": "点击暂停", "close": "关闭程序", "recognition": "面板识别"}
        for row, item in enumerate(logs):
            percentage = item.get("occlusion_percent")
            percent_text = "—" if percentage is None else (
                "<0.1%" if 0 < float(percentage) < 0.1 else f"{float(percentage):.1f}%"
            )
            try:
                occurred = datetime.fromisoformat(str(item.get("occurred_at")))
                log_time = occurred.astimezone(BEIJING_TZ).strftime("%Y年%m月%d日%H:%M:%S")
            except (ValueError, TypeError):
                log_time = "—"
            event_label = labels.get(item["event_type"], item["event_type"])
            if item["event_type"] in {"panel_opened", "panel_closed", "game_default"}:
                event_label = item.get("message") or panel_event_name(item["event_type"], item.get("panel_type"))
            values = (
                log_time,
                str(item.get("display_name") or item.get("hostname") or item["device_id"]),
                " / ".join(filter(None, (item.get("server_name"), item.get("role_name")))) or "整台从机",
                event_label, percent_text,
                "—" if item.get("minimized") is None else "是" if item["minimized"] else "否",
                reasons.get(item.get("reason"), "状态上报"),
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(f"{value}\n从机：{item.get('device_id') or ''}")
                self.monitor_log_table.setItem(row, column, cell)
        self._monitor_logs_next_id = int(logs[-1]["id"]) if logs else 0
        self.older_logs_button.setEnabled(bool(data.get("has_more")))
        self.monitor_log_summary.setText(f"监控日志 · {'最新' if not self._monitor_logs_before_id else '历史'} {len(logs)} 条")

    def _configure_client(self) -> None:
        try:
            self.client.configure(self.settings.access_token or self.settings.master_token)
        except ValueError as exc:
            self.status_label.setToolTip(str(exc))

    @Slot()
    def _open_account_dialog(self) -> None:
        if self.account_dialog is not None:
            self.account_dialog.raise_()
            self.account_dialog.activateWindow()
            return
        dialog = AccountDialog(self.settings.username, self)
        self.account_dialog = dialog
        dialog.login_button.clicked.connect(lambda: self._submit_account(False))
        dialog.register_button.clicked.connect(lambda: self._submit_account(True))
        dialog.finished.connect(lambda _result: setattr(self, "account_dialog", None))
        dialog.show()

    def _submit_account(self, register: bool) -> None:
        if self.account_dialog is None:
            return
        username, password = self.account_dialog.credentials()
        if len(username) < 3 or len(password) < 8:
            self.account_dialog.set_busy(False, "账号至少 3 位，密码至少 8 位。")
            return
        self.account_dialog.set_busy(True, "正在注册…" if register else "正在登录…")
        self.client.authenticate(username, password, register=register)

    @Slot(bool, str, object)
    def _on_authentication_result(self, success: bool, message: str, data: dict[str, Any]) -> None:
        if self.account_dialog is None:
            return
        if not success:
            self.account_dialog.set_busy(False, message or "登录失败")
            return
        account = data.get("account") or {}
        self.settings.username = str(account.get("username") or self.account_dialog.credentials()[0])
        self.settings.access_token = str(data.get("access_token") or "")
        self.settings.master_token = ""
        try:
            save_master_settings(self.settings)
        except OSError as exc:
            self.account_dialog.set_busy(False, f"登录成功，但无法保存登录状态：{exc}")
            return
        self.account_button.setText(self.settings.username)
        self._monitor_logs_before_id = 0
        self.monitor_log_table.setRowCount(0)
        self.older_logs_button.setEnabled(False)
        self._configure_client()
        self.account_dialog.accept()
        self.client.poll()

    @Slot()
    def _connect_now(self) -> None:
        self.status_label.setText("云端：连接中…")
        self.status_label.setStyleSheet(pill_style("#FFE9A8", "#3C2B10", "#6E5018"))
        self.client.poll()

    @Slot()
    def _open_settings(self) -> None:
        dialog = MasterSettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.settings = dialog.settings()
        try:
            save_master_settings(self.settings)
            self._configure_client()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "设置保存失败", str(exc))
            return
        self.poll_timer.setInterval(self.settings.poll_seconds * 1000)
        if not self.settings.popup_alert_enabled:
            self._close_alarm()
        self.client.poll()

    @Slot(bool, str)
    def _on_connection_changed(self, online: bool, message: str) -> None:
        self.status_label.setText("云端：已连接" if online else "云端：未连接")
        self.status_label.setStyleSheet(
            pill_style("#C7FBE8", "#0A3B31", "#176A58")
            if online
            else pill_style("#FFD8DF", "#351824", "#6E2941")
        )
        self.status_label.setToolTip(message)

    @Slot(object)
    def _on_events_received(self, events: list[dict[str, Any]]) -> None:
        previous_ids = set(self.events)
        incoming = {int(item["server_event_id"]): dict(item) for item in events}
        # A poll started before an acknowledgement may return its old state.
        for event_id, item in incoming.items():
            acknowledged = self.events.get(event_id, {}).get("acked_at")
            if acknowledged:
                item["acked_at"] = acknowledged
        self.events = incoming
        self._render_events()
        new_ids = {event_id for event_id in set(self.events) - previous_ids if not self.events[event_id].get("acked_at")}
        if new_ids and self.settings.popup_alert_enabled:
            newest = self.events[max(new_ids)]
            self._show_alarm(newest)

    @Slot(object)
    def _on_devices_received(self, devices: list[dict[str, Any]]) -> None:
        self.devices = devices
        online = 0
        for device in devices:
            try:
                if self._is_device_online(device, datetime.now(UTC)):
                    online += 1
            except (KeyError, TypeError, ValueError):
                pass
        self.device_label.setText(f"从机：{online}/{len(devices)} 台在线")
        self.device_label.setStyleSheet(
            pill_style("#C7FBE8", "#0A3B31", "#176A58")
            if online
            else pill_style("#C9D6E8", "#17233A", "#30415D")
        )
        self._render_devices()

    def _render_devices(self) -> None:
        now = datetime.now(UTC)
        self.device_table.clearContents()
        self.device_table.setRowCount(len(self.devices))
        for row, device in enumerate(self.devices):
            device_id = str(device.get("device_id") or "-")
            online = self._is_device_online(device, now) and not bool(device.get("disabled"))
            reported_version = str(device.get("app_version") or "-")
            version_mismatch = reported_version != __version__
            states: list[str] = []
            if device.get("disabled"):
                states.append("已禁用")
            if version_mismatch:
                states.append("版本不匹配")
            if not states:
                states.append("正常")
            values = (
                str(device.get("display_name") or device.get("hostname") or device_id),
                device_id,
                "在线" if online else "离线",
                self._format_time(device.get("last_seen_at")),
                reported_version,
                str(device.get("note") or "-"),
                " · ".join(states),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    f"主机版本：{__version__}\n从机版本：{reported_version}"
                    if column in {4, 6} and version_mismatch
                    else value
                )
                if version_mismatch and column in {4, 6}:
                    item.setForeground(QColor("#FBBF24"))
                self.device_table.setItem(row, column, item)
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(2, 1, 2, 1)
            action_layout.setSpacing(3)
            edit_button = QPushButton("编辑")
            edit_button.clicked.connect(
                lambda _checked=False, current=dict(device): self._edit_device(current)
            )
            toggle_button = QPushButton("启用" if device.get("disabled") else "禁用")
            toggle_button.clicked.connect(
                lambda _checked=False, current=dict(device): self._toggle_device(current)
            )
            reissue_button = QPushButton("重配")
            reissue_button.clicked.connect(
                lambda _checked=False, target=device_id: self._reissue_device(target)
            )
            remove_button = QPushButton("移除")
            remove_button.clicked.connect(
                lambda _checked=False, target=device_id: self._remove_device(target)
            )
            for button in (edit_button, toggle_button, reissue_button, remove_button):
                action_layout.addWidget(button)
            self.device_table.setCellWidget(row, 7, actions)

    def _edit_device(self, device: dict[str, Any]) -> None:
        device_id = str(device.get("device_id") or "")
        name, accepted = QInputDialog.getText(
            self, "编辑从机", "从机名称：", text=str(device.get("display_name") or "")
        )
        if not accepted or not name.strip():
            return
        note, accepted = QInputDialog.getText(
            self, "编辑从机", "备注：", text=str(device.get("note") or "")
        )
        if not accepted:
            return
        self.client.update_device(device_id, {"display_name": name.strip(), "note": note.strip()})

    def _toggle_device(self, device: dict[str, Any]) -> None:
        self.client.update_device(
            str(device.get("device_id") or ""), {"disabled": not bool(device.get("disabled"))}
        )

    def _reissue_device(self, device_id: str) -> None:
        answer = QMessageBox.question(
            self,
            "重新配对",
            f"重新配对会立即使 {device_id} 的旧凭据失效。是否继续？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.client.reissue_device(device_id)

    def _remove_device(self, device_id: str) -> None:
        answer = QMessageBox.question(
            self,
            "移除从机",
            f"确定移除 {device_id} 吗？该从机将无法继续上报，历史告警仍会保留。",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.client.remove_device(device_id)

    @Slot(bool, str, object)
    def _on_pairing_code_received(self, success: bool, message: str, data: dict[str, Any]) -> None:
        if not success:
            QMessageBox.warning(self, "配对码生成失败", message or "无法连接云端服务")
            return
        code = str(data.get("pairing_code") or "")
        target = str(data.get("target_device_id") or "")
        prefix = f"{target} 重新配对码" if target else "新从机配对码"
        self.pairing_code_label.setText(f"{prefix}：{code}（5 分钟有效）")
        self.tabs.setCurrentIndex(2)

    @Slot(bool, str)
    def _on_device_action_result(self, success: bool, message: str) -> None:
        if not success:
            QMessageBox.warning(self, "操作失败", message or "无法连接云端服务")
            return
        self.client.poll()

    @Slot(object)
    def _on_status_received(self, devices: list[dict[str, Any]]) -> None:
        self.client.fetch_monitor_logs(self._monitor_logs_before_id)
        rows: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for device in devices:
            device_id = str(device.get("device_id") or "-")
            online = self._is_device_online(device, now)
            windows = device.get("windows") or []
            if not windows:
                rows.append({"device_id": device_id, "online": online, "window": None})
            else:
                rows.extend({"device_id": device_id, "online": online, "window": window} for window in windows)
        self.window_status = rows
        self._render_status()

    @staticmethod
    def _is_device_online(device: dict[str, Any], now: datetime | None = None) -> bool:
        try:
            if bool(device.get("disabled")):
                return False
            current = now or datetime.now(UTC)
            seen = datetime.fromisoformat(str(device["last_seen_at"]))
            return seen >= current - timedelta(seconds=90)
        except (KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _format_money(window: dict[str, Any], key: str, online: bool) -> str:
        value = window.get(key)
        if value is None:
            return "未识别"
        current = online and window.get("money_readable") and window.get("monitoring", True) and not window.get("minimized")
        return f"{int(value):,}" + ("" if current else "（上次确认）")

    def _render_status(self) -> None:
        self.status_table.clearContents()
        self.status_table.setRowCount(len(self.window_status))
        for row, item in enumerate(self.window_status):
            window = item.get("window") or {}
            signal = str(window.get("status") or "unknown")
            status_text = str(window.get("status_text") or signal)
            if not item.get("online"):
                status_text = "从机离线"
            empty = window.get("empty_slots")
            confidence = window.get("confidence")
            values = (
                str(item.get("device_id") or "-"),
                f"{window.get('server_name') or '-'} / {window.get('role_name') or '未监控窗口'}" if window else "暂无游戏窗口",
                status_text,
                "-" if empty is None else str(empty),
                "-" if confidence is None else f"{float(confidence):.0%}",
                "-" if window.get("occlusion_percent") is None else f"{float(window['occlusion_percent']):.1f}%",
                ("是" if window.get("minimized") else "否") if window else "-",
                "在线" if item.get("online") else "离线",
            )
            for column, value in enumerate(values[:2]):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.status_table.setItem(row, column, cell)
            cash_text = self._format_money(window, "cash", bool(item.get("online")))
            confirmed_at = self._format_time(window.get("money_confirmed_at"))
            cash_item = QTableWidgetItem(cash_text)
            cash_item.setToolTip(f"{cash_text}\n确认时间：{confirmed_at}")
            self.status_table.setItem(row, 2, cash_item)
            cash_cell = QWidget()
            cash_layout = QVBoxLayout(cash_cell)
            cash_layout.setContentsMargins(4, 2, 4, 2)
            cash_layout.setSpacing(0)
            amount_label = QLabel(cash_text)
            amount_label.setStyleSheet("font-weight:700;")
            time_label = QLabel(f"确认 {confirmed_at}")
            time_label.setStyleSheet(f"color:{MUTED};font-size:10px;")
            cash_layout.addWidget(amount_label)
            cash_layout.addWidget(time_label)
            cash_cell.setToolTip(cash_item.toolTip())
            self.status_table.setCellWidget(row, 2, cash_cell)
            for column, value in enumerate(values[2:], start=3):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.status_table.setItem(row, column, cell)
        count = sum(1 for item in self.window_status if item.get("window"))
        self.window_summary.setText(
            "从机状态 · 暂无从机上报" if not self.window_status
            else f"从机状态 · 已同步 {count} 个游戏窗口（每 {self.settings.poll_seconds} 秒刷新）"
        )
        self.monitor_updated_label.setText(f"更新时间：{datetime.now().astimezone().strftime('%m-%d %H:%M:%S')}")

    def _render_events(self) -> None:
        items = sorted(self.events.values(), key=lambda item: int(item["server_event_id"]), reverse=True)
        self.table.clearContents()
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            event_id = int(item["server_event_id"])
            values = (
                str(event_id),
                str(item.get("device_id") or "-"),
                f"{item.get('server_name') or '未识别'} / {item.get('role_name') or '未识别'}",
                EVENT_NAMES.get(str(item.get("event_type")), str(item.get("event_type") or "未知")),
                self._format_time(item.get("occurred_at")),
                "已处理" if item.get("acked_at") else "未处理",
            )
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(value)
                table_item.setToolTip(self._event_detail(item) if column == 3 else value)
                self.table.setItem(row, column, table_item)
            if not item.get("acked_at"):
                button = QPushButton("确认")
                button.setProperty("role", "primary")
                button.clicked.connect(lambda _checked=False, target=event_id: self.client.acknowledge(target))
                self.table.setCellWidget(row, 6, button)
        count = sum(not item.get("acked_at") for item in items)
        self.summary.setText(f"告警记录 · 共 {len(items)} 条 · 未处理 {count} 条 · 已处理 {len(items) - count} 条")
        self.summary.setStyleSheet(
            banner_style("#C7FBE8", "#0A3B31", "#176A58")
            if count == 0
            else banner_style("#FFD8DF", "#351824", "#6E2941")
        )
        if count == 0:
            self._close_alarm()

    @staticmethod
    def _format_time(value: Any) -> str:
        try:
            return datetime.fromisoformat(str(value)).astimezone().strftime("%m-%d %H:%M:%S")
        except ValueError:
            return str(value or "-")

    @staticmethod
    def _event_detail(item: dict[str, Any]) -> str:
        if item.get("empty_slots") is not None:
            return f"剩余 {item['empty_slots']} 格"
        if item.get("occlusion_percent") is not None:
            return f"遮挡 {float(item['occlusion_percent']):.1f}%"
        if item.get("minimized"):
            return "窗口已最小化"
        return str((item.get("payload") or {}).get("details", {}).get("message") or "请及时处理")

    def _show_alarm(self, event: dict[str, Any]) -> None:
        if self.alert is None:
            self.alert = QMessageBox(None)
            apply_desktop_theme(self.alert)
            self.alert.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.alert.setWindowFlag(Qt.WindowType.Tool, True)
            self.alert.setWindowModality(Qt.WindowModality.NonModal)
            self.alert.setStandardButtons(QMessageBox.StandardButton.Close)
            self.alert.finished.connect(lambda _result: self._alarm_closed())
        name = EVENT_NAMES.get(str(event.get("event_type")), "从机告警")
        self.alert.setWindowTitle(name)
        self.alert.setIcon(QMessageBox.Icon.Warning)
        self.alert.setText(name)
        self.alert.setInformativeText(
            f"{event.get('device_id')}｜{event.get('server_name') or '未识别'} / {event.get('role_name') or '未识别'}\n"
            "请在主机告警列表中确认处理。"
        )
        button = self.alert.button(QMessageBox.StandardButton.Close)
        if button is not None:
            button.setText("停止声音")
        try:
            play_alarm_loop()
        except OSError:
            QApplication.beep()
        self.alert.show()
        self.alert.raise_()
        self.alert.activateWindow()

    def _alarm_closed(self) -> None:
        stop_alarm_loop()
        self.alert = None

    def _close_alarm(self) -> None:
        if self.alert is not None:
            self.alert.close()
        stop_alarm_loop()

    @Slot(int, bool, str)
    def _on_acknowledge_result(self, event_id: int, success: bool, message: str) -> None:
        if success:
            if event_id in self.events:
                self.events[event_id]["acked_at"] = datetime.now(UTC).isoformat()
            self._render_events()
            self.client.poll()
        else:
            QMessageBox.warning(self, "确认失败", message or "无法连接云端服务")

    def closeEvent(self, event: QCloseEvent) -> None:
        self.poll_timer.stop()
        self._close_alarm()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    configure_windows_app_id(MASTER_APP_ID)
    app = QApplication(argv or sys.argv)
    apply_application_icon(app)
    window = MasterWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
