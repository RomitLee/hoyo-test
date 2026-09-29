"""Desktop master that receives and acknowledges slave alarms via HTTPS."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .alarm_sound import play_alarm_loop, stop_alarm_loop
from .master_settings import MasterSettings, load_master_settings, save_master_settings
from .relay_client import MasterRelayClient

EVENT_NAMES: dict[str, str] = {
    "inventory_full": "背包已满",
    "inventory_capacity": "背包容量不足",
    "inventory_not_open": "背包未打开",
    "inventory_detection_blocked": "背包被遮挡",
    "window_minimized": "游戏窗口最小化",
    "window_occluded": "游戏窗口被遮挡",
}


class MasterSettingsDialog(QDialog):
    def __init__(self, settings: MasterSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("主机设置")
        self.setFixedWidth(520)
        form = QFormLayout(self)
        self.url_edit = QLineEdit(settings.relay_url)
        self.url_edit.setPlaceholderText("https://服务器公网 IP")
        self.token_edit = QLineEdit(settings.master_token)
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("服务器生成的主机密钥")
        self.poll_spin = QSpinBox()
        self.poll_spin.setRange(2, 60)
        self.poll_spin.setSuffix(" 秒")
        self.poll_spin.setValue(settings.poll_seconds)
        form.addRow("云端地址：", self.url_edit)
        form.addRow("主机密钥：", self.token_edit)
        form.addRow("拉取间隔：", self.poll_spin)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def settings(self) -> MasterSettings:
        return MasterSettings(
            relay_url=self.url_edit.text().strip(),
            master_token=self.token_edit.text().strip(),
            poll_seconds=self.poll_spin.value(),
        )


class MasterWindow(QMainWindow):
    """Receive pending alarms from all configured slave computers."""

    COLUMNS: ClassVar[tuple[str, ...]] = ("编号", "从机", "区服 / 角色", "告警", "发生时间", "状态", "操作")
    STATUS_COLUMNS: ClassVar[tuple[str, ...]] = ("从机编号", "窗口 / 角色", "当前状态", "空格", "置信度", "遮挡", "最小化", "最后更新", "在线")

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("梦幻西游背包监控 - 主机")
        self.setFixedSize(800, 600)
        self.settings = load_master_settings()
        self.events: dict[int, dict[str, Any]] = {}
        self.devices: list[dict[str, Any]] = []
        self.window_status: list[dict[str, Any]] = []
        self.alert: QMessageBox | None = None
        self._build_ui()
        self.client = MasterRelayClient(self)
        self.client.connection_changed.connect(self._on_connection_changed)
        self.client.events_received.connect(self._on_events_received)
        self.client.devices_received.connect(self._on_devices_received)
        self.client.status_received.connect(self._on_status_received)
        self.client.acknowledge_result.connect(self._on_acknowledge_result)
        self._configure_client()
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.client.poll)
        self.poll_timer.start(self.settings.poll_seconds * 1000)
        QTimer.singleShot(250, self.client.poll)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        title = QLabel("主机告警中心")
        title.setStyleSheet("font-size:24px;font-weight:800;color:#1e293b;")
        header.addWidget(title)
        header.addStretch(1)
        self.device_label = QLabel("从机：0 台")
        header.addWidget(self.device_label)
        self.status_label = QLabel("云端：未连接")
        self.status_label.setStyleSheet("color:#b91c1c;font-weight:700;")
        header.addWidget(self.status_label)
        connect_button = QPushButton("连接")
        connect_button.clicked.connect(self._connect_now)
        header.addWidget(connect_button)
        settings_button = QPushButton("设置")
        settings_button.clicked.connect(self._open_settings)
        header.addWidget(settings_button)
        layout.addLayout(header)

        self.window_summary = QLabel("从机状态 · 等待同步")
        self.window_summary.setStyleSheet("background:#eff6ff;color:#1d4ed8;padding:8px;border-radius:6px;font-weight:700;")
        layout.addWidget(self.window_summary)
        self.status_table = QTableWidget(0, len(self.STATUS_COLUMNS))
        self.status_table.setHorizontalHeaderLabels(self.STATUS_COLUMNS)
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.setAlternatingRowColors(True)
        self.status_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.status_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for column, width in enumerate((70, 130, 90, 38, 50, 52, 50, 116, 46)):
            self.status_table.setColumnWidth(column, width)
        layout.addWidget(self.status_table, 1)

        self.summary = QLabel("告警记录 · 暂无未处理告警")
        self.summary.setStyleSheet("background:#dcfce7;color:#15803d;padding:8px;border-radius:6px;font-weight:700;")
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        widths = (48, 75, 158, 130, 116, 65, 65)
        for column, width in enumerate(widths):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)
        note = QLabel("确认后记录保留为“已处理”；从机 90 秒无上报显示离线。网络中断不影响从机本地检测。")
        note.setStyleSheet("color:#64748b;")
        layout.addWidget(note)

    def _configure_client(self) -> None:
        try:
            self.client.configure(self.settings.relay_url, self.settings.master_token)
        except ValueError as exc:
            self.status_label.setToolTip(str(exc))

    @Slot()
    def _connect_now(self) -> None:
        self.status_label.setText("云端：连接中…")
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
        self.client.poll()

    @Slot(bool, str)
    def _on_connection_changed(self, online: bool, message: str) -> None:
        self.status_label.setText("云端：已连接" if online else "云端：未连接")
        self.status_label.setStyleSheet(f"color:{'#15803d' if online else '#b91c1c'};font-weight:700;")
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
        if new_ids:
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

    @Slot(object)
    def _on_status_received(self, devices: list[dict[str, Any]]) -> None:
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
            current = now or datetime.now(UTC)
            seen = datetime.fromisoformat(str(device["last_seen_at"]))
            return seen >= current - timedelta(seconds=90)
        except (KeyError, TypeError, ValueError):
            return False

    def _render_status(self) -> None:
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
                self._format_time(window.get("updated_at")) if window else "-",
                "在线" if item.get("online") else "离线",
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.status_table.setItem(row, column, cell)
        count = sum(1 for item in self.window_status if item.get("window"))
        self.window_summary.setText(
            "从机状态 · 暂无从机上报" if not self.window_status
            else f"从机状态 · 已同步 {count} 个游戏窗口（每 {self.settings.poll_seconds} 秒刷新）"
        )

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
                button.clicked.connect(lambda _checked=False, target=event_id: self.client.acknowledge(target))
                self.table.setCellWidget(row, 6, button)
        count = sum(not item.get("acked_at") for item in items)
        self.summary.setText(f"告警记录 · 共 {len(items)} 条 · 未处理 {count} 条 · 已处理 {len(items) - count} 条")
        self.summary.setStyleSheet(
            ("background:#dcfce7;color:#15803d;" if count == 0 else "background:#fee2e2;color:#b91c1c;")
            + "padding:10px;border-radius:6px;font-weight:700;"
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
    app = QApplication(argv or sys.argv)
    window = MasterWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
