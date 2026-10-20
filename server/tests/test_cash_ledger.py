from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from app import cash_ledger
from app.database import RelayDatabase


def database(tmp_path: Path) -> RelayDatabase:
    db = RelayDatabase(tmp_path / "cash.sqlite3")
    db.initialize()
    return db


def observe(db, cash, second, *, stream="run", sequence=None, active=True, character="123", account=1, device="slave"):
    return db.record_cash(account, device, {
        "character_id": character, "event_id": f"{stream}:{second}:{sequence}:{active}",
        "stream_id": stream, "sequence": sequence or second + 1,
        "occurred_at": f"2026-10-19T12:00:{second:02d}+08:00", "cash": cash,
        "active": active, "role_name": "角色", "server_name": "区服",
    })


def test_example_topups_are_excluded_from_purchase_spending(tmp_path):
    db = database(tmp_path)
    observe(db, 10000000, 0)
    observe(db, 20000000, 5)
    observe(db, 3000000, 10)
    row = db.cash_statistics(1, "2026-10-19")["daily"][0]
    assert (row["opening_cash"], row["closing_cash"]) == (10000000, 3000000)
    assert row["topups"] == 10000000
    assert row["purchases"] == 17000000
    assert row["unobserved_change"] == 0
    assert row["monitored_seconds"] == 10


def test_stop_gap_and_restart_never_count_interruption_differences(tmp_path):
    db = database(tmp_path)
    observe(db, 1000, 0)
    observe(db, 900, 5)
    observe(db, None, 6, active=False)
    observe(db, 2000, 10, stream="restart")
    observe(db, 1800, 15, stream="restart")
    row = db.cash_statistics(1, "2026-10-19")["daily"][0]
    assert row["topups"] == 0
    assert row["purchases"] == 300
    assert row["unobserved_change"] == 1100
    assert row["restarts"] == 1
    assert row["monitored_seconds"] == 10


def test_long_silence_rebuilds_baseline_even_without_explicit_stop(tmp_path):
    db = database(tmp_path)
    observe(db, 1000, 0)
    observe(db, 100, 20)
    observe(db, 80, 25)
    row = db.cash_statistics(1, "2026-10-19")["daily"][0]
    assert row["purchases"] == 20
    assert row["unobserved_change"] == -900
    assert row["monitored_seconds"] == 5


def test_duplicates_and_out_of_order_do_not_change_totals(tmp_path):
    db = database(tmp_path)
    observe(db, 1000, 0)
    assert observe(db, 900, 5) == "recorded"
    assert observe(db, 900, 5) == "duplicate"
    assert observe(db, 100, 3) == "out_of_order"
    assert db.cash_statistics(1, "2026-10-19")["daily"][0]["purchases"] == 100


def test_id_is_unique_across_windows_but_isolated_across_accounts(tmp_path):
    db = database(tmp_path)
    observe(db, 1000, 0)
    observe(db, 900, 5)
    assert observe(db, 999999, 6, device="other") == "source_conflict"
    observe(db, 5000, 21, device="other", stream="new-window")
    observe(db, 4900, 26, device="other", stream="new-window")
    observe(db, 1234, 0, account=2, device="tenant2")
    assert len(db.cash_statistics(1, "2026-10-19")["daily"]) == 1
    assert db.cash_statistics(1, "2026-10-19")["daily"][0]["purchases"] == 200
    assert db.cash_statistics(2, "2026-10-19")["daily"][0]["closing_cash"] == 1234


def test_natural_hour_and_beijing_day_boundaries(tmp_path):
    db = database(tmp_path)
    for i, (stamp, cash) in enumerate([
        ("2026-10-19T15:59:55Z", 1000), ("2026-10-19T15:59:59Z", 900),
        ("2026-10-19T16:00:02Z", 800), ("2026-10-19T18:00:00Z", 500),
    ]):
        db.record_cash(1, "slave", {"character_id": "123", "event_id": str(i), "stream_id": "run",
                                  "sequence": i + 1, "occurred_at": stamp, "cash": cash, "active": True})
    yesterday = db.cash_statistics(1, "2026-10-19")
    today = db.cash_statistics(1, "2026-10-20")
    assert yesterday["hourly"][0]["hour"] == 23
    assert yesterday["daily"][0]["purchases"] == 100
    assert [r["hour"] for r in today["hourly"]] == [0, 2]  # No fabricated 01:00 row.
    assert today["daily"][0]["purchases"] == 100
    assert today["daily"][0]["unobserved_change"] == -300
    assert today["hourly"][0]["monitored_seconds"] == 2


def test_statistics_survive_server_restart_and_rename(tmp_path):
    db = database(tmp_path)
    observe(db, 1000, 0)
    db = RelayDatabase(db.path)
    db.initialize()
    observe(db, 900, 5)
    assert db.cash_statistics(1, "2026-10-19")["daily"][0]["purchases"] == 100
    db.record_cash(1, "slave", {"character_id": "123", "event_id": "renamed", "stream_id": "run",
                              "sequence": 11, "occurred_at": "2026-10-19T12:00:10+08:00",
                              "cash": 800, "active": True, "role_name": "新昵称"})
    rows = db.cash_statistics(1, "2026-10-19")["daily"]
    assert len(rows) == 1
    assert rows[0]["role_name"] == "新昵称" and rows[0]["purchases"] == 200
    assert db.cash_statistics(1, "2026-10-18")["hourly"] == []


def test_full_day_continuous_monitoring_produces_24_hour_rows(tmp_path):
    db = database(tmp_path)
    start = datetime.fromisoformat("2026-10-18T00:00:00+08:00")
    with db._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for index in range(5761):
            cash_ledger.record(connection, 1, "slave", {
                "character_id": "123", "event_id": str(index), "stream_id": "full-day",
                "sequence": index + 1, "occurred_at": (start + timedelta(seconds=15 * index)).isoformat(),
                "cash": 1000000 - index, "active": True,
            })
    result = db.cash_statistics(1, "2026-10-18")
    assert [row["hour"] for row in result["hourly"]] == list(range(24))
    assert all(row["monitored_seconds"] == 3600 for row in result["hourly"])
    assert result["daily"][0]["purchases"] == 5759
    assert db.cash_statistics(1, "2026-10-19")["daily"][0]["purchases"] == 1
