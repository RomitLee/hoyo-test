"""Exercise the Qt upload queue against the real HTTP API, without production data."""
import os
import socket
from threading import Thread
from time import monotonic, sleep

import numpy as np
import uvicorn
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hoyo_analyzer.models import Event
from hoyo_analyzer.screenshots import ScreenshotUploader, capture_panel_screenshot


def test_recognition_frame_queue_http_upload_and_authenticated_view(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    token = 's' * 40
    master = 'm' * 40
    app = create_app(Settings(tmp_path/'db.sqlite3', master, {'slave':token}))
    images = {}

    class Store:
        configured = True

        def put(self, key, data):
            images[key] = data

        def signed_url(self, key):
            return 'https://screenshots.example.com/' + key + '?short-signature'

    app.state.screenshots = Store()
    with TestClient(app) as client:
        assert client.post('/api/v1/slave/monitor-logs', headers={
            'X-Device-ID':'slave', 'Authorization':'Bearer ' + token}, json={
            'event_id':'frame-event', 'event_type':'panel_opened', 'panel_type':'trade',
            'occurred_at':'2026-10-26T18:00:00+08:00', 'reason':'recognition'}).status_code == 202
    monkeypatch.setattr('hoyo_analyzer.screenshots.application_root', lambda: tmp_path)
    event = Event('frame-event', 100, 'panel_opened', payload={'panel_type':'trade'})
    path = capture_panel_screenshot(event, np.zeros((480,640,3), dtype=np.uint8))
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    address = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    sender = ScreenshotUploader(directory=tmp_path/'runtime'/'screenshot-outbox')
    sender.timer.stop()
    try:
        deadline = monotonic()+15
        while not server.started and monotonic() < deadline:
            sleep(0.01)
        assert server.started
        sender.configure(address, 'slave', token)
        sender.enqueue(event.event_id, path)
        sender.retry()
        while sender.queue and monotonic() < deadline:
            qt.processEvents()
            sleep(0.01)
        assert not sender.queue
        assert len(images) == 1 and next(iter(images.values())).startswith(b'\xff\xd8')
        with TestClient(app) as client:
            headers = {'Authorization':'Bearer ' + master}
            rows = client.get('/api/v1/master/monitor-logs', headers=headers).json()['logs']
            assert rows[0]['screenshot_ready'] == 1
            result = client.get(f"/api/v1/master/monitor-logs/{rows[0]['id']}/screenshot", headers=headers)
            assert result.status_code == 200
            assert result.json()['url'].startswith('https://screenshots.example.com/')
            assert result.headers['cache-control'] == 'no-store'
    finally:
        sender.timer.stop()
        sender.deleteLater()
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
