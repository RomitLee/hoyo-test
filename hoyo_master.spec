# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH)

a = Analysis(
    [str(project_root / "scripts" / "master_entry.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / "packaging" / "pyside6_runtime_hook.py")],
    excludes=["cv2", "numpy", "windows_capture"],
    noarchive=False,
    optimize=0,
)
# Ignore non-system ICU runtime DLLs that may be present on the build machine's
# PATH. Qt uses the ICU copy bundled with its own runtime when available.
blocked_root_binaries = {
    "icudt78.dll",
    "icuuc.dll",
}
a.binaries = [item for item in a.binaries if item[0].casefold() not in blocked_root_binaries]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="梦幻西游告警主机",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="梦幻西游告警主机",
)
