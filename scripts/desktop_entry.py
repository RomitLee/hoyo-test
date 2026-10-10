"""PyInstaller entry point for the windowed desktop application."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import traceback
import wave
from datetime import UTC, datetime

from hoyo_analyzer.paths import application_root


def _report_fatal_error(exc_type: type[BaseException], exc: BaseException, tb: object) -> None:
    """Persist startup crashes because the packaged app has no console window."""

    details = "".join(traceback.format_exception(exc_type, exc, tb))
    log_directory = application_root() / "runtime"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / "startup-error.log"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{datetime.now(UTC).astimezone().isoformat(timespec='seconds')}]\n{details}")
    ctypes.windll.user32.MessageBoxW(
        0,
        f"程序启动失败，详细信息已写入：\n{log_path}\n\n{exc_type.__name__}: {exc}",
        "梦幻西游背包监控",
        0x10,
    )


sys.excepthook = _report_fatal_error


def _run_package_self_test() -> int:
    """Exercise optional native dependencies from inside the frozen app."""

    result: dict[str, object] = {"ok": False}
    try:
        import cv2
        import pygame
        import windows_capture
        from PySide6.QtCore import qVersion

        from hoyo_analyzer import __version__
        from hoyo_analyzer.alarm_sound import ensure_alarm_sound_file

        alarm_path = ensure_alarm_sound_file()
        with wave.open(str(alarm_path), "rb") as alarm:
            alarm_details = {
                "path": str(alarm_path),
                "channels": alarm.getnchannels(),
                "sample_rate": alarm.getframerate(),
                "duration_seconds": round(alarm.getnframes() / alarm.getframerate(), 3),
            }

        result.update(
            {
                "ok": True,
                "client_version": __version__,
                "qt": qVersion(),
                "opencv": cv2.__version__,
                "pygame": pygame.version.ver,
                "windows_capture": windows_capture.__name__,
                "alarm_sound": alarm_details,
            }
        )
    except Exception as exc:  # noqa: BLE001 - report every packaging failure
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()

    output_directory = application_root() / "runtime"
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "package-self-test.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if result["ok"] else 1


def _run_package_ui_self_test() -> int:
    """Create the packaged slave window so packaging checks the real UI path."""
    result: dict[str, object] = {"ok": False}
    app = None
    window = None
    try:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication

        from hoyo_analyzer.app_identity import (
            SLAVE_APP_ID,
            apply_application_icon,
            configure_windows_app_id,
        )
        from hoyo_analyzer.multi_inventory_app import InventoryMonitorWindow

        configure_windows_app_id(SLAVE_APP_ID)
        app = QApplication.instance() or QApplication(["hoyo-package-ui-self-test"])
        icon = apply_application_icon(app)
        window = InventoryMonitorWindow()
        window.show()
        app.processEvents()
        checks = {
            "window_visible": window.isVisible(),
            "window_size": [window.width(), window.height()],
            "title_bar_height": window.title_bar.height(),
            "icon_loaded": not icon.isNull() and not window.windowIcon().isNull(),
            "add_window_button": window.add_window_button.text(),
        }
        result.update(
            {
                "ok": all(
                    (
                        checks["window_visible"],
                        checks["window_size"] == [800, 632],
                        checks["title_bar_height"] == 32,
                        checks["icon_loaded"],
                        checks["add_window_button"] == "＋ 增加窗口",
                    )
                ),
                "checks": checks,
            }
        )
    except Exception as exc:  # noqa: BLE001 - the report must retain every startup failure
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    finally:
        if window is not None:
            window.close()
        if app is not None:
            app.processEvents()

    output_directory = application_root() / "runtime"
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "package-ui-self-test.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if result["ok"] else 1


if "--package-self-test" in sys.argv:
    raise SystemExit(_run_package_self_test())

if "--package-ui-self-test" in sys.argv:
    raise SystemExit(_run_package_ui_self_test())

from hoyo_analyzer.multi_inventory_app import main

raise SystemExit(main())
