import sqlite3
from datetime import UTC, datetime

from app.database import EVENT_SCREENSHOT_PANELS, RelayDatabase
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

MASTER = {"Authorization": "Bearer " + "m" * 40}
SLAVE = {"Authorization": "Bearer " + "s" * 40, "X-Device-ID": "slave"}
ALL_EVENT_SCREENSHOTS = {panel: True for panel in EVENT_SCREENSHOT_PANELS}


class Store:
    configured = True

    def __init__(self):
        self.uploaded = set()

    def signed_put_url(self, key):
        return "https://cos.example.com/" + key + "?signature=day"

    def signed_url(self, key):
        return "https://screenshots.example.com/" + key + "?signature=short"

    def exists(self, key):
        if key not in self.uploaded:
            raise FileNotFoundError(key)


def test_live_screenshot_settings_direct_upload_and_force_request(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {"slave": "s" * 40}))
    store = Store()
    app.state.screenshots = store
    with TestClient(app) as client:
        assert client.get("/api/v1/master/live-screenshot-settings", headers=MASTER).json() == {
            "interval_minutes": 5,
            "event_screenshots": ALL_EVENT_SCREENSHOTS,
        }
        for invalid in (0, 61, 1.5, "5"):
            assert client.put("/api/v1/master/live-screenshot-settings", headers=MASTER,
                              json={"interval_minutes": invalid}).status_code == 422
        enabled_panels = {**ALL_EVENT_SCREENSHOTS, "give": False, "stall_other_buy": False}
        settings = client.put("/api/v1/master/live-screenshot-settings", headers=MASTER,
                              json={"interval_minutes": 12, "event_screenshots": enabled_panels})
        assert settings.status_code == 200
        assert settings.json() == {"interval_minutes": 12, "event_screenshots": enabled_panels}
        assert client.put(
            "/api/v1/master/live-screenshot-settings",
            headers=MASTER,
            json={"interval_minutes": 12, "event_screenshots": {**enabled_panels, "unknown": True}},
        ).status_code == 422
        legacy_save = client.put(
            "/api/v1/master/live-screenshot-settings",
            headers=MASTER,
            json={"interval_minutes": 11},
        )
        assert legacy_save.status_code == 200
        assert legacy_save.json() == {
            "interval_minutes": 11,
            "event_screenshots": enabled_panels,
        }
        window = {"window_id": "hwnd:42", "character_id": "123456", "updated_at": datetime.now(UTC).isoformat(), "monitoring": True}
        assert client.post("/api/v1/slave/status", headers=SLAVE,
                           json={"reported_at": window["updated_at"], "windows": [window]}).status_code == 202
        control = client.get("/api/v1/slave/live-screenshots/control", headers=SLAVE).json()
        assert control["interval_minutes"] == 11
        assert control["event_screenshots"] == enabled_panels
        assert client.post("/api/v1/slave/live-screenshots/permission", headers=SLAVE,
                           json={"window_id": "hwnd:missing"}).status_code == 404
        permission = client.post("/api/v1/slave/live-screenshots/permission", headers=SLAVE,
                                 json={"window_id": "hwnd:42"})
        assert permission.status_code == 200
        assert permission.json()["url"].startswith("https://cos.example.com/screenshots/live/1/")
        key = permission.json()["url"].split("cos.example.com/", 1)[1].split("?", 1)[0]

        requested = client.post("/api/v1/master/live-screenshots/slave/request", headers=MASTER,
                                json={"window_id": "hwnd:42"})
        assert requested.status_code == 200
        request_id = requested.json()["request_id"]
        control = client.get("/api/v1/slave/live-screenshots/control", headers=SLAVE).json()
        assert control["requests"] == [{"request_id": request_id, "window_id": "hwnd:42"}]
        payload = {"window_id": "hwnd:42", "captured_at": datetime.now(UTC).isoformat(), "request_id": request_id}
        assert client.post("/api/v1/slave/live-screenshots/complete", headers=SLAVE, json=payload).status_code == 409
        store.uploaded.add(key)
        assert client.post("/api/v1/slave/live-screenshots/complete", headers=SLAVE, json=payload).status_code == 200
        assert client.get(f"/api/v1/master/live-screenshots/requests/{request_id}", headers=MASTER).json()["state"] == "complete"
        status = client.get("/api/v1/master/status", headers=MASTER).json()
        assert status["devices"][0]["windows"][0]["screenshot_at"] == payload["captured_at"]
        view = client.get("/api/v1/master/live-screenshots/slave/view?window_id=hwnd:42", headers=MASTER)
        assert view.json()["url"].startswith("https://screenshots.example.com/screenshots/live/1/")
        assert view.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/master/live-screenshots/slave/view?window_id=hwnd:42").status_code == 401

        for capture_id in ("a" * 32, "b" * 32):
            permit = client.post("/api/v1/slave/live-screenshots/permission", headers=SLAVE,
                                 json={"window_id": "hwnd:42", "capture_id": capture_id})
            assert permit.status_code == 200
            unique_key = permit.json()["url"].split("cos.example.com/", 1)[1].split("?", 1)[0]
            assert capture_id in unique_key
            store.uploaded.add(unique_key)
            assert client.post("/api/v1/slave/live-screenshots/complete", headers=SLAVE,
                               json={"window_id": "hwnd:42", "capture_id": capture_id,
                                     "captured_at": datetime.now(UTC).isoformat()}).status_code == 200
        history = client.get("/api/v1/master/live-screenshots/history?character_id=123456&limit=1", headers=MASTER)
        assert history.status_code == 200
        assert history.json()["has_more"] is True
        newest = history.json()["items"][0]
        assert "b" * 32 in client.get(f"/api/v1/master/live-screenshots/history/{newest['id']}/view", headers=MASTER).json()["url"]
        older = client.get(f"/api/v1/master/live-screenshots/history?character_id=123456&before={newest['id']}", headers=MASTER)
        assert len(older.json()["items"]) == 1
        assert client.get(f"/api/v1/master/live-screenshots/history/{newest['id']}/view").status_code == 401


def test_legacy_snapshot_settings_gain_all_event_screenshot_panels(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE live_snapshot_settings (account_id INTEGER PRIMARY KEY, interval_minutes INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO live_snapshot_settings VALUES (1, 9)")

    database = RelayDatabase(path)
    database.initialize()

    assert database.snapshot_settings(1) == {
        "interval_minutes": 9,
        "event_screenshots": ALL_EVENT_SCREENSHOTS,
    }
