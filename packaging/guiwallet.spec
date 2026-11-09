# PyInstaller build recipe for guiwallet.exe. It leaves out the parts of Qt this app
# doesn't use, which makes the file much smaller. If a future screen needs one of them
# (images or charts, for example), take it off these lists.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent

UNUSED_MODULES = [
    "PySide6.QtNetwork",
    "PySide6.QtSvg",
    "tkinter",
    "unittest",
    "pydoc",
]

# Matched against each bundled file's path, in lower case.
UNUSED_FILES = (
    "opengl32sw",  # Software graphics driver; plain forms don't use OpenGL.
    "qt6network",
    "qt6svg",
    "qt6pdf",
    "qt6opengl",
    "qt6qml",
    "qt6quick",
    "qt6virtualkeyboard",
    "libcrypto-3-x64",  # Qt's copies for its networking. Python's HTTPS has its own.
    "libssl-3-x64",
    "/imageformats/",
    "/iconengines/",
    "/translations/",
    "/tls/",
    "/networkinformation/",
    "/generic/",
    "qdirect2d",
    "qminimal",
)
if sys.platform == "win32":
    # Linux keeps these: input methods (accents, other alphabets) and the screen-less mode its tests use.
    UNUSED_FILES += ("/platforminputcontexts/", "qoffscreen")


def wanted(entry):
    path = entry[0].replace("\\", "/").lower()
    return not any(name in path for name in UNUSED_FILES)


a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    excludes=UNUSED_MODULES,
    datas=[(str(ROOT / "src" / "guiwallet" / "assets" / "*.png"), "guiwallet/assets")],
)
a.binaries = [entry for entry in a.binaries if wanted(entry)]
a.datas = [entry for entry in a.datas if wanted(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="guiwallet",
    console=False,
    icon=str(ROOT / "packaging" / "guiwallet.ico") if sys.platform == "win32" else None,
    upx=False,  # UPX shrinks files further but makes antivirus false alarms more likely.
)
