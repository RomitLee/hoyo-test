from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

MASTER_TOKEN = "master-" + "a" * 40
DEVICE_TOKEN = "device-" + "b" * 40


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_path=tmp_path / "relay.sqlite3",
        master_token=MASTER_TOKEN,
        device_tokens={"slave-01": DEVICE_TOKEN},
    )
    return TestClient(create_app(settings))


def device_headers() -> dict[str, str]:
    return {"X-Device-ID": "slave-01", "Authorization": f"Bearer {DEVICE_TOKEN}"}


def master_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {MASTER_TOKEN}"}


def test_event_round_trip_and_duplicate_is_idempotent(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        payload = {
            "event_id": "slave-01:20260929:0001",
            "event_type": "inventory_full",
            "occurred_at": "2026-09-29T08:30:00+08:00",
            "server_name": "秦淮风光",
            "role_name": "清风知夏",
            "character_id": "54588235",
            "empty_slots": 0,
            "confidence": 0.98,
            "occlusion_percent": 0,
            "minimized": False,
        }

        created = client.post("/api/v1/slave/events", headers=device_headers(), json=payload)
        assert created.status_code == 202
        assert created.json()["duplicate"] is False
        server_event_id = created.json()["server_event_id"]

        duplicate = client.post("/api/v1/slave/events", headers=device_headers(), json=payload)
        assert duplicate.status_code == 202
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["server_event_id"] == server_event_id

        pending = client.get("/api/v1/master/events", headers=master_headers())
        assert pending.status_code == 200
        assert pending.json()["count"] == 1
        assert pending.json()["events"][0]["role_name"] == "清风知夏"

        acknowledged = client.post(
            f"/api/v1/master/events/{server_event_id}/ack",
            headers=master_headers(),
            json={},
        )
        assert acknowledged.status_code == 200
        assert acknowledged.json()["acknowledged"] is True
        assert client.get("/api/v1/master/events", headers=master_headers()).json()["count"] == 0
        history = client.get("/api/v1/master/events?state=all", headers=master_headers()).json()["events"]
        assert len(history) == 1
        assert history[0]["acked_at"] is not None


def test_heartbeat_and_authentication(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        unauthorized = client.post("/api/v1/slave/heartbeat", json={})
        assert unauthorized.status_code == 401

        heartbeat = client.post(
            "/api/v1/slave/heartbeat",
            headers=device_headers(),
            json={"hostname": "HOME-PC-01", "app_version": "0.3.0"},
        )
        assert heartbeat.status_code == 200

        event = client.post(
            "/api/v1/slave/events",
            headers=device_headers(),
            json={
                "event_id": "heartbeat-metadata-check",
                "event_type": "inventory_not_open",
                "occurred_at": "2026-09-29T08:30:00+08:00",
            },
        )
        assert event.status_code == 202

        devices = client.get("/api/v1/master/devices", headers=master_headers())
        assert devices.status_code == 200
        assert devices.json()["devices"][0]["hostname"] == "HOME-PC-01"


def test_unknown_event_and_ack_are_rejected(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        invalid = client.post(
            "/api/v1/slave/events",
            headers=device_headers(),
            json={"event_id": "contains spaces", "event_type": "INVALID-TYPE", "occurred_at": "bad"},
        )
        assert invalid.status_code == 422

        missing = client.post("/api/v1/master/events/999/ack", headers=master_headers(), json={})
        assert missing.status_code == 404


def test_window_status_snapshot_is_visible_to_master(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        heartbeat = client.post(
            "/api/v1/slave/heartbeat",
            headers=device_headers(),
            json={"hostname": "SLAVE-01", "app_version": "0.4.0"},
        )
        assert heartbeat.status_code == 200
        snapshot = client.post(
            "/api/v1/slave/status",
            headers=device_headers(),
            json={
                "reported_at": "2026-09-29T08:30:00+08:00",
                "windows": [
                    {
                        "window_id": "hwnd:1001",
                        "title": "梦幻西游 秦淮风光 清风知夏",
                        "server_name": "秦淮风光",
                        "role_name": "清风知夏",
                        "character_id": "54588235",
                        "status": "full",
                        "status_text": "背包已满",
                        "empty_slots": 0,
                        "occupied_slots": 20,
                        "confidence": 0.98,
                        "occlusion_percent": 0,
                        "minimized": False,
                        "monitoring": True,
                        "updated_at": "2026-09-29T08:30:00+08:00",
                    },
                    {
                        "window_id": "hwnd:1002",
                        "server_name": "兰亭序",
                        "role_name": "小号",
                        "status": "waiting",
                        "status_text": "等待检测",
                        "minimized": True,
                        "monitoring": True,
                        "updated_at": "2026-09-29T08:30:00+08:00",
                    },
                ],
            },
        )
        assert snapshot.status_code == 202
        assert snapshot.json()["window_count"] == 2

        status = client.get("/api/v1/master/status", headers=master_headers())
        assert status.status_code == 200
        device = status.json()["devices"][0]
        assert len(device["windows"]) == 2
        assert device["windows"][0]["status"] == "full"
        assert device["windows"][1]["minimized"] is True

        replacement = client.post(
            "/api/v1/slave/status",
            headers=device_headers(),
            json={
                "reported_at": "2026-09-29T08:31:00+08:00",
                "windows": [{
                    "window_id": "hwnd:1001",
                    "status": "not_full",
                    "status_text": "背包未满",
                    "updated_at": "2026-09-29T08:31:00+08:00",
                }],
            },
        )
        assert replacement.status_code == 202
        assert len(client.get("/api/v1/master/status", headers=master_headers()).json()["devices"][0]["windows"]) == 1
