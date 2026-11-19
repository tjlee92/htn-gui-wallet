"""Small building blocks shared by the screens."""

import segno
import math

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from guiwallet.ui import theme

QUIET_ZONE = 4  # QR scanners need this many blank modules around the code.
HIDDEN_AMOUNT = "••••"


def label(text: str = "", name: str | None = None) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def warn_box(text: str, width: int) -> QLabel:
    """An amber notice that is always tall enough for its text at the given width.

    QLabel works out its wrapped height without the style sheet's padding and border, so with wider fonts
    (as on Linux) the last lines were cut off. The sizes here match QLabel#warnBox in the theme.
    """
    box = label(text, "warnBox")
    box.setWordWrap(True)
    box.setFixedWidth(width)
    box.ensurePolished()  # Picks up the style sheet's font size before measuring.
    padding_x, padding_y, border = 12, 10, 1
    inner = QRect(0, 0, width - 2 * (padding_x + border), 0)
    text_height = box.fontMetrics().boundingRect(inner, Qt.TextFlag.TextWordWrap, text).height()
    box.setFixedHeight(text_height + 2 * (padding_y + border) + 2)
    return box


OFFICIAL_RPC_HINT = "The fastest synced public node is picked at each start. Public nodes see your addresses."


def official_rpc_hint() -> QLabel:
    """The line under a node field while it holds the official RPC. Hidden until then."""
    hint = label(OFFICIAL_RPC_HINT, "hint")
    hint.setWordWrap(True)
    hint.hide()
    return hint


def page_layout(page: QWidget, title: str, subtitle: str) -> QVBoxLayout:
    layout = QVBoxLayout(page)
    layout.setContentsMargins(32, 28, 32, 24)
    layout.setSpacing(4)
    layout.addWidget(label(title, "title"))
    layout.addWidget(label(subtitle, "subtitle"))
    layout.addSpacing(14)
    return layout


def card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("line")
    line.setFixedHeight(1)
    return line


def same_width(*buttons: QPushButton) -> None:
    """Make buttons that sit side by side match, however long their words are."""
    for button in buttons:
        button.ensurePolished()
    width = max(button.sizeHint().width() for button in buttons)
    for button in buttons:
        button.setMinimumWidth(width)


def fixed_width(width: int, *buttons: QPushButton) -> None:
    """Give buttons one fixed width: `width`, or more where the words need it. Linux has no Segoe UI,
    and the fonts it uses instead are wider, so a width that fits on Windows can cut the words off."""
    for button in buttons:
        button.ensurePolished()
    width = max([width] + [button.sizeHint().width() for button in buttons])
    for button in buttons:
        button.setFixedWidth(width)


def shorten_address(address: str) -> str:
    """Show the start and end of a long address. The full one is kept for its tooltip."""
    return address if len(address) <= 34 else f"{address[:20]}...{address[-10:]}"


def address_text(address: str, labels: dict[str, str] | None = None) -> str:
    """The short address, followed by its label if the user gave it one."""
    name = (labels or {}).get(address)
    return f"{shorten_address(address)}   ·   {name}" if name else shorten_address(address)


class StatusDot(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(9, 9)
        self.color = QColor("#6F7A92")

    def set_color(self, color: str) -> None:
        self.color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        painter.drawEllipse(self.rect())


class WindowButton(QPushButton):
    """The title bar's minimize and close buttons, which draw their own symbols."""

    def __init__(self, tooltip: str, is_close: bool = False) -> None:
        super().__init__()
        self.is_close = is_close
        self.setObjectName("windowCloseButton" if is_close else "windowButton")
        self.setFixedSize(32, 26)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # The hover background comes from the theme.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered_close = self.is_close and self.underMouse()
        painter.setPen(QPen(QColor("#FFFFFF" if hovered_close else theme.colour("#AEB8CC")), 1.5))
        x, y = self.width() / 2, self.height() / 2
        if self.is_close:
            painter.drawLine(QPointF(x - 4.5, y - 4.5), QPointF(x + 4.5, y + 4.5))
            painter.drawLine(QPointF(x - 4.5, y + 4.5), QPointF(x + 4.5, y - 4.5))
        else:
            painter.drawLine(QPointF(x - 5, y), QPointF(x + 5, y))


class ThemeButton(QPushButton):
    """Shows the mode the app is in: a moon in dark mode, a sun in light mode. Pressing it switches."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("windowButton")
        self.setFixedSize(32, 26)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._describe(theme.mode())
        theme.notifier.changed.connect(self._describe)

    def _describe(self, mode: str) -> None:
        text = "Dark mode (click for light)" if mode == theme.DARK else "Light mode (click for dark)"
        self.setToolTip(text)
        self.setAccessibleName(text)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # The hover background comes from the theme.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tone = QColor(theme.colour("#E7ECF5" if self.underMouse() else "#AEB8CC"))
        x, y = self.width() / 2, self.height() / 2
        if theme.mode() == theme.DARK:  # A crescent moon.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tone)
            moon = QPainterPath()
            moon.addEllipse(QPointF(x, y), 6, 6)
            bite = QPainterPath()
            bite.addEllipse(QPointF(x + 3.6, y - 2.6), 5, 5)
            painter.drawPath(moon.subtracted(bite))
        else:  # A sun: a ring with eight short rays.
            painter.setPen(QPen(tone, 1.5, c=Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(x, y), 3.2, 3.2)
            for ray in range(8):
                angle = ray * math.pi / 4
                painter.drawLine(
                    QPointF(x + 5.6 * math.cos(angle), y + 5.6 * math.sin(angle)),
                    QPointF(x + 7.4 * math.cos(angle), y + 7.4 * math.sin(angle)),
                )


class TitleBar(QFrame):
    """Replaces the Windows title bar: the app's name, the mode switch, minimize, close, and drag to move.

    Windows that can be minimized (the main window and the Setup Wizard) have the mode switch too.
    """

    def __init__(self, title: str, icon: QIcon, can_minimize: bool = True, version: str = "") -> None:
        super().__init__()
        self.setObjectName("titleBar")
        self.setFixedHeight(38)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 8, 0)
        layout.setSpacing(8)
        emblem = QLabel()
        emblem.setPixmap(theme.emblem(18))
        layout.addWidget(emblem)
        layout.addWidget(label(title, "appName"))
        if version:
            self.version = label(f"v{version}", "muted")
            layout.addWidget(self.version)
        layout.addStretch()
        if can_minimize:
            self.theme_button = ThemeButton()
            layout.addWidget(self.theme_button)
            self.minimize_button = WindowButton("Minimize")
            layout.addWidget(self.minimize_button)
        self.close_button = WindowButton("Close", is_close=True)
        layout.addWidget(self.close_button)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().windowHandle().startSystemMove()


class ResizeGrip(QWidget):
    """An invisible strip along one edge or corner of the window, for dragging it to a new size."""

    def __init__(self, parent: QWidget, edges: Qt.Edge, cursor: Qt.CursorShape) -> None:
        super().__init__(parent)
        self.edges = edges
        self.setCursor(cursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().windowHandle().startSystemResize(self.edges)


class EyeButton(QPushButton):
    """An eye that's open while balances are shown, and crossed out while they're hidden."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("eye")
        self.setCheckable(True)
        self.setFixedSize(24, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(self._describe)
        self._describe(False)

    def _describe(self, hidden: bool) -> None:
        text = "Show balances" if hidden else "Hide balances"
        self.setToolTip(text)
        self.setAccessibleName(text)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.colour("#E7ECF5" if self.underMouse() else "#8D98B0")), 1.5))
        x, y = self.width() / 2, self.height() / 2
        outline = QPainterPath(QPointF(x - 9, y))
        outline.quadTo(QPointF(x, y - 9), QPointF(x + 9, y))
        outline.quadTo(QPointF(x, y + 9), QPointF(x - 9, y))
        painter.drawPath(outline)
        painter.drawEllipse(QPointF(x, y), 2.5, 2.5)
        if self.isChecked():
            painter.drawLine(QPointF(x - 7, y + 7), QPointF(x + 7, y - 7))


class CopyButton(QPushButton):
    """Copies a piece of text to the clipboard and confirms it briefly."""

    def __init__(self, text: str = "") -> None:
        super().__init__("Copy")
        self.text_to_copy = text
        self.setMinimumWidth(72)
        self.clicked.connect(self._copy)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text_to_copy)
        self.setText("Copied")
        QTimer.singleShot(1500, lambda: self.setText("Copy"))


class QrCode(QWidget):
    """Draws a QR code, always dark on white, because that's what scanners read reliably."""

    def __init__(self, size: int = 264) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self.matrix: tuple | None = None

    def set_text(self, text: str | None) -> None:
        self.matrix = segno.make(text, error="m", micro=False).matrix if text else None
        self.update()

    def paintEvent(self, _event) -> None:
        if self.matrix is None:
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        modules = len(self.matrix)
        # A whole number of pixels per module keeps every square identical and crisp.
        module_size = self.width() // (modules + 2 * QUIET_ZONE)
        offset = (self.width() - module_size * modules) // 2
        black = QColor("black")
        for row, values in enumerate(self.matrix):
            for column, value in enumerate(values):
                if value:
                    painter.fillRect(
                        QRect(offset + column * module_size, offset + row * module_size, module_size, module_size),
                        black,
                    )


ICON_GREY = "#AEB8CC"
ICON_MUTED = "#7E89A3"
ICON_GREEN = "#B5F27A"


def _icon_canvas() -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pixmap, painter


def cycle_icon(colour: str) -> QIcon:
    """Two arrows chasing each other round a circle: the usual sign for something that repeats.
    The colour is a dark-mode one; light mode draws it in its partner colour."""
    pixmap, painter = _icon_canvas()
    painter.setPen(QPen(QColor(theme.colour(colour)), 5.5, c=Qt.PenCapStyle.RoundCap, j=Qt.PenJoinStyle.RoundJoin))
    centre, radius, head = 32, 20, 8
    box = QRectF(centre - radius, centre - radius, 2 * radius, 2 * radius)
    for start in (30, 210):
        sweep = 120
        path = QPainterPath()
        path.arcMoveTo(box, start)
        path.arcTo(box, start, sweep)
        painter.drawPath(path)
        angle = math.radians(start + sweep)
        tip = QPointF(centre + radius * math.cos(angle), centre - radius * math.sin(angle))
        along = QPointF(-math.sin(angle), -math.cos(angle)) * head
        across = QPointF(math.cos(angle), -math.sin(angle)) * head
        painter.drawPolyline([tip - along + across, tip, tip - along - across])
    painter.end()
    return QIcon(pixmap)


def copy_icon(colour: str, copied: bool = False) -> QIcon:
    """Two overlapping pages, or a tick once something has been copied."""
    pixmap, painter = _icon_canvas()
    painter.setPen(QPen(QColor(theme.colour(colour)), 5, c=Qt.PenCapStyle.RoundCap, j=Qt.PenJoinStyle.RoundJoin))
    if copied:
        painter.drawPolyline([QPointF(12, 33), QPointF(26, 47), QPointF(52, 18)])
    else:
        painter.drawRoundedRect(QRectF(22, 22, 30, 32), 6, 6)
        painter.drawPolyline([QPointF(42, 12), QPointF(18, 12), QPointF(12, 18), QPointF(12, 44)])
    painter.end()
    return QIcon(pixmap)


class CopyIconButton(QPushButton):
    """A small copy button that sits beside the text it copies, and shows a tick for a moment after."""

    def __init__(self, text: str, tooltip: str = "Copy address") -> None:
        super().__init__()
        self.setObjectName("copyIcon")
        self.text_to_copy = text
        self.tooltip = tooltip
        self.setFixedSize(28, 28)
        self.setIconSize(QSize(15, 15))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._show(copied=False)
        self.clicked.connect(self._copy)

    def _show(self, copied: bool) -> None:
        theme.set_icon(self, lambda: copy_icon(ICON_GREEN if copied else ICON_MUTED, copied))
        self.setToolTip("Copied" if copied else self.tooltip)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text_to_copy)
        self._show(copied=True)
        QTimer.singleShot(1500, lambda: self._show(copied=False))
