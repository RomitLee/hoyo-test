import numpy as np
import pytest

from hoyo_analyzer.capture import make_source
from hoyo_analyzer.config import AppConfig
from hoyo_analyzer.event_machine import EventMachine, SignalRule
from hoyo_analyzer.evidence import EvidenceWriter
from hoyo_analyzer.models import FramePacket, Observation
from hoyo_analyzer.realtime import RealtimeAnalyzer
from hoyo_analyzer.storage import CompositeEventSink, JsonlEventSink


class FakeSource:
    source_id = "fake"

    def __init__(self):
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        self.packets = [FramePacket(i, i * 200, image, "fake") for i in range(4)]
        self.closed = False

    def __iter__(self):
        yield from self.packets

    def close(self):
        self.closed = True


class FailingSource:
    source_id = "failing"

    def __init__(self):
        self.closed = False

    def __iter__(self):
        raise RuntimeError("capture failed")
        yield  # pragma: no cover - makes this method a generator

    def close(self):
        self.closed = True


class FakePerception:
    def observe(self, packet):
        return Observation(packet.frame_index, packet.timestamp_ms, {"battle_active": True}, 0.9, "fake")


def test_unsupported_source_is_rejected():
    with pytest.raises(ValueError, match="不支持的视频源类型"):
        make_source("unsupported-source", "unused", 3)


def test_realtime_analyzer_writes_confirmed_event(tmp_path):
    source = FakeSource()
    log_path = tmp_path / "events.jsonl"
    sink = CompositeEventSink([JsonlEventSink(log_path)])
    analyzer = RealtimeAnalyzer(
        source,
        AppConfig(),
        FakePerception(),
        EventMachine(rules=(SignalRule("battle_active", "battle_started", confirm_frames=1),)),
        sink,
        EvidenceWriter(tmp_path / "evidence"),
    )
    stats = analyzer.run(max_seconds=2)
    assert stats.captured_frames == 4
    # The live pipeline intentionally drops stale frames and keeps the newest one.
    assert 1 <= stats.analyzed_frames <= stats.captured_frames
    assert stats.dropped_frames >= 0
    assert "battle_started" in log_path.read_text(encoding="utf-8")
    assert source.closed


def test_realtime_analyzer_propagates_capture_failure(tmp_path):
    source = FailingSource()
    sink = CompositeEventSink([JsonlEventSink(tmp_path / "events.jsonl")])
    analyzer = RealtimeAnalyzer(
        source, AppConfig(), FakePerception(), EventMachine(), sink, EvidenceWriter(tmp_path / "evidence")
    )

    with pytest.raises(RuntimeError, match="capture failed"):
        analyzer.run(max_seconds=1)

    assert source.closed
