"""The site-wide console is separate from every tenant's monitor session."""

from datetime import UTC, datetime

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_admin_global_queries_and_tenant_isolation(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {}))
    with TestClient(app) as client:
        first = client.post("/api/v1/auth/register", json={"username": "merchant-one", "password": "safe-password-123"}).json()
        second = client.post("/api/v1/auth/register", json={"username": "merchant-two", "password": "safe-password-456"}).json()
        first_headers = {"Authorization": f"Bearer {first['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second['access_token']}"}
        master_headers = {"Authorization": "Bearer " + "m" * 40}
        for headers in (first_headers, second_headers, master_headers):
            assert client.get("/api/v1/admin/overview", headers=headers).status_code in (401, 403)
        assert client.get("/api/v1/admin/overview").status_code == 401
        assert app.state.database.set_admin("merchant-one", True)
        assert client.get("/api/v1/admin/me", headers=first_headers).status_code == 200
        assert client.get("/api/v1/admin/me", headers=second_headers).status_code == 403

        now = datetime.now(UTC).isoformat()
        for username, password, role_id in (("merchant-one", "safe-password-123", "12345"),
                                            ("merchant-two", "safe-password-456", "67890")):
            device = client.post("/api/v1/slave/login", json={"username": username,
                "password": password, "installation_id": f"installation-{role_id}-123456", "hostname": f"pc-{role_id}"})
            assert device.status_code == 201
            payload = device.json()
            device_headers = {"Authorization": f"Bearer {payload['device_token']}",
                              "X-Device-ID": payload["device_id"]}
            assert client.post("/api/v1/slave/status", headers=device_headers, json={"reported_at": now,
                "windows": [{"window_id": "hwnd:1", "character_id": role_id, "role_name": f"角色{role_id}",
                             "status": "not_full", "empty_slots": 8, "monitoring": True,
                             "updated_at": now}]}).status_code == 202

        summary = client.get("/api/v1/admin/overview", headers=first_headers).json()
        assert summary["accounts"] == 2 and summary["roles"] == 2 and summary["devices"] == 2
        assert summary["online_roles"] == 2
        accounts = client.get("/api/v1/admin/accounts?search=merchant", headers=first_headers).json()
        assert accounts["total"] == 2
        assert {item["role_count"] for item in accounts["items"]} == {1}
        roles = client.get("/api/v1/admin/roles?search=角色", headers=first_headers).json()
        assert {item["character_id"] for item in roles["items"]} == {"12345", "67890"}
        own_id = first["account"]["account_id"]
        filtered = client.get(f"/api/v1/admin/roles?account_id={own_id}", headers=first_headers).json()
        assert [item["character_id"] for item in filtered["items"]] == ["12345"]
        assert client.get("/api/v1/admin/devices?limit=1", headers=first_headers).json()["total"] == 2
        assert {item["character_id"] for item in client.get("/api/v1/master/status", headers=second_headers).json()["roles"]} == {"67890"}
        assert app.state.database.set_admin("merchant-one", False)
        assert client.get("/api/v1/admin/roles", headers=first_headers).status_code == 403


def test_admin_page_exists_but_requires_admin_for_data(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {}))
    with TestClient(app) as client:
        assert client.get("/admin").status_code == 200
        assert client.get("/api/v1/admin/accounts").status_code == 401
