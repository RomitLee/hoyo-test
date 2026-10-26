from datetime import UTC, datetime

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

MASTER = {"Authorization": "Bearer " + "m" * 40}
SLAVE = {"Authorization": "Bearer " + "s" * 40, "X-Device-ID": "slave"}


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
        assert client.get("/api/v1/master/live-screenshot-settings", headers=MASTER).json() == {"interval_minutes": 5}
        for invalid in (0, 61, 1.5, "5"):
            assert client.put("/api/v1/master/live-screenshot-settings", headers=MASTER,
                              json={"interval_minutes": invalid}).status_code == 422
        assert client.put("/api/v1/master/live-screenshot-settings", headers=MASTER,
                          json={"interval_minutes": 12}).json() == {"interval_minutes": 12}
        window = {"window_id": "hwnd:42", "updated_at": datetime.now(UTC).isoformat(), "monitoring": True}
        assert client.post("/api/v1/slave/status", headers=SLAVE,
                           json={"reported_at": window["updated_at"], "windows": [window]}).status_code == 202
        assert client.get("/api/v1/slave/live-screenshots/control", headers=SLAVE).json()["interval_minutes"] == 12
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
