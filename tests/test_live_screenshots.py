import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hoyo_analyzer.live_screenshots import LiveScreenshotClient


def test_live_screenshot_permissions_are_requested_serially(monkeypatch):
    app = QApplication.instance() or QApplication([])
    client = LiveScreenshotClient()
    client._token = "token"
    calls = []
    monkeypatch.setattr(client, "_post", lambda path, data, callback: calls.append((path, data, callback)))

    client.upload("hwnd:1", b"first", datetime.now(UTC).isoformat())
    client.upload("hwnd:2", b"second", datetime.now(UTC).isoformat())
    assert [call[1]["window_id"] for call in calls] == ["hwnd:1"]

    first_capture = client._pending["hwnd:1"][3]
    client._on_permission("hwnd:1", first_capture, None)
    assert [call[1]["window_id"] for call in calls] == ["hwnd:1", "hwnd:2"]
    client._timer.stop()
