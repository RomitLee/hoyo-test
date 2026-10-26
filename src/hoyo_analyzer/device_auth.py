"""Verify a saved device identity or sign in with an account password."""

from __future__ import annotations

import json
import platform
from typing import Any
from urllib.parse import urljoin

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import PROTOCOL_VERSION
from . import SLAVE_VERSION as __version__
from .cloud_config import CLOUD_RELAY_URL


class DeviceAuthClient(QObject):
    result = Signal(str, bool, str, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._active: QNetworkReply | None = None
        self._generation = 0

    def verify(self, device_id: str, token: str) -> None:
        if not device_id or not token:
            self.result.emit("verify", False, "请登录账号后使用", {"http_status": 401})
            return
        request = self._request("/api/v1/slave/auth-state")
        request.setRawHeader(b"X-Device-ID", device_id.encode())
        request.setRawHeader(b"Authorization", f"Bearer {token}".encode())
        self._start("verify", self._manager.get(request))

    def login(self, username: str, password: str, installation_id: str) -> None:
        if not username.strip() or not password:
            self.result.emit("login", False, "请输入账号和密码", {"http_status": 422})
            return
        request = self._request("/api/v1/slave/login")
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        payload = {
            "username": username.strip(), "password": password,
            "installation_id": installation_id, "hostname": platform.node() or None,
            "app_version": __version__, "protocol_version": PROTOCOL_VERSION,
        }
        self._start("login", self._manager.post(request, QByteArray(json.dumps(payload).encode("utf-8"))))

    @staticmethod
    def _request(path: str) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(urljoin(CLOUD_RELAY_URL + "/", path.lstrip("/"))))
        request.setTransferTimeout(12000)
        return request

    def _start(self, kind: str, reply: QNetworkReply) -> None:
        if self._active is not None:
            self._active.abort()
        self._generation += 1
        reply.setProperty("auth_generation", self._generation)
        self._active = reply
        watchdog = QTimer(reply)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(reply.abort)
        reply.finished.connect(lambda: self._finished(kind, reply, watchdog))
        watchdog.start(12000)

    def _finished(self, kind: str, reply: QNetworkReply, watchdog: QTimer) -> None:
        watchdog.stop()
        if reply is not self._active or reply.property("auth_generation") != self._generation:
            reply.deleteLater()
            return
        self._active = None
        status = int(reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0)
        success = reply.error() == QNetworkReply.NetworkError.NoError and status in {200, 201}
        message = "" if success else reply.errorString()
        data: dict[str, Any] = {}
        try:
            decoded = json.loads(bytes(reply.readAll()).decode("utf-8"))
            if isinstance(decoded, dict):
                data = decoded
                if not success:
                    message = str(decoded.get("detail") or message)
        except (UnicodeDecodeError, ValueError):
            if success:
                success, message = False, "服务端返回内容无效"
        reply.deleteLater()
        data["http_status"] = status
        self.result.emit(kind, success, message or "网络不可用，请稍后重试", data)

    def cancel(self) -> None:
        if self._active is not None:
            reply = self._active
            self._active = None
            self._generation += 1
            reply.abort()
