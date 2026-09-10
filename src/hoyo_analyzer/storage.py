"""JSONL and human-readable event sinks."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from .models import Event


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"无法序列化类型: {type(value)!r}")


def event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "timestamp_ms": event.timestamp_ms,
        "timestamp": event.display_time,
        "elapsed_timestamp": event.timestamp,
        "type": event.display_name,
        "event_type": event.type,
        "status": event.status.value,
        "confidence": round(float(event.confidence), 4),
        "payload": event.payload,
        "evidence_frame_index": event.evidence_frame_index,
        "evidence_path": event.evidence_path,
        "source": event.source,
        "pipeline_version": event.pipeline_version,
    }


class JsonlEventSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")

    def write(self, event: Event) -> None:
        self._handle.write(json.dumps(event_to_dict(event), ensure_ascii=False, default=_json_default) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


class ConsoleEventSink:
    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdout

    def write(self, event: Event) -> None:
        payload = ", ".join(f"{k}={v}" for k, v in event.payload.items())
        suffix = f" ({payload})" if payload else ""
        print(f"{event.display_time} {event.display_name}{suffix}", file=self.stream)

    def close(self) -> None:
        pass


class CompositeEventSink:
    def __init__(self, sinks: Iterable[Any]) -> None:
        self.sinks = list(sinks)

    def write(self, event: Event) -> None:
        for sink in self.sinks:
            sink.write(event)

    def close(self) -> None:
        for sink in self.sinks:
            sink.close()
