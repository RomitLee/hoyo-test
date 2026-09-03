"""Threaded real-time capture and analysis pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event as ThreadEvent
from threading import Thread
from time import monotonic, sleep

from .capture import FrameSource
from .config import AppConfig
from .event_machine import EventMachine
from .evidence import EvidenceWriter
from .models import FramePacket, Observation
from .perception import PerceptionEngine
from .sampler import AdaptiveSampler, LatestFrameQueue, SamplingProfile
from .storage import CompositeEventSink


@dataclass(slots=True)
class LiveStats:
    captured_frames: int = 0
    analyzed_frames: int = 0
    dropped_frames: int = 0
    started_at: float = field(default_factory=monotonic)
    last_timestamp_ms: int | None = None

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, monotonic() - self.started_at)

    @property
    def capture_fps(self) -> float:
        return self.captured_frames / self.elapsed_seconds if self.elapsed_seconds else 0.0

    @property
    def analysis_fps(self) -> float:
        return self.analyzed_frames / self.elapsed_seconds if self.elapsed_seconds else 0.0


class RealtimeAnalyzer:
    """Use a producer thread and latest-frame queue so analysis cannot build latency."""

    def __init__(
        self,
        source: FrameSource,
        config: AppConfig,
        perception: PerceptionEngine,
        machine: EventMachine,
        sink: CompositeEventSink,
        evidence: EvidenceWriter,
        sampler: AdaptiveSampler | None = None,
        queue: LatestFrameQueue | None = None,
        on_observation: Callable[[Observation], None] | None = None,
        on_frame: Callable[[FramePacket], None] | None = None,
        on_event: Callable[[object], None] | None = None,
    ) -> None:
        self.source, self.config, self.perception, self.machine, self.sink, self.evidence = (
            source,
            config,
            perception,
            machine,
            sink,
            evidence,
        )
        self.sampler = sampler or AdaptiveSampler(
            SamplingProfile(
                config.sampling.normal_fps,
                config.sampling.battle_fps,
                config.sampling.burst_fps,
                config.sampling.burst_duration_ms,
            )
        )
        self.queue = queue or LatestFrameQueue(config.sampling.queue_size)
        self.on_observation = on_observation
        self.on_frame = on_frame
        self.on_event = on_event
        self.stop_requested = ThreadEvent()
        self.producer_done = ThreadEvent()
        self.stats = LiveStats()
        self._producer: Thread | None = None
        self._last_observation: Observation | None = None

    def _produce(self) -> None:
        try:
            for packet in self.source:
                if self.stop_requested.is_set():
                    break
                self.queue.put(packet)
                self.stats.captured_frames += 1
                self.stats.last_timestamp_ms = packet.timestamp_ms
        finally:
            self.producer_done.set()

    def request_stop(self) -> None:
        self.stop_requested.set()
        self.source.close()

    def _process(self, packet: FramePacket) -> None:
        if self.on_frame is not None:
            self.on_frame(packet)
        if not self.sampler.accept(packet):
            return
        self.stats.analyzed_frames += 1
        self.evidence.add(packet)
        observation = self.perception.observe(packet)
        self._last_observation = observation
        if self.on_observation is not None:
            self.on_observation(observation)
        for event in self.machine.update(observation):
            self.evidence.save(event, packet)
            self.sink.write(event)
            if self.on_event is not None:
                self.on_event(event)

    def run(self, max_seconds: float | None = None) -> LiveStats:
        self._producer = Thread(target=self._produce, name="hoyo-capture", daemon=True)
        self._producer.start()
        started = monotonic()
        try:
            while not self.producer_done.is_set() or len(self.queue):
                if max_seconds is not None and monotonic() - started >= max_seconds:
                    self.request_stop()
                packet = self.queue.get()
                if packet is None:
                    sleep(0.002)
                    continue
                self._process(packet)
        finally:
            self.request_stop()
            if self._producer is not None:
                self._producer.join(timeout=2.0)
            self.stats.dropped_frames = self.queue.dropped
            if self._last_observation is not None:
                for event in self.machine.close(self._last_observation):
                    self.sink.write(event)
            self.sink.close()
        return self.stats
