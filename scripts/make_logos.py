"""Builds the GitHub and Discord logos for the About page, and the darker drop-down arrow for light mode.

The app itself leaves out Qt's SVG support to stay small, so the logos are drawn here, once, into PNG
files. Run from the project folder:

    .venv\\Scripts\\python scripts\\make_logos.py

GitHub's mark is from GitHub's Octicons (MIT licence). Discord's is its brand mark, used only to link to
Discord. Both are listed in THIRD-PARTY-NOTICES.md.
"""

from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "src" / "guiwallet" / "assets"
SIZE = 48  # Shown at 15 pixels, so this stays sharp at up to 300% scaling.

GITHUB = (
    16,
    "M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49"
    "-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07"
    "-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2"
    ".82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82"
    " 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0"
    " 0016 8c0-4.42-3.58-8-8-8z",
)
DISCORD = (
    24,
    "M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083"
    " 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037"
    " 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824"
    ".0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731"
    "-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0"
    " 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933"
    " 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0"
    " 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961"
    "-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061"
    ".061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189"
    " 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857"
    "-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189"
    "-2.1568 2.4189Z",
)
CHEVRON_LIGHT = QColor("#4A5570")  # Light mode's icon grey, for the arrow on white fields.


def logo(view_box: int, path: str) -> QImage:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {view_box} {view_box}">'
        f'<path fill="#FFFFFF" d="{path}"/></svg>'
    )
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return image


def recoloured(image: QImage, colour: QColor) -> QImage:
    """The same shape and transparency, in another colour."""
    result = image.convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(result.height()):
        for x in range(result.width()):
            alpha = result.pixelColor(x, y).alpha()
            result.setPixelColor(x, y, QColor(colour.red(), colour.green(), colour.blue(), alpha))
    return result


def main() -> None:
    QGuiApplication([])
    logo(*GITHUB).save(str(ASSETS / "github.png"))
    logo(*DISCORD).save(str(ASSETS / "discord.png"))
    recoloured(QImage(str(ASSETS / "chevron.png")), CHEVRON_LIGHT).save(str(ASSETS / "chevron-light.png"))
    print(f"Wrote github.png, discord.png and chevron-light.png to {ASSETS}")


if __name__ == "__main__":
    main()
