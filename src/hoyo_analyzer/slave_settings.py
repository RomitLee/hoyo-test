"""Persistent local settings for the inventory-monitor slave application."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .paths import application_root

CAPACITY_ALERT_NONE = 1
CAPACITY_ALERT_HOST = 2
CAPACITY_ALERT_LOCAL = 3
CAPACITY_ALERT_LEVELS = {
    CAPACITY_ALERT_NONE,
    CAPACITY_ALERT_HOST,
    CAPACITY_ALERT_LOCAL,
}
MIN_CAPACITY_THRESHOLD = 0
MAX_CAPACITY_THRESHOLD = 20

# Legacy values are retained only so an existing settings file can be migrated.
_LEGACY_ALERT_LEVELS = {
    "host": CAPACITY_ALERT_HOST,
    "local_host": CAPACITY_ALERT_LOCAL,
    "local": CAPACITY_ALERT_LOCAL,
}


@dataclass(slots=True)
class SlaveSettings:
    """User-configurable inventory alert and cloud-relay settings."""

    inventory_capacity_alert_level: int = CAPACITY_ALERT_NONE
    inventory_capacity_threshold: int = MIN_CAPACITY_THRESHOLD
    # ``relay_url`` remains only for reading old settings. New clients always
    # use CLOUD_RELAY_URL and never ask the operator for a server address.
    relay_url: str = ""
    device_id: str = ""
    display_name: str = ""
    account_username: str = ""
    device_token: str = ""
    installation_id: str = ""
    pairing_code: str = ""

    @property
    def inventory_capacity_alert_enabled(self) -> bool:
        """Compatibility view used by older callers and settings files."""

        return self.inventory_capacity_alert_level in {CAPACITY_ALERT_HOST, CAPACITY_ALERT_LOCAL}

    @inventory_capacity_alert_enabled.setter
    def inventory_capacity_alert_enabled(self, enabled: bool) -> None:
        self.inventory_capacity_alert_level = CAPACITY_ALERT_HOST if enabled else CAPACITY_ALERT_NONE

    @property
    def inventory_capacity_local_alert_enabled(self) -> bool:
        return self.inventory_capacity_alert_level == CAPACITY_ALERT_LOCAL

    @property
    def inventory_capacity_host_alert_enabled(self) -> bool:
        return self.inventory_capacity_alert_level == CAPACITY_ALERT_HOST

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> SlaveSettings:
        if "inventory_capacity_alert_level" in raw:
            alert_level = _alert_level(raw["inventory_capacity_alert_level"])
        elif "inventory_capacity_alert_enabled" in raw:
            alert_level = (
                CAPACITY_ALERT_HOST
                if _boolean(raw["inventory_capacity_alert_enabled"])
                else CAPACITY_ALERT_NONE
            )
        else:
            alert_level = _LEGACY_ALERT_LEVELS.get(
                str(raw.get("inventory_alert", "none")), CAPACITY_ALERT_NONE
            )
        return cls(
            inventory_capacity_alert_level=alert_level,
            inventory_capacity_threshold=_capacity_threshold(
                raw.get("inventory_capacity_threshold", MIN_CAPACITY_THRESHOLD)
            ),
            relay_url=str(raw.get("relay_url", "")).strip().rstrip("/"),
            device_id=str(raw.get("device_id", "")).strip(),
            display_name=str(raw.get("display_name", "")).strip()[:64],
            account_username=str(raw.get("account_username", "")).strip()[:32],
            device_token=str(raw.get("device_token", "")).strip(),
            installation_id=str(raw.get("installation_id", "")).strip() or uuid4().hex,
            pairing_code=str(raw.get("pairing_code", "")).strip().upper(),
        )


def settings_path() -> Path:
    local_data = os.environ.get("LOCALAPPDATA")
    if local_data:
        return Path(local_data) / "HoyoInventoryMonitor" / "slave_settings.json"
    return Path.home() / ".hoyo-inventory-monitor" / "slave_settings.json"


def load_slave_settings(path: Path | None = None) -> SlaveSettings:
    target = path or settings_path()
    legacy = application_root() / "runtime" / "slave_settings.json"
    if path is None and not target.exists() and legacy.exists():
        target = legacy
    if not target.exists():
        return SlaveSettings(installation_id=uuid4().hex)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SlaveSettings(installation_id=uuid4().hex)
    settings = SlaveSettings.from_mapping(raw) if isinstance(raw, dict) else SlaveSettings(installation_id=uuid4().hex)
    if path is None and target == legacy:
        try:
            save_slave_settings(settings)
        except OSError:
            pass  # Keep the valid legacy session available until a later save succeeds.
    return settings


def save_slave_settings(settings: SlaveSettings, path: Path | None = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = SlaveSettings.from_mapping(asdict(settings))
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(json.dumps(asdict(normalized), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _alert_level(value: Any) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        return CAPACITY_ALERT_NONE
    return level if level in CAPACITY_ALERT_LEVELS else CAPACITY_ALERT_NONE


def _capacity_threshold(value: Any) -> int:
    try:
        threshold = int(value)
    except (TypeError, ValueError):
        return MIN_CAPACITY_THRESHOLD
    return max(MIN_CAPACITY_THRESHOLD, min(MAX_CAPACITY_THRESHOLD, threshold))
