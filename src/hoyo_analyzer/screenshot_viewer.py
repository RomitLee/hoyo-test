"""Asynchronous private screenshot preview in the master application."""

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QDialog, QLabel, QScrollArea, QVBoxLayout


class ScreenshotViewer(QDialog):
    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("监控日志 · 截图")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(850, 680)
        layout = QVBoxLayout(self)
        self.image = QLabel("正在加载截图…")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.image)
        layout.addWidget(scroll)
        layout.addWidget(QLabel("截图保留30天；当前显示触发该条日志时的游戏窗口画面。"))
        target = QUrl(url)
        if target.scheme() != "https" or not target.host() or target.userInfo():
            self.image.setText("截图地址无效，请检查服务端配置")
            return
        self.manager = QNetworkAccessManager(self)
        request = QNetworkRequest(target)
        request.setTransferTimeout(20_000)
        self.reply = self.manager.get(request)
        self.watchdog = QTimer(self)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(self.reply.abort)
        self.watchdog.start(25_000)
        self.reply.finished.connect(self._finished)

    def _finished(self):
        self.watchdog.stop()
        raw = bytes(self.reply.readAll())
        pixmap = QPixmap()
        if (
            self.reply.error() == QNetworkReply.NetworkError.NoError
            and len(raw) <= 2 * 1024 * 1024
            and pixmap.loadFromData(raw, "JPEG")
        ):
            self.image.setPixmap(pixmap)
            self.image.setMinimumSize(pixmap.size())
        else:
            self.image.setText("截图加载失败或链接已过期，请关闭后重新查看")
        self.reply.deleteLater()
