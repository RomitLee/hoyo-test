"""Settings dialog for the slave capacity alert and cloud relay."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .desktop_theme import MUTED, PRIMARY, apply_desktop_theme
from .slave_settings import (
    CAPACITY_ALERT_HOST,
    CAPACITY_ALERT_HOST_LOCAL,
    CAPACITY_ALERT_NONE,
    MAX_CAPACITY_THRESHOLD,
    MIN_CAPACITY_THRESHOLD,
    SlaveSettings,
)


class SlaveSettingsDialog(QDialog):
    """Edit the capacity warning delivery mode and HTTPS credentials."""

    def __init__(self, settings: SlaveSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("从机设置")
        self.setFixedWidth(560)
        apply_desktop_theme(self)
        self._build_ui()
        self._load_values(settings)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        title = QLabel("从机控制中心")
        title.setStyleSheet("font-size:20px;font-weight:800;color:#F3F7FD;")
        subtitle = QLabel("设置背包容量提醒与从机配对")
        subtitle.setStyleSheet(f"color:{MUTED};font-size:11px;")
        root.addWidget(title)
        root.addWidget(subtitle)

        warning_group = QGroupBox("告警设置")
        warning_form = QFormLayout(warning_group)
        capacity_row = QHBoxLayout()
        self.capacity_alert_slider = QSlider(Qt.Orientation.Horizontal)
        self.capacity_alert_slider.setRange(CAPACITY_ALERT_NONE, CAPACITY_ALERT_HOST_LOCAL)
        self.capacity_alert_slider.setSingleStep(1)
        self.capacity_alert_slider.setPageStep(1)
        self.capacity_alert_slider.setTickInterval(1)
        self.capacity_alert_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.capacity_alert_slider.setToolTip(
            "1 档不提醒；2 档上报主机；3 档上报主机并在本机弹窗、循环播放声音"
        )
        self.capacity_alert_label = QLabel()
        self.capacity_alert_label.setMinimumWidth(154)
        self.capacity_alert_slider.valueChanged.connect(self._refresh_capacity_label)
        capacity_row.addWidget(self.capacity_alert_slider, 1)
        capacity_row.addWidget(self.capacity_alert_label)
        warning_form.addRow("背包容量：", capacity_row)
        self.capacity_threshold_spin = QSpinBox()
        self.capacity_threshold_spin.setRange(MIN_CAPACITY_THRESHOLD, MAX_CAPACITY_THRESHOLD)
        self.capacity_threshold_spin.setSuffix(" 格")
        self.capacity_threshold_spin.setToolTip("当背包剩余空格小于或等于此数值并持续 5 秒时触发")
        warning_form.addRow("空格阈值：", self.capacity_threshold_spin)
        note = QLabel(
            "达到空格阈值并连续确认 5 秒后：2 档上报告警记录到主机；"
            "3 档同样上报主机，并在本机弹窗、循环播放声音，直到手动确认。"
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{MUTED};padding:4px 0;")
        warning_form.addRow(note)
        root.addWidget(warning_group)

        host_group = QGroupBox("从机配对")
        host_form = QFormLayout(host_group)
        self.binding_status_label = QLabel()
        host_form.addRow("当前状态：", self.binding_status_label)
        self.pairing_code_edit = QLineEdit()
        self.pairing_code_edit.setMaxLength(6)
        self.pairing_code_edit.setPlaceholderText("输入主机生成的 6 位配对码")
        host_form.addRow("配对码：", self.pairing_code_edit)
        note = QLabel(
            "服务器地址已内置。输入一次性配对码并保存后，程序会自动完成绑定；"
            "配对码有效期为 5 分钟且只能使用一次。"
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{MUTED};padding:4px 0;")
        host_form.addRow(note)
        root.addWidget(host_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("role", "primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _load_values(self, settings: SlaveSettings) -> None:
        self._source_settings = settings
        self.capacity_alert_slider.setValue(settings.inventory_capacity_alert_level)
        self.capacity_threshold_spin.setValue(settings.inventory_capacity_threshold)
        self._refresh_capacity_label()
        self.binding_status_label.setText(
            f"已绑定 · {settings.device_id}" if settings.device_id and settings.device_token else "尚未绑定"
        )
        self.pairing_code_edit.setText(settings.pairing_code)

    def _refresh_capacity_label(self) -> None:
        level = self.capacity_alert_slider.value()
        labels = {
            CAPACITY_ALERT_NONE: "1 档 · 不提醒",
            CAPACITY_ALERT_HOST: "2 档 · 主机推送",
            CAPACITY_ALERT_HOST_LOCAL: "3 档 · 主机推送 + 本机提醒",
        }
        enabled = level != CAPACITY_ALERT_NONE
        self.capacity_alert_label.setText(labels[level])
        self.capacity_alert_label.setMinimumWidth(220)
        self.capacity_alert_label.setStyleSheet(f"color:{PRIMARY if enabled else MUTED};font-weight:700;")
        self.capacity_threshold_spin.setEnabled(enabled)

    def settings(self) -> SlaveSettings:
        return SlaveSettings(
            inventory_capacity_alert_level=self.capacity_alert_slider.value(),
            inventory_capacity_threshold=self.capacity_threshold_spin.value(),
            device_id=self._source_settings.device_id,
            device_token=self._source_settings.device_token,
            installation_id=self._source_settings.installation_id,
            pairing_code=self.pairing_code_edit.text().strip().upper(),
        )
