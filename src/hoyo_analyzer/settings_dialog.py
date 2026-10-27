"""Read-only account status for the slave; alert policy lives in the web admin."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

from .desktop_theme import MUTED, apply_desktop_theme
from .slave_settings import SlaveSettings


class SlaveSettingsDialog(QDialog):
    def __init__(self, settings: SlaveSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("从机信息")
        self.setFixedWidth(430)
        apply_desktop_theme(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        title = QLabel("从机信息")
        title.setStyleSheet("font-size:20px;font-weight:800;color:#243247;")
        root.addWidget(title)

        group = QGroupBox("登录状态")
        form = QFormLayout(group)
        self.binding_status_label = QLabel(
            f"已登录 · {settings.account_username or '后台账号'} · {settings.display_name or '未命名从机'}"
            if settings.device_id and settings.device_token else "未登录"
        )
        form.addRow("当前状态：", self.binding_status_label)
        root.addWidget(group)

        note = QLabel("手机推送、本机报警、电脑网页提示音、背包空格、游戏币和体力满提醒，统一在后台管理系统的设置页面配置。")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{MUTED};padding:4px 0;")
        root.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
