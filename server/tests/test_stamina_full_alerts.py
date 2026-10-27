from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


MASTER = {"Authorization": "Bearer " + "m" * 40}
SLAVE = {"Authorization": "Bearer " + "s" * 40, "X-Device-ID": "slave"}


def make_client(tmp_path):
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {"slave": "s" * 40}))
    return TestClient(app)


def observe(client, event_id, when, current, maximum=100):
    return client.post("/api/v1/slave/monitor-logs", headers=SLAVE, json={
        "event_id": event_id, "event_type": "panel_opened", "panel_type": "attributes",
        "reason": "recognition", "occurred_at": when.isoformat(), "window_id": "hwnd:1",
        "server_name": "秦淮风光", "role_name": "清风知夏", "character_id": "123",
        "character_level": 100, "stamina_current": current,
        "stamina_max": maximum, "stamina_observed_at": when.isoformat(),
    })


def events(client):
    return client.get("/api/v1/master/events?state=all", headers=MASTER).json()["events"]


def test_measured_full_is_one_alert_until_a_new_recovery_cycle(tmp_path):
    with make_client(tmp_path) as client:
        database = client.app.state.database
        now = datetime.now(UTC).replace(microsecond=0)
        assert observe(client, "full-1", now, 100).status_code == 202
        assert database.check_stamina_full_alerts(now + timedelta(seconds=1)) == 0
        assert events(client) == []  # The account-level switch defaults to off.
        saved = client.put("/api/v1/master/notifications/bark", headers=MASTER,
                           json={"enabled": False, "local_alert_enabled": True,
                                 "stamina_full_enabled": True})
        assert saved.status_code == 200 and saved.json()["stamina_full_enabled"] is True
        assert database.check_stamina_full_alerts(now + timedelta(seconds=2)) == 1
        assert database.check_stamina_full_alerts(now + timedelta(seconds=3)) == 0
        database.initialize()
        assert database.check_stamina_full_alerts(now + timedelta(seconds=3)) == 0
        alert = events(client)[0]
        assert alert["event_type"] == "stamina_full"
        assert alert["payload"]["details"]["source"] == "measured"
        pending = client.post("/api/v1/slave/status", headers=SLAVE,
                              json={"reported_at": now.isoformat(), "windows": []}).json()["local_stamina_alerts"]
        assert len(pending) == 1 and pending[0]["id"] == alert["server_event_id"]
        assert client.post(f"/api/v1/slave/local-alerts/{pending[0]['id']}/ack", headers=SLAVE).status_code == 200
        assert client.post("/api/v1/slave/heartbeat", headers=SLAVE,
                           json={"hostname": "machine", "app_version": "V2.11.0"}).json()["local_stamina_alerts"] == []
        assert observe(client, "lower", now + timedelta(seconds=4), 90).status_code == 202
        assert database.check_stamina_full_alerts(now + timedelta(seconds=5)) == 0
        assert observe(client, "full-2", now + timedelta(seconds=6), 100).status_code == 202
        assert database.check_stamina_full_alerts(now + timedelta(seconds=7)) == 1
        assert len(events(client)) == 2


def test_prediction_requires_uninterrupted_online_monitoring(tmp_path):
    with make_client(tmp_path) as client:
        database = client.app.state.database
        now = datetime.now(UTC).replace(microsecond=0)
        client.put("/api/v1/master/notifications/bark", headers=MASTER,
                   json={"enabled": False, "stamina_full_enabled": True})
        assert observe(client, "low", now, 99).status_code == 202
        assert client.post("/api/v1/slave/status", headers=SLAVE, json={
            "reported_at": now.isoformat(), "windows": [{"window_id": "hwnd:1",
                "character_id": "123", "status": "not_full", "monitoring": True,
                "updated_at": now.isoformat()}],
        }).status_code == 202
        assert database.check_stamina_full_alerts(now) == 0
        for seconds in range(10, 301, 10):
            tick = now + timedelta(seconds=seconds)
            with database._connect() as connection:
                connection.execute("UPDATE devices SET last_seen_at=? WHERE device_id='slave'", (tick.isoformat(),))
                connection.execute("UPDATE device_window_status SET received_at=? WHERE device_id='slave'",
                                   (tick.isoformat(),))
            assert database.check_stamina_full_alerts(tick) == (1 if seconds == 300 else 0)
        assert events(client)[0]["payload"]["details"]["source"] == "predicted"
        assert database.check_stamina_full_alerts(now + timedelta(seconds=310)) == 0

    with make_client(tmp_path / "interrupted") as client:
        database = client.app.state.database
        client.put("/api/v1/master/notifications/bark", headers=MASTER,
                   json={"enabled": False, "stamina_full_enabled": True})
        assert observe(client, "low", now, 99).status_code == 202
        assert database.check_stamina_full_alerts(now) == 0
        assert database.check_stamina_full_alerts(now + timedelta(seconds=30)) == 0
        due = now + timedelta(minutes=5)
        with database._connect() as connection:
            connection.execute("UPDATE devices SET last_seen_at=? WHERE device_id='slave'", (due.isoformat(),))
        assert database.check_stamina_full_alerts(due) == 0
        assert events(client) == []
