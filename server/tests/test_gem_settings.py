"""Gem calculator data belongs to a signed-in account and saves atomically."""

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_gem_settings_are_persistent_isolated_and_validate_revisions(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {}))
    with TestClient(app) as client:
        first = client.post("/api/v1/auth/register", json={
            "username": "gem-one", "password": "safe-password-123"}).json()
        second = client.post("/api/v1/auth/register", json={
            "username": "gem-two", "password": "safe-password-456"}).json()
        first_headers = {"Authorization": f"Bearer {first['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second['access_token']}"}
        route = "/api/v1/master/gem-settings"
        assert client.get(route).status_code == 401
        initial = client.get(route, headers=first_headers).json()
        assert initial["revision"] == 0 and initial["zones"] == []
        assert initial["price_ratios"]["normal"]["12"] > 1
        initial["zones"] = [{"id": "qinhuai", "name": "秦淮风光", "quote_yuan": 222,
                             "purchase_wan": 9000, "margin_percent": 17, "account_count": 3,
                             "target_profit_yuan": 30}]
        initial["price_ratios"]["normal"]["12"] = 1.30
        saved = client.put(route, headers=first_headers, json=initial)
        assert saved.status_code == 200 and saved.json()["revision"] == 1
        assert client.get(route, headers=first_headers).json()["zones"][0]["quote_yuan"] == 222
        assert client.get(route, headers=second_headers).json()["zones"] == []
        assert client.put(route, headers=first_headers, json=initial).status_code == 409
        invalid = {**saved.json(), "zones": [{**saved.json()["zones"][0], "quote_yuan": 0}]}
        assert client.put(route, headers=first_headers, json=invalid).status_code == 422


def test_gem_settings_survive_app_restart(tmp_path):
    settings = Settings(tmp_path / "relay.sqlite3", "m" * 40, {})
    with TestClient(create_app(settings)) as client:
        account = client.post("/api/v1/auth/register", json={
            "username": "gem-merchant", "password": "safe-password-123"}).json()
        headers = {"Authorization": f"Bearer {account['access_token']}"}
        config = client.get("/api/v1/master/gem-settings", headers=headers).json()
        config["synthesis_prices"]["star"] = 136000
        assert client.put("/api/v1/master/gem-settings", headers=headers, json=config).status_code == 200
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/master/gem-settings", headers=headers).json()["synthesis_prices"]["star"] == 136000
