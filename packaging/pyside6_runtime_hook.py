"""Make PySide6's split DLL directories visible to the Windows loader."""

from __future__ import annotations

import os
import sys
from pathlib import Path


if sys.platform.startswith("win"):
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    for directory in (root, root / "PySide6", root / "shiboken6"):
        if directory.is_dir():
            try:
                os.add_dll_directory(str(directory))
            except (OSError, AttributeError):
                pass
    os.environ["PATH"] = os.pathsep.join(
        [str(root), str(root / "PySide6"), str(root / "shiboken6"), os.environ.get("PATH", "")]
    )
