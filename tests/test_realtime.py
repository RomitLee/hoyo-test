import numpy as np

from hoyo_analyzer.capture import ObsVirtualCameraSource, make_source
from hoyo_analyzer.config import AppConfig
from hoyo_analyzer.event_machine import EventMachine
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


class FakePerception:
    def observe(self, packet):
        return Observation(packet.frame_index, packet.timestamp_ms, {"battle_active": True}, 0.9, "fake")


def test_obs_virtual_camera_factory():
    source = make_source("obs-virtual-camera", "unused", 3)
    assert isinstance(source, ObsVirtualCameraSource)


def test_realtime_analyzer_writes_confirmed_event(tmp_path):
    source = FakeSource()
    log_path = tmp_path / "events.jsonl"
    sink = CompositeEventSink([JsonlEventSink(log_path)])
    analyzer = RealtimeAnalyzer(
        source, AppConfig(), FakePerception(), EventMachine(), sink, EvidenceWriter(tmp_path / "evidence")
    )
    stats = analyzer.run(max_seconds=2)
    assert stats.captured_frames == 4
    assert stats.analyzed_frames == 4
    assert "battle_started" in log_path.read_text(encoding="utf-8")
    assert source.closed
