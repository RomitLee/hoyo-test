# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH)

datas = []
binaries = []
hiddenimports = [
    # Imported lazily when Windows Graphics Capture starts. Keep it explicit so
    # a packaged desktop build can never pass analysis while omitting capture.
    "windows_capture",
]

a = Analysis(
    [str(project_root / "scripts" / "desktop_entry.py")],
    pathex=[str(project_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / "packaging" / "pyside6_runtime_hook.py")],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Ignore non-system ICU runtime DLLs that may be present on the build machine's
# PATH. Qt uses Windows ICU and must not load unrelated copies.
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
    name="梦幻西游背包监控",
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
    name="梦幻西游背包监控",
)
