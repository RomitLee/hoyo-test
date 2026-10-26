"""Asynchronous HTTPS clients used by the slave and master applications."""

from __future__ import annotations

import json
import logging
import platform
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin
from uuid import uuid4

from PySide6.QtCore import QByteArray, QEventLoop, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import PROTOCOL_VERSION
from . import SLAVE_VERSION as __version__
from .cloud_config import CLOUD_RELAY_URL
from .live_screenshots import LiveScreenshotClient
from .paths import application_root
from .screenshots import ScreenshotUploader


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
    pairing_result = Signal(bool, str, object)
    profile_received = Signal(object)
    profile_result = Signal(bool, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self.screenshots = ScreenshotUploader(self)
        self.live_screenshots = LiveScreenshotClient(self)
        self._base_url = ""
        self._device_id = ""
        self._device_token = ""
        self._online = False
        self._log_path = application_root() / "runtime" / "monitor-log-outbox.json"
        try:
            saved = json.loads(self._log_path.read_text(encoding="utf-8"))
            self._log_outbox = saved if isinstance(saved, list) else []
        except (OSError, ValueError):
            self._log_outbox = []
        self._log_inflight = False
        self._log_dirty = False
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(5000)
        self._log_timer.timeout.connect(self._retry_log_outbox)
        self._log_timer.start()
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(30_000)
        self._heartbeat_timer.timeout.connect(self.connect_now)

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._device_id and self._device_token)

    @property
    def online(self) -> bool:
        return self._online

    def configure(
        self, device_id_or_url: str, device_token_or_id: str, legacy_device_token: str | None = None
    ) -> None:
        """Configure a device while accepting the old three-argument call."""

        self._base_url = CLOUD_RELAY_URL
        if legacy_device_token is None:
            self._device_id = device_id_or_url.strip()
            self._device_token = device_token_or_id.strip()
        else:
            self._device_id = device_token_or_id.strip()
            self._device_token = legacy_device_token.strip()
        self._heartbeat_timer.stop()
        self.screenshots.configure(self._base_url, self._device_id, self._device_token)
        self.live_screenshots.configure(self._base_url, self._device_id, self._device_token)
        self._set_online(False, "尚未连接")
        if self.configured:
            self._heartbeat_timer.start()

    def connect_now(self) -> None:
        if not self.configured:
            self._set_online(False, "请在设置中输入主机生成的配对码")
            return
        self._post(
            "/api/v1/slave/heartbeat",
            {"hostname": platform.node() or None, "protocol_version": PROTOCOL_VERSION, "app_version": __version__},
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

    def rename_device(self, name: str) -> None:
        name = name.strip()
        if not self.configured or not 1 <= len(name) <= 64:
            self.profile_result.emit(False, "请先配对，并填写 1～64 字的从机名称")
            return
        self._post("/api/v1/slave/profile", {"display_name": name}, "profile")

    def send_event(self, payload: dict[str, Any]) -> None:
        if not self.configured:
            self.event_result.emit(False, "云端参数未配置，告警未上报")
            self._set_online(False, "云端参数未配置")
            return
        self._post("/api/v1/slave/events", payload, "event")

    def send_monitor_log(self, event_type: str, reason: str) -> None:
        self.send_monitor_payload({
            "event_id": uuid4().hex, "event_type": event_type, "reason": reason,
            "occurred_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        })

    def send_monitor_payload(self, payload: dict[str, Any], screenshot_path: str | None = None) -> None:
        self._log_outbox.append({"device_id": self._device_id, "payload": payload})
        self._save_log_outbox()
        if screenshot_path:
            self.screenshots.enqueue(payload["event_id"], screenshot_path)
        self._send_next_log()

    def send_cash_observation(self, payload: dict[str, Any]) -> None:
        self._log_outbox.append({"device_id": self._device_id, "payload": payload, "route": "cash"})
        self._save_log_outbox()
        self._send_next_log()

    def _save_log_outbox(self) -> None:
        # Windows scanners can briefly hold the destination without delete sharing.
        # Keep the current queue in memory and retry on the Qt timer, never sleep
        # in an analysis/network callback or abandon the remaining uploads.
        was_dirty = self._log_dirty
        self._log_dirty = True
        temporary = self._log_path.with_name(f"{self._log_path.stem}-{uuid4().hex}.tmp")
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(self._log_outbox, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self._log_path)
        except OSError:
            if not was_dirty:
                logging.getLogger(__name__).warning(
                    "待上报记录暂时无法保存，保留内存队列并每 5 秒重试", exc_info=True,
                )
        else:
            self._log_dirty = False
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # A locked temporary file must not interrupt monitoring either.

    def _retry_log_outbox(self) -> None:
        if self._log_dirty:
            self._save_log_outbox()
        self._send_next_log()

    def _send_next_log(self) -> None:
        if not self.configured or self._log_inflight:
            return
        for item in self._log_outbox:
            if not item["device_id"]:
                item["device_id"] = self._device_id
                self._save_log_outbox()
            if item["device_id"] == self._device_id:
                self._log_inflight = True
                path = "/api/v1/slave/cash-observations" if item.get("route") == "cash" else "/api/v1/slave/monitor-logs"
                self._post(path, item["payload"], "monitor_log")
                return

    def flush_monitor_logs(self, timeout_ms: int = 2000) -> None:
        """Give normal shutdown a bounded chance to deliver; retain failures on disk."""
        self._retry_log_outbox()
        if not self.configured or not self._log_inflight:
            return
        loop = QEventLoop()
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        check = QTimer()
        check.timeout.connect(lambda: loop.quit() if not self._log_inflight else None)
        check.start(25)
        timeout.start(timeout_ms)
        loop.exec()
        check.stop()
        timeout.stop()

    def pair(self, pairing_code: str, installation_id: str) -> None:
        code = pairing_code.strip().upper()
        if not code:
            self.pairing_result.emit(False, "请输入配对码", {})
            return
        self._base_url = CLOUD_RELAY_URL
        self._post(
            "/api/v1/slave/pair",
            {
                "pairing_code": code,
                "installation_id": installation_id,
                "hostname": platform.node() or None,
                "protocol_version": PROTOCOL_VERSION, "app_version": __version__,
            },
            "pair",
            authenticated=False,
        )

    def _post(self, path: str, payload: dict[str, Any], kind: str, *, authenticated: bool = True) -> None:
        # QNetworkRequest.setTransferTimeout() does not reliably abort a TLS
        # handshake on every Windows/Qt network backend. Keep an explicit
        # watchdog so the UI can never remain in "连接中" forever.
        try:
            request = self._request(path, authenticated=authenticated)
            reply = self._manager.post(
                request,
                QByteArray(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
                ),
            )
        except Exception as exc:  # noqa: BLE001 - report network setup failures to the UI
            if kind == "monitor_log":
                self._log_inflight = False
            message = f"无法发起 HTTPS 请求：{exc}"
            self._set_online(False, message)
            if kind == "event":
                self.event_result.emit(False, f"云端告警发送失败：{message}")
            elif kind == "profile":
                self.profile_result.emit(False, message)
            return

        reply.setProperty("relay_kind", kind)
        if kind == "monitor_log":
            reply.setProperty("log_event_id", payload["event_id"])
        timeout_timer = QTimer(reply)
        timeout_timer.setSingleShot(True)
        timeout_timer.setInterval(12_000)
        timeout_timer.timeout.connect(lambda current=reply: self._on_reply_timeout(current))
        reply.setProperty("relay_timeout_timer", timeout_timer)
        reply.finished.connect(lambda current=reply: self._on_finished(current))
        timeout_timer.start()

    def _request(self, path: str, *, authenticated: bool = True) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(urljoin(f"{self._base_url}/", path.lstrip("/"))))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        if authenticated:
            request.setRawHeader(b"Authorization", f"Bearer {self._device_token}".encode())
            request.setRawHeader(b"X-Device-ID", self._device_id.encode("utf-8"))
        request.setTransferTimeout(10_000)
        return request

    def _on_reply_timeout(self, reply: QNetworkReply) -> None:
        if reply.isFinished():
            return
        reply.setProperty("relay_timed_out", True)
        if str(reply.property("relay_kind")) == "monitor_log":
            self._log_inflight = False
        message = "连接超时，请检查云端地址、网络或 HTTPS 证书"
        self._set_online(False, message)
        if str(reply.property("relay_kind")) == "event":
            self.event_result.emit(False, f"云端告警发送失败：{message}")
        elif str(reply.property("relay_kind")) == "pair":
            self.pairing_result.emit(False, message, {})
        elif str(reply.property("relay_kind")) == "profile":
            self.profile_result.emit(False, message)
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
        success = reply.error() == QNetworkReply.NetworkError.NoError and status in {200, 201, 202}
        message = "" if success else reply.errorString()
        data: dict[str, Any] = {}
        raw = bytes(reply.readAll())
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8"))
                data = parsed if isinstance(parsed, dict) else {}
                if not success:
                    message = str(data.get("detail") or message)
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                if success:
                    success = False
                    message = "云端返回的数据格式不正确"
        reply.deleteLater()
        if kind == "monitor_log":
            self._log_inflight = False
            if success:
                log_id = reply.property("log_event_id")
                self._log_outbox = [item for item in self._log_outbox
                                    if item["payload"]["event_id"] != log_id]
                self._save_log_outbox()
                self._send_next_log()
        if success:
            if kind in {"heartbeat", "profile"} and data.get("display_name"):
                self.profile_received.emit(data)
            if kind == "profile":
                self.profile_result.emit(True, "从机名称已保存")
            if kind == "pair":
                self.pairing_result.emit(True, "从机配对成功", data)
                return
            self._set_online(True, "HTTPS 连接正常")
            if kind == "event":
                self.event_result.emit(True, "告警已发送到云端")
            return
        self._set_online(False, message or "连接失败")
        if kind == "event":
            self.event_result.emit(False, f"云端告警发送失败：{message or '连接失败'}")
        elif kind == "pair":
            self.pairing_result.emit(False, message or "配对失败", {})
        elif kind == "profile":
            self.profile_result.emit(False, message or "名称保存失败")

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
    monitor_logs_received = Signal(object)
    screenshot_result = Signal(bool, object)
    monitor_log_settings_received = Signal(object)
    monitor_log_settings_result = Signal(bool, str)
    cash_statistics_received = Signal(object)
    acknowledge_result = Signal(int, bool, str)
    authentication_result = Signal(bool, str, object)
    pairing_code_received = Signal(bool, str, object)
    device_action_result = Signal(bool, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._base_url = ""
        self._master_token = ""
        self._pending_kinds: set[str] = set()
        self._poll_errors: list[str] = []
        self._event_buffer: list[dict[str, Any]] = []
        self._logs_fetching = False
        self._desired_log_query = (0, "", "")
        self._cash_fetching = False
        self._configuration_generation = 0

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._master_token)

    def configure(self, token_or_url: str, legacy_token: str | None = None) -> None:
        """Use the fixed endpoint; accept the former (url, token) signature."""

        self._base_url = CLOUD_RELAY_URL
        self._master_token = (legacy_token if legacy_token is not None else token_or_url).strip()
        self._configuration_generation += 1

    def poll(self) -> None:
        if not self.configured:
            self.connection_changed.emit(False, "请先登录主机账号")
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

    def fetch_monitor_logs(self, before_id: int = 0, *, event_type: str = "", panel_type: str = "") -> None:
        self._desired_log_query = (before_id, event_type, panel_type)
        if self.configured and not self._logs_fetching:
            self._logs_fetching = True
            from urllib.parse import urlencode
            query = urlencode({"limit": 100, "before_id": before_id, "event_type": event_type, "panel_type": panel_type})
            self._get(f"/api/v1/master/monitor-logs?{query}", "monitor_logs")

    def fetch_monitor_log_settings(self) -> None:
        if self.configured:
            self._get("/api/v1/master/monitor-log-settings", "monitor_log_settings")

    def fetch_screenshot(self, log_id: int) -> None:
        if self.configured:
            self._get(f"/api/v1/master/monitor-logs/{int(log_id)}/screenshot", "screenshot")

    def save_monitor_log_settings(self, values: dict[str, Any]) -> None:
        self._send_custom("PUT", "/api/v1/master/monitor-log-settings", values, "monitor_log_settings_update")

    def fetch_cash_statistics(self, day: str) -> None:
        if self.configured and not self._cash_fetching:
            self._cash_fetching = True
            self._get(f"/api/v1/master/cash-statistics?day={day}", "cash_statistics")

    def authenticate(self, username: str, password: str, *, register: bool = False) -> None:
        path = "/api/v1/auth/register" if register else "/api/v1/auth/login"
        payload: dict[str, Any] = {"username": username.strip(), "password": password}
        if register:
            payload["display_name"] = username.strip()
        self._post_json(path, payload, "auth", authenticated=False)

    def create_pairing_code(self, target_device_id: str | None = None) -> None:
        self._post_json(
            "/api/v1/master/pairing-codes",
            {"target_device_id": target_device_id},
            "pairing_code",
        )

    def update_device(self, device_id: str, changes: dict[str, Any]) -> None:
        self._send_custom("PATCH", f"/api/v1/master/devices/{device_id}", changes, "device_action")

    def remove_device(self, device_id: str) -> None:
        self._send_custom("DELETE", f"/api/v1/master/devices/{device_id}", {}, "device_action")

    def reissue_device(self, device_id: str) -> None:
        self._post_json(f"/api/v1/master/devices/{device_id}/reissue", {}, "pairing_code")

    def _post_json(
        self, path: str, payload: dict[str, Any], kind: str, *, authenticated: bool = True
    ) -> None:
        self._send_custom("POST", path, payload, kind, authenticated=authenticated)

    def _send_custom(
        self, method: str, path: str, payload: dict[str, Any], kind: str, *, authenticated: bool = True
    ) -> None:
        try:
            request = self._request(path, authenticated=authenticated)
            body = QByteArray(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
            reply = self._manager.sendCustomRequest(request, method.encode(), body)
        except Exception as exc:  # noqa: BLE001
            message = f"无法发起 HTTPS 请求：{exc}"
            if kind == "auth":
                self.authentication_result.emit(False, message, {})
            elif kind == "pairing_code":
                self.pairing_code_received.emit(False, message, {})
            elif kind == "monitor_log_settings_update":
                self.monitor_log_settings_result.emit(False, message)
            else:
                self.device_action_result.emit(False, message)
            return
        reply.setProperty("relay_kind", kind)
        if kind == "monitor_log_settings_update":
            reply.setProperty("settings_generation", self._configuration_generation)
        self._watch(reply)

    def _get(self, path: str, kind: str) -> None:
        try:
            reply = self._manager.get(self._request(path))
        except Exception as exc:  # noqa: BLE001 - surface request setup errors to the UI
            if kind == "screenshot":
                self.screenshot_result.emit(False, {"detail": "无法连接截图服务"})
                return
            if kind == "monitor_log_settings":
                self.monitor_log_settings_result.emit(False, str(exc))
                return
            if kind == "monitor_logs":
                self._logs_fetching = False
            if kind == "cash_statistics":
                self._cash_fetching = False
            self._finish_poll_kind(kind, f"无法发起 HTTPS 请求：{exc}")
            return
        reply.setProperty("relay_kind", kind)
        if kind == "screenshot":
            reply.setProperty("screenshot_generation", self._configuration_generation)
        if kind == "monitor_logs":
            reply.setProperty("log_query", self._desired_log_query)
            reply.setProperty("log_generation", self._configuration_generation)
        if kind == "monitor_log_settings":
            reply.setProperty("settings_generation", self._configuration_generation)
        if kind == "cash_statistics":
            reply.setProperty("cash_generation", self._configuration_generation)
        self._watch(reply)

    def _watch(self, reply: QNetworkReply) -> None:
        timeout_timer = QTimer(reply)
        timeout_timer.setSingleShot(True)
        timeout_timer.setInterval(12_000)
        timeout_timer.timeout.connect(lambda current=reply: self._on_reply_timeout(current))
        reply.setProperty("relay_timeout_timer", timeout_timer)
        reply.finished.connect(lambda current=reply: self._on_finished(current))
        timeout_timer.start()

    def _request(self, path: str, *, authenticated: bool = True) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(urljoin(f"{self._base_url}/", path.lstrip("/"))))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        if authenticated:
            request.setRawHeader(b"Authorization", f"Bearer {self._master_token}".encode())
        request.setTransferTimeout(10_000)
        return request

    def _on_reply_timeout(self, reply: QNetworkReply) -> None:
        if reply.isFinished():
            return
        reply.setProperty("relay_timed_out", True)
        message = "连接超时，请检查云端地址、网络或 HTTPS 证书"
        kind = str(reply.property("relay_kind"))
        if kind == "screenshot":
            if reply.property("screenshot_generation") == self._configuration_generation:
                self.screenshot_result.emit(False, {"detail": "截图请求超时，请重试"})
            reply.abort()
            return
        if kind == "monitor_logs":
            self._logs_fetching = False
        if kind == "cash_statistics":
            self._cash_fetching = False
        if kind == "ack":
            self.acknowledge_result.emit(int(reply.property("server_event_id") or 0), False, message)
        elif kind == "auth":
            self.authentication_result.emit(False, message, {})
        elif kind == "pairing_code":
            self.pairing_code_received.emit(False, message, {})
        elif kind == "device_action":
            self.device_action_result.emit(False, message)
        elif kind in {"monitor_log_settings", "monitor_log_settings_update"}:
            self.monitor_log_settings_result.emit(False, message)
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
        success = reply.error() == QNetworkReply.NetworkError.NoError and status in {200, 201, 202, 204}
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
        if kind == "screenshot":
            if reply.property("screenshot_generation") == self._configuration_generation:
                self.screenshot_result.emit(success, data if success else {"detail": message or "读取截图失败"})
            return
        if kind == "monitor_logs":
            self._logs_fetching = False
            requested = reply.property("log_query")
            generation = reply.property("log_generation")
            if (generation is not None and generation != self._configuration_generation) or (
                requested is not None and tuple(requested) != self._desired_log_query
            ):
                before, event, panel = self._desired_log_query
                self.fetch_monitor_logs(before, event_type=event, panel_type=panel)
                return
        if kind in {"monitor_log_settings", "monitor_log_settings_update"}:
            if reply.property("settings_generation") != self._configuration_generation:
                return
            if success:
                self.monitor_log_settings_received.emit(data)
            self.monitor_log_settings_result.emit(success, "账号遮挡日志规则已同步" if success else
                ("服务器尚未升级，请使用本地预览；规则尚未生效" if status == 404 else message or "规则同步失败"))
            return
        if kind == "cash_statistics":
            self._cash_fetching = False
            if reply.property("cash_generation") != self._configuration_generation:
                return
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
        elif kind == "monitor_logs" and success:
            self.monitor_logs_received.emit(data)
        elif kind == "cash_statistics" and success:
            self.cash_statistics_received.emit(data)
        elif kind == "ack":
            self.acknowledge_result.emit(event_id, success, message)
        elif kind == "auth":
            self.authentication_result.emit(success, message, data)
        elif kind == "pairing_code":
            self.pairing_code_received.emit(success, message, data)
        elif kind == "device_action":
            self.device_action_result.emit(success, message)
        if kind not in {"ack", "auth", "pairing_code", "device_action"}:
            self._finish_poll_kind(kind, "" if success else message or "连接失败")
