"""The app's look: a very dark blue theme, with the owl emblem's green as the accent."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

ASSETS = Path(__file__).resolve().parent.parent / "assets"
ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]

GREEN = "#8BE83A"
AMBER = "#E8B93A"
RED = "#F0706A"  # htnwallet not running.
GREY = "#6F7A92"
TOOLTIP_DELAY_MS = 200  # Qt waits 700 by default, which feels slow when reading addresses.

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


def apply_theme(application: QApplication) -> None:
    application.setStyle(_AppStyle())
    application.setStyleSheet(STYLE.replace("@CHEVRON@", (ASSETS / "chevron.png").as_posix()))
    # Tooltips appear at once, instead of following the Windows slide or fade animation.
    application.setEffectEnabled(Qt.UIEffect.UI_AnimateTooltip, False)
    application.setEffectEnabled(Qt.UIEffect.UI_FadeTooltip, False)


def app_icon() -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap(str(ASSETS / f"icon-{size}.png")))
    return icon
