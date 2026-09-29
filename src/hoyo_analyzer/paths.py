"""Filesystem locations shared by source runs and packaged desktop builds."""

from __future__ import annotations

import sys
from pathlib import Path


def application_root() -> Path:
    """Return the directory containing user-editable application resources.

    In a PyInstaller onedir build, configuration, models, assets, and runtime
    output live beside the executable. During development they live at the
    repository root.
    """

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resolve_application_path(value: str | Path) -> Path:
    """Resolve a path relative to the installed application/repository root."""

    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return application_root() / candidate
