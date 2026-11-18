"""The app's look: a very dark blue theme, with the owl emblem's green as the accent, and a light one.

The dark colours are written out once, in STYLE and in the few widgets that set their own. Light mode
swaps each of them for its partner in LIGHT. A widget that sets its own colours does so through
set_style, set_rich_text or set_icon, so switching modes can apply them again without a restart.
"""

import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QProxyStyle, QStyle, QWidget

ASSETS = Path(__file__).resolve().parent.parent / "assets"
ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]

GREEN = "#8BE83A"
AMBER = "#E8B93A"
RED = "#F0706A"  # htnwallet not running.
GREY = "#6F7A92"
# Green and amber used as text. They differ by one from GREEN and AMBER, which can't be told apart by
# eye, only so that light mode can darken the text without darkening the main button or the status dots.
GREEN_TEXT = "#8BE83B"
AMBER_TEXT = "#E8B93B"
TOOLTIP_DELAY_MS = 200  # Qt waits 700 by default, which feels slow when reading addresses.

DARK, LIGHT_MODE = "dark", "light"
MODES = (DARK, LIGHT_MODE)

# Each dark colour and the one light mode uses in its place. The status dots' green, amber and red
# read well on both, so they stay.
LIGHT = {
    "#0A0F1C": "#F3F5F9",  # Window.
    "#0E1526": "#FFFFFF",  # Title bar, sidebar.
    "#121A2E": "#FFFFFF",  # Cards, menus.
    "#18243F": "#EEF8E4",  # Selected card.
    "#182238": "#ECEFF5",  # Hover.
    "#1C2A47": "#E3F2D4",  # Chosen menu item or tab.
    "#212B44": "#DEE3EC",  # Borders.
    "#2C3857": "#C9D0DE",  # Field borders.
    "#E7ECF5": "#151B28",  # Text.
    "#8D98B0": "#5E6982",  # Quieter text.
    "#AEB8CC": "#4A5570",  # Menu text, icons.
    "#CBD3E3": "#2A3346",  # Addresses.
    "#6F7A92": "#7C869B",  # Hints.
    "#B5F27A": "#3B7A0C",  # Green text: the bright green can't be read on white.
    "#A3F05C": "#7BD62B",  # Main button, hovered.
    "#F28B82": "#C0362C",  # Errors.
    "#F0706A": "#C0362C",  # Sent, in History.
    "#0D1321": "#EEF1F6",
    "#232C42": "#E1E5EC",
    "#667089": "#8C95A8",
    "#5C6680": "#9AA2B3",
    "#34507F": "#BFD3F5",
    "#4F5A73": "#A3ABBB",
    "#16223A": "#EAF1FD",
    "#31456B": "#B9CBEA",
    "#DCE6FA": "#1E3A6B",
    "#241E0E": "#FFF6DD",  # Warning box.
    "#6B5A1E": "#E6C66B",
    "#F0D88A": "#6B5000",
    "#33290F": "#FBEBC0",
    "#E8C547": "#8A6A00",
    "#4A5778": "#B5BDCC",
    "#1A2338": "#E3E7EE",
    "#7E89A3": "#6A748A",
    "#3D444D": "#24292F",  # GitHub button edge: on white it matches the button.
    GREEN_TEXT: "#3B7A0C",
    AMBER_TEXT: "#A87400",
}
_LIGHT_EXTRA = """
QLabel#resultGood, QLabel#ok { color: #3B7A0C; }
QLabel#resultWarning { color: #A87400; }
"""
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")
_STYLE, _HTML, _ICON = "themeStyle", "themeHtml", "themeIcon"
_mode = DARK


def mode() -> str:
    return _mode


def colour(dark: str) -> str:
    """The colour to use now for a colour written for dark mode."""
    return LIGHT.get(dark.upper(), dark) if _mode == LIGHT_MODE else dark


def recolour(text: str) -> str:
    """A stylesheet or piece of HTML written for dark mode, with its colours for the current mode."""
    return _HEX.sub(lambda match: colour(match.group(0)), text) if _mode == LIGHT_MODE else text


def set_style(widget: QWidget, dark_style: str) -> None:
    """Give a widget its own stylesheet, written in dark colours, that follows the mode."""
    widget.setProperty(_STYLE, dark_style)
    widget.setStyleSheet(recolour(dark_style))


def set_rich_text(widget: QLabel, dark_html: str) -> None:
    widget.setProperty(_HTML, dark_html)
    widget.setText(recolour(dark_html))


def set_icon(widget, make: Callable[[], QIcon]) -> None:
    """Give a button an icon drawn in the current mode's colours, drawn again when the mode changes."""
    widget.setProperty(_ICON, make)
    widget.setIcon(make())


class _Notifier(QObject):
    changed = Signal(str)


notifier = _Notifier()


def set_mode(new_mode: str) -> None:
    """Switch every open window to dark or light."""
    global _mode
    _mode = new_mode if new_mode in MODES else DARK
    application = QApplication.instance()
    if application is None:
        return
    application.setStyleSheet(_stylesheet())
    for widget in application.allWidgets():
        if (dark_style := widget.property(_STYLE)) is not None:
            widget.setStyleSheet(recolour(dark_style))
        if (dark_html := widget.property(_HTML)) is not None:
            widget.setText(recolour(dark_html))
        if (make := widget.property(_ICON)) is not None:
            widget.setIcon(make())
        widget.update()  # Widgets that paint themselves read their colours as they paint.
    notifier.changed.emit(_mode)


def _stylesheet() -> str:
    chevron = ASSETS / ("chevron-light.png" if _mode == LIGHT_MODE else "chevron.png")
    style = STYLE.replace("@CHEVRON@", chevron.as_posix())
    return recolour(style) + _LIGHT_EXTRA if _mode == LIGHT_MODE else style


STYLE = """
* { font-family: "Segoe UI"; color: #E7ECF5; }
QWidget#root, QDialog { background: #0A0F1C; }
QFrame#root { border: 1px solid #212B44; border-radius: 12px; }
QDialog#sendDialog { background: transparent; }
QFrame#titleBar {
    background: #0E1526; border-bottom: 1px solid #212B44;
    border-top-left-radius: 11px; border-top-right-radius: 11px;
}
QLabel#appName { font-size: 13px; font-weight: 600; }
QPushButton#windowButton, QPushButton#windowCloseButton {
    border: none; padding: 0; background: transparent; border-radius: 6px;
}
QPushButton#windowButton:hover { background: #1C2A47; }
QPushButton#windowCloseButton:hover { background: #C42B1C; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
QFrame#sidebar { background: #0E1526; border-right: 1px solid #212B44; border-bottom-left-radius: 11px; }
QLabel#balance { font-size: 25px; font-weight: 600; }
QLabel#unit, QLabel#sectionLabel { color: #8D98B0; font-size: 12px; font-weight: 600; }
QLabel#muted { color: #8D98B0; font-size: 12px; }
QLabel#title { font-size: 24px; font-weight: 600; }
QLabel#subtitle { color: #8D98B0; font-size: 13px; }
QLabel#cardValue { font-size: 21px; font-weight: 600; }
QLabel#address { font-family: "Consolas"; font-size: 13px; color: #CBD3E3; }
QLabel#amount { font-size: 13px; font-weight: 600; }
QLabel#error { color: #F28B82; font-size: 12px; }
QLabel#dialogTitle { font-size: 19px; font-weight: 600; }
QLabel#dialogAmount { font-size: 17px; font-weight: 600; }
QLabel#dialogValue { font-size: 13px; }
QLabel#fullAddress { font-family: "Consolas"; font-size: 13px; }
QLabel#resultGood, QLabel#resultBad, QLabel#resultWarning { font-size: 19px; font-weight: 600; }
QLabel#resultGood { color: #8BE83A; }
QLabel#resultBad { color: #F28B82; }
QLabel#resultWarning { color: #E8B93A; }
QFrame#debugBox { background: #0D1321; border: 1px solid #6B5A1E; border-radius: 8px; }
QLabel#debugTitle { color: #E8C547; font-size: 12px; font-weight: 600; }
QLabel#debugText { font-family: "Consolas"; font-size: 12px; color: #CBD3E3; }
QLabel#banner {
    background: #16223A; border: 1px solid #31456B; border-radius: 8px;
    padding: 9px 12px; color: #DCE6FA; font-size: 13px;
}
QFrame#card { background: #121A2E; border: 1px solid #212B44; border-radius: 12px; }
QFrame#selectedCard { background: #18243F; border: 1px solid #8BE83A; border-radius: 12px; }
QFrame#line { background: #212B44; }

QPushButton#nav {
    text-align: left; padding: 10px 14px; border: none; border-radius: 8px;
    background: transparent; color: #AEB8CC; font-size: 14px;
}
QPushButton#nav:hover { background: #182238; color: #E7ECF5; }
QPushButton#nav:checked { background: #1C2A47; color: #B5F27A; font-weight: 600; }

QPushButton {
    background: transparent; border: 1px solid #2C3857; border-radius: 8px;
    padding: 8px 14px; font-size: 13px;
}
QPushButton:hover { background: #182238; }
QPushButton:disabled { color: #5C6680; border-color: #212B44; }
QPushButton#eye { border: none; padding: 0; background: transparent; }
QPushButton#primary {
    background: #8BE83A; color: #0B1F05; border-color: #8BE83A; font-weight: 600;
}
QPushButton#primary:hover { background: #A3F05C; border-color: #A3F05C; }
QPushButton#primary:disabled { background: #232C42; border-color: #232C42; color: #667089; }

QLineEdit, QComboBox {
    background: #0A0F1C; border: 1px solid #2C3857; border-radius: 8px;
    padding: 9px 12px; font-size: 14px; selection-background-color: #34507F;
}
QLineEdit:focus, QComboBox:focus { border-color: #8BE83A; }
QLineEdit:disabled, QComboBox:disabled { color: #667089; background: #0D1321; }
QFrame#sendTabs { background: #0E1526; border: 1px solid #212B44; border-radius: 10px; }
QPushButton#sendTab {
    border: none; border-radius: 7px; padding: 8px 0; color: #AEB8CC; background: transparent;
}
QPushButton#sendTab:hover { background: #182238; }
QPushButton#sendTab:checked { background: #1C2A47; color: #B5F27A; font-weight: 600; }
QPushButton#copyIcon { border: none; background: transparent; border-radius: 6px; padding: 0; }
QPushButton#copyIcon:hover { background: #1C2A47; }
QPushButton#compound { padding: 0; }
QPushButton#pageButton { padding: 0; font-size: 12px; border-radius: 6px; }
QPushButton#sendAll:checked {
    border-color: #8BE83A; color: #B5F27A; font-weight: 600; background: #18243F;
}
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow { image: url("@CHEVRON@"); width: 12px; height: 12px; margin-right: 12px; }
QComboBox QAbstractItemView {
    background: #121A2E; border: 1px solid #2C3857; selection-background-color: #1C2A47;
}
QCheckBox { color: #8D98B0; font-size: 12px; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #2C3857; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenu { background: #121A2E; border: 1px solid #2C3857; padding: 4px; }
QMenu::item { padding: 7px 22px; border-radius: 4px; }
QMenu::item:selected { background: #1C2A47; }
QMessageBox { background: #0A0F1C; }
QFrame#choice { background: #121A2E; border: 1px solid #212B44; border-radius: 12px; }
QFrame#choiceOn { background: #18243F; border: 1px solid #8BE83A; border-radius: 12px; }
QFrame#choice:disabled { background: #0D1321; }
QLabel#choiceTitle { font-size: 15px; font-weight: 600; }
QLabel#choiceTitle:disabled { color: #4F5A73; }
QLabel#step { color: #8D98B0; font-size: 12px; font-weight: 600; }
QLabel#wordNo { color: #6F7A92; font-size: 11px; }
QLineEdit#word { font-family: "Consolas"; font-size: 13px; padding: 6px 8px; }
QLabel#seedWord {
    font-family: "Consolas"; font-size: 14px; background: #0A0F1C; border: 1px solid #212B44;
    border-radius: 8px; padding: 7px 10px;
}
QLabel#warnBox {
    background: #241E0E; border: 1px solid #6B5A1E; border-radius: 8px; padding: 10px 12px;
    color: #F0D88A; font-size: 13px;
}
QLabel#note {
    background: #16223A; border: 1px solid #31456B; border-radius: 8px; padding: 9px 12px;
    color: #DCE6FA; font-size: 12px;
}
QLabel#ok { color: #8BE83A; font-size: 12px; }
QLabel#hint { color: #6F7A92; font-size: 11px; }
QLabel#addressLabel { color: #8D98B0; font-size: 12px; }
QLabel#addressLabelEmpty { color: #4F5A73; font-size: 12px; }
QLabel#addressLabel:hover, QLabel#addressLabelEmpty:hover { color: #B5F27A; }
QLineEdit#labelEdit { font-size: 12px; padding: 3px 6px; border-radius: 6px; }
QPlainTextEdit#phrase {
    background: #0A0F1C; border: 1px solid #2C3857; border-radius: 8px; padding: 8px 10px;
    font-family: "Consolas"; font-size: 14px; selection-background-color: #34507F;
}
QPlainTextEdit#phrase:focus { border-color: #8BE83A; }
QFrame#notice { background: #241E0E; border: 1px solid #6B5A1E; border-radius: 8px; }
QFrame#notice QLabel { color: #F0D88A; font-size: 13px; background: transparent; border: none; }
QLabel#noticeTitle { font-weight: 600; }
QFrame#notice QPushButton {
    color: #F0D88A; border: 1px solid #6B5A1E; border-radius: 6px; padding: 5px 12px; font-size: 12px;
    background: transparent;
}
QFrame#notice QPushButton:hover { background: #33290F; }
QLabel#navBadge {
    background: #E5484D; color: #FFFFFF; border-radius: 10px; font-size: 11px; font-weight: 700;
}
QPushButton#smallButton { padding: 0 12px; font-size: 12px; border-radius: 6px; }
QLabel#rowTitle { font-size: 14px; font-weight: 600; }
QLabel#rowText { color: #8D98B0; font-size: 12px; }
QPushButton#githubPill, QPushButton#discordPill {
    color: #FFFFFF; border-radius: 15px; padding: 0 14px 0 11px; font-size: 12px; font-weight: 600;
}
QPushButton#githubPill { background: #24292F; border: 1px solid #3D444D; }
QPushButton#githubPill:hover { background: #32383F; }
QPushButton#discordPill { background: #5865F2; border: 1px solid #5865F2; }
QPushButton#discordPill:hover { background: #4752C4; border-color: #4752C4; }
QToolTip { background: #121A2E; color: #E7ECF5; border: 1px solid #2C3857; padding: 4px 6px; font-size: 12px; }
"""


class _AppStyle(QProxyStyle):
    """Fusion, a base that looks the same on every Windows version, with quicker tooltips."""

    def __init__(self) -> None:
        super().__init__("Fusion")

    def styleHint(self, hint, option=None, widget=None, returnData=None) -> int:
        if hint == QStyle.StyleHint.SH_ToolTip_WakeUpDelay:
            return TOOLTIP_DELAY_MS
        return super().styleHint(hint, option, widget, returnData)


def apply_theme(application: QApplication, start_mode: str = DARK) -> None:
    global _mode
    _mode = start_mode if start_mode in MODES else DARK
    application.setStyle(_AppStyle())
    application.setStyleSheet(_stylesheet())
    # Tooltips appear at once, instead of following the Windows slide or fade animation.
    application.setEffectEnabled(Qt.UIEffect.UI_AnimateTooltip, False)
    application.setEffectEnabled(Qt.UIEffect.UI_FadeTooltip, False)


def app_icon() -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap(str(ASSETS / f"icon-{size}.png")))
    return icon


def emblem(size: int, scale: float = 2.0) -> QPixmap:
    """The owl with rounded corners, for the title bar and the About page. Drawn at twice the size, so
    it stays sharp on screens scaled beyond 100%. Small sizes use the close-up of the owl's face."""
    source = QImage(str(ASSETS / ("icon-48.png" if size <= 32 else "icon-256.png")))
    pixels = round(size * scale)
    image = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    corner = pixels * 0.22
    outline = QPainterPath()
    outline.addRoundedRect(QRectF(0, 0, pixels, pixels), corner, corner)
    painter.setClipPath(outline)
    painter.drawImage(QRectF(0, 0, pixels, pixels), source)
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(scale)
    return pixmap
