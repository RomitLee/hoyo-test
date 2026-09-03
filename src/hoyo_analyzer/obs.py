"""Small Windows OBS process/status helpers used by the desktop UI."""

from __future__ import annotations

import os
import subprocess


def is_obs_running() -> bool:
    """Check whether OBS Studio is running without adding a process dependency."""
    if os.name != "nt":
        return False
    for image_name in ("obs64.exe", "obs.exe"):
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        if image_name.lower() in result.stdout.lower():
            return True
    return False
