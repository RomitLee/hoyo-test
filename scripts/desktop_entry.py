"""PyInstaller entry point for the windowed desktop application."""

from __future__ import annotations

import ctypes
import json
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


if "--package-self-test" in sys.argv:
    raise SystemExit(_run_package_self_test())

from hoyo_analyzer.multi_inventory_app import main

raise SystemExit(main())
