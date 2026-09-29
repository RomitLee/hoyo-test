from __future__ import annotations

import sys
from pathlib import Path

from hoyo_analyzer.paths import application_root, resolve_application_path


def test_application_root_is_repository_root_in_source_checkout() -> None:
    assert (application_root() / "pyproject.toml").is_file()


def test_application_root_is_executable_directory_when_frozen(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "梦幻西游-希联文超助手.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert application_root() == tmp_path
    assert resolve_application_path("models/equipment_tooltip/best.pt") == (
        tmp_path / "models" / "equipment_tooltip" / "best.pt"
    )
