"""Persistent local settings for the inventory-monitor slave application."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import application_root

ALERT_NONE = "none"
ALERT_LOCAL = "local"
ALERT_HOST = "host"
ALERT_LOCAL_HOST = "local_host"
ALERT_MODES = {ALERT_NONE, ALERT_LOCAL, ALERT_HOST, ALERT_LOCAL_HOST}

LOCAL_ALERT_POPUP = "popup"
LOCAL_ALERT_POPUP_SOUND = "popup_sound"
LOCAL_ALERT_STYLES = {LOCAL_ALERT_POPUP, LOCAL_ALERT_POPUP_SOUND}


@dataclass(slots=True)
class SlaveSettings:
    """User-configurable warning and cloud-relay settings."""

    minimize_alert: str = ALERT_LOCAL
    occlusion_threshold_percent: int = 100
    occlusion_alert: str = ALERT_LOCAL
    inventory_empty_threshold: int = 0
    inventory_alert: str = ALERT_LOCAL
    inventory_not_open_alert: str = ALERT_LOCAL
    inventory_blocked_alert: str = ALERT_LOCAL
    local_alert_style: str = LOCAL_ALERT_POPUP_SOUND
    relay_url: str = ""
    device_id: str = ""
    device_token: str = ""

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> SlaveSettings:
        settings = cls()
        for key in asdict(settings):
            if key in raw:
                setattr(settings, key, raw[key])
        settings.minimize_alert = _alert_mode(settings.minimize_alert)
        settings.occlusion_alert = _alert_mode(settings.occlusion_alert)
        settings.inventory_alert = _alert_mode(settings.inventory_alert)
        settings.inventory_not_open_alert = _alert_mode(settings.inventory_not_open_alert)
        settings.inventory_blocked_alert = _alert_mode(settings.inventory_blocked_alert)
        settings.local_alert_style = (
            str(settings.local_alert_style)
            if str(settings.local_alert_style) in LOCAL_ALERT_STYLES
            else LOCAL_ALERT_POPUP_SOUND
        )
        settings.occlusion_threshold_percent = _bounded_int(settings.occlusion_threshold_percent, 1, 100, 100)
        settings.inventory_empty_threshold = _bounded_int(settings.inventory_empty_threshold, 0, 20, 0)
        settings.relay_url = str(settings.relay_url).strip().rstrip("/")
        settings.device_id = str(settings.device_id).strip()
        settings.device_token = str(settings.device_token).strip()
        return settings


def settings_path() -> Path:
    return application_root() / "runtime" / "slave_settings.json"


def load_slave_settings(path: Path | None = None) -> SlaveSettings:
    target = path or settings_path()
    if not target.exists():
        return SlaveSettings()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SlaveSettings()
    return SlaveSettings.from_mapping(raw) if isinstance(raw, dict) else SlaveSettings()


def save_slave_settings(settings: SlaveSettings, path: Path | None = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = SlaveSettings.from_mapping(asdict(settings))
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(json.dumps(asdict(normalized), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def _alert_mode(value: Any) -> str:
    normalized = str(value)
    return normalized if normalized in ALERT_MODES else ALERT_LOCAL


def _bounded_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, parsed))
