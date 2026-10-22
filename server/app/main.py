"""Multi-account HTTPS relay for inventory-monitor master and slave apps."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Annotated, Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .bark import BarkPushError, event_message, send_bark_notification
from .cash_ledger import BEIJING
from .config import Settings
from .database import RelayDatabase

VERSION = "2.5.1"
WEB_ROOT = Path(__file__).parent / "web"
SESSION_DAYS = 30
PAIRING_MINUTES = 5
PASSWORD_ITERATIONS = 260_000
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    device_id: str
    account_id: int


class CredentialsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)


class RegisterIn(CredentialsIn):
    display_name: str | None = Field(default=None, max_length=64)


class PairingCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_device_id: str | None = Field(default=None, max_length=64)


class PairDeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pairing_code: str = Field(min_length=6, max_length=16, pattern=r"^[A-Za-z0-9]+$")
    installation_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    hostname: str | None = Field(default=None, max_length=255)
    app_version: str | None = Field(default=None, max_length=64)


class DeviceUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=200)
    disabled: bool | None = None


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    event_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    occurred_at: datetime
    server_name: str | None = Field(default=None, max_length=128)
    role_name: str | None = Field(default=None, max_length=128)
    character_id: str | None = Field(default=None, max_length=64)
    empty_slots: int | None = Field(default=None, ge=0, le=20)
    confidence: float | None = Field(default=None, ge=0, le=1)
    occlusion_percent: float | None = Field(default=None, ge=0, le=100)
    minimized: bool | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hostname: str | None = Field(default=None, max_length=255)
    app_version: str | None = Field(default=None, max_length=64)


class CashObservationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=128)
    character_id: str = Field(min_length=1, max_length=64, pattern=r"^\d+$")
    stream_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1, le=9223372036854775807, strict=True)
    occurred_at: datetime
    cash: int | None = Field(default=None, ge=0, le=999999999999, strict=True)
    active: bool = True
    server_name: str | None = Field(default=None, max_length=128)
    role_name: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_observation(self) -> CashObservationIn:
        if self.occurred_at.tzinfo is None:
            raise ValueError("金额观测必须包含时区")
        if self.active and self.cash is None:
            raise ValueError("有效观测必须包含现金金额")
        if self.occurred_at > datetime.now(UTC) + timedelta(minutes=2):
            raise ValueError("金额观测时间超前，请校准从机时间")
        return self


class WindowStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=255)
    server_name: str | None = Field(default=None, max_length=128)
    role_name: str | None = Field(default=None, max_length=128)
    character_id: str | None = Field(default=None, max_length=64)
    status: str = Field(default="unknown", min_length=1, max_length=64)
    status_text: str | None = Field(default=None, max_length=128)
    empty_slots: int | None = Field(default=None, ge=0, le=20)
    occupied_slots: int | None = Field(default=None, ge=0, le=20)
    cash: int | None = Field(default=None, ge=0, le=999999999999)
    bank_money: int | None = Field(default=None, ge=0, le=999999999999)
    money_confirmed_at: datetime | None = None
    money_readable: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)
    occlusion_percent: float | None = Field(default=None, ge=0, le=100)
    minimized: bool = False
    monitoring: bool = True
    updated_at: datetime


class WindowStatusSnapshotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reported_at: datetime
    windows: list[WindowStatusIn] = Field(default_factory=list, max_length=8)


class AckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=500)


class MonitorLogIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    event_type: Literal["monitoring_started", "monitoring_paused", "panel_opened", "panel_closed", "game_default"]
    occurred_at: datetime
    reason: Literal["startup", "resume", "pause", "close", "recognition"]
    window_id: str | None = Field(default=None, max_length=128)
    server_name: str | None = Field(default=None, max_length=128)
    role_name: str | None = Field(default=None, max_length=128)
    character_id: str | None = Field(default=None, max_length=64)
    panel_type: Literal["inventory", "give", "trade", "attributes", "stall", "stall_records"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    message: str | None = Field(default=None, max_length=128)


class BarkSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    device_key: str | None = Field(default=None, min_length=8, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")
    call_enabled: bool = True
    repeat_minutes: Literal[0, 5, 10, 30, 60] = 10


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        return ""
    scheme, separator, token = authorization.partition(" ")
    return token.strip() if separator and scheme.lower() == "bearer" else ""


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest.hex()}"


def _password_matches(password: str, stored: str) -> bool:
    try:
        algorithm, raw_iterations, salt, expected = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(raw_iterations)
        ).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    database = RelayDatabase(app_settings.database_path)

    def deliver_bark(notification: dict[str, Any]) -> bool:
        if not notification or notification.get("acked_at") or not notification.get("enabled"):
            return False
        title, body = event_message(notification)
        try:
            send_bark_notification(
                str(notification["device_key"]), title=title, body=body,
                call_enabled=bool(notification.get("call_enabled")),
            )
        except BarkPushError:
            logger.exception("Bark notification failed for event %s", notification.get("server_event_id"))
            return False
        database.mark_bark_sent(int(notification["server_event_id"]))
        return True

    def deliver_bark_event(account_id: int, event_id: int) -> bool:
        notification = database.bark_notification_for_event(account_id, event_id)
        return deliver_bark(notification) if notification else False

    async def bark_reminder_loop() -> None:
        while True:
            await asyncio.sleep(30)
            for notification in database.due_bark_notifications():
                await asyncio.to_thread(deliver_bark, notification)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        database.purge_acknowledged_events(app_settings.retention_days)
        reminder_task = asyncio.create_task(bark_reminder_loop())
        try:
            yield
        finally:
            reminder_task.cancel()
            with suppress(asyncio.CancelledError):
                await reminder_task

    app = FastAPI(
        title="Hoyo Inventory Alarm Relay", version=VERSION, docs_url=None,
        redoc_url=None, openapi_url=None, lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.database = database
    app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")
    attempt_log: dict[tuple[str, str], list[float]] = {}

    def rate_limit(request: Request, bucket: str, *, limit: int, seconds: int) -> None:
        address = request.client.host if request.client else "unknown"
        key = (bucket, address)
        now = monotonic()
        recent = [timestamp for timestamp in attempt_log.get(key, []) if timestamp > now - seconds]
        if len(recent) >= limit:
            raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试")
        recent.append(now)
        attempt_log[key] = recent

    def authenticate_master(authorization: Annotated[str | None, Header()] = None) -> int:
        supplied = _bearer_token(authorization)
        if not supplied:
            raise HTTPException(status_code=401, detail="请先登录主机账号")
        account_id = database.authenticate_session(_secret_hash(supplied))
        if account_id is not None:
            return account_id
        if hmac.compare_digest(app_settings.master_token, supplied):
            return 1
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")

    def authenticate_device(
        x_device_id: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> DeviceIdentity:
        device_id = (x_device_id or "").strip()
        supplied = _bearer_token(authorization)
        if device_id and supplied:
            account_id = database.authenticate_device(device_id, _secret_hash(supplied))
            if account_id is not None:
                return DeviceIdentity(device_id, account_id)
            legacy = app_settings.device_tokens.get(device_id, "")
            if legacy and hmac.compare_digest(legacy, supplied):
                return DeviceIdentity(
                    device_id, database.register_legacy_device(device_id, _secret_hash(supplied))
                )
        raise HTTPException(status_code=401, detail="从机尚未配对或凭据已失效")

    def issue_session(account_id: int) -> tuple[str, str]:
        token = secrets.token_urlsafe(40)
        expires = (datetime.now(UTC) + timedelta(days=SESSION_DAYS)).isoformat(timespec="milliseconds")
        database.create_session(account_id, _secret_hash(token), expires)
        return token, expires

    def issue_pairing_code(account_id: int, target_device_id: str | None = None) -> tuple[str, str]:
        expires = (datetime.now(UTC) + timedelta(minutes=PAIRING_MINUTES)).isoformat(timespec="milliseconds")
        for _ in range(10):
            code = f"{secrets.randbelow(1_000_000):06d}"
            try:
                database.create_pairing_code(account_id, _secret_hash(code), expires, target_device_id)
                return code, expires
            except Exception as exc:  # unique collision is exceptionally unlikely
                if isinstance(exc, LookupError):
                    raise
        raise HTTPException(status_code=503, detail="暂时无法生成配对码，请稍后重试")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_ROOT / "index.html")

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION}

    @app.get("/api/v1/updates/latest", include_in_schema=False)
    def latest_update() -> FileResponse:
        manifest = app_settings.update_directory / "release-manifest.json"
        if not manifest.is_file():
            raise HTTPException(status_code=404, detail="暂无可用更新")
        return FileResponse(
            manifest,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/v1/updates/download/{role}/{client_version}", include_in_schema=False)
    def download_update(role: str, client_version: str) -> FileResponse:
        if role not in {"slave", "master"} or not re.fullmatch(r"V\d+\.\d+\.\d+", client_version):
            raise HTTPException(status_code=404, detail="更新安装包不存在")
        manifest_path = app_settings.update_directory / "release-manifest.json"
        try:
            document = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            package = document["packages"][role]
            if document["client_version"] != client_version:
                raise KeyError(client_version)
            filename = str(package["filename"])
            archive = (app_settings.update_directory / filename).resolve()
            archive.relative_to(app_settings.update_directory.resolve())
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=404, detail="更新安装包不存在") from exc
        if not archive.is_file():
            raise HTTPException(status_code=404, detail="更新安装包不存在")
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=filename,
            headers={"Cache-Control": "public, max-age=3600, immutable"},
        )

    @app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
    def register(payload: RegisterIn, request: Request) -> dict[str, Any]:
        rate_limit(request, "register", limit=5, seconds=3600)
        username = payload.username.strip()
        try:
            account = database.create_account(
                username, (payload.display_name or username).strip(), _password_hash(payload.password)
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        token, expires = issue_session(int(account["account_id"]))
        return {"access_token": token, "expires_at": expires, "account": account}

    @app.post("/api/v1/auth/login")
    def login(payload: CredentialsIn, request: Request) -> dict[str, Any]:
        rate_limit(request, "login", limit=30, seconds=300)
        account = database.account_by_username(payload.username.strip())
        if not account or bool(account["disabled"]) or not _password_matches(payload.password, str(account["password_hash"])):
            raise HTTPException(status_code=401, detail="账号或密码错误")
        token, expires = issue_session(int(account["id"]))
        return {
            "access_token": token,
            "expires_at": expires,
            "account": {"account_id": account["id"], "username": account["username"], "display_name": account["display_name"]},
        }

    @app.get("/api/v1/master/account")
    def current_account(account_id: int = Depends(authenticate_master)) -> dict[str, Any]:
        account = database.account_by_id(account_id)
        return {"account": account}

    @app.get("/api/v1/master/notifications/bark")
    def get_bark_settings(account_id: int = Depends(authenticate_master)) -> dict[str, Any]:
        settings_data = database.get_bark_settings(account_id)
        device_key = str(settings_data.pop("device_key", ""))
        settings_data.pop("notify_after", None)
        return {
            **settings_data,
            "configured": bool(device_key),
            "masked_key": f"••••••{device_key[-4:]}" if device_key else "",
        }

    @app.put("/api/v1/master/notifications/bark")
    def update_bark_settings(
        payload: BarkSettingsIn, account_id: int = Depends(authenticate_master)
    ) -> dict[str, Any]:
        try:
            settings_data = database.update_bark_settings(
                account_id, device_key=payload.device_key, enabled=payload.enabled,
                call_enabled=payload.call_enabled, repeat_minutes=payload.repeat_minutes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        device_key = str(settings_data.pop("device_key", ""))
        settings_data.pop("notify_after", None)
        return {
            **settings_data,
            "configured": bool(device_key),
            "masked_key": f"••••••{device_key[-4:]}" if device_key else "",
        }

    @app.post("/api/v1/master/notifications/bark/test")
    def test_bark_notification(account_id: int = Depends(authenticate_master)) -> dict[str, bool]:
        settings_data = database.get_bark_settings(account_id)
        if not settings_data["device_key"]:
            raise HTTPException(status_code=400, detail="请先保存 Bark 设备密钥")
        try:
            send_bark_notification(
                str(settings_data["device_key"]), title="背包监控 · 测试通知",
                body="Bark 手机通知已经配置成功。", call_enabled=bool(settings_data["call_enabled"]),
            )
        except BarkPushError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"sent": True}

    @app.post("/api/v1/master/pairing-codes", status_code=status.HTTP_201_CREATED)
    def create_pairing_code(
        payload: PairingCodeIn, account_id: int = Depends(authenticate_master)
    ) -> dict[str, Any]:
        try:
            code, expires = issue_pairing_code(account_id, payload.target_device_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"pairing_code": code, "expires_at": expires, "target_device_id": payload.target_device_id}

    @app.post("/api/v1/slave/pair", status_code=status.HTTP_201_CREATED)
    def pair_device(payload: PairDeviceIn, request: Request) -> dict[str, Any]:
        rate_limit(request, "pair", limit=30, seconds=300)
        device_token = secrets.token_urlsafe(40)
        result = database.consume_pairing_code(
            _secret_hash(payload.pairing_code.upper()), credential_hash=_secret_hash(device_token),
            installation_id=payload.installation_id, hostname=payload.hostname, app_version=payload.app_version,
        )
        if result is None:
            raise HTTPException(status_code=400, detail="配对码无效、已使用或已过期")
        device_id, _account_id = result
        return {"device_id": device_id, "device_token": device_token}

    @app.post("/api/v1/slave/heartbeat")
    def heartbeat(
        payload: HeartbeatIn, request: Request,
        identity: DeviceIdentity = Depends(authenticate_device),  # noqa: B008
    ) -> dict[str, str]:
        device_id = identity.device_id
        seen_at = database.upsert_device(
            device_id, hostname=payload.hostname, app_version=payload.app_version,
            ip_address=request.client.host if request.client else None,
        )
        return {"device_id": device_id, "server_time": seen_at}

    @app.post("/api/v1/slave/cash-observations", status_code=status.HTTP_202_ACCEPTED)
    def cash_observation(
        payload: CashObservationIn,
        identity: DeviceIdentity = Depends(authenticate_device),  # noqa: B008
    ) -> dict[str, Any]:
        # Acknowledge legacy clients' durable queues without creating any more
        # inferred purchase/top-up statistics. Current cash uses window status.
        return {"accepted": True, "disposition": "statistics_disabled"}

    @app.get("/api/v1/master/cash-statistics")
    def cash_statistics(
        day: date | None = None, account_id: int = Depends(authenticate_master),
    ) -> dict[str, Any]:
        return {"day": (day or datetime.now(BEIJING).date()).isoformat(),
                "timezone": "Asia/Shanghai", "daily": [], "hourly": []}

    @app.post("/api/v1/slave/status", status_code=status.HTTP_202_ACCEPTED)
    def update_status(
        payload: WindowStatusSnapshotIn, request: Request,
        identity: DeviceIdentity = Depends(authenticate_device),  # noqa: B008
    ) -> dict[str, Any]:
        device_id = identity.device_id
        database.upsert_device(device_id, hostname=None, app_version=None,
                               ip_address=request.client.host if request.client else None)
        received_at = database.replace_window_status(
            device_id, [window.model_dump(mode="json") for window in payload.windows],
            payload.reported_at.isoformat(timespec="milliseconds"),
        )
        return {"accepted": True, "device_id": device_id, "window_count": len(payload.windows), "received_at": received_at}

    @app.post("/api/v1/slave/monitor-logs", status_code=status.HTTP_202_ACCEPTED)
    def create_monitor_log(
        payload: MonitorLogIn,
        identity: DeviceIdentity = Depends(authenticate_device),  # noqa: B008
    ) -> dict[str, bool]:
        if payload.event_type in {"panel_opened", "panel_closed"} and not payload.panel_type:
            raise HTTPException(status_code=422, detail="面板日志缺少面板类型")
        database.insert_monitor_log(identity.account_id, identity.device_id, payload.model_dump(mode="json"))
        return {"accepted": True}

    @app.get("/api/v1/master/monitor-logs")
    def list_monitor_logs(
        before_id: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        account_id: int = Depends(authenticate_master),
    ) -> dict[str, Any]:
        rows = database.list_monitor_logs(account_id, before_id=before_id, limit=limit + 1)
        return {"logs": rows[:limit], "has_more": len(rows) > limit}

    @app.post("/api/v1/slave/events", status_code=status.HTTP_202_ACCEPTED)
    def create_event(
        payload: EventIn, request: Request, background_tasks: BackgroundTasks,
        identity: DeviceIdentity = Depends(authenticate_device),  # noqa: B008
    ) -> dict[str, Any]:
        device_id, account_id = identity.device_id, identity.account_id
        database.upsert_device(device_id, hostname=None, app_version=None,
                               ip_address=request.client.host if request.client else None)
        event_id, duplicate, received_at = database.insert_event(
            account_id, device_id, payload.model_dump(mode="json")
        )
        if not duplicate:
            background_tasks.add_task(deliver_bark_event, account_id, event_id)
        return {"accepted": True, "duplicate": duplicate, "server_event_id": event_id, "received_at": received_at}

    @app.get("/api/v1/master/events")
    def list_events(
        state: Literal["pending", "all"] = "pending",
        after_id: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        account_id: int = Depends(authenticate_master),
    ) -> dict[str, Any]:
        events = database.list_events(account_id, pending_only=state == "pending", after_id=after_id, limit=limit)
        return {"events": events, "count": len(events)}

    @app.post("/api/v1/master/events/{server_event_id}/ack")
    def acknowledge_event(
        server_event_id: int, _payload: AckIn,
        account_id: int = Depends(authenticate_master),
    ) -> dict[str, Any]:
        found, acknowledged_at = database.acknowledge_event(account_id, server_event_id)
        if not found:
            raise HTTPException(status_code=404, detail="event not found")
        return {"acknowledged": True, "server_event_id": server_event_id, "acknowledged_at": acknowledged_at}

    @app.get("/api/v1/master/devices")
    def list_devices(account_id: int = Depends(authenticate_master)) -> dict[str, Any]:
        devices = database.list_devices(account_id)
        return {"devices": devices, "count": len(devices)}

    @app.patch("/api/v1/master/devices/{device_id}")
    def update_device(
        device_id: str, payload: DeviceUpdateIn,
        account_id: int = Depends(authenticate_master),
    ) -> dict[str, Any]:
        changes = payload.model_dump(exclude_none=True)
        if not changes or not database.update_device(account_id, device_id, changes):
            raise HTTPException(status_code=404, detail="从机不存在或没有可修改内容")
        return {"updated": True, "device_id": device_id}

    @app.delete("/api/v1/master/devices/{device_id}")
    def remove_device(device_id: str, account_id: int = Depends(authenticate_master)) -> dict[str, Any]:
        if not database.remove_device(account_id, device_id):
            raise HTTPException(status_code=404, detail="从机不存在")
        return {"removed": True, "device_id": device_id}

    @app.post("/api/v1/master/devices/{device_id}/reissue", status_code=status.HTTP_201_CREATED)
    def reissue_device(device_id: str, account_id: int = Depends(authenticate_master)) -> dict[str, Any]:
        try:
            code, expires = issue_pairing_code(account_id, device_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"pairing_code": code, "expires_at": expires, "target_device_id": device_id}

    @app.get("/api/v1/master/status")
    def list_status(account_id: int = Depends(authenticate_master)) -> dict[str, Any]:
        devices = database.list_device_status(account_id)
        return {"devices": devices, "count": len(devices)}

    return app
