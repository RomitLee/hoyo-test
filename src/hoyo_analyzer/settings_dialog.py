"""Settings dialog for the slave capacity alert and cloud relay."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .desktop_theme import MUTED, apply_desktop_theme
from .slave_settings import (
    CAPACITY_ALERT_HOST,
    CAPACITY_ALERT_LOCAL,
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
        title.setStyleSheet("font-size:20px;font-weight:800;color:#243247;")
        subtitle = QLabel("设置背包容量提醒，查看登录状态")
        subtitle.setStyleSheet(f"color:{MUTED};font-size:11px;")
        root.addWidget(title)
        root.addWidget(subtitle)

        warning_group = QGroupBox("告警设置")
        warning_form = QFormLayout(warning_group)
        capacity_row = QHBoxLayout()
        self.capacity_alert_group = QButtonGroup(self)
        self.capacity_alert_group.setExclusive(True)
        self.capacity_alert_buttons: dict[int, QRadioButton] = {}
        for level, text in (
            (CAPACITY_ALERT_NONE, "1 不提醒"),
            (CAPACITY_ALERT_HOST, "2 云端推送"),
            (CAPACITY_ALERT_LOCAL, "3 本机提醒"),
        ):
            button = QRadioButton(text)
            self.capacity_alert_group.addButton(button, level)
            self.capacity_alert_buttons[level] = button
            capacity_row.addWidget(button)
        capacity_row.addStretch(1)
        self.capacity_alert_group.idClicked.connect(self._refresh_capacity_controls)
        warning_form.addRow("背包容量：", capacity_row)
        self.capacity_threshold_spin = QSpinBox()
        self.capacity_threshold_spin.setRange(MIN_CAPACITY_THRESHOLD, MAX_CAPACITY_THRESHOLD)
        self.capacity_threshold_spin.setSuffix(" 格")
        self.capacity_threshold_spin.setToolTip("当背包剩余空格小于或等于此数值并持续 5 秒时触发")
        warning_form.addRow("空格阈值：", self.capacity_threshold_spin)
        note = QLabel(
            "达到空格阈值并连续确认 5 秒后：2 档仅向云端推送告警；"
            "3 档仅在本机弹窗并循环播放声音，直到手动确认。"
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{MUTED};padding:4px 0;")
        warning_form.addRow(note)
        root.addWidget(warning_group)

        host_group = QGroupBox("登录状态")
        host_form = QFormLayout(host_group)
        self.binding_status_label = QLabel()
        host_form.addRow("当前状态：", self.binding_status_label)
        note = QLabel(
            "服务器地址已内置。首次使用请在启动界面输入后台账号和密码；"
            "以后启动会自动验证本机登录状态。"
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
        button = self.capacity_alert_buttons.get(
            settings.inventory_capacity_alert_level,
            self.capacity_alert_buttons[CAPACITY_ALERT_NONE],
        )
        button.setChecked(True)
        self.capacity_threshold_spin.setValue(settings.inventory_capacity_threshold)
        self._refresh_capacity_controls()
        self.binding_status_label.setText(
            f"已登录 · {settings.account_username or '后台账号'} · {settings.display_name or '未命名从机'}"
            if settings.device_id and settings.device_token else "未登录"
        )

    def _refresh_capacity_controls(self, _level: int | None = None) -> None:
        self.capacity_threshold_spin.setEnabled(
            self.capacity_alert_group.checkedId() != CAPACITY_ALERT_NONE
        )

    def settings(self) -> SlaveSettings:
        return SlaveSettings(
            inventory_capacity_alert_level=self.capacity_alert_group.checkedId(),
            inventory_capacity_threshold=self.capacity_threshold_spin.value(),
            device_id=self._source_settings.device_id,
            display_name=self._source_settings.display_name,
            account_username=self._source_settings.account_username,
            device_token=self._source_settings.device_token,
            installation_id=self._source_settings.installation_id,
            pairing_code="",
        )
