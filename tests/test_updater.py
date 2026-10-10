from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from hoyo_analyzer.updater import InstallError, replace_application, rollback_application, safe_extract


def test_safe_extract_rejects_parent_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../escaped.txt", "bad")
    with pytest.raises(InstallError, match="不安全路径"):
        safe_extract(archive, tmp_path / "output")
    assert not (tmp_path / "escaped.txt").exists()


def test_replace_and_rollback_preserve_runtime(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    payload = tmp_path / "payload"
    backup = app_root / "runtime" / "updates" / "backup"
    (app_root / "runtime").mkdir(parents=True)
    (app_root / "runtime" / "settings.json").write_text("secret", encoding="utf-8")
    (app_root / "old.exe").write_text("old", encoding="utf-8")
    payload.mkdir()
    (payload / "new.exe").write_text("new", encoding="utf-8")
    (payload / "runtime").mkdir()
    (payload / "runtime" / "settings.json").write_text("empty", encoding="utf-8")

    replace_application(app_root, payload, backup)
    assert not (app_root / "old.exe").exists()
    assert (app_root / "new.exe").read_text(encoding="utf-8") == "new"
    assert (app_root / "runtime" / "settings.json").read_text(encoding="utf-8") == "secret"

    rollback_application(app_root, backup)
    assert (app_root / "old.exe").read_text(encoding="utf-8") == "old"
    assert not (app_root / "new.exe").exists()
    assert (app_root / "runtime" / "settings.json").read_text(encoding="utf-8") == "secret"
