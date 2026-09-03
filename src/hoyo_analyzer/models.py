"""Domain models shared by the video analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Any

import numpy as np


class EventStatus(StrEnum):
    CONFIRMED = "confirmed"
    UPDATED = "updated"


@dataclass(slots=True)
class FramePacket:
    frame_index: int
    timestamp_ms: int
    image: np.ndarray
    source_id: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def timestamp(self) -> str:
        return format_timestamp(self.timestamp_ms)


@dataclass(slots=True)
class OcrText:
    text: str
    confidence: float = 0.0
    box: tuple[int, int, int, int] | None = None


@dataclass(slots=True)
class Observation:
    frame_index: int
    timestamp_ms: int
    signals: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    source: str = "rule-based"

    def get(self, key: str, default: Any = None) -> Any:
        return self.signals.get(key, default)


@dataclass(slots=True)
class Event:
    event_id: str
    timestamp_ms: int
    type: str
    status: EventStatus = EventStatus.CONFIRMED
    confidence: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_frame_index: int | None = None
    evidence_path: str | None = None
    source: str = "state-machine"
    pipeline_version: str = "0.1.0"

    @property
    def timestamp(self) -> str:
        return format_timestamp(self.timestamp_ms)


@dataclass(slots=True)
class RuntimeState:
    game_visible: bool = False
    map_name: str | None = None
    inventory_open: bool = False
    inventory_items: tuple[str, ...] = ()
    battle_active: bool = False
    skill_name: str | None = None
    damage_amount: int | None = None


def format_timestamp(timestamp_ms: int) -> str:
    delta = timedelta(milliseconds=max(0, timestamp_ms))
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    millis = timestamp_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
