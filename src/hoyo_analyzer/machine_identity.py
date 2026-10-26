"""Stable, account-scoped machine fingerprint without sending the Windows GUID."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from .slave_settings import settings_path


def machine_id() -> str:
    guid = _windows_machine_guid()
    if guid:
        source = guid
    else:
        try:
            source = _persistent_fallback()
        except OSError:
            return ""
    return hashlib.sha256(f"hoyo-inventory-monitor:v1:{source}".encode()).hexdigest()


def _windows_machine_guid() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0]).strip().lower()
    except (OSError, ValueError):
        return ""


def _persistent_fallback() -> str:
    path: Path = settings_path().with_name("machine_identity.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("id"), str) and len(value["id"]) == 32:
            return value["id"]
    except (OSError, ValueError):
        pass
    value = uuid4().hex
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": value}), encoding="utf-8")
    return value
