# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build specification for Employee Monitoring Agent.

Fully self-contained bundle: Python interpreter, stdlib (including _sqlite3),
SQLAlchemy, PySide6/Qt6, Pillow, mss, keyring, httpx, APScheduler and
Windows-only backends (pywin32, pynput) when building on Windows.
No external Python/SQLite/VC++ runtime required on the target machine
(VC++ redist is embedded via PyInstaller binaries).

Usage (Windows, per docs/TECH_STACK.md):

    uv sync --group dev
    uv run pyinstaller installer/build.spec --noconfirm --clean
    # output: dist/EmployeeMonitoring/EmployeeMonitoring.exe (onedir)

On non-Windows (CI smoke), the spec still builds with Dummy providers only;
Windows extras are guarded with try/except so analysis never fails on Linux.

See docs/TECH_STACK.md:17 (PyInstaller --onedir windowed) and
    docs/DATA_MODEL.md:24-36 (storage layout, not install dir).
"""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

# ------------------------------------------------------------------ paths
try:
    SPEC_DIR = Path(__file__).parent.resolve()  # type: ignore[name-defined]  # noqa: F821
except NameError:
    # PyInstaller exec()s the spec without __file__ on some versions — SPECPATH is set by PyInstaller
    try:
        SPEC_DIR = Path(SPECPATH)  # type: ignore[name-defined]  # noqa: F821
    except NameError:
        SPEC_DIR = Path.cwd() / "installer"
PROJECT_ROOT = SPEC_DIR.parent
ICON = PROJECT_ROOT / "assets" / "icons" / "app.ico"
ASSETS_SRC = PROJECT_ROOT / "assets"

block_cipher = None

# ---------------------------------------------------------------- collect helpers
def _maybe_collect(name: str):  # type: ignore[no-untyped-def]
    """Try to collect a package; return (datas, binaries, hiddenimports) or empties."""
    try:
        return collect_all(name)
    except Exception:
        return [], [], []

datas: list[tuple[str, str]] = []
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = []

# PySide6 is the heaviest dependency — Qt DLLs, plugins (qwindows, imageformats).
try:
    d, b, h = collect_all("PySide6")
    datas += d
    binaries += b
    hiddenimports += h
except Exception:
    # Fallback minimal hiddenimports if collect_all unavailable
    hiddenimports += ["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"]

# SQLAlchemy — ensure dialects/sqlite are not pruned
try:
    hiddenimports += collect_submodules("sqlalchemy")
    datas += collect_data_files("sqlalchemy", include_py_files=False)
except Exception:
    hiddenimports += ["sqlalchemy.sql.default_comparator", "sqlalchemy.ext.declarative"]

# Pillow, mss, keyring, httpx, apscheduler, pydantic-settings
for pkg in ("PIL", "mss", "keyring", "httpx", "apscheduler", "pydantic_settings", "pydantic"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass
    try:
        datas += collect_data_files(pkg, include_py_files=False)
    except Exception:
        pass

# Copy importlib.metadata for packages that query version at runtime
for pkg in ("PySide6", "SQLAlchemy", "Pillow", "mss", "httpx", "keyring", "APScheduler", "pydantic-settings"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

# Windows-only backends — guarded so Linux CI still analyses correctly
for win_pkg in ("win32cred", "win32api", "win32gui", "win32process", "pythoncom", "pywintypes"):
    try:
        hiddenimports += collect_submodules(win_pkg)
    except Exception:
        # may not be installed on this platform
        hiddenimports.append(win_pkg)

for win_pkg in ("pynput", "pynput.keyboard._win32", "pynput.mouse._win32"):
    try:
        hiddenimports += collect_submodules(win_pkg)
    except Exception:
        hiddenimports.append(win_pkg)

# Explicit adds that PyInstaller occasionally misses behind dynamic imports
hiddenimports += [
    "sqlalchemy.sql.default_comparator",
    "sqlalchemy.ext.baked",
    "_sqlite3",
    "sqlite3",
    "keyring.backends.Windows",
    "keyring.backends.fail",
]

# ---------------------------------------------------------------- datas — app assets
# Keep branding/icons inside the bundle so the installed app never depends on
# an external checkout. At runtime the app copies nothing from assets to
# %LOCALAPPDATA%; assets are read from _MEIPASS / dist folder only.
if ASSETS_SRC.exists():
    datas.append((str(ASSETS_SRC), "assets"))

# ---------------------------------------------------------------- Analysis
a = Analysis(  # type: ignore[name-defined]  # noqa: F821 — provided by PyInstaller runtime
    [str(PROJECT_ROOT / "app" / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude Tk/Tcl — we use Qt only; trims ~15MB
        "tkinter",
        "Tkinter",
        "_tkinter",
        # Exclude large unused stdlib/test helpers
        "unittest",
        "test",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Remove duplicate datas that collect_all may have over-added (keep bundle lean)
# PyInstaller will dedup internally; explicit filtering not needed.

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # type: ignore[name-defined]  # noqa: F821

exe = EXE(  # type: ignore[name-defined]  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EmployeeMonitoring",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX increases AV false positives (docs/SECURITY_PRIVACY.md:58)
    console=False,  # windowed — no console (docs/TECH_STACK.md:17)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)

coll = COLLECT(  # type: ignore[name-defined]  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EmployeeMonitoring",
)
