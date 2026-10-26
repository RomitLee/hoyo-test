"""Keep one Qt application alive and keep test log queues out of real runtime data."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


@pytest.fixture(autouse=True)
def isolated_monitor_log_outbox(monkeypatch, tmp_path):
    monkeypatch.setattr("hoyo_analyzer.relay_client.application_root", lambda: tmp_path)
    monkeypatch.setattr("hoyo_analyzer.screenshots.application_root", lambda: tmp_path)
