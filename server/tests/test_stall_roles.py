from datetime import UTC, datetime

from app.config import Settings
from app.bark import event_message
from app.main import create_app
from fastapi.testclient import TestClient


MASTER = {"Authorization": "Bearer " + "m" * 40}
SLAVE = {"Authorization": "Bearer " + "s" * 40, "X-Device-ID": "slave"}


def snapshot(client, session, panel=None, count=None):
    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    return client.post("/api/v1/slave/status", headers=SLAVE, json={
        "reported_at": now, "windows": [{"window_id": "hwnd:1", "character_id": "123",
            "server_name": "秦淮风光", "role_name": "清风知夏", "status": "inventory_closed",
            "monitoring": True, "updated_at": now, "monitor_session_id": session,
            "stall_panel_type": panel, "sell_stock_count": count}],
    })


def role(client):
    return client.get("/api/v1/master/status", headers=MASTER).json()["roles"][0]


def sale_events(client):
    events = client.get("/api/v1/master/events?state=all", headers=MASTER).json()["events"]
    return [item for item in events if item["event_type"] == "stall_sold"]


def test_role_groups_and_sold_alert_use_confirmed_counts_within_one_session(tmp_path):
    app = create_app(Settings(tmp_path / "db.sqlite3", "m" * 40, {"slave": "s" * 40}))
    with TestClient(app) as client:
        assert client.put("/api/v1/master/notifications/bark", headers=MASTER, json={
            "enabled": False, "local_alert_enabled": True, "stall_sold_enabled": True,
        }).status_code == 200
        session = "session-1234567890abcdef"
        assert snapshot(client, session).status_code == 202
        assert role(client)["stall_mode"] == "unknown"
        assert snapshot(client, session, "stall_buy").status_code == 202
        assert role(client)["stall_mode"] == "buy"
        assert snapshot(client, session, "stall", 20).status_code == 202
        assert (role(client)["stall_mode"], role(client)["sell_stock_count"]) == ("sell", 20)
        assert sale_events(client) == []
        assert snapshot(client, session, "stall", 19).status_code == 202
        assert len(sale_events(client)) == 1
        assert sale_events(client)[0]["payload"]["details"] == {
            "previous_count": 20, "current_count": 19, "sold_count": 1,
        }
        bark_row = app.state.database.bark_notification_for_event(1, sale_events(client)[0]["server_event_id"])
        assert "20→19" in event_message(bark_row)[1]
        assert snapshot(client, session, "stall", 19).status_code == 202
        assert len(sale_events(client)) == 1
        assert snapshot(client, session, "stall", 20).status_code == 202
        assert snapshot(client, session, "stall", 19).status_code == 202
        assert len(sale_events(client)) == 2
        assert snapshot(client, "next-session-1234567890").status_code == 202
        assert (role(client)["stall_mode"], role(client)["sell_stock_count"]) == ("unknown", None)
        assert snapshot(client, "next-session-1234567890", "stall", 18).status_code == 202
        assert len(sale_events(client)) == 2  # New monitoring session establishes a fresh baseline.
        pending = client.post("/api/v1/slave/heartbeat", headers=SLAVE,
                              json={"hostname": "machine", "app_version": "V2.12.0"}).json()["local_alerts"]
        assert len(pending) == 2 and all(item["event_type"] == "stall_sold" for item in pending)
        assert client.post(f"/api/v1/slave/local-alerts/{pending[0]['id']}/ack", headers=SLAVE).status_code == 200


def test_sold_alert_requires_sell_panel_and_valid_count(tmp_path):
    app = create_app(Settings(tmp_path / "db.sqlite3", "m" * 40, {"slave": "s" * 40}))
    with TestClient(app) as client:
        assert snapshot(client, "session-1234567890abcdef", "stall_buy", 19).status_code == 422
        assert snapshot(client, "session-1234567890abcdef", "stall", 21).status_code == 422
        assert snapshot(client, "session-1234567890abcdef", "stall", True).status_code == 422
        assert snapshot(client, "session-1234567890abcdef", "stall", 20).status_code == 202
        assert snapshot(client, "session-1234567890abcdef", "stall", 19).status_code == 202
        assert role(client)["sell_stock_count"] == 19
        assert sale_events(client) == []  # Account switch is off by default.


def test_brief_buy_panel_event_updates_role_even_before_next_status(tmp_path):
    app = create_app(Settings(tmp_path / "db.sqlite3", "m" * 40, {"slave": "s" * 40}))
    with TestClient(app) as client:
        session = "session-1234567890abcdef"
        assert snapshot(client, session).status_code == 202
        now = datetime.now(UTC).isoformat(timespec="milliseconds")
        result = client.post("/api/v1/slave/monitor-logs", headers=SLAVE, json={
            "event_id": "stall-buy-brief", "event_type": "panel_opened",
            "panel_type": "stall_buy", "reason": "recognition", "occurred_at": now,
            "window_id": "hwnd:1", "character_id": "123", "monitor_session_id": session,
        })
        assert result.status_code == 202
        assert role(client)["stall_mode"] == "buy"
