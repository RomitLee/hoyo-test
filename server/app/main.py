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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .bark import BarkPushError, event_message, send_bark_notification
from .cash_ledger import BEIJING
from .config import Settings
from .database import RelayDatabase
from .screenshots import MAX_SCREENSHOT_BYTES, VIEW_URL_EXPIRES_SECONDS, ScreenshotStore, validate_jpeg

VERSION = "2.22.0"
WEB_ROOT = Path(__file__).parent / "web"
ADMIN_ROOT = Path(__file__).parent / "web-admin"
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
    protocol_version: int | None = Field(default=None, ge=1, le=65535, strict=True)


class DeviceLoginIn(CredentialsIn):
    installation_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    hostname: str | None = Field(default=None, max_length=255)
    app_version: str | None = Field(default=None, max_length=64)
    protocol_version: int | None = Field(default=None, ge=1, le=65535, strict=True)
    machine_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class DeviceMachineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    machine_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    hostname: str | None = Field(default=None, max_length=255)


class DeviceUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=200)
    disabled: bool | None = None


class DeviceNameIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    display_name: str = Field(min_length=1, max_length=64)


class MonitorLogSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    occlusion_enabled: bool = True
    occlusion_percent: float = Field(default=60, ge=0, le=100)
    occlusion_seconds: int = Field(default=5, ge=0, le=3600, strict=True)


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
    protocol_version: int | None = Field(default=None, ge=1, le=65535, strict=True)


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
    monitor_session_id: str | None = Field(default=None, min_length=16, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    stall_panel_type: Literal["stall", "stall_buy"] | None = None
    sell_stock_count: int | None = Field(default=None, ge=0, le=20, strict=True)
    status: str = Field(default="unknown", min_length=1, max_length=64)
    status_text: str | None = Field(default=None, max_length=128)
    empty_slots: int | None = Field(default=None, ge=0, le=20)
    occupied_slots: int | None = Field(default=None, ge=0, le=20)
    cash: int | None = Field(default=None, ge=0, le=999999999999)
    bank_money: int | None = Field(default=None, ge=0, le=999999999999)
    money_confirmed_at: datetime | None = None
    money_readable: bool = False
    stamina_current: int | None = Field(default=None, ge=0, le=99999, strict=True)
    stamina_max: int | None = Field(default=None, ge=1, le=99999, strict=True)
    character_level: int | None = Field(default=None, ge=1, le=175, strict=True)
    stamina_observed_at: datetime | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    occlusion_percent: float | None = Field(default=None, ge=0, le=100)
    minimized: bool = False
    monitoring: bool = True
    updated_at: datetime


class WindowStatusSnapshotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reported_at: datetime
    windows: list[WindowStatusIn] = Field(default_factory=list, max_length=8)


class LiveSnapshotSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_minutes: int = Field(ge=1, le=60, strict=True)


class LiveSnapshotUploadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_id: str = Field(min_length=1, max_length=128)
    captured_at: datetime
    request_id: str | None = Field(default=None, max_length=64)
    capture_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class LiveSnapshotWindowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_id: str = Field(min_length=1, max_length=128)


class LiveSnapshotPermissionIn(LiveSnapshotWindowIn):
    capture_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class AckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=500)


class MonitorLogIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    event_type: Literal["monitoring_started", "monitoring_paused", "window_monitored", "panel_opened", "panel_closed", "game_default", "panel_target_changed"]
    occurred_at: datetime
    reason: Literal["startup", "resume", "pause", "close", "recognition"]
    window_id: str | None = Field(default=None, max_length=128)
    server_name: str | None = Field(default=None, max_length=128)
    role_name: str | None = Field(default=None, max_length=128)
    character_id: str | None = Field(default=None, max_length=64)
    monitor_session_id: str | None = Field(default=None, min_length=16, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    panel_type: Literal["inventory", "give", "trade", "attributes", "stall", "stall_buy", "stall_records"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    message: str | None = Field(default=None, max_length=128)
    target_name: str | None = Field(default=None, min_length=1, max_length=16)
    target_level: int | None = Field(default=None, ge=1, le=175, strict=True)
    target_confidence: float | None = Field(default=None, ge=0, le=1)
    target_status: Literal["recognized", "unrecognized"] | None = None
    stamina_current: int | None = Field(default=None, ge=0, le=99999, strict=True)
    stamina_max: int | None = Field(default=None, ge=1, le=99999, strict=True)
    character_level: int | None = Field(default=None, ge=1, le=175, strict=True)
    stamina_observed_at: datetime | None = None


class BarkSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    device_key: str | None = Field(default=None, min_length=8, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")
    call_enabled: bool = True
    repeat_minutes: Literal[0, 5, 10, 30, 60] = 10
    local_alert_enabled: bool | None = None
    web_sound_enabled: bool | None = None
    capacity_threshold: int | None = Field(default=None, ge=0, le=20, strict=True)
    cash_threshold_wan: int | None = Field(default=None, ge=1, le=99999999, strict=True)
    stamina_full_enabled: bool | None = None
    stall_sold_enabled: bool | None = None


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
    app_screenshots = ScreenshotStore(app_settings)

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

    async def history_cleanup_loop() -> None:
        while True:
            await asyncio.sleep(24 * 3600)
            await asyncio.to_thread(database.purge_live_snapshot_history)

    async def stamina_alert_loop() -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await asyncio.to_thread(database.check_stamina_full_alerts)
            except Exception:
                logger.exception("Stamina alert evaluation failed")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        database.backfill_closed_windows()
        database.purge_acknowledged_events(app_settings.retention_days)
        database.purge_live_snapshot_history()
        reminder_task = asyncio.create_task(bark_reminder_loop())
        cleanup_task = asyncio.create_task(history_cleanup_loop())
        stamina_task = asyncio.create_task(stamina_alert_loop())
        try:
            yield
        finally:
            reminder_task.cancel()
            cleanup_task.cancel()
            stamina_task.cancel()
            with suppress(asyncio.CancelledError):
                await reminder_task
            with suppress(asyncio.CancelledError):
                await cleanup_task
            with suppress(asyncio.CancelledError):
                await stamina_task

    app = FastAPI(
        title="Hoyo Inventory Alarm Relay", version=VERSION, docs_url=None,
        redoc_url=None, openapi_url=None, lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.database = database
    app.state.screenshots = app_screenshots
    app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")
    app.mount("/assets", StaticFiles(directory=ADMIN_ROOT / "assets"), name="admin-assets")
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
        return FileResponse(ADMIN_ROOT / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/legacy", include_in_schema=False)
    def legacy_index() -> FileResponse:
        return FileResponse(WEB_ROOT / "index.html")

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION}

    @app.get("/api/v1/updates/latest", include_in_schema=False)
    def latest_update() -> FileResponse:
        manifest = app_settings.update_directory / "legacy-release-manifest.json"
        if not manifest.is_file():
            manifest = app_settings.update_directory / "release-manifest.json"
        if not manifest.is_file():
            raise HTTPException(status_code=404, detail="暂无可用更新")
        return FileResponse(
            manifest,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/v1/updates/latest/{role}", include_in_schema=False)
    def role_update(role: str) -> FileResponse:
        if role not in {"master", "slave"}:
            raise HTTPException(status_code=404, detail="程序类型不存在")
        manifest = app_settings.update_directory / f"release-manifest-{role}.json"
        if not manifest.is_file():
            raise HTTPException(status_code=404, detail="暂无该程序的独立更新")
        return FileResponse(manifest, media_type="application/json", headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/updates/download/{role}/{client_version}", include_in_schema=False)
    def download_update(role: str, client_version: str) -> FileResponse:
        if role not in {"slave", "master"} or not re.fullmatch(r"V\d+\.\d+\.\d+", client_version):
            raise HTTPException(status_code=404, detail="更新安装包不存在")
        try:
            package = None
            for name in (f"release-manifest-{role}.json", "legacy-release-manifest.json", "release-manifest.json",
                         f"history/{role}/{client_version}/release-manifest.json"):
                manifest_path = app_settings.update_directory / name
                if not manifest_path.is_file():
                    continue
                document = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                if document.get("client_version") != client_version:
                    continue
                if document.get("schema_version") == 3 and document.get("release_role") != role:
                    continue
                candidate = document["packages"][role]
                if candidate.get("client_version", client_version) != client_version:
                    continue
                package = candidate
                break
            if package is None:
                raise KeyError(client_version)
            filename = str(package["filename"])
            archive_root = manifest_path.parent if name.startswith("history/") else app_settings.update_directory
            if name == "legacy-release-manifest.json" and (archive_root / "legacy").is_dir():
                archive_root = archive_root / "legacy"
            archive = (archive_root / filename).resolve()
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
                local_alert_enabled=payload.local_alert_enabled,
                web_sound_enabled=payload.web_sound_enabled,
                capacity_threshold=payload.capacity_threshold,
                cash_threshold_wan=payload.cash_threshold_wan,
                stamina_full_enabled=payload.stamina_full_enabled,
                stall_sold_enabled=payload.stall_sold_enabled,
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
        database.set_device_protocol(device_id, payload.protocol_version)
        profile = database.device_profile(_account_id, device_id)
        return {"device_id": device_id, "device_token": device_token, **profile}

    @app.post("/api/v1/slave/login", status_code=status.HTTP_201_CREATED)
    def login_device(payload: DeviceLoginIn, request: Request) -> dict[str, Any]:
        rate_limit(request, "slave_login", limit=15, seconds=300)
        account = database.account_by_username(payload.username.strip())
        if not account or bool(account["disabled"]) or not _password_matches(payload.password, str(account["password_hash"])):
            raise HTTPException(status_code=401, detail="账号或密码错误")
        device_token = secrets.token_urlsafe(40)
        try:
            device_id = database.login_device(
                int(account["id"]), payload.installation_id, _secret_hash(device_token),
                payload.hostname, payload.app_version, payload.protocol_version, payload.machine_id,
            )
            if payload.machine_id:
                device_id = database.bind_machine(int(account["id"]), device_id,
                                                  payload.machine_id, payload.hostname)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        profile = database.device_profile(int(account["id"]), device_id)
        return {"device_id": device_id, "device_token": device_token,
                "account_username": account["username"], **profile}

    @app.get("/api/v1/slave/auth-state")
    def device_auth_state(identity: DeviceIdentity = Depends(authenticate_device)) -> dict[str, Any]:  # noqa: B008
        account = database.account_by_id(identity.account_id)
        return {"device_id": identity.device_id, "account_username": account["username"] if account else "",
                **database.device_profile(identity.account_id, identity.device_id)}

    @app.post("/api/v1/slave/auth-state")
    def bind_device_machine(payload: DeviceMachineIn,
                            identity: DeviceIdentity = Depends(authenticate_device)) -> dict[str, Any]:  # noqa: B008
        try:
            device_id = database.bind_machine(identity.account_id, identity.device_id,
                                              payload.machine_id, payload.hostname)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        account = database.account_by_id(identity.account_id)
        return {"device_id": device_id, "account_username": account["username"] if account else "",
                **database.device_profile(identity.account_id, device_id)}

    @app.post("/api/v1/slave/profile")
    def rename_self(
        payload: DeviceNameIn,
        identity: DeviceIdentity = Depends(authenticate_device),  # noqa: B008
    ) -> dict[str, Any]:
        if not database.update_device(identity.account_id, identity.device_id, payload.model_dump()):
            raise HTTPException(status_code=404, detail="从机不存在")
        return database.device_profile(identity.account_id, identity.device_id)

    @app.post("/api/v1/slave/heartbeat")
    def heartbeat(
        payload: HeartbeatIn, request: Request,
        identity: DeviceIdentity = Depends(authenticate_device),  # noqa: B008
    ) -> dict[str, Any]:
        device_id = identity.device_id
        seen_at = database.upsert_device(
            device_id, hostname=payload.hostname, app_version=payload.app_version,
            ip_address=request.client.host if request.client else None,
        )
        database.set_device_protocol(device_id, payload.protocol_version)
        return {"device_id": device_id, "server_time": seen_at,
                **database.device_profile(identity.account_id, device_id)}

    @app.post("/api/v1/slave/local-alerts/{event_id}/ack")
    def acknowledge_local_alert(
        event_id: int, identity: DeviceIdentity = Depends(authenticate_device),  # noqa: B008
    ) -> dict[str, bool]:
        if not database.acknowledge_local_alert(identity.account_id, identity.device_id, event_id):
            raise HTTPException(status_code=404, detail="告警不存在")
        return {"acknowledged": True}

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
        for window in payload.windows:
            if (window.sell_stock_count is not None and window.stall_panel_type != "stall"
                    or window.stall_panel_type is not None and window.monitor_session_id is None):
                raise HTTPException(status_code=422, detail="摊位商品状态缺少有效监控会话或卖货面板")
        database.upsert_device(device_id, hostname=None, app_version=None,
                               ip_address=request.client.host if request.client else None)
        received_at = database.replace_window_status(
            device_id, [window.model_dump(mode="json") for window in payload.windows],
            payload.reported_at.isoformat(timespec="milliseconds"),
        )
        return {"accepted": True, "device_id": device_id, "window_count": len(payload.windows),
                "received_at": received_at, **database.device_profile(identity.account_id, device_id)}

    @app.post("/api/v1/slave/monitor-logs", status_code=status.HTTP_202_ACCEPTED)
    def create_monitor_log(
        payload: MonitorLogIn,
        identity: DeviceIdentity = Depends(authenticate_device),  # noqa: B008
    ) -> dict[str, bool]:
        if payload.event_type in {"panel_opened", "panel_closed"} and not payload.panel_type:
            raise HTTPException(status_code=422, detail="面板日志缺少面板类型")
        if payload.event_type == "panel_target_changed" and payload.panel_type not in {"give", "trade"}:
            raise HTTPException(status_code=422, detail="对象日志仅支持给予和交易面板")
        if payload.event_type == "panel_target_changed" and payload.target_status is None:
            raise HTTPException(status_code=422, detail="对象日志缺少识别状态")
        if any(value is not None for value in (payload.target_status, payload.target_name, payload.target_level, payload.target_confidence)):
            if payload.event_type not in {"panel_opened", "panel_target_changed"} or payload.panel_type not in {"give", "trade"}:
                raise HTTPException(status_code=422, detail="对象信息仅支持给予和交易面板")
            if payload.target_status == "recognized":
                if not payload.target_name or payload.target_level is None or any(c.isspace() or not c.isprintable() for c in payload.target_name):
                    raise HTTPException(status_code=422, detail="对象昵称与等级必须同时确认")
            elif (payload.target_status != "unrecognized" or payload.target_name is not None
                  or payload.target_level is not None or payload.target_confidence is not None):
                raise HTTPException(status_code=422, detail="未识别对象不能携带旧昵称或等级")
        stamina_fields = (payload.stamina_current, payload.stamina_max, payload.character_level, payload.stamina_observed_at)
        if any(value is not None for value in stamina_fields):
            if payload.event_type != "panel_opened" or payload.panel_type != "attributes" or any(value is None for value in stamina_fields):
                raise HTTPException(status_code=422, detail="体力信息仅支持完整的属性面板观测")
            if payload.stamina_current > payload.stamina_max:
                raise HTTPException(status_code=422, detail="当前体力不能超过体力上限")
        database.insert_monitor_log(identity.account_id, identity.device_id, payload.model_dump(mode="json"))
        return {"accepted": True}

    @app.put("/api/v1/slave/monitor-logs/{event_id}/screenshot")
    async def upload_monitor_screenshot(event_id: str, request: Request,
            identity: DeviceIdentity = Depends(authenticate_device)):  # noqa: B008
        row = database.screenshot_log(identity.account_id, identity.device_id, event_id)
        if not row:
            raise HTTPException(status_code=404, detail="对应监控日志尚未接收")
        if (row["panel_type"] not in {"give", "trade", "attributes", "stall", "stall_buy"}
                or row["event_type"] not in {"panel_opened", "panel_target_changed"}
                or row["panel_type"] in {"attributes", "stall", "stall_buy"} and row["event_type"] != "panel_opened"):
            raise HTTPException(status_code=422, detail="该面板日志不支持上传截图")
        if not app.state.screenshots.configured:
            raise HTTPException(status_code=503, detail="截图存储暂未配置")
        if request.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            raise HTTPException(status_code=415, detail="仅支持 JPEG 截图")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_SCREENSHOT_BYTES:
                raise HTTPException(status_code=413, detail="截图不能超过 2MB")
        try:
            await asyncio.to_thread(validate_jpeg, bytes(data))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        def save():
            token = secrets.token_hex(16)
            state, key = database.claim_screenshot(identity.account_id, row["id"], token)
            if state == "busy":
                raise HTTPException(status_code=409, detail="截图正在上传，请稍后重试")
            if state == "ready":
                return {"accepted": True, "duplicate": True}
            try:
                app.state.screenshots.put(key, bytes(data))
            except Exception:  # noqa: BLE001 - prevent SDK request credentials from reaching responses
                database.finish_screenshot(row["id"], token, success=False)
                # SDK exception strings may contain signed requests; never expose them.
                raise HTTPException(status_code=503, detail="截图存储暂不可用，请稍后重试") from None
            database.finish_screenshot(row["id"], token, success=True)
            return {"accepted": True}
        return await asyncio.to_thread(save)

    @app.get("/api/v1/master/monitor-logs/{log_id}/screenshot")
    def view_monitor_screenshot(log_id: int, account_id: int = Depends(authenticate_master)):
        row = database.get_screenshot(account_id, log_id)
        if not row:
            raise HTTPException(status_code=404, detail="暂无截图或尚未上传完成")
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            raise HTTPException(status_code=410, detail="截图已超过30天保留期")
        if not app.state.screenshots.configured:
            raise HTTPException(status_code=503, detail="截图存储暂未配置")
        try:
            return JSONResponse({"url": app.state.screenshots.signed_url(row["object_key"]), "expires_in": VIEW_URL_EXPIRES_SECONDS},
                                headers={"Cache-Control": "no-store"})
        except Exception:  # noqa: BLE001 - prevent SDK request credentials from reaching responses
            raise HTTPException(status_code=503, detail="暂时无法读取截图，请稍后重试") from None

    @app.get("/api/v1/master/monitor-log-settings")
    def get_monitor_log_settings(account_id: int = Depends(authenticate_master)) -> dict[str, Any]:
        return database.get_monitor_log_settings(account_id)

    @app.put("/api/v1/master/monitor-log-settings")
    def set_monitor_log_settings(payload: MonitorLogSettingsIn, account_id: int = Depends(authenticate_master)) -> dict[str, Any]:
        return database.update_monitor_log_settings(account_id, payload.model_dump())

    @app.get("/api/v1/master/monitor-logs")
    def list_monitor_logs(
        before_id: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        event_type: Annotated[str, Query(max_length=64)] = "",
        panel_type: Annotated[str, Query(max_length=64)] = "",
        account_id: int = Depends(authenticate_master),
    ) -> dict[str, Any]:
        rows = database.list_monitor_logs(account_id, before_id=before_id, limit=limit + 1,
                                          event_type=event_type, panel_type=panel_type)
        return {"logs": rows[:limit], "has_more": len(rows) > limit,
                "filters": {"event_type": event_type, "panel_type": panel_type}}

    @app.post("/api/v1/slave/events", status_code=status.HTTP_202_ACCEPTED)
    def create_event(
        payload: EventIn, request: Request, background_tasks: BackgroundTasks,
        identity: DeviceIdentity = Depends(authenticate_device),  # noqa: B008
    ) -> dict[str, Any]:
        device_id, account_id = identity.device_id, identity.account_id
        if payload.event_type == "cash_low":
            cash = payload.details.get("cash")
            threshold_wan = payload.details.get("threshold_wan")
            if (not isinstance(cash, int) or isinstance(cash, bool) or cash < 0
                    or not isinstance(threshold_wan, int) or isinstance(threshold_wan, bool)
                    or not 1 <= threshold_wan <= 99999999 or cash >= threshold_wan * 10000):
                raise HTTPException(status_code=422, detail="游戏币低额告警数据无效")
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
        latest: bool = False,
        account_id: int = Depends(authenticate_master),
    ) -> dict[str, Any]:
        events = database.list_events(account_id, pending_only=state == "pending", after_id=after_id,
                                      limit=limit, latest=latest)
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
        for device in devices:
            for window in device["windows"]:
                shot = database.live_snapshot(account_id, device["device_id"], window["window_id"])
                window["screenshot_at"] = shot["captured_at"] if shot else None
        return {"devices": devices, "roles": database.list_game_roles(account_id, devices), "count": len(devices)}

    def live_key(identity: DeviceIdentity, window_id: str, capture_id: str | None = None) -> str:
        device_hash = hashlib.sha256(identity.device_id.encode()).hexdigest()[:24]
        window_hash = hashlib.sha256(window_id.encode()).hexdigest()[:24]
        base = f"screenshots/live/{identity.account_id}/{device_hash}/{window_hash}"
        return f"{base}/{capture_id}.jpg" if capture_id else f"{base}.jpg"

    @app.get("/api/v1/master/live-screenshot-settings")
    def get_live_settings(account_id: int = Depends(authenticate_master)) -> dict[str, int]:
        return {"interval_minutes": database.snapshot_interval(account_id)}

    @app.put("/api/v1/master/live-screenshot-settings")
    def put_live_settings(payload: LiveSnapshotSettingsIn, account_id: int = Depends(authenticate_master)) -> dict[str, int]:
        return {"interval_minutes": database.set_snapshot_interval(account_id, payload.interval_minutes)}

    @app.get("/api/v1/slave/live-screenshots/control")
    def live_control(identity: DeviceIdentity = Depends(authenticate_device)) -> dict[str, Any]:  # noqa: B008
        return {"interval_minutes": database.snapshot_interval(identity.account_id),
                "requests": database.snapshot_requests(identity.device_id)}

    @app.post("/api/v1/slave/live-screenshots/permission")
    def live_permission(payload: LiveSnapshotPermissionIn, identity: DeviceIdentity = Depends(authenticate_device)) -> dict[str, Any]:  # noqa: B008
        if not database.snapshot_window(identity.account_id, identity.device_id, payload.window_id):
            raise HTTPException(status_code=404, detail="该窗口未在监控中")
        if not app.state.screenshots.configured:
            raise HTTPException(status_code=503, detail="截图存储未配置")
        key = live_key(identity, payload.window_id, payload.capture_id)
        try:
            url = app.state.screenshots.signed_put_url(key)
        except Exception:  # noqa: BLE001 - do not expose signing credentials
            raise HTTPException(status_code=503, detail="暂时无法签发上传许可") from None
        return {"url": url, "expires_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                "window_id": payload.window_id}

    @app.post("/api/v1/slave/live-screenshots/complete")
    def live_complete(payload: LiveSnapshotUploadIn, identity: DeviceIdentity = Depends(authenticate_device)) -> dict[str, Any]:  # noqa: B008
        if not database.snapshot_window(identity.account_id, identity.device_id, payload.window_id):
            raise HTTPException(status_code=404, detail="该窗口未在监控中")
        if payload.captured_at.tzinfo is None or abs((datetime.now(UTC) - payload.captured_at).total_seconds()) > 120:
            raise HTTPException(status_code=422, detail="截图时间无效")
        if payload.request_id:
            request = database.snapshot_request(identity.account_id, payload.request_id)
            if not request or request["device_id"] != identity.device_id or request["window_id"] != payload.window_id or request["completed_at"]:
                raise HTTPException(status_code=409, detail="实时截图请求已失效")
        key = live_key(identity, payload.window_id, payload.capture_id)
        try:
            app.state.screenshots.exists(key)
        except Exception:  # noqa: BLE001 - COS failure is an unavailable object
            raise HTTPException(status_code=409, detail="COS 中尚未找到上传的截图") from None
        database.save_live_snapshot(identity.device_id, payload.window_id, key, payload.captured_at.isoformat(), payload.request_id,
                                    keep_history=bool(payload.capture_id))
        return {"accepted": True}

    @app.post("/api/v1/master/live-screenshots/{device_id}/request")
    def request_live_screenshot(device_id: str, payload: LiveSnapshotWindowIn,
                                account_id: int = Depends(authenticate_master)) -> dict[str, str]:
        row = database.snapshot_window(account_id, device_id, payload.window_id)
        if not row:
            raise HTTPException(status_code=404, detail="该游戏窗口当前不在监控中")
        if row["disabled"] or row["minimized"] or not row["monitoring"] or datetime.now(UTC) - datetime.fromisoformat(row["last_seen_at"]) > timedelta(seconds=45):
            raise HTTPException(status_code=409, detail="从机离线或窗口已最小化，无法获取实时截图")
        request_id = secrets.token_urlsafe(18)
        database.create_snapshot_request(account_id, device_id, payload.window_id, request_id)
        return {"request_id": request_id}

    @app.get("/api/v1/master/live-screenshots/requests/{request_id}")
    def get_live_request(request_id: str, account_id: int = Depends(authenticate_master)) -> dict[str, Any]:
        row = database.snapshot_request(account_id, request_id)
        if not row:
            raise HTTPException(status_code=404, detail="实时截图请求不存在")
        state = "complete" if row["completed_at"] and not row["error"] else "failed" if row["error"] else "expired" if datetime.now(UTC) > datetime.fromisoformat(row["expires_at"]) else "pending"
        return {"state": state, "error": row["error"], "device_id": row["device_id"], "window_id": row["window_id"]}

    @app.get("/api/v1/master/live-screenshots/{device_id}/view")
    def view_live_screenshot(device_id: str, window_id: str = Query(min_length=1, max_length=128),
                             account_id: int = Depends(authenticate_master)) -> dict[str, Any]:
        row = database.live_snapshot(account_id, device_id, window_id)
        if not row:
            raise HTTPException(status_code=404, detail="该窗口尚无截图")
        try:
            url = app.state.screenshots.signed_url(row["object_key"])
        except Exception:  # noqa: BLE001 - do not expose signing credentials
            raise HTTPException(status_code=503, detail="暂时无法读取截图") from None
        return JSONResponse({"url": url, "captured_at": row["captured_at"], "expires_in": VIEW_URL_EXPIRES_SECONDS}, headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/master/live-screenshots/history")
    def live_history(character_id: str = Query(pattern=r"^\d{1,64}$"), before: int | None = Query(default=None, ge=1),
                     limit: int = Query(default=30, ge=1, le=100),
                     account_id: int = Depends(authenticate_master)) -> dict[str, Any]:
        rows = database.live_snapshot_history(account_id, character_id, before=before, limit=limit + 1)
        return {"items": rows[:limit], "has_more": len(rows) > limit,
                "total": database.count_live_snapshot_history(account_id, character_id)}

    @app.get("/api/v1/master/live-screenshots/history/{snapshot_id}/view")
    def view_historical_snapshot(snapshot_id: int, account_id: int = Depends(authenticate_master)) -> dict[str, Any]:
        row = database.historical_snapshot(account_id, snapshot_id)
        if not row:
            raise HTTPException(status_code=404, detail="历史截图不存在或已过期")
        try:
            url = app.state.screenshots.signed_url(row["object_key"])
        except Exception:  # noqa: BLE001 - do not expose COS signing details
            raise HTTPException(status_code=503, detail="暂时无法读取截图") from None
        return JSONResponse({"url": url, "captured_at": row["captured_at"], "expires_in": VIEW_URL_EXPIRES_SECONDS},
                            headers={"Cache-Control": "no-store"})

    return app
