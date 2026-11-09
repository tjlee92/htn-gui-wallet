"""The windows for starting and stopping auto-compound on one address."""

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from guiwallet.core.compound import (
    DEFAULT_INTERVAL_SECONDS,
    MAX_INTERVAL_SECONDS,
    RunningCompound,
    validate_interval,
)
from guiwallet.core.validation import InvalidInput, validate_password
from guiwallet.ui.send_dialog import _add_detail, _selectable
from guiwallet.ui.theme import app_icon
from guiwallet.ui.widgets import TitleBar, label, shorten_address

BUTTON_WIDTH = 130


class _FramedDialog(QDialog):
    """The app's own title bar and rounded corners, like the send window. Can't be closed while busy."""

    def __init__(self, parent: QWidget, title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.busy = False
        self.setObjectName("sendDialog")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowSystemMenuHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        title_bar = TitleBar(parent.windowTitle(), app_icon(), can_minimize=False)
        title_bar.close_button.clicked.connect(self.close)
        content = QWidget()
        root = QFrame()
        root.setObjectName("root")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(1, 1, 1, 1)
        root_layout.setSpacing(0)
        root_layout.addWidget(title_bar)
        root_layout.addWidget(content)
        window_layout = QVBoxLayout(self)
        window_layout.setContentsMargins(0, 0, 0, 0)
        window_layout.addWidget(root)
        self.layout_ = QVBoxLayout(content)
        self.layout_.setContentsMargins(24, 22, 24, 20)
        self.layout_.setSpacing(12)
        self.layout_.addWidget(label(title, "dialogTitle"))
        self.error = label("", "error")
        self.error.setWordWrap(True)
        self.error.hide()

    def _buttons(self, cancel_text: str, action_text: str) -> tuple[QPushButton, QPushButton]:
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton(cancel_text)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        action = QPushButton(action_text)
        action.setObjectName("primary")
        action.setAutoDefault(False)
        for button in (cancel, action):
            button.setFixedWidth(BUTTON_WIDTH)  # Side by side, so the same size.
            row.addWidget(button)
        self.layout_.addWidget(self.error)
        self.layout_.addSpacing(4)
        self.layout_.addLayout(row)
        return cancel, action

    def show_error(self, message: str) -> None:
        self.busy = False
        self.error.setText(message)
        self.error.show()
        self.adjustSize()

    def reject(self) -> None:
        if not self.busy:
            super().reject()

    def closeEvent(self, event) -> None:
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)


class StartCompoundDialog(_FramedDialog):
    start_requested = Signal(str, int, str)  # To address, seconds, password.

    def __init__(self, parent: QWidget, from_address: str, wallet_addresses: list[str]) -> None:
        super().__init__(parent, "Start auto-compound")
        intro = label(
            "Combines the UTXOs of this address into one transaction at each interval, "
            "until stopped or the application is closed.",
            "subtitle",
        )
        intro.setWordWrap(True)
        self.layout_.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        _add_detail(grid, 0, "From", _selectable(from_address, "fullAddress"))
        self.target = QComboBox()
        self.target.addItem(f"Same address   ·   {shorten_address(from_address)}", from_address)
        for address in wallet_addresses:
            if address != from_address:
                self.target.addItem(shorten_address(address), address)
                self.target.setItemData(self.target.count() - 1, address, Qt.ItemDataRole.ToolTipRole)
        grid.addWidget(label("To", "subtitle"), 1, 0)
        grid.addWidget(self.target, 1, 1)
        self.interval = QLineEdit(str(DEFAULT_INTERVAL_SECONDS))
        self.interval.setValidator(QIntValidator(0, MAX_INTERVAL_SECONDS, self))
        self.interval.setFixedWidth(90)
        interval_row = QHBoxLayout()
        interval_row.setSpacing(10)
        interval_row.addWidget(self.interval)
        interval_row.addWidget(label("seconds", "subtitle"))
        interval_row.addStretch()
        grid.addWidget(label("Interval", "subtitle"), 2, 0)
        grid.addLayout(interval_row, 2, 1)
        grid.setColumnStretch(1, 1)
        self.layout_.addLayout(grid)

        self.layout_.addWidget(label("Wallet password", "subtitle"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.layout_.addWidget(self.password)
        self.cancel_button, self.start_button = self._buttons("Cancel", "Start")
        self.start_button.setEnabled(False)
        self.password.textChanged.connect(lambda text: self.start_button.setEnabled(bool(text) and not self.busy))
        self.start_button.clicked.connect(self._start_clicked)
        self.password.setFocus()

    def _start_clicked(self) -> None:
        try:
            seconds = validate_interval(self.interval.text())
            password = validate_password(self.password.text())
        except InvalidInput as error:
            self.show_error(str(error))
            return
        self.busy = True
        self.error.hide()
        for widget in (self.target, self.interval, self.password, self.cancel_button, self.start_button):
            widget.setEnabled(False)
        self.start_button.setText("Starting…")
        self.start_requested.emit(self.target.currentData(), seconds, password)

    def show_error(self, message: str) -> None:
        super().show_error(message)
        for widget in (self.target, self.interval, self.password, self.cancel_button):
            widget.setEnabled(True)
        self.start_button.setText("Start")
        self.start_button.setEnabled(bool(self.password.text()))


class StopCompoundDialog(_FramedDialog):
    stop_requested = Signal()

    def __init__(self, parent: QWidget, compound: RunningCompound, details: tuple[int, float] | None) -> None:
        super().__init__(parent, "Stop auto-compound?")
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        from_text = "\n".join(compound.from_addresses) or "All addresses in the wallet"
        _add_detail(grid, 0, "From", _selectable(from_text, "fullAddress"))
        same = compound.to_address is None or compound.from_addresses == (compound.to_address,)
        to_value = _selectable("Same address", "dialogValue") if same else _selectable(compound.to_address, "fullAddress")
        _add_detail(grid, 1, "To", to_value)
        _add_detail(grid, 2, "Interval", _selectable(f"{compound.interval} seconds", "dialogValue"))
        if details is not None:
            count, started_at = details
            noun = "transaction" if count == 1 else "transactions"
            summary = f"{count:,} {noun} since {time.strftime('%H:%M', time.localtime(started_at))}"
            _add_detail(grid, 3, "Compounded", _selectable(summary, "dialogValue"))
        else:
            _add_detail(grid, 3, "Started", _selectable("Outside this application", "dialogValue"))
        grid.setColumnStretch(1, 1)
        self.layout_.addLayout(grid)
        self.keep_button, self.stop_button = self._buttons("Keep running", "Stop")
        self.stop_button.clicked.connect(self._stop_clicked)

    def _stop_clicked(self) -> None:
        self.busy = True
        self.error.hide()
        self.keep_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.stop_button.setText("Stopping…")
        self.stop_requested.emit()

    def show_error(self, message: str) -> None:
        super().show_error(message)
        self.keep_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("Stop")
