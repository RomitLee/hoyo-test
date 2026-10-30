"""Capture event frames and retry screenshot delivery independently of log uploads."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from time import time
from urllib.parse import quote, urljoin
from uuid import uuid4

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .paths import application_root

logger = logging.getLogger(__name__)
MAX_BYTES = 2 * 1024 * 1024
EVENT_SCREENSHOT_PANELS = ("give", "trade", "attributes", "stall", "stall_buy", "stall_other_buy")


def capture_panel_screenshot(event, image, enabled_panels=EVENT_SCREENSHOT_PANELS) -> str | None:
    panel_type = event.payload.get("panel_type")
    if panel_type not in EVENT_SCREENSHOT_PANELS or panel_type not in enabled_panels:
        return None
    if event.type not in {"panel_opened", "market_stall_changed"} and not (
        event.type == "panel_target_changed" and event.payload.get("panel_type") in {"give", "trade"}
        and event.payload.get("target_status") == "recognized"
    ):
        return None
    # Master packages omit OpenCV; only the slave's capture path needs it.
    import cv2

    try:
        directory = application_root() / "runtime" / "screenshot-outbox"
        directory.mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if not ok or encoded.nbytes > MAX_BYTES:
            logger.warning("截图编码失败或超过2MB，事件日志仍将正常上报")
            return None
        path = directory / f"{uuid4().hex}.jpg"
        path.write_bytes(encoded.tobytes())
        event.evidence_path = str(path)
        return str(path)
    except (OSError, cv2.error):
        logger.warning("截图暂存失败，事件日志仍将正常上报", exc_info=True)
        return None


class ScreenshotUploader(QObject):
    """Persistent queue; no COS credentials, and failures cannot block monitoring logs."""

    def __init__(self, parent=None, *, directory: Path | None = None):
        super().__init__(parent)
        self.directory = directory or application_root() / "runtime" / "screenshot-outbox"
        self.manifest = self.directory / "queue.json"
        try:
            saved = json.loads(self.manifest.read_text(encoding="utf-8"))
            self.queue = [
                item
                for item in saved
                if isinstance(item, dict)
                and all(isinstance(item.get(key), str) for key in ("device_id", "event_id", "file"))
                and isinstance(item.get("created"), (int, float))
                and math.isfinite(item["created"])
                and Path(item["file"]).name == item["file"]
                and item["file"].endswith(".jpg")
            ]
        except (OSError, ValueError, TypeError):
            self.queue = []
        self.base_url = self.device_id = self.token = ""
        self.manager = QNetworkAccessManager(self)
        self.inflight = False
        self.timer = QTimer(self)
        self.timer.setInterval(30_000)
        self.timer.timeout.connect(self.retry)
        self.timer.start()
        self.soon = QTimer(self)
        self.soon.setSingleShot(True)
        self.soon.timeout.connect(self.retry)

    def configure(self, base_url, device_id, token):
        self.base_url, self.device_id, self.token = base_url, device_id, token

    def _save(self):
        temporary = self.manifest.with_suffix(".tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(self.queue), encoding="utf-8")
            temporary.replace(self.manifest)
        except OSError:
            logger.warning("截图上传队列暂时无法保存，将继续重试")

    def enqueue(self, event_id: str, path: str):
        candidate = Path(path).resolve()
        if candidate.parent != self.directory.resolve() or not candidate.is_file():
            return
        self.queue.append(
            {"device_id": self.device_id, "event_id": event_id, "file": candidate.name, "created": time()}
        )
        self._save()
        # Normal uploads start as soon as the log is acknowledged. This timer
        # remains as a fallback for restarts and brief network races.
        self.soon.start(1000)

    def retry(self):
        self._save()
        if self.inflight or not all((self.base_url, self.device_id, self.token)):
            return
        now = time()
        for item in list(self.queue):
            path = self.directory / Path(item["file"]).name
            if now - item["created"] > 30 * 86400 or not path.is_file():
                self._remove(item)
                continue
            if not item["device_id"]:
                item["device_id"] = self.device_id
                self._save()
            if item["device_id"] != self.device_id:
                continue
            try:
                if path.stat().st_size > MAX_BYTES:
                    self._remove(item)
                    continue
                data = path.read_bytes()
                route = f"/api/v1/slave/monitor-logs/{quote(item['event_id'], safe='')}/screenshot"
                request = QNetworkRequest(QUrl(urljoin(self.base_url + "/", route)))
                request.setRawHeader(b"Authorization", f"Bearer {self.token}".encode())
                request.setRawHeader(b"X-Device-ID", self.device_id.encode())
                request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "image/jpeg")
                request.setTransferTimeout(30_000)
                reply = self.manager.put(request, QByteArray(data))
            except (OSError, RuntimeError):
                logger.warning("截图上传暂不可用，将继续重试")
                return
            self.inflight = True
            watchdog = QTimer(reply)
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(reply.abort)
            watchdog.start(35_000)
            reply.finished.connect(
                lambda current=reply, timer=watchdog, entry=item: self._finished(current, timer, entry)
            )
            return

    def _finished(self, reply, watchdog, item):
        watchdog.stop()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        success = reply.error() == QNetworkReply.NetworkError.NoError and status in {200, 201, 202}
        self.inflight = False
        if success:
            self._remove(item)
            self.soon.start(100)
        elif status in {413, 415, 422}:
            logger.warning("截图内容被服务器拒绝，已跳过该图片，监控日志不受影响")
            self._remove(item)
        reply.deleteLater()

    def _remove(self, item):
        # Save removal before deleting: a crash can only leave an orphan, never lose a queued upload.
        if item in self.queue:
            self.queue.remove(item)
        self._save()
        try:
            (self.directory / Path(item["file"]).name).unlink(missing_ok=True)
        except OSError:
            pass
