"""Builds the app's icons from the original emblem. Run this if the emblem changes.

Small sizes are cropped to the owl's face, because the emblem's fine detail disappears
at tray size. Run from the project folder:

    .venv\\Scripts\\python scripts\\make_icons.py
"""

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "Neon Tech Owl Emblem.png"
ASSETS = ROOT / "src" / "guiwallet" / "assets"
ICON_FILE = ROOT / "packaging" / "guiwallet.ico"

FACE = QRect(317, 190, 620, 620)  # The owl's face within the 1254 x 1254 emblem.
FACE_SIZES = [16, 24, 32, 48]
FULL_SIZES = [64, 128, 256]


def scaled(image: QImage, size: int) -> QImage:
    return image.scaled(
        size,
        size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def write_ico(path: Path, images: dict[int, bytes]) -> None:
    """Write a Windows .ico file. The format is a small header, then the PNG files."""
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    data = b""
    offset = len(header) + 16 * len(images)
    for size, png in images.items():
        pixels = 0 if size == 256 else size  # In this format, 0 means 256.
        entries += struct.pack("<BBBBHHII", pixels, pixels, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.write_bytes(header + entries + data)


def main() -> None:
    QGuiApplication([])
    emblem = QImage(str(SOURCE))
    if emblem.isNull():
        raise SystemExit(f"Could not read {SOURCE}")
    face = emblem.copy(FACE)

    ASSETS.mkdir(parents=True, exist_ok=True)
    images = {}
    for size in FACE_SIZES:
        images[size] = png_bytes(scaled(face, size))
    for size in FULL_SIZES:
        images[size] = png_bytes(scaled(emblem, size))
    for size, png in images.items():
        (ASSETS / f"icon-{size}.png").write_bytes(png)
    write_ico(ICON_FILE, images)
    print(f"Wrote {len(images)} icon sizes to {ASSETS} and {ICON_FILE}")


if __name__ == "__main__":
    main()
