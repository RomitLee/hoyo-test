from pathlib import Path

import pytest
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

TOKEN = "test-device-" + "x" * 40
HEADERS = {"X-Device-ID": "target-slave", "Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(database_path=tmp_path / "db.sqlite3", master_token="", device_tokens={"target-slave": TOKEN})
    with TestClient(create_app(settings)) as client:
        yield client


def payload(**values):
    return {"event_id": "target-001", "event_type": "panel_target_changed", "panel_type": "give",
            "occurred_at": "2026-10-23T18:00:00+08:00", "reason": "recognition", "window_id": "hwnd:10",
            "target_name": "图图防御符4", "target_level": 144, "target_confidence": 0.98,
            "target_status": "recognized", **values}


def register(client, name):
    result = client.post("/api/v1/auth/register", json={"username": name, "password": "password123"})
    assert result.status_code == 201
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def test_target_metadata_survives_retry_and_remains_account_isolated(client):
    owner = register(client, "target_owner")
    other = register(client, "target_other")
    assert client.post("/api/v1/slave/monitor-logs", headers=HEADERS, json=payload()).status_code == 202
    # A retry with the same event id must not replace the original recipient.
    assert client.post("/api/v1/slave/monitor-logs", headers=HEADERS, json=payload(target_name="不同玩家")).status_code == 202
    result = client.get("/api/v1/master/monitor-logs?panel_type=give", headers=owner).json()
    assert len(result["logs"]) == 1
    row = result["logs"][0]
    assert row["target_name"] == "图图防御符4" and row["target_level"] == 144
    assert row["target_status"] == "recognized" and row["target_confidence"] == 0.98
    assert client.get("/api/v1/master/monitor-logs", headers=other).json()["logs"] == []
    assert client.get("/api/v1/master/monitor-logs?panel_type=trade", headers=owner).json()["logs"] == []


def test_unrecognized_log_never_reuses_last_recipient_and_old_clients_still_work(client):
    owner = register(client, "target_owner")
    response = client.post("/api/v1/slave/monitor-logs", headers=HEADERS,
                           json=payload(target_name=None, target_level=None, target_confidence=None, target_status="unrecognized"))
    assert response.status_code == 202
    old = {key: value for key, value in payload(event_id="old", event_type="panel_opened").items()
           if not key.startswith("target_")}
    assert client.post("/api/v1/slave/monitor-logs", headers=HEADERS, json=old).status_code == 202
    logs = client.get("/api/v1/master/monitor-logs", headers=owner).json()["logs"]
    assert len(logs) == 1  # Unreadability is not a recipient change.
    assert all(row["target_name"] is None and row["target_level"] is None for row in logs)


@pytest.mark.parametrize("changes", [
    {"target_level": None}, {"target_name": None}, {"target_level": 999}, {"target_level": True},
    {"target_status": "unrecognized"}, {"target_status": None}, {"target_confidence": 1.5},
    {"panel_type": "inventory"}, {"target_name": "玩家\n名字"},
    {"target_status": "unrecognized", "target_name": None, "target_level": None},
])
def test_invalid_or_partial_recipient_is_rejected(client, changes):
    assert client.post("/api/v1/slave/monitor-logs", headers=HEADERS, json=payload(**changes)).status_code == 422


def report(client, **changes):
    data = payload(**changes)
    response = client.post("/api/v1/slave/monitor-logs", headers=HEADERS, json=data)
    assert response.status_code == 202, response.text
    return data


def rows(client, owner, panel="give"):
    return client.get(f"/api/v1/master/monitor-logs?panel_type={panel}", headers=owner).json()["logs"]


@pytest.mark.parametrize("panel", ["give", "trade"])
def test_initial_confirmation_completes_original_open_log_and_retries_cannot_reset_it(client, panel):
    owner = register(client, "target_owner")
    opening = report(client, event_id="open", event_type="panel_opened", panel_type=panel,
                     target_status="unrecognized", target_name=None, target_level=None, target_confidence=None)
    original = rows(client, owner, panel)[0]
    assert original["message"].endswith("对象未识别")
    report(client, panel_type=panel, occurred_at="2026-10-23T18:00:03+08:00")
    result = rows(client, owner, panel)
    assert len(result) == 1
    assert result[0]["id"] == original["id"]
    assert result[0]["client_event_id"] == "open"
    assert result[0]["event_type"] == "panel_opened"
    assert result[0]["occurred_at"] == original["occurred_at"]
    assert result[0]["target_name"] == "图图防御符4" and result[0]["target_level"] == 144
    assert result[0]["target_confirmed_at"].startswith("2026-10-23T18:00:03")
    report(client, **opening)
    report(client, panel_type=panel, target_name="重试不能改名", occurred_at="2026-10-23T18:00:04+08:00")
    assert rows(client, owner, panel)[0]["target_name"] == "图图防御符4"
    assert len(rows(client, owner, panel)) == 1


def test_only_real_recipient_changes_add_rows_and_ocr_recovery_does_not(client):
    owner = register(client, "target_owner")
    report(client, event_id="open", event_type="panel_opened", target_name="甲")
    report(client, event_id="same", target_name="甲", occurred_at="2026-10-23T18:00:01+08:00")
    report(client, event_id="changed", target_name="乙", occurred_at="2026-10-23T18:00:02+08:00")
    report(client, event_id="lost", target_name=None, target_level=None, target_confidence=None,
           target_status="unrecognized", occurred_at="2026-10-23T18:00:03+08:00")
    report(client, event_id="recovered", target_name="乙", occurred_at="2026-10-23T18:00:04+08:00")
    assert len(rows(client, owner)) == 2
    report(client, event_id="back", target_name="甲", occurred_at="2026-10-23T18:00:05+08:00")
    assert [row["target_name"] for row in rows(client, owner)] == ["甲", "乙", "甲"]
    # A delayed retry of the first confirmation must not undo the later change.
    client.app.state.database.initialize()
    report(client, event_id="same", target_name="甲", occurred_at="2026-10-23T18:00:01+08:00")
    assert len(rows(client, owner)) == 3


@pytest.mark.parametrize("boundary", ["monitoring_started", "monitoring_paused", "game_default", "panel_closed", "panel_opened", "window_minimized"])
def test_confirmation_does_not_cross_monitoring_or_panel_boundaries(client, boundary):
    owner = register(client, "target_owner")
    report(client, event_id="open", event_type="panel_opened", target_status="unrecognized",
           target_name=None, target_level=None, target_confidence=None)
    data = {k: v for k, v in payload(event_id="boundary", event_type=boundary,
            occurred_at="2026-10-23T18:00:01+08:00").items() if not k.startswith("target_")}
    data["panel_type"] = "trade" if boundary == "panel_opened" else None
    if boundary.startswith("monitoring_"):
        data["window_id"] = None
    client.app.state.database.insert_monitor_log(1, "target-slave", data)
    report(client, event_id="after", occurred_at="2026-10-23T18:00:02+08:00")
    logs = rows(client, owner)
    assert next(row for row in logs if row["client_event_id"] == "open")["target_name"] is None
    assert next(row for row in logs if row["client_event_id"] == "after")["target_name"] == "图图防御符4"


@pytest.mark.parametrize("scope", [{"window_id": "hwnd:11"}, {"character_id": "another"},
                                   {"server_name": "another"}, {"role_name": "another"}])
def test_confirmation_never_completes_another_window_or_character(client, scope):
    owner = register(client, "target_owner")
    report(client, event_id="open", event_type="panel_opened", target_status="unrecognized",
           target_name=None, target_level=None, target_confidence=None)
    report(client, event_id="other", occurred_at="2026-10-23T18:00:01+08:00", **scope)
    logs = rows(client, owner)
    assert len(logs) == 2
    assert next(row for row in logs if row["client_event_id"] == "open")["target_name"] is None


def test_late_confirmation_does_not_attach_to_a_later_reopened_dialog(client):
    owner = register(client, "target_owner")
    report(client, event_id="first", event_type="panel_opened", target_status="unrecognized",
           target_name=None, target_level=None, target_confidence=None)
    closed = {k: v for k, v in payload(event_id="closed", event_type="game_default",
              panel_type=None, occurred_at="2026-10-23T18:00:10+08:00").items() if not k.startswith("target_")}
    client.app.state.database.insert_monitor_log(1, "target-slave", closed)
    report(client, event_id="second", event_type="panel_opened", target_status="unrecognized",
           target_name=None, target_level=None, target_confidence=None, occurred_at="2026-10-23T18:00:20+08:00")
    report(client, event_id="late", target_name="甲", occurred_at="2026-10-23T18:00:05+08:00")
    logs = rows(client, owner)
    assert next(row for row in logs if row["client_event_id"] == "first")["target_name"] == "甲"
    assert next(row for row in logs if row["client_event_id"] == "second")["target_name"] is None
    report(client, event_id="new", target_name="乙", occurred_at="2026-10-23T18:00:21+08:00")
    assert len(rows(client, owner)) == 2


def test_equivalent_timezone_offsets_are_compared_as_actual_times(client):
    owner = register(client, "target_owner")
    report(client, event_id="open", event_type="panel_opened", target_status="unrecognized",
           target_name=None, target_level=None, target_confidence=None, occurred_at="2026-10-23T10:00:00Z")
    report(client, occurred_at="2026-10-23T18:00:01+08:00")
    assert len(rows(client, owner)) == 1
    assert rows(client, owner)[0]["target_name"] == "图图防御符4"


def test_simultaneous_confirmations_only_complete_one_opening_row(client):
    from concurrent.futures import ThreadPoolExecutor

    owner = register(client, "target_owner")
    report(client, event_id="open", event_type="panel_opened", target_status="unrecognized",
           target_name=None, target_level=None, target_confidence=None)
    db = client.app.state.database
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = [pool.submit(db.insert_monitor_log, 1, "target-slave", payload(event_id=f"concurrent-{i}",
                occurred_at="2026-10-23T18:00:01+08:00")) for i in range(2)]
        for result in work:
            result.result()
    assert len(rows(client, owner)) == 1


def test_delayed_first_recognition_still_completes_opening_without_arbitrary_timeout(client):
    owner = register(client, "target_owner")
    report(client, event_id="open", event_type="panel_opened", target_status="unrecognized",
           target_name=None, target_level=None, target_confidence=None)
    report(client, occurred_at="2026-10-23T18:10:00+08:00")
    assert len(rows(client, owner)) == 1
    assert rows(client, owner)[0]["target_name"] == "图图防御符4"
    assert client.get("/api/v1/master/monitor-logs?event_type=panel_target_changed", headers=owner).json()["logs"] == []


def test_confirmation_cannot_merge_across_devices_or_accounts(client):
    owner = register(client, "target_owner")
    report(client, event_id="open", event_type="panel_opened", target_status="unrecognized",
           target_name=None, target_level=None, target_confidence=None)
    db = client.app.state.database
    db.insert_monitor_log(1, "another-device", payload(event_id="another-device", occurred_at="2026-10-23T18:00:01+08:00"))
    db.insert_monitor_log(2, "target-slave", payload(event_id="another-account", occurred_at="2026-10-23T18:00:02+08:00"))
    assert next(row for row in rows(client, owner) if row["client_event_id"] == "open")["target_name"] is None
    assert len(db.list_monitor_logs(2)) == 1


def test_legacy_unreadable_history_is_kept_but_does_not_trigger_recovery_duplicates(client):
    owner = register(client, "target_owner")
    report(client, event_id="open", event_type="panel_opened", target_name="甲")
    db = client.app.state.database
    with db._connect() as connection:
        connection.execute("""INSERT INTO monitor_logs(account_id,device_id,client_event_id,event_type,window_id,
            occurred_at,received_at,panel_type,target_status) VALUES (1,'target-slave','legacy-unknown',
            'panel_target_changed','hwnd:10','2026-10-23T18:00:01+08:00','2026-10-23T18:00:01+08:00','give','unrecognized')""")
    report(client, event_id="recovery", target_name="甲", occurred_at="2026-10-23T18:00:02+08:00")
    logs = rows(client, owner)
    assert len(logs) == 2
    assert any(row["client_event_id"] == "legacy-unknown" for row in logs)
    assert all(row["client_event_id"] != "recovery" for row in logs)
