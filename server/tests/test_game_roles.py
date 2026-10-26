"""A game account has one card even when it moves between slave machines."""

import sqlite3
from datetime import UTC, datetime

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

MASTER = {"Authorization": "Bearer " + "m" * 40}
SLAVE_A = {"Authorization": "Bearer " + "a" * 40, "X-Device-ID": "slave-a"}
SLAVE_B = {"Authorization": "Bearer " + "b" * 40, "X-Device-ID": "slave-b"}


def test_role_is_global_and_history_spans_devices(tmp_path):
    path = tmp_path / "relay.sqlite3"
    app = create_app(Settings(path, "m" * 40, {"slave-a": "a" * 40, "slave-b": "b" * 40}))
    with TestClient(app) as client:
        now = datetime.now(UTC).isoformat()
        for headers, window_id in ((SLAVE_A, "hwnd:1"), (SLAVE_B, "hwnd:9")):
            assert client.post("/api/v1/slave/status", headers=headers, json={
                "reported_at": now, "windows": [{"window_id": window_id, "character_id": "12345",
                    "role_name": "商人", "status": "not_full", "monitoring": True, "updated_at": now}],
            }).status_code == 202
            app.state.database.save_live_snapshot(headers["X-Device-ID"], window_id,
                f"screenshots/{window_id}.jpg", now, None, keep_history=True)
        status = client.get("/api/v1/master/status", headers=MASTER).json()
        assert len(status["devices"]) == 2
        assert len(status["roles"]) == 1
        assert status["roles"][0]["character_id"] == "12345"
        assert status["roles"][0]["role_online"] is True
        history = client.get("/api/v1/master/live-screenshots/history?character_id=12345", headers=MASTER).json()
        assert {shot["device_id"] for shot in history["items"]} == {"slave-a", "slave-b"}
        assert client.post("/api/v1/slave/status", headers=SLAVE_B,
            json={"reported_at": now, "windows": []}).status_code == 202
        status = client.get("/api/v1/master/status", headers=MASTER).json()
        assert len(status["roles"]) == 1
        assert status["roles"][0]["device_id"] == "slave-a"
        assert status["roles"][0]["role_online"] is True
        assert client.post("/api/v1/slave/monitor-logs", headers=SLAVE_B, json={
            "event_id": "other-role", "event_type": "window_monitored", "reason": "startup",
            "occurred_at": now, "window_id": "hwnd:7", "character_id": "77777",
        }).status_code == 202
        ordered = client.get("/api/v1/master/status", headers=MASTER).json()["roles"]
        assert [role["character_id"] for role in ordered] == ["12345", "77777"]
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM game_roles WHERE character_id='12345'").fetchone()[0] == 1


def test_first_sight_log_keeps_role_without_status_or_screenshot(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {"slave-a": "a" * 40}))
    with TestClient(app) as client:
        response = client.post("/api/v1/slave/monitor-logs", headers=SLAVE_A, json={
            "event_id": "first-sight", "event_type": "window_monitored", "reason": "startup",
            "occurred_at": datetime.now(UTC).isoformat(), "window_id": "hwnd:1",
            "character_id": "99887", "role_name": "短暂监控",
        })
        assert response.status_code == 202
        roles = client.get("/api/v1/master/status", headers=MASTER).json()["roles"]
        assert len(roles) == 1 and roles[0]["character_id"] == "99887"
        assert roles[0]["role_online"] is False
