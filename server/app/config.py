"""Environment-backed configuration for the relay service."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    master_token: str
    device_tokens: dict[str, str]
    retention_days: int = 30
    update_directory: Path = Path("/releases")
    cos_secret_id: str = field(default="", repr=False)
    cos_secret_key: str = field(default="", repr=False)
    cos_bucket: str = ""
    cos_region: str = ""
    cos_screenshot_domain: str = ""
    cos_release_secret_id: str = field(default="", repr=False)
    cos_release_secret_key: str = field(default="", repr=False)
    cos_release_bucket: str = ""
    cos_release_region: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        master_token = os.environ.get("HOYO_MASTER_TOKEN", "").strip()
        if master_token and len(master_token) < 32:
            raise RuntimeError("HOYO_MASTER_TOKEN must contain at least 32 characters")

        raw_tokens = os.environ.get("HOYO_DEVICE_TOKENS", "{}").strip() or "{}"
        try:
            parsed_tokens = json.loads(raw_tokens)
        except json.JSONDecodeError as exc:
            raise RuntimeError("HOYO_DEVICE_TOKENS must be a JSON object") from exc
        if not isinstance(parsed_tokens, dict):
            raise TypeError("HOYO_DEVICE_TOKENS must be a JSON object")

        device_tokens: dict[str, str] = {}
        for raw_device_id, raw_token in parsed_tokens.items():
            device_id = str(raw_device_id).strip()
            token = str(raw_token).strip()
            if not device_id or len(device_id) > 64:
                raise RuntimeError("device IDs must contain 1 to 64 characters")
            if len(token) < 32:
                raise RuntimeError(f"token for device {device_id!r} must contain at least 32 characters")
            device_tokens[device_id] = token

        try:
            retention_days = int(os.environ.get("HOYO_RETENTION_DAYS", "30"))
        except ValueError as exc:
            raise RuntimeError("HOYO_RETENTION_DAYS must be an integer") from exc

        return cls(
            database_path=Path(os.environ.get("HOYO_DATABASE_PATH", "/data/hoyo-relay.sqlite3")),
            master_token=master_token,
            device_tokens=device_tokens,
            retention_days=max(1, min(retention_days, 3650)),
            update_directory=Path(os.environ.get("HOYO_UPDATE_DIRECTORY", "/releases")),
            cos_secret_id=os.environ.get("COS_SECRET_ID", ""),
            cos_secret_key=os.environ.get("COS_SECRET_KEY", ""),
            cos_bucket=os.environ.get("COS_BUCKET", ""),
            cos_region=os.environ.get("COS_REGION", ""),
            cos_screenshot_domain=os.environ.get("COS_SCREENSHOT_DOMAIN", ""),
            cos_release_secret_id=os.environ.get("COS_RELEASE_SECRET_ID", ""),
            cos_release_secret_key=os.environ.get("COS_RELEASE_SECRET_KEY", ""),
            cos_release_bucket=os.environ.get("COS_RELEASE_BUCKET", ""),
            cos_release_region=os.environ.get("COS_RELEASE_REGION", ""),
        )
