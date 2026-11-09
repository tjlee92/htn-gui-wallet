"""The last step of a send: check the details, enter the password, and see what happened."""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from guiwallet.core.send import SendOutcome, SendRequest, SendResult
from guiwallet.core.validation import InvalidInput, validate_password
from guiwallet.ui.theme import app_icon
from guiwallet.ui.widgets import CopyButton, TitleBar, label, same_width

CANCELLED_SHOWN_MS = 2500  # How long "Transaction cancelled" stays up before the window closes itself.
WAIT_MESSAGE = (
    "Nothing has been sent yet. Please double check the recipient address and make sure that everything "
    "is correct. Once it's sent, it cannot be undone!"
)


def _selectable(text: str, name: str, wrap: bool = False) -> QLabel:
    widget = label(text, name)
    widget.setWordWrap(wrap)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def _add_detail(grid: QGridLayout, row: int, name: str, value: QLabel) -> None:
    """A name beside its value, on the same line as the value's first line, however big its text."""
    name_label = label(name, "subtitle")
    name_label.ensurePolished()
    value.ensurePolished()
    gap = QFontMetrics(value.font()).ascent() - QFontMetrics(name_label.font()).ascent()
    name_label.setContentsMargins(0, max(0, gap), 0, 0)
    grid.addWidget(name_label, row, 0, Qt.AlignmentFlag.AlignTop)
    grid.addWidget(value, row, 1)


class SendDialog(QDialog):
    """Stays open until the outcome is known, and can't be closed while a send is running."""

    send_requested = Signal(str)
    cancel_requested = Signal()
    send_now_requested = Signal()

    def __init__(self, parent: QWidget, request: SendRequest, command: str, show_debug: bool = False) -> None:
        """`command` is shown, password masked, only when Settings has debug details switched on."""
        super().__init__(parent)
        self.show_debug = show_debug
        self.setWindowTitle("Confirm send")
        self.busy = False  # htnwallet is running, so the dialog can't be closed.
        self.waiting = False  # Nothing has happened yet, so closing the dialog cancels the send.
        self.outcome: SendOutcome | None = None

        # Like the main window: the app's own title bar and rounded corners, with no minimize button.
        self.setObjectName("sendDialog")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowSystemMenuHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.title_bar = TitleBar(parent.windowTitle(), app_icon(), can_minimize=False)
        self.title_bar.close_button.clicked.connect(self.close)
        content = QWidget()
        root = QFrame()
        root.setObjectName("root")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(1, 1, 1, 1)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.title_bar)
        root_layout.addWidget(content)
        window_layout = QVBoxLayout(self)
        window_layout.setContentsMargins(0, 0, 0, 0)
        window_layout.addWidget(root)

        self.outer = QVBoxLayout(content)
        self.outer.setContentsMargins(24, 22, 24, 20)
        self.outer.setSpacing(12)
        self.form = QWidget()
        self.waiting_view = self._build_waiting_view(request)
        self.waiting_view.hide()
        self.result_view = QWidget()
        self.result_view.hide()
        self.outer.addWidget(self.form)
        self.outer.addWidget(self.waiting_view)
        self.outer.addWidget(self.result_view)

        layout = QVBoxLayout(self.form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(label("Confirm send", "dialogTitle"))
        layout.addWidget(label("This can't be undone once it's sent. Check the address and the amount.", "subtitle"))

        amount = "All spendable HTN, minus the fee" if request.send_all else f"{request.amount} HTN"
        rows = (
            # The address is never wrapped or cut off: the window takes its width from it.
            ("To", _selectable(request.to_address, "fullAddress")),
            ("Amount", _selectable(amount, "dialogAmount", wrap=True)),
            ("Fee", _selectable("0.0001 HTN for each coin spent", "dialogValue", wrap=True)),
            ("From", _selectable(request.from_address or "Any address", "dialogValue")),
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for row, (name, value) in enumerate(rows):
            _add_detail(grid, row, name, value)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        if self.show_debug:
            layout.addWidget(self._debug_box("DEBUG: the command that will run", command))

        layout.addWidget(label("Wallet password", "subtitle"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.textChanged.connect(self._password_changed)
        layout.addWidget(self.password)
        self.error = label("", "error")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.send_button = QPushButton("Send")  # The amount is in the details above, however long it is.
        self.send_button.setObjectName("primary")
        # Enter in the password box does nothing: sending always takes a deliberate click.
        self.send_button.setAutoDefault(False)
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self._send_clicked)
        buttons.addWidget(self.send_button)
        same_width(self.cancel_button, self.send_button)
        layout.addSpacing(4)
        layout.addLayout(buttons)
        self.password.setFocus()

    def _build_waiting_view(self, request: SendRequest) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.waiting_heading = label("Processing", "dialogTitle")
        layout.addWidget(self.waiting_heading)
        self.waiting_message = _selectable(WAIT_MESSAGE, "dialogValue", wrap=True)
        layout.addWidget(self.waiting_message)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        amount = "All spendable HTN, minus the fee" if request.send_all else f"{request.amount} HTN"
        _add_detail(grid, 0, "To", _selectable(request.to_address, "fullAddress"))
        _add_detail(grid, 1, "Amount", _selectable(amount, "dialogAmount", wrap=True))
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.countdown = label("", "dialogValue")
        layout.addWidget(self.countdown)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_send_button = QPushButton("Cancel send")
        self.cancel_send_button.setAutoDefault(False)
        self.cancel_send_button.clicked.connect(self.cancel_requested)
        buttons.addWidget(self.cancel_send_button)
        self.send_now_button = QPushButton("Send now")
        self.send_now_button.setObjectName("primary")
        # Only a mouse click: the keyboard can't land on it, and the focus stays on Cancel send.
        self.send_now_button.setAutoDefault(False)
        self.send_now_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.send_now_button.clicked.connect(self.send_now_requested)
        buttons.addWidget(self.send_now_button)
        same_width(self.cancel_send_button, self.send_now_button)
        layout.addSpacing(4)
        layout.addLayout(buttons)
        return view

    def _debug_box(self, title: str, text: str) -> QFrame:
        box = QFrame()
        box.setObjectName("debugBox")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(6)
        layout.addWidget(label(title, "debugTitle"))
        layout.addWidget(_selectable(text, "debugText"))
        return box

    def _password_changed(self, text: str) -> None:
        self.send_button.setEnabled(bool(text))
        self.error.hide()

    def _send_clicked(self) -> None:
        try:
            password = validate_password(self.password.text())
        except InvalidInput as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self.password.clear()  # So it isn't left sitting in the window during or after the send.
        self.send_requested.emit(password)

    def show_waiting(self, seconds_left: int) -> None:
        self.waiting = True
        self.setWindowTitle("Processing")
        self.setMinimumWidth(self.width())
        self.form.hide()
        self.show_countdown(seconds_left)
        self.waiting_view.show()
        self.adjustSize()
        self.cancel_send_button.setFocus()

    def show_countdown(self, seconds_left: int) -> None:
        minutes, seconds = divmod(seconds_left, 60)
        self.countdown.setText(f"Sending in {minutes}:{seconds:02d}")

    def show_sending(self) -> None:
        """The last few seconds, which can't be stopped, so nothing here offers to."""
        self.waiting = False
        self.busy = True
        self.setWindowTitle("Sending")
        self.waiting_heading.setText("Sending…")
        self.waiting_message.setText("Your transaction is being sent. Please wait.")
        self.countdown.hide()
        self.cancel_send_button.hide()
        self.send_now_button.hide()

    def show_outcome(self, outcome: SendOutcome) -> None:
        self.busy = False
        self.waiting = False
        self.outcome = outcome
        self.setMinimumWidth(self.width())
        self.setWindowTitle("Send result")
        self.form.hide()
        self.waiting_view.hide()

        layout = QVBoxLayout(self.result_view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        heading, name = {
            SendResult.SENT: ("Sent", "resultGood"),
            SendResult.NOT_SENT: ("Nothing was sent", "resultBad"),
            SendResult.MAY_HAVE_BEEN_SENT: ("This may have been sent", "resultWarning"),
        }[outcome.result]
        layout.addWidget(label(heading, name))
        layout.addWidget(_selectable(outcome.message, "dialogValue", wrap=True))
        for transaction_id in outcome.transaction_ids:
            row = QHBoxLayout()
            row.addWidget(_selectable(transaction_id, "address"))
            row.addStretch()
            row.addWidget(CopyButton(transaction_id))
            layout.addWidget(label("Transaction ID", "subtitle"))
            layout.addLayout(row)
        if self.show_debug and (outcome.detail or outcome.seconds is not None):
            lines = []
            if outcome.seconds is not None:
                lines.append(f"Took {outcome.seconds:.1f} seconds")
            if outcome.detail:
                lines.append(f"htnwallet said: {outcome.detail}")
            debug = self._debug_box("DEBUG", "\n".join(lines))
            debug.findChild(QLabel, "debugText").setWordWrap(True)
            layout.addWidget(debug)
        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton("Close")
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addSpacing(4)
        layout.addLayout(buttons)
        self.result_view.show()
        self.adjustSize()

    def show_cancelled(self) -> None:
        """Say so for a moment, then close by itself. The X closes it straight away."""
        self.waiting = False
        self.outcome = SendOutcome(SendResult.NOT_SENT, "Transaction cancelled. No funds were sent.")
        self.setMinimumWidth(self.width())
        self.setWindowTitle("Transaction cancelled")
        self.form.hide()
        self.waiting_view.hide()
        layout = QVBoxLayout(self.result_view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(label("Transaction cancelled", "dialogTitle"))
        layout.addWidget(label("No funds were sent.", "dialogValue"))
        self.result_view.show()
        self.adjustSize()
        self.close_timer = QTimer(self, singleShot=True, interval=CANCELLED_SHOWN_MS)
        self.close_timer.timeout.connect(self.accept)
        self.close_timer.start()

    # Esc and the window's close button: refused during the send itself, and a cancel during the wait.
    def reject(self) -> None:
        if self.waiting:
            self.cancel_requested.emit()
        elif not self.busy:
            super().reject()

    def closeEvent(self, event) -> None:
        if self.waiting:
            event.ignore()
            self.cancel_requested.emit()
        elif self.busy:
            event.ignore()
        else:
            super().closeEvent(event)
