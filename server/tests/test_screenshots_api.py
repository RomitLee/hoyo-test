from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from app.config import Settings
from app.main import create_app
from app.screenshots import ScreenshotStore
from fastapi.testclient import TestClient
from PIL import Image

MASTER = {"Authorization": "Bearer " + "m" * 40}
SLAVE = {"Authorization": "Bearer " + "s" * 40, "X-Device-ID": "slave"}


def test_real_sdk_configuration_and_custom_domain_signature(tmp_path):
    store = ScreenshotStore(
        Settings(
            tmp_path / "unused.db",
            "",
            {},
            cos_secret_id="test-id",
            cos_secret_key="test-key",
            cos_bucket="mh-1257029073",
            cos_region="ap-hongkong",
            cos_screenshot_domain="https://screenshots.maipi.top",
        )
    )
    assert store.configured
    assert store._client() is not None
    assert store.signed_url("screenshots/1/test.jpg").startswith(
        "https://screenshots.maipi.top/screenshots/1/test.jpg?"
    )


class Store:
    configured = True

    def __init__(self):
        self.images = {}
        self.fail = False

    def put(self, key, data):
        if self.fail:
            raise RuntimeError("secret must never appear in response")
        self.images[key] = data

    def signed_url(self, key):
        return "https://screenshots.example.com/" + key + "?signature=short"


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(tmp_path / "db.sqlite3", "m" * 40, {"slave": "s" * 40, "other-device": "o" * 40}))
    app.state.screenshots = Store()
    with TestClient(app) as result:
        yield result


def report(client, event_id="opening", **changes):
    values = {
        "event_id": event_id,
        "event_type": "panel_opened",
        "panel_type": "give",
        "reason": "recognition",
        "occurred_at": "2026-10-26T10:00:00+08:00",
        "window_id": "hwnd:1",
        "character_id": "123",
        "target_status": "unrecognized",
    }
    values.update(changes)
    assert client.post("/api/v1/slave/monitor-logs", headers=SLAVE, json=values).status_code == 202


def jpeg():
    buffer = BytesIO()
    Image.new("RGB", (640, 480), (80, 30, 120)).save(buffer, "JPEG")
    return buffer.getvalue()


def upload(client, event_id="opening", data=None, headers=SLAVE):
    return client.put(
        f"/api/v1/slave/monitor-logs/{event_id}/screenshot",
        headers={**headers, "Content-Type": "image/jpeg"},
        content=jpeg() if data is None else data,
    )


def logs(client):
    return client.get("/api/v1/master/monitor-logs", headers=MASTER).json()["logs"]


def test_capture_retry_and_initial_ocr_confirmation_share_one_screenshot(client):
    report(client)
    assert upload(client).status_code == 200
    assert upload(client).json()["duplicate"]
    report(
        client,
        "confirmed",
        event_type="panel_target_changed",
        target_status="recognized",
        target_name="交易玩家",
        target_level=109,
        occurred_at="2026-10-26T10:00:03+08:00",
    )
    assert upload(client, "confirmed").json()["duplicate"]
    rows = logs(client)
    assert len(rows) == 1 and rows[0]["screenshot_ready"] == 1
    assert len(client.app.state.screenshots.images) == 1
    response = client.get(f"/api/v1/master/monitor-logs/{rows[0]['id']}/screenshot", headers=MASTER)
    assert response.status_code == 200 and response.json()["expires_in"] == 300
    report(
        client,
        "changed",
        event_type="panel_target_changed",
        target_status="recognized",
        target_name="另一个玩家",
        target_level=144,
        occurred_at="2026-10-26T10:00:06+08:00",
    )
    assert upload(client, "changed").status_code == 200
    assert len(logs(client)) == 2 and len(client.app.state.screenshots.images) == 2


def test_authentication_account_and_device_isolation(client):
    client.post("/api/v1/auth/register", json={"username": "first_owner", "password": "password123"})
    report(client)
    assert upload(client).status_code == 200
    log_id = logs(client)[0]["id"]
    endpoint = f"/api/v1/master/monitor-logs/{log_id}/screenshot"
    assert client.get(endpoint).status_code == 401
    account = client.post("/api/v1/auth/register", json={"username": "other_owner", "password": "password123"}).json()
    assert client.get(endpoint, headers={"Authorization": "Bearer " + account["access_token"]}).status_code == 404
    other = {"X-Device-ID": "other-device", "Authorization": "Bearer " + "o" * 40}
    assert upload(client, headers=other).status_code == 404
    assert upload(client, headers={}).status_code == 401


def test_invalid_size_type_missing_log_and_expiry(client):
    assert upload(client).status_code == 404
    report(client)
    assert upload(client, data=b"not an image").status_code == 422
    assert upload(client, data=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    assert client.put("/api/v1/slave/monitor-logs/opening/screenshot", headers=SLAVE, content=jpeg()).status_code == 415
    assert upload(client).status_code == 200
    log_id = logs(client)[0]["id"]
    with client.app.state.database._connect() as connection:
        connection.execute(
            "UPDATE monitor_screenshots SET expires_at=?", ((datetime.now(UTC) - timedelta(days=1)).isoformat(),)
        )
    assert client.get(f"/api/v1/master/monitor-logs/{log_id}/screenshot", headers=MASTER).status_code == 410


def test_storage_failure_does_not_mark_ready_or_leak_credentials_and_retry_succeeds(client):
    report(client)
    client.app.state.screenshots.fail = True
    result = upload(client)
    assert result.status_code == 503 and "secret" not in result.text
    assert not logs(client)[0]["screenshot_ready"]
    client.app.state.screenshots.fail = False
    assert upload(client).status_code == 200
    assert logs(client)[0]["screenshot_ready"]


def test_non_dialog_logs_cannot_upload_and_missing_storage_is_retryable(client):
    report(client, panel_type="inventory", target_status=None)
    assert upload(client).status_code == 422
    report(client, "trade", panel_type="trade")
    client.app.state.screenshots.configured = False
    assert upload(client, "trade").status_code == 503


def test_pending_upload_lease_is_retryable_and_expired_lease_can_recover(client):
    report(client)
    database = client.app.state.database
    log_id = logs(client)[0]["id"]
    assert database.claim_screenshot(1, log_id, "first")[0] == "claimed"
    assert upload(client).status_code == 409
    with database._connect() as connection:
        connection.execute(
            "UPDATE monitor_screenshots SET created_at=?", ((datetime.now(UTC) - timedelta(minutes=6)).isoformat(),)
        )
    assert upload(client).status_code == 200
