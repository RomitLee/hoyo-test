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

        from hoyo_analyzer import __version__

        ssl.create_default_context()
        result.update(
            {
                "ok": True,
                "client_version": __version__,
                "qt": qVersion(),
                "pygame": pygame.version.ver,
                "tls": "available",
            }
        )
    except Exception as exc:  # noqa: BLE001 - packaging report must include every startup dependency failure
        result["error"] = f"{type(exc).__name__}: {exc}"
    output = application_root() / "runtime" / "master-package-self-test.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if "--package-self-test" in sys.argv:
    raise SystemExit(_self_test())


def _ui_self_test() -> int:
    import os
    import traceback

    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    result: dict[str, object] = {"ok": False}
    app = None
    window = None
    try:
        from PySide6.QtWidgets import QApplication

        from hoyo_analyzer import __version__
        from hoyo_analyzer.master_app import MasterWindow

        app = QApplication.instance() or QApplication(["hoyo-master-ui-self-test"])
        window = MasterWindow()
        window.show()
        app.processEvents()
        checks = {
            "window_visible": window.isVisible(),
            "window_size": [window.width(), window.height()],
            "monitor_log_tab": any(window.tabs.tabText(index) == "监控日志" for index in range(window.tabs.count())),
            "monitor_log_columns": window.monitor_log_table.columnCount(),
            "cash_only": "现金" in window.STATUS_COLUMNS and "存银" not in window.STATUS_COLUMNS and window.tabs.count() == 4,
            "separate_pages": [window.tabs.tabText(i) for i in range(window.tabs.count())] == ["监控", "告警", "从机管理", "监控日志"],
            "client_version": __version__,
        }
        result.update(ok=checks["window_visible"] and checks["window_size"] == [800, 632]
                      and checks["monitor_log_tab"] and checks["monitor_log_columns"] == 7
                      and checks["cash_only"] and checks["separate_pages"], checks=checks)
    except Exception as exc:  # noqa: BLE001 - persist frozen UI startup failures
        result.update(error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    finally:
        if window is not None:
            window.close()
        if app is not None:
            app.processEvents()
    output = application_root() / "runtime" / "master-package-ui-self-test.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if "--package-ui-self-test" in sys.argv:
    raise SystemExit(_ui_self_test())

from hoyo_analyzer.master_app import main

raise SystemExit(main())
