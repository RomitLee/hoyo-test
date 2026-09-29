"""Persistent settings for the master alarm receiver."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import application_root


@dataclass(slots=True)
class MasterSettings:
    relay_url: str = ""
    master_token: str = ""
    poll_seconds: int = 5


def master_settings_path() -> Path:
    return application_root() / "runtime" / "master_settings.json"


def load_master_settings(path: Path | None = None) -> MasterSettings:
    target = path or master_settings_path()
    if not target.exists():
        return MasterSettings()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return MasterSettings()
    if not isinstance(raw, dict):
        return MasterSettings()
    try:
        poll_seconds = int(raw.get("poll_seconds", 5))
    except (TypeError, ValueError):
        poll_seconds = 5
    return MasterSettings(
        relay_url=str(raw.get("relay_url", "")).strip().rstrip("/"),
        master_token=str(raw.get("master_token", "")).strip(),
        poll_seconds=max(2, min(60, poll_seconds)),
    )


def save_master_settings(settings: MasterSettings, path: Path | None = None) -> None:
    target = path or master_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
