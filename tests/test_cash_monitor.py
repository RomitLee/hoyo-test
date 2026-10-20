from datetime import UTC, datetime, timedelta

from hoyo_analyzer.cash_monitor import CashMonitor

START = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)


def signal(cash=1000, *, detected=True, readable=True):
    return {"cash": cash, "cash_detected": detected, "cash_readable": readable}


def test_changes_are_not_lost_between_five_second_snapshots():
    monitor = CashMonitor({"character_id": "123"})
    observations = []
    for second, cash in ((0, 1000), (1, 2000), (2, 500)):
        observations.extend(monitor.update(signal(cash), True, START + timedelta(seconds=second)))
    assert [row["cash"] for row in observations] == [1000, 2000, 500]
    assert len({row["stream_id"] for row in observations}) == 1
    assert [row["sequence"] for row in observations] == [1, 2, 3]


def test_unreadable_and_disconnection_both_end_continuity():
    monitor = CashMonitor({"character_id": "123"})
    old = monitor.update(signal(), True, START)[0]
    assert monitor.update(signal(readable=False), True, START + timedelta(seconds=1)) == []
    end = monitor.update(signal(detected=False), True, START + timedelta(seconds=2))[0]
    assert not end["active"]
    assert end["stream_id"] == old["stream_id"]
    assert monitor.update(signal(), False, START + timedelta(seconds=3)) == []
    new = monitor.update(signal(9000), True, START + timedelta(seconds=4))[0]
    assert new["stream_id"] != old["stream_id"]


def test_heartbeat_and_natural_hour_boundary_with_unchanged_cash():
    monitor = CashMonitor({"character_id": "123"})
    start = START.replace(minute=59, second=59)
    monitor.update(signal(), True, start)
    assert len(monitor.update(signal(), True, start + timedelta(seconds=1))) == 1
    assert monitor.update(signal(), True, start + timedelta(seconds=2)) == []
    assert len(monitor.update(signal(), True, start + timedelta(seconds=6))) == 1


def test_long_frame_gap_starts_a_new_stream():
    monitor = CashMonitor({"character_id": "123"})
    old = monitor.update(signal(), True, START)[0]
    end, new = monitor.update(signal(800), True, START + timedelta(seconds=20))
    assert not end["active"]
    assert new["active"] and new["stream_id"] != old["stream_id"]
    assert end["occurred_at"] < new["occurred_at"]


def test_queued_pre_interruption_frame_cannot_reopen_monitoring():
    monitor = CashMonitor({"character_id": "123"})
    monitor.update(signal(), True, START)
    monitor.stop(START + timedelta(seconds=2))
    assert monitor.update(signal(900), True, START + timedelta(seconds=1)) == []
    assert monitor.update(signal(900), True, START + timedelta(seconds=3))[0]["active"]
