"""PyInstaller entry point for the master alarm receiver."""

from __future__ import annotations

import json
import ssl
import sys

from hoyo_analyzer.paths import application_root


def _self_test() -> int:
    result: dict[str, object] = {"ok": False}
    try:
        import pygame
        from PySide6.QtCore import qVersion

        ssl.create_default_context()
        result.update({"ok": True, "qt": qVersion(), "pygame": pygame.version.ver, "tls": "available"})
    except Exception as exc:  # noqa: BLE001 - packaging report must include every startup dependency failure
        result["error"] = f"{type(exc).__name__}: {exc}"
    output = application_root() / "runtime" / "master-package-self-test.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if "--package-self-test" in sys.argv:
    raise SystemExit(_self_test())

from hoyo_analyzer.master_app import main

raise SystemExit(main())
