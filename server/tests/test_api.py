from __future__ import annotations

import json
from pathlib import Path

from app import main as main_module
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

MASTER_TOKEN = "master-" + "a" * 40
DEVICE_TOKEN = "device-" + "b" * 40


def make_client(tmp_path: Path, update_directory: Path | None = None) -> TestClient:
    settings = Settings(
        database_path=tmp_path / "relay.sqlite3",
        master_token=MASTER_TOKEN,
        device_tokens={"slave-01": DEVICE_TOKEN},
        update_directory=update_directory or tmp_path / "releases",
    )
    return TestClient(create_app(settings))


def device_headers() -> dict[str, str]:
    return {"X-Device-ID": "slave-01", "Authorization": f"Bearer {DEVICE_TOKEN}"}


def master_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {MASTER_TOKEN}"}


def test_legacy_cash_observations_are_acknowledged_without_statistics(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        payload = {"event_id": "balance-1", "character_id": "123456", "stream_id": "run1",
                   "sequence": 1, "occurred_at": "2026-10-19T12:00:00+08:00", "cash": 1000, "active": True}
        assert client.post("/api/v1/slave/cash-observations", headers=device_headers(), json=payload).status_code == 202
        payload.update(event_id="balance-2", sequence=2, occurred_at="2026-10-19T12:00:05+08:00", cash=800)
        for _ in range(2):
            assert client.post("/api/v1/slave/cash-observations", headers=device_headers(), json=payload).status_code == 202
        result = client.get("/api/v1/master/cash-statistics?day=2026-10-19", headers=master_headers())
        assert result.status_code == 200
        assert result.json()["daily"] == []
        assert result.json()["hourly"] == []
        assert client.get("/api/v1/master/cash-statistics?day=invalid", headers=master_headers()).status_code == 422
        assert client.get("/api/v1/master/cash-statistics?day=2026-10-19").status_code == 401
        payload["character_id"] = ""
        assert client.post("/api/v1/slave/cash-observations", headers=device_headers(), json=payload).status_code == 422


def test_money_snapshot_roundtrip_and_legacy_compatibility(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        window = {"window_id": "hwnd:money", "updated_at": "2026-10-20T22:00:00+08:00",
                  "cash": 1450000, "bank_money": 0, "money_readable": True,
                  "money_confirmed_at": "2026-10-20T22:00:00+08:00"}
        snapshot = {"reported_at": window["updated_at"], "windows": [window]}
        assert client.post("/api/v1/slave/status", headers=device_headers(), json=snapshot).status_code == 202
        row = client.get("/api/v1/master/status", headers=master_headers()).json()["devices"][0]["windows"][0]
        assert row["cash"] == 1450000
        assert row["bank_money"] is None
        assert row["money_readable"] == 1
        assert row["money_confirmed_at"]
        window["cash"] = -1
        assert client.post("/api/v1/slave/status", headers=device_headers(), json=snapshot).status_code == 422
        for name in ("cash", "bank_money", "money_readable", "money_confirmed_at"):
            window.pop(name)
        assert client.post("/api/v1/slave/status", headers=device_headers(), json=snapshot).status_code == 202
        row = client.get("/api/v1/master/status", headers=master_headers()).json()["devices"][0]["windows"][0]
        assert row["cash"] is None
        assert not row["money_readable"]


def test_monitor_logs_archive_abnormal_snapshots_and_deduplicate_retries(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.put("/api/v1/master/monitor-log-settings", headers=master_headers(),
                   json={"occlusion_enabled": True, "occlusion_percent": 0, "occlusion_seconds": 0})
        window = {
            "window_id": "hwnd:123", "server_name": "秦淮风光", "role_name": "角色一",
            "occlusion_percent": 0.021, "minimized": False,
            "updated_at": "2026-10-19T10:00:00+08:00",
        }
        snapshot = {"reported_at": window["updated_at"], "windows": [window]}
        for _ in range(2):
            assert client.post("/api/v1/slave/status", headers=device_headers(), json=snapshot).status_code == 202
        url = "/api/v1/master/monitor-logs"
        logs = client.get(url, headers=master_headers()).json()["logs"]
        assert len(logs) == 1
        assert logs[0]["occlusion_percent"] == 0.021
        assert logs[0]["event_type"] == "window_occluded"
        # A later report of the same abnormal state is a new observation.
        snapshot["reported_at"] = window["updated_at"] = "2026-10-19T10:00:05+08:00"
        window["occlusion_percent"] = 0
        window["minimized"] = True
        assert client.post("/api/v1/slave/status", headers=device_headers(), json=snapshot).status_code == 202
        assert len(client.get(url, headers=master_headers()).json()["logs"]) == 2
        snapshot["reported_at"] = window["updated_at"] = "2026-10-19T10:00:10+08:00"
        client.post("/api/v1/slave/status", headers=device_headers(), json=snapshot)
        assert len(client.get(url, headers=master_headers()).json()["logs"]) == 3
        window["minimized"] = False
        snapshot["reported_at"] = window["updated_at"] = "2026-10-19T10:00:15+08:00"
        client.post("/api/v1/slave/status", headers=device_headers(), json=snapshot)
        assert len(client.get(url, headers=master_headers()).json()["logs"]) == 3
        assert client.get("/api/v1/master/events", headers=master_headers()).json()["count"] == 0


def test_panel_logs_store_window_identity_and_time_without_creating_alarm(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        for kind in ["inventory", "give", "trade", "attributes", "stall", "stall_records"]:
            payload = {"event_id": f"panel:{kind}", "event_type": "panel_opened", "reason": "recognition",
                       "panel_type": kind, "occurred_at": "2026-10-20T10:31:20+08:00",
                       "window_id": "hwnd:123", "server_name": "区服", "role_name": "角色", "confidence": 0.98}
            for _ in range(2):
                assert client.post("/api/v1/slave/monitor-logs", headers=device_headers(), json=payload).status_code == 202
        logs = client.get("/api/v1/master/monitor-logs", headers=master_headers()).json()["logs"]
        assert len(logs) == 6
        assert {log["panel_type"] for log in logs} == {"inventory", "give", "trade", "attributes", "stall", "stall_records"}
        assert all(log["window_id"] == "hwnd:123" and log["role_name"] == "角色" for log in logs)
        assert logs[0]["confidence"] == 0.98
        assert client.get("/api/v1/master/events", headers=master_headers()).json()["count"] == 0
        payload.pop("panel_type")
        assert client.post("/api/v1/slave/monitor-logs", headers=device_headers(), json=payload).status_code == 422
        payload.update(event_type="game_default", event_id="default-scene")
        assert client.post("/api/v1/slave/monitor-logs", headers=device_headers(), json=payload).status_code == 202


def test_monitor_lifecycle_logs_pagination_isolation_and_persistence(tmp_path: Path) -> None:
    url = "/api/v1/master/monitor-logs"
    with make_client(tmp_path) as client:
        assert client.get(url).status_code == 401
        assert client.post("/api/v1/slave/monitor-logs", json={}).status_code == 401
        for index, (kind, reason) in enumerate([
            ("monitoring_started", "startup"), ("monitoring_paused", "pause"),
            ("monitoring_started", "resume"), ("monitoring_paused", "close"),
        ]):
            payload = {"event_id": f"lifecycle:{index}", "event_type": kind, "reason": reason,
                       "occurred_at": f"2026-10-19T10:00:0{index}+08:00"}
            for _ in range(2):
                assert client.post("/api/v1/slave/monitor-logs", headers=device_headers(), json=payload).status_code == 202
        page = client.get(url + "?limit=2", headers=master_headers()).json()
        assert page["has_more"]
        assert [row["reason"] for row in page["logs"]] == ["close", "resume"]
        before = page["logs"][-1]["id"]
        older = client.get(f"{url}?limit=2&before_id={before}", headers=master_headers()).json()
        assert not older["has_more"]
        assert [row["reason"] for row in older["logs"]] == ["pause", "startup"]
        assert client.get(url + "?limit=501", headers=master_headers()).status_code == 422
        # Claim the legacy account, then create an independent second account.
        client.post("/api/v1/auth/register", json={"username": "first-user", "password": "test-pass-123"})
        second = client.post("/api/v1/auth/register", json={"username": "second-user", "password": "test-pass-123"}).json()
        assert client.get(url, headers={"Authorization": f"Bearer {second['access_token']}"}).json()["logs"] == []
        assert client.get("/api/v1/master/events", headers=master_headers()).json()["count"] == 0
    with make_client(tmp_path) as client:
        assert len(client.get(url, headers=master_headers()).json()["logs"]) == 4


def test_public_update_manifest_and_download(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    releases.mkdir()
    archive = releases / "slave.zip"
    archive.write_bytes(b"signed-package")
    manifest = {
        "schema_version": 2,
        "client_version": "V1.2.0",
        "packages": {"slave": {"filename": archive.name}, "master": {"filename": "master.zip"}},
    }
    (releases / "release-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with make_client(tmp_path, releases) as client:
        latest = client.get("/api/v1/updates/latest")
        assert latest.status_code == 200
        assert latest.json()["client_version"] == "V1.2.0"
        downloaded = client.get("/api/v1/updates/download/slave/V1.2.0")
        assert downloaded.status_code == 200
        assert downloaded.content == b"signed-package"
        assert client.get("/api/v1/updates/download/slave/V9.9.9").status_code == 404


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


def test_bark_settings_test_and_new_event_push(tmp_path: Path, monkeypatch) -> None:
    sent: list[dict[str, object]] = []

    def fake_send(device_key: str, **payload) -> None:
        sent.append({"device_key": device_key, **payload})

    monkeypatch.setattr(main_module, "send_bark_notification", fake_send)
    with make_client(tmp_path) as client:
        settings = client.get("/api/v1/master/notifications/bark", headers=master_headers())
        assert settings.status_code == 200
        assert settings.json()["configured"] is False

        missing_key = client.put(
            "/api/v1/master/notifications/bark",
            headers=master_headers(),
            json={"enabled": True, "call_enabled": True, "repeat_minutes": 10},
        )
        assert missing_key.status_code == 400

        saved = client.put(
            "/api/v1/master/notifications/bark",
            headers=master_headers(),
            json={
                "enabled": True,
                "device_key": "BarkDeviceKey123",
                "call_enabled": True,
                "repeat_minutes": 5,
            },
        )
        assert saved.status_code == 200
        assert saved.json()["configured"] is True
        assert saved.json()["masked_key"].endswith("y123")
        assert "device_key" not in saved.json()

        tested = client.post(
            "/api/v1/master/notifications/bark/test", headers=master_headers(), json={}
        )
        assert tested.status_code == 200

        created = client.post(
            "/api/v1/slave/events",
            headers=device_headers(),
            json={
                "event_id": "bark-event-001",
                "event_type": "inventory_full",
                "occurred_at": "2026-10-08T18:00:00+08:00",
                "server_name": "秦淮风光",
                "role_name": "清风知夏",
                "empty_slots": 0,
            },
        )
        assert created.status_code == 202
        assert len(sent) == 2
        assert sent[1]["device_key"] == "BarkDeviceKey123"
        assert "清风知夏" in str(sent[1]["body"])


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
        windows = client.get("/api/v1/master/status", headers=master_headers()).json()["devices"][0]["windows"]
        assert len(windows) == 2
        assert next(item for item in windows if item["window_id"] == "hwnd:1002")["status"] == "window_closed"


def test_mobile_status_page_and_static_assets_are_available(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
        assert "梦幻西游监控后台" in page.text
        assert "assets/index-" in page.text
        assert page.headers["cache-control"] == "no-cache"
        admin_script = page.text.split('src="/assets/')[1].split('"')[0]
        script = client.get("/assets/" + admin_script)
        assert script.status_code == 200
        assert "api/v1/master/monitor-log-settings" in script.text
        assert "api/v1/master/status" in script.text
        assert "api/v1/auth/login" in script.text

        page = client.get("/legacy")
        assert page.status_code == 200
        assert "登录主机账号" in page.text
        assert 'id="username"' in page.text
        assert 'id="password"' in page.text
        assert "告警记录" in page.text
        assert "从机管理" in page.text
        assert "Bark 手机通知" in page.text
        assert 'id="add-device"' in page.text
        assert "主机密钥" not in page.text

        javascript = client.get("/static/app.js")
        assert javascript.status_code == 200
        assert "api/v1/master/status" in javascript.text
        assert "api/v1/auth/login" in javascript.text
        assert "api/v1/master/events" in javascript.text
        assert "api/v1/master/devices" in javascript.text
        assert "api/v1/master/pairing-codes" in javascript.text
        assert "api/v1/master/notifications/bark" in javascript.text

        stylesheet = client.get("/static/style.css")
        assert stylesheet.status_code == 200
        assert "summary-grid" in stylesheet.text
        assert "window-metrics" in stylesheet.text
        assert "dashboard-tabs" in stylesheet.text
        assert "alerts-list" in stylesheet.text
        assert "managed-devices" in stylesheet.text


def test_recent_alert_order_for_web_dashboard(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        for index in range(3):
            created = client.post(
                "/api/v1/slave/events", headers=device_headers(),
                json={"event_id": f"web-alert-{index}", "event_type": "inventory_full",
                      "occurred_at": "2026-09-29T08:30:00+08:00"},
            )
            assert created.status_code == 202
        recent = client.get("/api/v1/master/events?state=all&limit=2&latest=true", headers=master_headers())
        assert recent.status_code == 200
        recent_ids = [item["server_event_id"] for item in recent.json()["events"]]
        assert recent_ids == sorted(recent_ids, reverse=True)
        original = client.get("/api/v1/master/events?state=all&limit=2", headers=master_headers())
        original_ids = [item["server_event_id"] for item in original.json()["events"]]
        assert original_ids == sorted(original_ids)
        assert recent_ids[0] > original_ids[-1]


def _register(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "safe-password-123", "display_name": username},
    )
    assert response.status_code == 201
    return str(response.json()["access_token"])


def test_monitor_policy_follows_account_and_validates_values(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        first, second = _register(client, "policy-one"), _register(client, "policy-two")
        a, b = {"Authorization": f"Bearer {first}"}, {"Authorization": f"Bearer {second}"}
        path = "/api/v1/master/monitor-log-settings"
        defaults = {"occlusion_enabled": True, "occlusion_percent": 60.0, "occlusion_seconds": 5}
        assert client.get(path, headers=a).json() == defaults
        values = {"occlusion_enabled": False, "occlusion_percent": 80, "occlusion_seconds": 10}
        assert client.put(path, headers=a, json=values).json() == values
        assert client.get(path, headers=a).json() == values
        assert client.get(path, headers=b).json() == defaults
        assert client.get(path).status_code == 401
        for field, value in (("occlusion_percent", 101), ("occlusion_percent", -1),
                             ("occlusion_seconds", -1), ("occlusion_seconds", 3601), ("occlusion_seconds", 2.5)):
            assert client.put(path, headers=a, json={**values, field: value}).status_code == 422


def test_log_filter_is_applied_before_pagination(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        for index in range(125):
            panel = "trade" if index < 5 else "attributes"
            payload = {"event_id": f"filter:{index}", "event_type": "panel_opened", "panel_type": panel,
                       "occurred_at": "2026-10-20T10:31:20+08:00", "reason": "recognition"}
            assert client.post("/api/v1/slave/monitor-logs", headers=device_headers(), json=payload).status_code == 202
        url = "/api/v1/master/monitor-logs?panel_type=trade&limit=3"
        first = client.get(url, headers=master_headers()).json()
        assert len(first["logs"]) == 3 and first["has_more"]
        assert all(row["panel_type"] == "trade" for row in first["logs"])
        second = client.get(url + f'&before_id={first["logs"][-1]["id"]}', headers=master_headers()).json()
        assert len(second["logs"]) == 2 and not second["has_more"]
        assert client.get("/api/v1/master/monitor-logs?event_type=window_occluded", headers=master_headers()).json()["logs"] == []


def _pair(client: TestClient, master_token: str, installation_id: str) -> tuple[str, str]:
    code_response = client.post(
        "/api/v1/master/pairing-codes",
        headers={"Authorization": f"Bearer {master_token}"},
        json={},
    )
    assert code_response.status_code == 201
    paired = client.post(
        "/api/v1/slave/pair",
        json={
            "pairing_code": code_response.json()["pairing_code"],
            "installation_id": installation_id,
            "hostname": installation_id,
            "app_version": "2.0.0",
        },
    )
    assert paired.status_code == 201
    return str(paired.json()["device_id"]), str(paired.json()["device_token"])


def test_slave_can_rename_only_itself_and_heartbeat_tracks_master_rename(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        owner = _register(client, "rename-owner")
        other = _register(client, "rename-other")
        first_id, first_token = _pair(client, owner, "rename-installation-001")
        other_id, other_token = _pair(client, other, "rename-installation-002")
        first = {"X-Device-ID": first_id, "Authorization": f"Bearer {first_token}"}
        foreign = {"X-Device-ID": other_id, "Authorization": f"Bearer {other_token}"}
        owner_headers = {"Authorization": f"Bearer {owner}"}
        renamed = client.post("/api/v1/slave/profile", headers=first, json={"display_name": " 收购电脑01 "})
        assert renamed.status_code == 200
        assert renamed.json()["display_name"] == "收购电脑01"
        devices = client.get("/api/v1/master/devices", headers=owner_headers).json()["devices"]
        assert devices[0]["device_id"] == first_id
        assert devices[0]["display_name"] == "收购电脑01"
        for invalid in ({"display_name": "   "}, {"display_name": "x" * 65},
                        {"display_name": "越权", "device_id": first_id}, {"display_name": "越权", "disabled": False}):
            assert client.post("/api/v1/slave/profile", headers=foreign, json=invalid).status_code == 422
        assert client.post("/api/v1/slave/profile", json={"display_name": "未授权"}).status_code == 401
        assert client.patch(f"/api/v1/master/devices/{first_id}", headers=owner_headers,
                            json={"display_name": "主机修改名称"}).status_code == 200
        heartbeat = client.post("/api/v1/slave/heartbeat", headers=first, json={"hostname": "旧电脑主机名"})
        assert heartbeat.json()["display_name"] == "主机修改名称"
        assert client.post("/api/v1/slave/heartbeat", headers=foreign, json={}).json()["display_name"] != "主机修改名称"


def test_accounts_pairing_and_data_are_tenant_isolated(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        alice_token = _register(client, "alice")
        bob_token = _register(client, "bob")
        alice_device, alice_device_token = _pair(client, alice_token, "alice-installation-001")
        bob_device, bob_device_token = _pair(client, bob_token, "bob-installation-001")

        for device_id, device_token, role in (
            (alice_device, alice_device_token, "Alice角色"),
            (bob_device, bob_device_token, "Bob角色"),
        ):
            created = client.post(
                "/api/v1/slave/events",
                headers={"X-Device-ID": device_id, "Authorization": f"Bearer {device_token}"},
                json={
                    "event_id": f"event-{device_id}",
                    "event_type": "inventory_full",
                    "occurred_at": "2026-09-30T08:30:00+08:00",
                    "role_name": role,
                    "empty_slots": 0,
                },
            )
            assert created.status_code == 202

        alice_events = client.get(
            "/api/v1/master/events?state=all",
            headers={"Authorization": f"Bearer {alice_token}"},
        ).json()["events"]
        bob_events = client.get(
            "/api/v1/master/events?state=all",
            headers={"Authorization": f"Bearer {bob_token}"},
        ).json()["events"]
        assert [item["role_name"] for item in alice_events] == ["Alice角色"]
        assert [item["role_name"] for item in bob_events] == ["Bob角色"]
        assert client.post(
            f"/api/v1/master/events/{alice_events[0]['server_event_id']}/ack",
            headers={"Authorization": f"Bearer {bob_token}"},
            json={},
        ).status_code == 404


def test_device_management_disable_reissue_and_remove(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        token = _register(client, "manager")
        headers = {"Authorization": f"Bearer {token}"}
        device_id, device_token = _pair(client, token, "manager-installation-001")
        device_headers = {"X-Device-ID": device_id, "Authorization": f"Bearer {device_token}"}

        updated = client.patch(
            f"/api/v1/master/devices/{device_id}",
            headers=headers,
            json={"display_name": "工作室电脑", "note": "二楼", "disabled": True},
        )
        assert updated.status_code == 200
        assert client.post("/api/v1/slave/heartbeat", headers=device_headers, json={}).status_code == 401

        client.patch(
            f"/api/v1/master/devices/{device_id}", headers=headers, json={"disabled": False}
        )
        reissue = client.post(
            f"/api/v1/master/devices/{device_id}/reissue", headers=headers, json={}
        )
        assert reissue.status_code == 201
        assert client.post("/api/v1/slave/heartbeat", headers=device_headers, json={}).status_code == 401
        repaired = client.post(
            "/api/v1/slave/pair",
            json={
                "pairing_code": reissue.json()["pairing_code"],
                "installation_id": "manager-installation-001",
            },
        )
        assert repaired.status_code == 201
        new_headers = {
            "X-Device-ID": device_id,
            "Authorization": f"Bearer {repaired.json()['device_token']}",
        }
        assert client.post("/api/v1/slave/heartbeat", headers=new_headers, json={}).status_code == 200
        assert client.delete(f"/api/v1/master/devices/{device_id}", headers=headers).status_code == 200
        assert client.post("/api/v1/slave/heartbeat", headers=new_headers, json={}).status_code == 401


def test_independent_updates_keep_legacy_slave_unchanged(tmp_path: Path):
    releases = tmp_path / "releases"
    (releases / "legacy").mkdir(parents=True)
    packages = {"master": {"filename": "master.zip"}, "slave": {"filename": "slave.zip"}}
    for role, version in (("master", "V2.1.0"), ("slave", "V2.0.0")):
        document = {"schema_version": 3, "release_role": role, "client_version": version, "packages": packages}
        (releases / f"release-manifest-{role}.json").write_text(json.dumps(document), encoding="utf-8")
        (releases / packages[role]["filename"]).write_bytes(f"new-{role}".encode())
        (releases / "legacy" / packages[role]["filename"]).write_bytes(f"old-{role}".encode())
    legacy = {"schema_version": 2, "client_version": "V2.0.0", "packages": packages}
    (releases / "legacy-release-manifest.json").write_text(json.dumps(legacy), encoding="utf-8")
    with make_client(tmp_path, releases) as client:
        assert client.get("/api/v1/updates/latest").json()["client_version"] == "V2.0.0"
        assert client.get("/api/v1/updates/latest/master").json()["client_version"] == "V2.1.0"
        assert client.get("/api/v1/updates/latest/slave").json()["client_version"] == "V2.0.0"
        assert client.get("/api/v1/updates/latest/invalid").status_code == 404
        assert client.get("/api/v1/updates/download/master/V2.1.0").content == b"new-master"
        assert client.get("/api/v1/updates/download/master/V2.0.0").content == b"old-master"
        assert client.get("/api/v1/updates/download/slave/V2.1.0").status_code == 404


def test_protocol_is_reported_and_legacy_heartbeat_remains_supported(tmp_path: Path):
    with make_client(tmp_path) as client:
        response = client.post("/api/v1/slave/heartbeat", headers=device_headers(), json={"app_version": "V3.0.0", "protocol_version": 2})
        assert response.status_code == 200
        devices = client.get("/api/v1/master/devices", headers=master_headers()).json()["devices"]
        assert devices[0]["protocol_version"] == 2
        response = client.post("/api/v1/slave/heartbeat", headers=device_headers(), json={"app_version": "V2.0.0"})
        assert response.status_code == 200
        devices = client.get("/api/v1/master/devices", headers=master_headers()).json()["devices"]
        assert devices[0]["protocol_version"] is None


def test_download_role_binding_and_history_are_preserved(tmp_path: Path):
    releases = tmp_path / "releases"
    archive_root = releases / "history" / "master" / "V2.0.0"
    archive_root.mkdir(parents=True)
    old = {"schema_version": 2, "client_version": "V2.0.0", "packages": {"master": {"filename": "master.zip"}}}
    (archive_root / "release-manifest.json").write_text(json.dumps(old), encoding="utf-8")
    (archive_root / "master.zip").write_bytes(b"historical-master")
    current = {"schema_version": 3, "release_role": "master", "client_version": "V2.2.0",
               "packages": {"master": {"filename": "master.zip", "client_version": "V2.2.0"},
                            "slave": {"filename": "slave.zip", "client_version": "V2.1.0"}}}
    (releases / "release-manifest.json").write_text(json.dumps(current), encoding="utf-8")
    (releases / "slave.zip").write_bytes(b"slave-v210")
    with make_client(tmp_path, releases) as client:
        assert client.get("/api/v1/updates/download/master/V2.0.0").content == b"historical-master"
        assert client.get("/api/v1/updates/download/slave/V2.2.0").status_code == 404
        assert client.get("/api/v1/updates/download/slave/V2.1.0").status_code == 404
