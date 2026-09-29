"""Settings dialog for local warnings and cloud relay connectivity."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .slave_settings import (
    ALERT_HOST,
    ALERT_LOCAL,
    ALERT_LOCAL_HOST,
    ALERT_NONE,
    LOCAL_ALERT_POPUP,
    LOCAL_ALERT_POPUP_SOUND,
    SlaveSettings,
)


class SlaveSettingsDialog(QDialog):
    """Edit slave warning behavior and HTTPS cloud relay credentials."""

    ALERT_ITEMS = (
        ("不警告", ALERT_NONE),
        ("当前程序警告", ALERT_LOCAL),
        ("主机警告（经云端）", ALERT_HOST),
        ("本地弹窗 + 主机警告", ALERT_LOCAL_HOST),
    )

    def __init__(self, settings: SlaveSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("从机设置")
        self.setFixedWidth(560)
        self._build_ui()
        self._load_values(settings)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        warning_group = QGroupBox("告警设置")
        warning_form = QFormLayout(warning_group)
        self.minimize_alert_combo = self._make_alert_combo()
        warning_form.addRow("游戏窗口最小化：", self.minimize_alert_combo)

        occlusion_row = QHBoxLayout()
        self.occlusion_threshold_spin = QSpinBox()
        self.occlusion_threshold_spin.setRange(1, 100)
        self.occlusion_threshold_spin.setSuffix(" %")
        self.occlusion_threshold_spin.setToolTip("100% 表示窗口完全被遮挡时触发；30% 表示遮挡达到 30% 时触发")
        self.occlusion_alert_combo = self._make_alert_combo()
        occlusion_row.addWidget(QLabel("达到"))
        occlusion_row.addWidget(self.occlusion_threshold_spin)
        occlusion_row.addWidget(self.occlusion_alert_combo, 1)
        warning_form.addRow("窗口遮挡：", occlusion_row)

        inventory_row = QHBoxLayout()
        self.inventory_threshold_spin = QSpinBox()
        self.inventory_threshold_spin.setRange(0, 20)
        self.inventory_threshold_spin.setSuffix(" 格")
        self.inventory_threshold_spin.setToolTip("0 表示背包已满时触发；3 表示只剩 3 格或更少时触发")
        self.inventory_alert_combo = self._make_alert_combo()
        inventory_row.addWidget(QLabel("剩余不超过"))
        inventory_row.addWidget(self.inventory_threshold_spin)
        inventory_row.addWidget(self.inventory_alert_combo, 1)
        warning_form.addRow("背包容量：", inventory_row)

        self.inventory_not_open_alert_combo = self._make_alert_combo()
        warning_form.addRow("背包未打开：", self.inventory_not_open_alert_combo)

        self.inventory_blocked_alert_combo = self._make_alert_combo()
        warning_form.addRow("背包被遮挡：", self.inventory_blocked_alert_combo)

        self.local_alert_style_combo = QComboBox()
        self.local_alert_style_combo.addItem("弹窗警告", LOCAL_ALERT_POPUP)
        self.local_alert_style_combo.addItem("弹窗 + 持续声音（关闭告警后停止）", LOCAL_ALERT_POPUP_SOUND)
        warning_form.addRow("当前程序警告：", self.local_alert_style_combo)
        root.addWidget(warning_group)

        host_group = QGroupBox("云端中转连接")
        host_form = QFormLayout(host_group)
        self.relay_url_edit = QLineEdit()
        self.relay_url_edit.setPlaceholderText("https://服务器公网 IP")
        host_form.addRow("云端地址：", self.relay_url_edit)
        self.device_id_edit = QLineEdit()
        self.device_id_edit.setPlaceholderText("例如：slave-01")
        host_form.addRow("设备编号：", self.device_id_edit)
        self.device_token_edit = QLineEdit()
        self.device_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.device_token_edit.setPlaceholderText("该从机独立设备密钥")
        host_form.addRow("设备密钥：", self.device_token_edit)
        note = QLabel("从机仍可离线执行本地检测；选择“主机警告”或“本地弹窗 + 主机警告”后，异常才会经 HTTPS 发送到主机。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b;")
        host_form.addRow(note)
        root.addWidget(host_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _make_alert_combo(self) -> QComboBox:
        combo = QComboBox()
        for label, value in self.ALERT_ITEMS:
            combo.addItem(label, value)
        combo.setItemData(2, "通过公网 HTTPS 中转给主机程序", role=3)
        combo.setItemData(3, "从机弹窗，同时通过公网 HTTPS 中转给主机程序", role=3)
        return combo

    def _load_values(self, settings: SlaveSettings) -> None:
        self._select_data(self.minimize_alert_combo, settings.minimize_alert)
        self.occlusion_threshold_spin.setValue(settings.occlusion_threshold_percent)
        self._select_data(self.occlusion_alert_combo, settings.occlusion_alert)
        self.inventory_threshold_spin.setValue(settings.inventory_empty_threshold)
        self._select_data(self.inventory_alert_combo, settings.inventory_alert)
        self._select_data(self.inventory_not_open_alert_combo, settings.inventory_not_open_alert)
        self._select_data(self.inventory_blocked_alert_combo, settings.inventory_blocked_alert)
        self._select_data(self.local_alert_style_combo, settings.local_alert_style)
        self.relay_url_edit.setText(settings.relay_url)
        self.device_id_edit.setText(settings.device_id)
        self.device_token_edit.setText(settings.device_token)

    @staticmethod
    def _select_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(index, 0))

    def settings(self) -> SlaveSettings:
        return SlaveSettings(
            minimize_alert=str(self.minimize_alert_combo.currentData()),
            occlusion_threshold_percent=self.occlusion_threshold_spin.value(),
            occlusion_alert=str(self.occlusion_alert_combo.currentData()),
            inventory_empty_threshold=self.inventory_threshold_spin.value(),
            inventory_alert=str(self.inventory_alert_combo.currentData()),
            inventory_not_open_alert=str(self.inventory_not_open_alert_combo.currentData()),
            inventory_blocked_alert=str(self.inventory_blocked_alert_combo.currentData()),
            local_alert_style=str(self.local_alert_style_combo.currentData()),
            relay_url=self.relay_url_edit.text().strip(),
            device_id=self.device_id_edit.text().strip(),
            device_token=self.device_token_edit.text().strip(),
        )
