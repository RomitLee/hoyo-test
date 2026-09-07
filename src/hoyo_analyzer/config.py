"""Dependency-free TOML configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class CaptureConfig:
    source: str = "windows-graphics-capture"
    path: str = "recordings/demo.mp4"
    device_index: int = 0
    source_id: str = "wgc-window"
    window_title_keyword: str = "梦幻西游"


@dataclass(slots=True)
class SamplingConfig:
    normal_fps: float = 5.0
    battle_fps: float = 10.0
    burst_fps: float = 20.0
    burst_duration_ms: int = 1500
    # Live analysis keeps only the newest frame; a tiny buffer avoids stale-frame latency.
    queue_size: int = 3


@dataclass(slots=True)
class EvidenceConfig:
    pre_buffer_ms: int = 2000
    post_buffer_ms: int = 3000
    directory: str = "runtime/evidence"


@dataclass(slots=True)
class OutputConfig:
    event_log: str = "runtime/events/events.jsonl"
    console: bool = True


@dataclass(slots=True)
class AppConfig:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    templates: dict[str, str] = field(default_factory=dict)


def load_config(path: str | Path | None = None) -> AppConfig:
    config = AppConfig()
    if path is None:
        return config
    with Path(path).open("rb") as handle:
        data = tomllib.load(handle)
    for name, target in (
        ("capture", config.capture),
        ("sampling", config.sampling),
        ("evidence", config.evidence),
        ("output", config.output),
    ):
        section = data.get(name, {})
        if isinstance(section, dict):
            for key, value in section.items():
                if hasattr(target, key):
                    setattr(target, key, value)
    templates = data.get("templates", {})
    if isinstance(templates, dict):
        config.templates = {str(k): str(v) for k, v in templates.items()}
    return config
