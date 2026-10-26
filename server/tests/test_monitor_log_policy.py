from datetime import UTC, datetime, timedelta

from app import database as module
from app.database import RelayDatabase


def test_occlusion_requires_continuous_observations_and_resets_on_interruptions(tmp_path, monkeypatch):
    db = RelayDatabase(tmp_path / "rules.sqlite3")
    db.initialize()
    db.register_legacy_device("device", "hash")
    base = datetime(2026, 10, 23, tzinfo=UTC)

    def report(second, percent=80, *, monitoring=True, minimized=False, missing=False):
        timestamp = (base + timedelta(seconds=second)).isoformat()
        monkeypatch.setattr(module, "utc_now", lambda: timestamp)
        windows = [] if missing else [{"window_id": "one", "occlusion_percent": percent,
                                       "monitoring": monitoring, "minimized": minimized, "updated_at": timestamp}]
        db.replace_window_status("device", windows, timestamp)
        return len(db.list_monitor_logs(1, event_type="window_occluded"))

    assert report(0, 60) == 0
    assert report(4) == 0
    assert report(5, 60) == 1
    assert report(6, 59) == 1
    assert report(7) == 1
    assert report(12) == 2
    assert report(13, monitoring=False) == 2
    assert report(18) == 2
    assert report(19, minimized=True) == 2
    assert report(24) == 2
    assert report(45) == 2  # A 21-second reporting gap starts a new interval.
    assert report(50) == 3
    assert report(50) == 3  # A duplicate cannot advance timing or create a row.
    assert report(51, missing=True) == 3
    assert report(56) == 3
    assert report(61) == 4
    values = {"occlusion_enabled": False, "occlusion_percent": 80, "occlusion_seconds": 10}
    db.update_monitor_log_settings(1, values)
    assert report(66, 100) == 4
    db.update_monitor_log_settings(1, {**values, "occlusion_enabled": True})
    assert report(70, 79) == 4
    assert report(80, 80) == 4
    assert report(89, 80) == 4
    assert report(90, 80) == 5
    assert db.get_monitor_log_settings(2)["occlusion_seconds"] == 5


def test_client_clock_cannot_satisfy_duration_without_elapsed_server_time(tmp_path, monkeypatch):
    db = RelayDatabase(tmp_path / "rules.sqlite3")
    db.initialize()
    db.register_legacy_device("device", "hash")
    base = datetime(2026, 10, 23, tzinfo=UTC)
    for received, sample in ((0, 0), (1, 3600)):
        monkeypatch.setattr(module, "utc_now", lambda value=received: (base + timedelta(seconds=value)).isoformat())
        db.replace_window_status("device", [{"window_id": "one", "occlusion_percent": 99,
                                             "updated_at": (base + timedelta(seconds=sample)).isoformat()}])
    assert db.list_monitor_logs(1) == []
