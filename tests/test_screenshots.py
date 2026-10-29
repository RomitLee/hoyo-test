import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication

from hoyo_analyzer.models import Event
from hoyo_analyzer.screenshots import ScreenshotUploader, capture_panel_screenshot


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_original_game_frame_saved_only_for_open_or_confirmed_target_change(tmp_path, monkeypatch):
    monkeypatch.setattr("hoyo_analyzer.screenshots.application_root", lambda: tmp_path)
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:, 400:] = (0, 255, 0)
    event = Event("open", 100, "panel_opened", payload={"panel_type": "give"})
    path = capture_panel_screenshot(event, image)
    assert path == event.evidence_path
    decoded = cv2.imdecode(np.frombuffer(Path(path).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == image.shape
    assert decoded[240, 500, 1] > 240
    for kind, panel, status in (
        ("panel_closed", "give", None),
        ("panel_opened", "inventory", None),
        ("panel_target_changed", "trade", "unrecognized"),
    ):
        assert (
            capture_panel_screenshot(
                Event("skip", 100, kind, payload={"panel_type": panel, "target_status": status}), image
            )
            is None
        )
    changed = Event(
        "changed", 200, "panel_target_changed", payload={"panel_type": "trade", "target_status": "recognized"}
    )
    assert capture_panel_screenshot(changed, image)
    attributes = Event("attributes", 300, "panel_opened", payload={"panel_type": "attributes"})
    assert capture_panel_screenshot(attributes, image)
    assert attributes.evidence_path is not None
    market = Event("market", 400, "panel_opened", payload={"panel_type": "stall_other_buy"})
    assert capture_panel_screenshot(market, image)
    assert market.evidence_path is not None


def test_capture_disk_failure_does_not_interrupt_recognition(tmp_path, monkeypatch):
    monkeypatch.setattr("hoyo_analyzer.screenshots.application_root", lambda: tmp_path)
    (tmp_path / "runtime").write_text("file blocks directory")
    assert (
        capture_panel_screenshot(
            Event("a", 0, "panel_opened", payload={"panel_type": "trade"}), np.zeros((480, 640, 3), np.uint8)
        )
        is None
    )


def test_queue_survives_restart_failure_and_never_uses_another_device(app, tmp_path, monkeypatch):
    directory = tmp_path / "outbox"
    directory.mkdir()
    path = directory / "sample.jpg"
    path.write_bytes(b"jpeg bytes")
    sender = ScreenshotUploader(directory=directory)
    sender.timer.stop()
    sender.configure("https://relay.example", "device-a", "secret-a")
    sender.enqueue("event-123", str(path))
    sender.configure("", "", "")
    sender.deleteLater()
    restored = ScreenshotUploader(directory=directory)
    restored.timer.stop()
    restored.configure("https://relay.example", "device-b", "secret-b")
    calls = []
    replies = []

    class Reply(QObject):
        finished = Signal()

        def __init__(self, parent):
            super().__init__(parent)
            self.status = None
            self.network_error = QNetworkReply.NetworkError.NoError

        def abort(self):
            pass

        def attribute(self, _key):
            return self.status

        def setAttribute(self, _key, value):
            self.status = value

        def error(self):
            return self.network_error

        def setError(self, value, _message):
            self.network_error = value

    def put(request, body):
        calls.append((request.url().toString(), bytes(request.rawHeader("Authorization")), bytes(body)))
        reply = Reply(restored)
        replies.append(reply)
        return reply

    monkeypatch.setattr(restored.manager, "put", put)
    restored.retry()
    assert not calls and path.exists()
    restored.configure("https://relay.example", "device-a", "secret-a")
    restored.retry()
    assert calls[0][0].endswith("/event-123/screenshot")
    assert calls[0][1] == b"Bearer secret-a"
    replies[-1].setAttribute(None, 503)
    replies[-1].setError(QNetworkReply.NetworkError.ServiceUnavailableError, "retry")
    replies[-1].finished.emit()
    assert path.exists() and restored.queue and not restored.inflight
    restored.retry()
    replies[-1].setAttribute(None, 200)
    replies[-1].finished.emit()
    assert not path.exists() and not restored.queue
    assert json.loads(restored.manifest.read_text()) == []
    restored.deleteLater()


def test_invalid_queue_path_cannot_upload_arbitrary_local_file(app, tmp_path):
    sender = ScreenshotUploader(directory=tmp_path / "outbox")
    sender.timer.stop()
    path = tmp_path / "private.txt"
    path.write_text("not a screenshot")
    sender.enqueue("event", str(path))
    assert not sender.queue
    sender.deleteLater()
