import sqlite3
from datetime import UTC, datetime, timedelta

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


def test_reinstall_recovers_renamed_machine_and_retires_duplicate(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {}))
    with TestClient(app) as client:
        account = client.post("/api/v1/auth/register", json={
            "username": "merchant", "password": "safe-password-123",
        }).json()
        owner = {"Authorization": "Bearer " + account["access_token"]}
        base = {"username": "merchant", "password": "safe-password-123", "hostname": "DESKTOP-ONE"}
        original = client.post("/api/v1/slave/login", json={**base,
            "installation_id": "installation-original-123"}).json()
        assert client.patch(f"/api/v1/master/devices/{original['device_id']}", headers=owner,
                            json={"display_name": "宝石商人电脑"}).status_code == 200
        duplicate = client.post("/api/v1/slave/login", json={**base,
            "installation_id": "installation-reinstalled-123"}).json()
        assert duplicate["device_id"] != original["device_id"]
        with sqlite3.connect(app.state.settings.database_path) as connection:
            connection.execute("UPDATE devices SET last_seen_at=? WHERE device_id=?",
                               ((datetime.now(UTC) - timedelta(minutes=2)).isoformat(), original["device_id"]))
        credential = {"Authorization": "Bearer " + duplicate["device_token"],
                      "X-Device-ID": duplicate["device_id"]}
        recovered = client.post("/api/v1/slave/auth-state", headers=credential,
                                json={"machine_id": "a" * 64, "hostname": "DESKTOP-ONE"})
        assert recovered.status_code == 200
        assert recovered.json()["device_id"] == original["device_id"]
        assert recovered.json()["display_name"] == "宝石商人电脑"
        assert client.get("/api/v1/slave/auth-state", headers=credential).status_code == 401
        credential["X-Device-ID"] = original["device_id"]
        assert client.get("/api/v1/slave/auth-state", headers=credential).status_code == 200
        assert len(client.get("/api/v1/master/devices", headers=owner).json()["devices"]) == 1
        again = client.post("/api/v1/slave/login", json={**base,
            "installation_id": "installation-third-123", "machine_id": "a" * 64}).json()
        assert again["device_id"] == original["device_id"]
        assert again["display_name"] == "宝石商人电脑"


def test_ambiguous_hostname_does_not_merge_machines(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {}))
    with TestClient(app) as client:
        account = client.post("/api/v1/auth/register", json={
            "username": "merchant", "password": "safe-password-123",
        }).json()
        owner = {"Authorization": "Bearer " + account["access_token"]}
        base = {"username": "merchant", "password": "safe-password-123", "hostname": "SAME-HOST"}
        first = client.post("/api/v1/slave/login", json={**base,
            "installation_id": "installation-first-123"}).json()
        second = client.post("/api/v1/slave/login", json={**base,
            "installation_id": "installation-second-123"}).json()
        for machine, name in ((first, "一号机"), (second, "二号机")):
            client.patch(f"/api/v1/master/devices/{machine['device_id']}", headers=owner,
                         json={"display_name": name})
        third = client.post("/api/v1/slave/login", json={**base,
            "installation_id": "installation-third-123", "machine_id": "b" * 64}).json()
        assert third["device_id"] not in {first["device_id"], second["device_id"]}


def test_password_login_after_reinstall_also_restores_original_name(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {}))
    with TestClient(app) as client:
        account = client.post("/api/v1/auth/register", json={
            "username": "merchant", "password": "safe-password-123",
        }).json()
        owner = {"Authorization": "Bearer " + account["access_token"]}
        base = {"username": "merchant", "password": "safe-password-123", "hostname": "DESKTOP-ONE"}
        original = client.post("/api/v1/slave/login", json={**base,
            "installation_id": "installation-original-123"}).json()
        client.patch(f"/api/v1/master/devices/{original['device_id']}", headers=owner,
                     json={"display_name": "宝石商人电脑"})
        with sqlite3.connect(app.state.settings.database_path) as connection:
            connection.execute("UPDATE devices SET last_seen_at=? WHERE device_id=?",
                               ((datetime.now(UTC) - timedelta(minutes=2)).isoformat(), original["device_id"]))
        recovered = client.post("/api/v1/slave/login", json={**base,
            "installation_id": "installation-new-123", "machine_id": "c" * 64}).json()
        assert recovered["device_id"] == original["device_id"]
        assert recovered["display_name"] == "宝石商人电脑"
        assert len(client.get("/api/v1/master/devices", headers=owner).json()["devices"]) == 1
