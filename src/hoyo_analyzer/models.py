"""Domain models shared by the video analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

import numpy as np


class EventStatus(StrEnum):
    CONFIRMED = "confirmed"
    UPDATED = "updated"


# A fixed UTC+08:00 timezone is sufficient for Beijing time and avoids making
# the desktop app depend on the optional system ``tzdata`` package. Mainland
# China does not observe daylight-saving time.
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

EVENT_NAMES_ZH: dict[str, str] = {
    "application_opened": "游戏画面已接入",
    "screen_changed": "画面发生变化",
    "inventory_opened": "打开背包",
    "inventory_closed": "关闭背包",
    "equipment_tooltip_opened": "显示装备属性",
    "equipment_tooltip_closed": "装备属性消失",
    "battle_started": "进入战斗",
    "battle_ended": "战斗结束",
    "map_entered": "进入地图",
    "inventory_items_read": "读取背包物品",
    "skill_used": "使用技能",
    "damage_dealt": "造成伤害",
}


def event_name_zh(event_type: str) -> str:
    """Return a player-facing Chinese label while keeping the stable code."""
    return EVENT_NAMES_ZH.get(event_type, event_type)


def format_beijing_time(value: datetime | None = None) -> str:
    """Format the current/received time as Beijing local time in 24-hour form."""
    current = value or datetime.now(BEIJING_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BEIJING_TZ)
    return current.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


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
    beijing_time: str = ""

    @property
    def timestamp(self) -> str:
        """Elapsed source time, retained for replay/cooldown calculations."""
        return format_timestamp(self.timestamp_ms)

    @property
    def display_time(self) -> str:
        """Player-facing Beijing time in 24-hour format."""
        return self.beijing_time or format_beijing_time()

    @property
    def display_name(self) -> str:
        return event_name_zh(self.type)


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
