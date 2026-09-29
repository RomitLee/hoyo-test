from datetime import datetime

import pytest

from hoyo_analyzer.relay_client import make_event_payload, normalize_relay_url


def test_repeated_success_emits_connection_result() -> None:
    from PySide6.QtWidgets import QApplication

    from hoyo_analyzer.relay_client import SlaveRelayClient

    app = QApplication.instance() or QApplication([])
    client = SlaveRelayClient(app)
    results = []
    client.connection_changed.connect(lambda online, message: results.append(online))
    client._set_online(True, "connected")
    client._set_online(True, "reconnected")
    assert results == [True, True]


def test_master_polls_history_and_status_without_overlapping(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication

    from hoyo_analyzer.relay_client import MasterRelayClient

    app = QApplication.instance() or QApplication([])
    client = MasterRelayClient(app)
    client.configure("https://example.com", "test")
    requests = []
    results = []
    monkeypatch.setattr(client, "_get", lambda path, kind: requests.append((path, kind)))
    client.connection_changed.connect(lambda online, message: results.append(online))
    client.poll()
    client.poll()
    assert len(requests) == 2
    assert "state=all" in requests[0][0]
    client._finish_poll_kind("status", "HTTP 404")
    client._finish_poll_kind("events")
    assert results == [False]  # Event success cannot hide a broken status endpoint.
    client.poll()
    assert len(requests) == 4


def test_master_history_paginates_and_emits_only_complete_snapshot(monkeypatch) -> None:
    import json
    from types import SimpleNamespace

    from PySide6.QtNetwork import QNetworkReply
    from PySide6.QtWidgets import QApplication

    from hoyo_analyzer.relay_client import MasterRelayClient

    app = QApplication.instance() or QApplication([])
    client = MasterRelayClient(app)
    client._pending_kinds = {"events", "status"}
    requests, snapshots = [], []
    monkeypatch.setattr(client, "_get", lambda path, kind: requests.append(path))
    client.events_received.connect(snapshots.append)

    def reply(events):
        return SimpleNamespace(
            property=lambda key: "events" if key == "relay_kind" else None,
            attribute=lambda key: 200,
            error=lambda: QNetworkReply.NetworkError.NoError,
            readAll=lambda: json.dumps({"events": events}).encode(),
            deleteLater=lambda: None,
        )

    client._on_finished(reply([{"server_event_id": i} for i in range(1, 501)]))
    assert snapshots == []
    assert "after_id=500" in requests[-1]
    client._on_finished(reply([{"server_event_id": 501}]))
    assert len(snapshots[0]) == 501
    assert client._pending_kinds == {"status"}


def test_relay_url_requires_https() -> None:
    assert normalize_relay_url(" https://relay.example.com/ ") == "https://relay.example.com"
    with pytest.raises(ValueError, match="https"):
        normalize_relay_url("http://relay.example.com")


def test_event_payload_matches_server_contract() -> None:
    payload = make_event_payload(
        "inventory_full",
        server_name="秦淮风光",
        role_name="清风知夏",
        character_id="54588235",
        empty_slots=0,
        confidence=0.96,
    )

    assert len(payload["event_id"]) == 32
    assert payload["event_type"] == "inventory_full"
    assert datetime.fromisoformat(payload["occurred_at"]).tzinfo is not None
    assert payload["server_name"] == "秦淮风光"
    assert payload["empty_slots"] == 0
