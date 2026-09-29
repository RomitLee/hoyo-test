"""HTTP API for relaying slave alarms to a single master application."""

import hmac
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .database import RelayDatabase

VERSION = "1.0.0"


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


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        return ""
    scheme, separator, token = authorization.partition(" ")
    if separator and scheme.lower() == "bearer":
        return token.strip()
    return ""


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    database = RelayDatabase(app_settings.database_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        database.purge_acknowledged_events(app_settings.retention_days)
        yield

    app = FastAPI(
        title="Hoyo Inventory Alarm Relay",
        version=VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.database = database

    def authenticate_device(
        x_device_id: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> str:
        device_id = (x_device_id or "").strip()
        expected = app_settings.device_tokens.get(device_id, "")
        supplied = _bearer_token(authorization)
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid device credentials")
        return device_id

    def authenticate_master(authorization: Annotated[str | None, Header()] = None) -> None:
        supplied = _bearer_token(authorization)
        if not supplied or not hmac.compare_digest(app_settings.master_token, supplied):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid master credentials")

    @app.get("/")
    def index() -> dict[str, str]:
        return {"service": "hoyo-inventory-alarm-relay", "version": VERSION}

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION}

    @app.post("/api/v1/slave/heartbeat")
    def heartbeat(
        payload: HeartbeatIn,
        request: Request,
        device_id: Annotated[str, Depends(authenticate_device)],
    ) -> dict[str, str]:
        seen_at = database.upsert_device(
            device_id,
            hostname=payload.hostname,
            app_version=payload.app_version,
            ip_address=request.client.host if request.client else None,
        )
        return {"device_id": device_id, "server_time": seen_at}

    @app.post("/api/v1/slave/status", status_code=status.HTTP_202_ACCEPTED)
    def update_status(
        payload: WindowStatusSnapshotIn,
        request: Request,
        device_id: Annotated[str, Depends(authenticate_device)],
    ) -> dict[str, Any]:
        database.upsert_device(
            device_id,
            hostname=None,
            app_version=None,
            ip_address=request.client.host if request.client else None,
        )
        received_at = database.replace_window_status(
            device_id,
            [window.model_dump(mode="json") for window in payload.windows],
        )
        return {"accepted": True, "device_id": device_id, "window_count": len(payload.windows), "received_at": received_at}

    @app.post("/api/v1/slave/events", status_code=status.HTTP_202_ACCEPTED)
    def create_event(
        payload: EventIn,
        request: Request,
        device_id: Annotated[str, Depends(authenticate_device)],
    ) -> dict[str, Any]:
        database.upsert_device(
            device_id,
            hostname=None,
            app_version=None,
            ip_address=request.client.host if request.client else None,
        )
        normalized = payload.model_dump(mode="json")
        server_event_id, duplicate, received_at = database.insert_event(device_id, normalized)
        return {
            "accepted": True,
            "duplicate": duplicate,
            "server_event_id": server_event_id,
            "received_at": received_at,
        }

    @app.get("/api/v1/master/events")
    def list_events(
        _: Annotated[None, Depends(authenticate_master)],
        state: Literal["pending", "all"] = "pending",
        after_id: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        events = database.list_events(pending_only=state == "pending", after_id=after_id, limit=limit)
        return {"events": events, "count": len(events)}

    @app.post("/api/v1/master/events/{server_event_id}/ack")
    def acknowledge_event(
        server_event_id: int,
        _payload: AckIn,
        _: Annotated[None, Depends(authenticate_master)],
    ) -> dict[str, Any]:
        found, acknowledged_at = database.acknowledge_event(server_event_id)
        if not found:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="event not found")
        return {"acknowledged": True, "server_event_id": server_event_id, "acknowledged_at": acknowledged_at}

    @app.get("/api/v1/master/devices")
    def list_devices(_: Annotated[None, Depends(authenticate_master)]) -> dict[str, Any]:
        devices = database.list_devices()
        return {"devices": devices, "count": len(devices)}

    @app.get("/api/v1/master/status")
    def list_status(_: Annotated[None, Depends(authenticate_master)]) -> dict[str, Any]:
        devices = database.list_device_status()
        return {"devices": devices, "count": len(devices)}

    return app
