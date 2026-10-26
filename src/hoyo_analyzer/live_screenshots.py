"""Direct COS uploads for scheduled and requested game-window snapshots."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


class LiveScreenshotClient(QObject):
    control_received = Signal(object)
    upload_failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._network = QNetworkAccessManager(self)
        self._base_url = ""
        self._device_id = ""
        self._token = ""
        self._permissions: dict[str, tuple[str, datetime, str]] = {}
        self._pending: dict[str, tuple[bytes, str, str, str]] = {}
        self._inflight: set[str] = set()
        self._retries: dict[str, int] = {}
        self._polling = False
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.poll)

    def configure(self, base_url: str, device_id: str, token: str) -> None:
        self._base_url, self._device_id, self._token = base_url, device_id, token
        self._permissions.clear()
        self._pending.clear()
        self._inflight.clear()
        self._retries.clear()
        self._timer.stop()
        if base_url and device_id and token:
            self._timer.start()
            QTimer.singleShot(1000, self.poll)

    def _request(self, path: str) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(urljoin(self._base_url + "/", path.lstrip("/"))))
        request.setRawHeader(b"Authorization", f"Bearer {self._token}".encode())
        request.setRawHeader(b"X-Device-ID", self._device_id.encode())
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setTransferTimeout(15000)
        return request

    def _finish(self, reply: QNetworkReply, callback: Callable[[dict[str, Any] | None], None]) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        try:
            data = json.loads(bytes(reply.readAll()).decode("utf-8")) if reply.error() == QNetworkReply.NetworkError.NoError and status in {200, 201, 202} else None
        except (UnicodeDecodeError, ValueError):
            data = None
        reply.deleteLater()
        callback(data if isinstance(data, dict) else None)

    def _post(self, path: str, data: dict[str, Any], callback: Callable[[dict[str, Any] | None], None]) -> None:
        reply = self._network.post(self._request(path), QByteArray(json.dumps(data, separators=(",", ":")).encode()))
        reply.finished.connect(lambda: self._finish(reply, callback))

    def poll(self) -> None:
        if not self._token or self._polling:
            return
        self._polling = True
        reply = self._network.get(self._request("/api/v1/slave/live-screenshots/control"))
        reply.finished.connect(lambda: self._finish(reply, self._on_control))

    def _on_control(self, data: dict[str, Any] | None) -> None:
        self._polling = False
        if data is not None:
            self.control_received.emit(data)

    def upload(self, window_id: str, jpeg: bytes, captured_at: str, request_id: str = "") -> None:
        if not self._token or not jpeg or len(jpeg) > 2 * 1024 * 1024:
            return
        self._pending[window_id] = (jpeg, captured_at, request_id, uuid.uuid4().hex)
        self._permissions.pop(window_id, None)
        self._retries[window_id] = 0
        self._upload_next(window_id)

    def _upload_next(self, window_id: str) -> None:
        if window_id in self._inflight or window_id not in self._pending:
            return
        permission = self._permissions.get(window_id)
        capture_id = self._pending[window_id][3]
        if permission is None or permission[2] != capture_id or permission[1] <= datetime.now(UTC) + timedelta(minutes=2):
            self._inflight.add(window_id)
            self._post("/api/v1/slave/live-screenshots/permission",
                       {"window_id": window_id, "capture_id": self._pending[window_id][3]},
                       lambda data: self._on_permission(window_id, capture_id, data))
            return
        jpeg, captured_at, request_id, capture_id = self._pending.pop(window_id)
        self._inflight.add(window_id)
        request = QNetworkRequest(QUrl(permission[0]))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "image/jpeg")
        request.setRawHeader(b"Cache-Control", b"private, no-store")
        request.setTransferTimeout(30000)
        reply = self._network.put(request, QByteArray(jpeg))
        reply.finished.connect(lambda: self._on_upload(reply, window_id, jpeg, captured_at, request_id, capture_id))

    def _on_permission(self, window_id: str, capture_id: str, data: dict[str, Any] | None) -> None:
        self._inflight.discard(window_id)
        if window_id not in self._pending or self._pending[window_id][3] != capture_id:
            self._upload_next(window_id)
            return
        if data and isinstance(data.get("url"), str):
            try:
                self._permissions[window_id] = (data["url"], datetime.fromisoformat(data["expires_at"]), capture_id)
                self._upload_next(window_id)
                return
            except (ValueError, KeyError):
                pass
        self._retry(window_id, "无法取得截图上传许可")

    def _retry(self, window_id: str, reason: str) -> None:
        if window_id not in self._pending:
            return
        self._retries[window_id] = self._retries.get(window_id, 0) + 1
        if self._retries[window_id] > 8:
            self._pending.pop(window_id, None)
            self._retries.pop(window_id, None)
            self.upload_failed.emit(f"{window_id}：{reason}")
            return
        QTimer.singleShot(5000, lambda: self._upload_next(window_id))

    def _on_upload(self, reply: QNetworkReply, window_id: str, jpeg: bytes, captured_at: str,
                   request_id: str, capture_id: str) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        ok = reply.error() == QNetworkReply.NetworkError.NoError and status in {200, 201, 204}
        reply.deleteLater()
        if not ok:
            self._inflight.discard(window_id)
            self._pending.setdefault(window_id, (jpeg, captured_at, request_id, capture_id))
            self._permissions.pop(window_id, None)
            self._retry(window_id, "COS 截图上传失败")
            return
        self._post("/api/v1/slave/live-screenshots/complete",
                   {"window_id": window_id, "captured_at": captured_at, "request_id": request_id or None,
                    "capture_id": capture_id},
                   lambda data: self._on_complete(window_id, jpeg, captured_at, request_id, capture_id, data))

    def _on_complete(self, window_id: str, jpeg: bytes, captured_at: str, request_id: str, capture_id: str,
                     data: dict[str, Any] | None) -> None:
        self._inflight.discard(window_id)
        if data is None:
            self._pending.setdefault(window_id, (jpeg, captured_at, request_id, capture_id))
            self._retry(window_id, "截图已上传，但登记失败")
            return
        self._retries.pop(window_id, None)
        self._upload_next(window_id)
