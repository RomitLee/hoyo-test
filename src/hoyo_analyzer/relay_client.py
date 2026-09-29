"""Asynchronous HTTPS clients used by the slave and master applications."""

from __future__ import annotations

import json
import platform
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin
from uuid import uuid4

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__


def normalize_relay_url(value: str) -> str:
    """Normalize and validate the relay's HTTPS base URL."""

    normalized = value.strip().rstrip("/")
    if not normalized:
        return ""
    if not normalized.lower().startswith("https://"):
        raise ValueError("云端地址必须以 https:// 开头")
    return normalized


def make_event_payload(
    event_type: str,
    *,
    server_name: str | None = None,
    role_name: str | None = None,
    character_id: str | None = None,
    empty_slots: int | None = None,
    confidence: float | None = None,
    occlusion_percent: float | None = None,
    minimized: bool | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable, idempotent event shape expected by the relay."""

    return {
        "event_id": uuid4().hex,
        "event_type": event_type,
        "occurred_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "server_name": server_name,
        "role_name": role_name,
        "character_id": character_id,
        "empty_slots": empty_slots,
        "confidence": confidence,
        "occlusion_percent": occlusion_percent,
        "minimized": minimized,
        "details": details or {},
    }


class SlaveRelayClient(QObject):
    """Send slave heartbeats and confirmed warning events without blocking Qt."""

    connection_changed = Signal(bool, str)
    event_result = Signal(bool, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._base_url = ""
        self._device_id = ""
        self._device_token = ""
        self._online = False
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(30_000)
        self._heartbeat_timer.timeout.connect(self.connect_now)

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._device_id and self._device_token)

    @property
    def online(self) -> bool:
        return self._online

    def configure(self, relay_url: str, device_id: str, device_token: str) -> None:
        self._base_url = normalize_relay_url(relay_url)
        self._device_id = device_id.strip()
        self._device_token = device_token.strip()
        self._heartbeat_timer.stop()
        self._set_online(False, "尚未连接")
        if self.configured:
            self._heartbeat_timer.start()

    def connect_now(self) -> None:
        if not self.configured:
            self._set_online(False, "请先在设置中填写云端地址、设备编号和设备密钥")
            return
        self._post(
            "/api/v1/slave/heartbeat",
            {"hostname": platform.node() or None, "app_version": __version__},
            "heartbeat",
        )

    def send_status(self, windows: list[dict[str, Any]]) -> None:
        if not self.configured:
            return
        self._post(
            "/api/v1/slave/status",
            {
                "reported_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "windows": windows,
            },
            "status",
        )

    def send_event(self, payload: dict[str, Any]) -> None:
        if not self.configured:
            self.event_result.emit(False, "云端参数未配置，告警未上报")
            self._set_online(False, "云端参数未配置")
            return
        self._post("/api/v1/slave/events", payload, "event")

    def _post(self, path: str, payload: dict[str, Any], kind: str) -> None:
        # QNetworkRequest.setTransferTimeout() does not reliably abort a TLS
        # handshake on every Windows/Qt network backend. Keep an explicit
        # watchdog so the UI can never remain in "连接中" forever.
        try:
            request = self._request(path)
            reply = self._manager.post(
                request,
                QByteArray(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
                ),
            )
        except Exception as exc:  # noqa: BLE001 - report network setup failures to the UI
            message = f"无法发起 HTTPS 请求：{exc}"
            self._set_online(False, message)
            if kind == "event":
                self.event_result.emit(False, f"云端告警发送失败：{message}")
            return

        reply.setProperty("relay_kind", kind)
        timeout_timer = QTimer(reply)
        timeout_timer.setSingleShot(True)
        timeout_timer.setInterval(12_000)
        timeout_timer.timeout.connect(lambda current=reply: self._on_reply_timeout(current))
        reply.setProperty("relay_timeout_timer", timeout_timer)
        reply.finished.connect(lambda current=reply: self._on_finished(current))
        timeout_timer.start()

    def _request(self, path: str) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(urljoin(f"{self._base_url}/", path.lstrip("/"))))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setRawHeader(b"Authorization", f"Bearer {self._device_token}".encode())
        request.setRawHeader(b"X-Device-ID", self._device_id.encode("utf-8"))
        request.setTransferTimeout(10_000)
        return request

    def _on_reply_timeout(self, reply: QNetworkReply) -> None:
        if reply.isFinished():
            return
        reply.setProperty("relay_timed_out", True)
        message = "连接超时，请检查云端地址、网络或 HTTPS 证书"
        self._set_online(False, message)
        if str(reply.property("relay_kind")) == "event":
            self.event_result.emit(False, f"云端告警发送失败：{message}")
        # Abort after publishing the timeout state. The finished callback will
        # clean up the reply without emitting a second result.
        reply.abort()

    def _on_finished(self, reply: QNetworkReply) -> None:
        timer = reply.property("relay_timeout_timer")
        if isinstance(timer, QTimer):
            timer.stop()
        if bool(reply.property("relay_timed_out")):
            reply.deleteLater()
            return

        kind = str(reply.property("relay_kind"))
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        success = reply.error() == QNetworkReply.NetworkError.NoError and status in {200, 202}
        message = ""
        if not success:
            message = reply.errorString()
            try:
                body = json.loads(bytes(reply.readAll()).decode("utf-8"))
                message = str(body.get("detail") or message)
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                pass
        reply.deleteLater()
        if success:
            self._set_online(True, "HTTPS 连接正常")
            if kind == "event":
                self.event_result.emit(True, "告警已发送到云端")
            return
        self._set_online(False, message or "连接失败")
        if kind == "event":
            self.event_result.emit(False, f"云端告警发送失败：{message or '连接失败'}")

    def _set_online(self, online: bool, message: str) -> None:
        self._online = online
        # Every completed attempt must release the UI's "connecting" state,
        # including successful reconnects while already online.
        self.connection_changed.emit(online, message)


class MasterRelayClient(QObject):
    """Poll and acknowledge relay events for the master desktop application."""

    connection_changed = Signal(bool, str)
    events_received = Signal(object)
    devices_received = Signal(object)
    status_received = Signal(object)
    acknowledge_result = Signal(int, bool, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._base_url = ""
        self._master_token = ""
        self._pending_kinds: set[str] = set()
        self._poll_errors: list[str] = []
        self._event_buffer: list[dict[str, Any]] = []

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._master_token)

    def configure(self, relay_url: str, master_token: str) -> None:
        self._base_url = normalize_relay_url(relay_url)
        self._master_token = master_token.strip()

    def poll(self) -> None:
        if not self.configured:
            self.connection_changed.emit(False, "请先填写云端地址和主机密钥")
            return
        if self._pending_kinds:
            return
        self._pending_kinds = {"events", "status"}
        self._poll_errors = []
        self._event_buffer = []
        self._get("/api/v1/master/events?state=all&limit=500", "events")
        self._get("/api/v1/master/status", "status")

    def _finish_poll_kind(self, kind: str, error: str = "") -> None:
        if error:
            self._poll_errors.append(f"{kind}: {error}")
        self._pending_kinds.discard(kind)
        if not self._pending_kinds:
            self.connection_changed.emit(
                not self._poll_errors,
                "; ".join(self._poll_errors) if self._poll_errors else "HTTPS 连接正常",
            )

    def acknowledge(self, server_event_id: int) -> None:
        request = self._request(f"/api/v1/master/events/{server_event_id}/ack")
        reply = self._manager.post(request, QByteArray(b'{"note":"master desktop confirmed"}'))
        reply.setProperty("relay_kind", "ack")
        reply.setProperty("server_event_id", server_event_id)
        self._watch(reply)

    def _get(self, path: str, kind: str) -> None:
        try:
            reply = self._manager.get(self._request(path))
        except Exception as exc:  # noqa: BLE001 - surface request setup errors to the UI
            self._finish_poll_kind(kind, f"无法发起 HTTPS 请求：{exc}")
            return
        reply.setProperty("relay_kind", kind)
        self._watch(reply)

    def _watch(self, reply: QNetworkReply) -> None:
        timeout_timer = QTimer(reply)
        timeout_timer.setSingleShot(True)
        timeout_timer.setInterval(12_000)
        timeout_timer.timeout.connect(lambda current=reply: self._on_reply_timeout(current))
        reply.setProperty("relay_timeout_timer", timeout_timer)
        reply.finished.connect(lambda current=reply: self._on_finished(current))
        timeout_timer.start()

    def _request(self, path: str) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(urljoin(f"{self._base_url}/", path.lstrip("/"))))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setRawHeader(b"Authorization", f"Bearer {self._master_token}".encode())
        request.setTransferTimeout(10_000)
        return request

    def _on_reply_timeout(self, reply: QNetworkReply) -> None:
        if reply.isFinished():
            return
        reply.setProperty("relay_timed_out", True)
        message = "连接超时，请检查云端地址、网络或 HTTPS 证书"
        kind = str(reply.property("relay_kind"))
        if kind == "ack":
            self.acknowledge_result.emit(int(reply.property("server_event_id") or 0), False, message)
        else:
            self._finish_poll_kind(kind, message)
        # Abort after publishing the timeout state. The finished callback will
        # clean up the reply without emitting a second result.
        reply.abort()

    def _on_finished(self, reply: QNetworkReply) -> None:
        timer = reply.property("relay_timeout_timer")
        if isinstance(timer, QTimer):
            timer.stop()
        if bool(reply.property("relay_timed_out")):
            reply.deleteLater()
            return

        kind = str(reply.property("relay_kind"))
        event_id = int(reply.property("server_event_id") or 0)
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        success = reply.error() == QNetworkReply.NetworkError.NoError and status == 200
        raw = bytes(reply.readAll())
        message = reply.errorString() if not success else ""
        data: dict[str, Any] = {}
        if raw:
            try:
                data = json.loads(raw.decode("utf-8"))
                if not isinstance(data, dict):
                    raise TypeError("expected JSON object")
                if not success:
                    message = str(data.get("detail") or message)
            except (UnicodeDecodeError, ValueError, TypeError):
                success = False
                message = "云端返回的数据格式不正确"
        reply.deleteLater()
        if kind == "events" and success:
            events = data.get("events", [])
            self._event_buffer.extend(events)
            if len(events) == 500:
                after_id = int(events[-1]["server_event_id"])
                self._get(f"/api/v1/master/events?state=all&limit=500&after_id={after_id}", "events")
                return
            self.events_received.emit(self._event_buffer)
        elif kind == "devices" and success:
            self.devices_received.emit(data.get("devices", []))
        elif kind == "status" and success:
            self.status_received.emit(data.get("devices", []))
            self.devices_received.emit(data.get("devices", []))
        elif kind == "ack":
            self.acknowledge_result.emit(event_id, success, message)
        if kind != "ack":
            self._finish_poll_kind(kind, "" if success else message or "连接失败")
