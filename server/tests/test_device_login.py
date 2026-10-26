from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_device_login_creates_then_reuses_machine_and_preserves_name(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {}))
    with TestClient(app) as client:
        account = client.post("/api/v1/auth/register", json={
            "username": "merchant", "password": "safe-password-123",
        }).json()
        owner = {"Authorization": "Bearer " + account["access_token"]}
        payload = {"username": "merchant", "password": "safe-password-123",
                   "installation_id": "installation-1234567890", "hostname": "DESKTOP-ONE",
                   "app_version": "V2.4.0", "protocol_version": 1}
        assert client.post("/api/v1/slave/login", json={**payload, "password": "wrong-password"}).status_code == 401
        first = client.post("/api/v1/slave/login", json=payload)
        assert first.status_code == 201
        machine = first.json()
        assert machine["display_name"] == "DESKTOP-ONE"
        device = {"X-Device-ID": machine["device_id"], "Authorization": "Bearer " + machine["device_token"]}
        assert client.get("/api/v1/slave/auth-state", headers=device).json()["account_username"] == "merchant"
        assert client.patch(f"/api/v1/master/devices/{machine['device_id']}", headers=owner,
                            json={"display_name": "宝石商人电脑"}).status_code == 200
        second = client.post("/api/v1/slave/login", json=payload)
        assert second.status_code == 201
        assert second.json()["device_id"] == machine["device_id"]
        assert second.json()["display_name"] == "宝石商人电脑"
        assert second.json()["device_token"] != machine["device_token"]
        assert client.get("/api/v1/slave/auth-state", headers=device).status_code == 401
        device["Authorization"] = "Bearer " + second.json()["device_token"]
        assert client.get("/api/v1/slave/auth-state", headers=device).status_code == 200
        assert len(client.get("/api/v1/master/devices", headers=owner).json()["devices"]) == 1
        assert client.patch(f"/api/v1/master/devices/{machine['device_id']}", headers=owner,
                            json={"disabled": True}).status_code == 200
        assert client.get("/api/v1/slave/auth-state", headers=device).status_code == 401
        assert client.post("/api/v1/slave/login", json=payload).status_code == 403


def test_removed_machine_can_sign_in_as_a_new_device(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {}))
    with TestClient(app) as client:
        account = client.post("/api/v1/auth/register", json={
            "username": "merchant", "password": "safe-password-123",
        }).json()
        owner = {"Authorization": "Bearer " + account["access_token"]}
        payload = {"username": "merchant", "password": "safe-password-123",
                   "installation_id": "installation-1234567890"}
        original = client.post("/api/v1/slave/login", json=payload).json()["device_id"]
        assert client.delete(f"/api/v1/master/devices/{original}", headers=owner).status_code == 200
        replacement = client.post("/api/v1/slave/login", json=payload)
        assert replacement.status_code == 201
        assert replacement.json()["device_id"] != original


def test_login_requires_an_account_and_isolated_installations(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {}))
    with TestClient(app) as client:
        assert client.post("/api/v1/slave/login", json={"username": "missing", "password": "abcdefgh",
            "installation_id": "installation-1234567890"}).status_code == 401
        for username in ("first", "second"):
            client.post("/api/v1/auth/register", json={"username": username, "password": "safe-password-123"})
        credentials = {"password": "safe-password-123", "installation_id": "installation-1234567890"}
        first = client.post("/api/v1/slave/login", json={"username": "first", **credentials}).json()
        second = client.post("/api/v1/slave/login", json={"username": "second", **credentials}).json()
        assert first["device_id"] != second["device_id"]
