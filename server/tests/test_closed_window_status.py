import sqlite3
from datetime import UTC, datetime

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

MASTER = {"Authorization": "Bearer " + "m" * 40}
SLAVE = {"Authorization": "Bearer " + "s" * 40, "X-Device-ID": "slave"}


class ScreenshotStore:
    configured = True

    @staticmethod
    def signed_url(key):
        return "https://screenshots.example.com/" + key


def test_closed_game_window_stays_visible_with_its_last_screenshot(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {"slave": "s" * 40}))
    app.state.screenshots = ScreenshotStore()
    with TestClient(app) as client:
        def send(windows):
            now = datetime.now(UTC).isoformat()
            response = client.post("/api/v1/slave/status", headers=SLAVE, json={
                "reported_at": now,
                "windows": [
                    {"window_id": f"hwnd:{number}", "character_id": str(4 if number == 44 else number),
                     "role_name": f"角色{4 if number == 44 else number}", "status": "not_full", "empty_slots": 12,
                     "monitoring": True, "updated_at": now}
                    for number in windows
                ],
            })
            assert response.status_code == 202
            return now

        captured_at = send([1, 2, 3, 4])
        app.state.database.save_live_snapshot("slave", "hwnd:4", "screenshots/live/test-4.jpg",
                                              captured_at, None)
        send([1, 2, 3])
        rows = client.get("/api/v1/master/status", headers=MASTER).json()["devices"][0]["windows"]
        assert len(rows) == 4
        closed = next(row for row in rows if row["character_id"] == "4")
        assert closed["status"] == "window_closed"
        assert closed["monitoring"] is False
        assert closed["empty_slots"] is None
        assert closed["screenshot_at"] == captured_at
        view = client.get("/api/v1/master/live-screenshots/slave/view?window_id=hwnd%3A4", headers=MASTER)
        assert view.json()["url"].endswith("screenshots/live/test-4.jpg")
        request = client.post("/api/v1/master/live-screenshots/slave/request", headers=MASTER,
                              json={"window_id": "hwnd:4"})
        assert request.status_code == 409

        send([1, 2, 3, 44])
        rows = client.get("/api/v1/master/status", headers=MASTER).json()["devices"][0]["windows"]
        assert len(rows) == 4
        assert {row["character_id"] for row in rows} == {"1", "2", "3", "4"}
        assert any(row["window_id"] == "hwnd:44" for row in rows)
        assert all(row["window_id"] != "hwnd:4" for row in rows)


def test_backfill_restores_a_card_lost_before_the_fix(tmp_path):
    path = tmp_path / "relay.sqlite3"
    app = create_app(Settings(path, "m" * 40, {"slave": "s" * 40}))
    app.state.screenshots = ScreenshotStore()
    with TestClient(app) as client:
        now = datetime.now(UTC).isoformat()
        payload = {"reported_at": now, "windows": [{
            "window_id": "hwnd:8", "character_id": "9988", "role_name": "旧角色",
            "status": "not_full", "monitoring": True, "updated_at": now,
        }]}
        assert client.post("/api/v1/slave/status", headers=SLAVE, json=payload).status_code == 202
        app.state.database.save_live_snapshot("slave", "hwnd:8", "screenshots/live/old.jpg",
                                              now, None, keep_history=True)
        with sqlite3.connect(path) as connection:
            connection.execute("DELETE FROM device_window_status WHERE device_id='slave' AND window_id='hwnd:8'")
        assert app.state.database.backfill_closed_windows() == 1
        assert app.state.database.backfill_closed_windows() == 0
        rows = client.get("/api/v1/master/status", headers=MASTER).json()["devices"][0]["windows"]
        assert len(rows) == 1
        assert rows[0]["role_name"] == "旧角色" and rows[0]["status"] == "window_closed"
        assert rows[0]["screenshot_at"] == now
