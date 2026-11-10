"""The Setup Wizard: create a wallet, restore one, or use an existing one.

It opens by itself on the first launch, and from Settings at any time. It works on a copy of the
settings and only hands its changes over when it finishes, so Cancel or Exit leaves everything as it was.
"""

import dataclasses
import random
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from guiwallet.core.commands import wallet_context
from guiwallet.core.daemon import (
    DaemonNotSynced,
    close_daemon,
    last_log_line,
    port_is_open,
    read_receive_addresses,
    read_wallet,
    running_daemon_version,
    start_daemon,
)
from guiwallet.core.nodes import OFFICIAL_RPC, Node, NodeError, find_nodes, is_official
from guiwallet.core.settings import Settings, settings_path
from guiwallet.core.setup import (
    INVALID_BACKUP,
    INVALID_KEY,
    INVALID_PHRASE,
    RunningDaemon,
    SetupError,
    check_new_password,
    create_wallet,
    find_running_daemons,
    free_wallet_file,
    keys_file_setting,
    read_seed_phrase,
    restore_from_phrase,
    restore_private_key,
    restore_web_wallet_backup,
    restore_web_wallet_phrase,
    tool_folders,
    wallet_file,
)
from guiwallet.core.tools import Tool, ToolError, find_tool
from guiwallet.core.validation import InvalidInput, validate_daemon_address, validate_node_address
from guiwallet.core.wallet import format_htn
from guiwallet.ui.background import run_in_background
from guiwallet.ui.compound_dialog import _FramedDialog
from guiwallet.ui.theme import AMBER, GREEN, RED, app_icon
from guiwallet.ui.widgets import StatusDot, TitleBar, copy_icon, label, official_rpc_hint, shorten_address

BUTTON_WIDTH = 130
COLUMN_WIDTH = 600
CHECK_WORDS = 3  # How many words of the new seed phrase the user types back.
COPIED_SECONDS = 1.5
DONE_POLL_MS = 2000
DONE_START_TIMEOUT_SECONDS = 120
MISMATCH = "These words don't match. Check what you wrote down, or go Back to see the words again."

# What the wizard asks of the main window when it finishes.
START = "start"  # Start htnwallet with the wallet file now in the settings.
STARTED = "started"  # The wizard already started htnwallet.
SAVED = "saved"  # A new wallet was made: only save the settings, so the user can check them before pressing Start.
CONNECT = "connect"  # Use the htnwallet already running at the address now in the settings.


class _Radio(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.checked = False
        self.setFixedSize(18, 18)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        painter.setPen(QColor(GREEN if self.checked and enabled else "#4A5778"))
        painter.drawEllipse(1, 1, 16, 16)
        if self.checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(GREEN))
            painter.drawEllipse(5, 5, 8, 8)


class ChoiceCard(QFrame):
    """A card with a radio mark, a title and a description. Cards in one group behave like radio buttons."""

    clicked = Signal()

    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        self.setObjectName("choice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 14, 18, 14)
        row.setSpacing(14)
        self.radio = _Radio()
        row.addWidget(self.radio, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(3)
        self.title = label(title, "choiceTitle")
        text.addWidget(self.title)
        self.description = label(description, "subtitle")
        self.description.setWordWrap(True)
        text.addWidget(self.description)
        row.addLayout(text, 1)

    def set_checked(self, checked: bool) -> None:
        self.radio.checked = checked
        self.setObjectName("choiceOn" if checked else "choice")
        self.style().unpolish(self)
        self.style().polish(self)
        self.radio.update()

    def is_checked(self) -> bool:
        return self.radio.checked

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.clicked.emit()


def _group(cards: list[ChoiceCard], on_change=None) -> None:
    def choose(chosen: ChoiceCard) -> None:
        for card in cards:
            card.set_checked(card is chosen)
        if on_change:
            on_change()

    for card in cards:
        card.clicked.connect(lambda card=card: choose(card))
    cards[0].set_checked(True)


def _select(cards: list[ChoiceCard], chosen: ChoiceCard) -> None:
    for card in cards:
        card.set_checked(card is chosen)


class _Page(QWidget):
    """A centred column with an optional step line, a title and a subtitle, and buttons at the bottom."""

    def __init__(self, step: str, title: str, subtitle: str) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch()
        column = QWidget()
        column.setFixedWidth(COLUMN_WIDTH)
        row.addWidget(column)
        row.addStretch()
        self.layout_ = QVBoxLayout(column)
        self.layout_.setContentsMargins(0, 34, 0, 26)
        self.layout_.setSpacing(4)
        self.step = label(step, "step")
        self.step.setVisible(bool(step))
        self.layout_.addWidget(self.step)
        self.layout_.addSpacing(4)
        self.title = label(title, "title")
        self.layout_.addWidget(self.title)
        self.subtitle = label(subtitle, "subtitle")
        self.subtitle.setWordWrap(True)
        self.layout_.addWidget(self.subtitle)
        self.layout_.addSpacing(18)
        self.error = label("", "error")
        self.error.setWordWrap(True)
        self.error.hide()

    def add(self, widget: QWidget, space_after: int = 0) -> QWidget:
        self.layout_.addWidget(widget)
        if space_after:
            self.layout_.addSpacing(space_after)
        return widget

    def finish(self, back_text: str, next_text: str) -> tuple[QPushButton, QPushButton]:
        self.layout_.addSpacing(6)
        self.layout_.addWidget(self.error)
        self.layout_.addStretch()
        buttons = QHBoxLayout()
        buttons.addStretch()
        back = QPushButton(back_text)
        nxt = QPushButton(next_text)
        nxt.setObjectName("primary")
        for button in (back, nxt):
            button.setFixedWidth(BUTTON_WIDTH)  # Side by side, so the same size.
            button.setAutoDefault(False)
            buttons.addWidget(button)
        self.layout_.addLayout(buttons)
        return back, nxt

    def show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))


def _field_label(text: str) -> QLabel:
    return label(text, "subtitle")


def _password_box() -> QLineEdit:
    box = QLineEdit()
    box.setEchoMode(QLineEdit.EchoMode.Password)
    return box


def _phrase_box() -> QPlainTextEdit:
    box = QPlainTextEdit()
    box.setObjectName("phrase")
    box.setFixedHeight(110)
    box.setTabChangesFocus(True)
    return box


class ExistingWalletDialog(_FramedDialog):
    """Asked before creating or restoring while a wallet file is already in place."""

    def __init__(self, parent: QWidget, existing: Path, new_file: Path) -> None:
        super().__init__(parent, "You already have a wallet")
        self.setFixedWidth(540)
        intro = label("A wallet file is already in use on this PC:", "subtitle")
        self.layout_.addWidget(intro)
        path = label(str(existing), "fullAddress")
        path.setWordWrap(True)
        self.layout_.addWidget(path)
        warning = label(
            f"It won't be deleted or overwritten. The new wallet is saved as {new_file.name} beside it, and the "
            "app switches to it. To go back, choose the old file in Settings, Wallet file.\n\n"
            "Make sure you have the seed phrase of your current wallet before going on.",
            "warnBox",
        )
        warning.setWordWrap(True)
        self.layout_.addWidget(warning)
        _cancel, proceed = self._buttons("Cancel", "Continue")
        proceed.clicked.connect(self.accept)


class SetupWizard(QDialog):
    def __init__(self, parent: QWidget | None, settings: Settings, app_owns_daemon: bool = False) -> None:
        super().__init__(parent)
        self.settings = settings
        self.draft = dataclasses.replace(settings)  # Handed back only when the wizard finishes.
        self.app_owns_daemon = app_owns_daemon  # The main window started the htnwallet now running.
        self.action: str | None = None
        self.busy = False
        self.mode = "create"  # "create", "restore" or "existing"
        self.source = "htnwallet"  # For restore: "htnwallet", "web" or "key"
        self.target: Path | None = None  # Where the new wallet file goes.
        self.created: Path | None = None  # The wallet file this wizard made, if any.
        self.words: list[str] = []
        self.check_positions: list[int] = []
        self.daemons: list[RunningDaemon] = []
        self.done_process: subprocess.Popen | None = None
        self.done_since = 0.0
        self.done_ready = False
        self.done_polling = False
        self.started_process: subprocess.Popen | None = None  # Handed to the main window by Open wallet.
        self.done_node: Node | None = None  # The official RPC node the last page's htnwallet was given.
        self.started_node: Node | None = None  # Handed to the main window with started_process.

        self.setWindowTitle("HTN GUI Wallet")
        self.setWindowIcon(app_icon())
        self.setObjectName("sendDialog")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(1000, 698)

        title_bar = TitleBar("HTN GUI Wallet", app_icon())
        title_bar.minimize_button.clicked.connect(self.showMinimized)
        title_bar.close_button.clicked.connect(self.reject)
        self.pages = QStackedWidget()
        root = QFrame()
        root.setObjectName("root")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(1, 1, 1, 1)
        root_layout.setSpacing(0)
        root_layout.addWidget(title_bar)
        root_layout.addWidget(self.pages, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        self._build_welcome()
        self._build_source()
        self._build_phrase()
        self._build_web()
        self._build_key()
        self._build_password()
        self._build_seed()
        self._build_check()
        self._build_done()
        self._build_existing()

        self.copied_timer = QTimer(self, singleShot=True, interval=int(COPIED_SECONDS * 1000))
        self.copied_timer.timeout.connect(self._copy_reset)
        self.done_timer = QTimer(self, singleShot=True, interval=DONE_POLL_MS)
        self.done_timer.timeout.connect(self._done_poll)

        self._detect()
        self.pages.setCurrentWidget(self.welcome)

    # ----- Welcome -----

    def _build_welcome(self) -> None:
        page = self.welcome = _Page(
            "SETUP WIZARD",
            "Welcome to HTN GUI Wallet",
            "Choose how to set up. This only appears the first time; the wallet file and htnwallet address "
            "can be changed later in Settings.",
        )
        self.create_card = ChoiceCard("Create a new wallet", "htnwallet makes a new 24-word seed phrase for you to write down.")
        self.restore_card = ChoiceCard("Restore a wallet", "From a seed phrase, an HTN web wallet backup file, or a private key.")
        self.existing_card = ChoiceCard(
            "Use an existing wallet",
            "You already use htnwallet on this PC. The app connects to it if it's running, or starts it with "
            "your wallet file.",
        )
        self.welcome_cards = [self.create_card, self.restore_card, self.existing_card]
        _group(self.welcome_cards, lambda: page.show_error(""))
        for card in self.welcome_cards:
            page.add(card, 8)
        page.layout_.addSpacing(4)

        found = QHBoxLayout()
        found.setSpacing(8)
        self.found_dot = StatusDot()
        found.addWidget(self.found_dot)
        self.found_text = label("", "muted")
        self.found_text.setWordWrap(True)
        found.addWidget(self.found_text, 1)
        page.layout_.addLayout(found)

        # Only when htnwallet.exe isn't beside the app or in the Main folder.
        self.tools_row = QWidget()
        tools = QHBoxLayout(self.tools_row)
        tools.setContentsMargins(0, 10, 0, 0)
        tools_text = label("htnwallet.exe wasn't found beside the app. Choose the folder that has it.", "error")
        tools_text.setWordWrap(True)
        tools.addWidget(tools_text, 1)
        browse = QPushButton("Choose folder…")
        browse.setFixedWidth(BUTTON_WIDTH)
        browse.clicked.connect(self._choose_tools_folder)
        tools.addWidget(browse)
        page.add(self.tools_row)

        self.welcome_back, self.welcome_next = page.finish("Exit" if self.parent() is None else "Cancel", "Next")
        self.welcome_back.clicked.connect(self.reject)
        self.welcome_next.clicked.connect(self._welcome_next)
        self.pages.addWidget(page)

    def _detect(self) -> None:
        """What's already on this PC: a running htnwallet, a wallet file, and htnwallet.exe itself."""
        try:
            self.daemons = find_running_daemons()
        except Exception:  # Reading other processes is a convenience; never let it stop the wizard.
            self.daemons = []
        existing = wallet_file(self.draft)
        parts = []
        if self.daemons:
            parts.append("htnwallet running at " + ", ".join(daemon.address for daemon in self.daemons))
        if existing.is_file():
            parts.append("a wallet file " + ("in the usual place" if not self.draft.keys_file else f"at {existing}"))
        if parts:
            self.found_dot.set_color(GREEN)
            self.found_text.setText("Found on this PC: " + ", and ".join(parts) + ".")
            if self.parent() is None:  # First launch: point at what's there. From Settings, nothing is preselected.
                _select(self.welcome_cards, self.existing_card)
        else:
            self.found_dot.set_color("#6F7A92")
            self.found_text.setText("No htnwallet or wallet file found on this PC.")
        self._check_tools()

    def _tool(self) -> Path | None:
        try:
            return find_tool(Tool.HTNWALLET, tool_folders(self.draft))
        except ToolError:
            return None

    def _check_tools(self) -> None:
        found = self._tool() is not None
        self.tools_row.setVisible(not found)
        self.welcome_next.setEnabled(found)

    def _choose_tools_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder containing htnwallet.exe")
        if folder:
            self.draft.tools_folder = folder
            self._check_tools()

    def _outside_daemon_running(self) -> bool:
        """htnwallet answers at the address in Settings, and this app didn't start it."""
        return port_is_open(self.draft.daemon_port) and not self.app_owns_daemon

    def _welcome_next(self) -> None:
        page = self.welcome
        page.show_error("")
        if self.existing_card.is_checked():
            self.mode = "existing"
            self._prepare_existing()
            self.pages.setCurrentWidget(self.existing)
            return
        self.mode = "create" if self.create_card.is_checked() else "restore"
        if self._outside_daemon_running():
            page.show_error(
                f"htnwallet is running at {self.draft.daemon_address}. Stop it first, then press Next again."
            )
            return
        existing = wallet_file(self.draft)
        if existing.exists():
            new_file = free_wallet_file(existing)
            if ExistingWalletDialog(self, existing, new_file).exec() != QDialog.DialogCode.Accepted:
                return
            self.target = new_file
        else:
            self.target = existing
        if self.mode == "create":
            self._open_password("NEW WALLET  ·  STEP 1 OF 3", "Create")
        else:
            self.pages.setCurrentWidget(self.source_page)

    # ----- Restore: where from, and what to enter -----

    def _build_source(self) -> None:
        page = self.source_page = _Page(
            "RESTORE  ·  STEP 1 OF 3",
            "Where is your wallet from?",
            "Each kind of wallet works out its addresses differently, so pick the one your seed phrase came from.",
        )
        self.source_htnwallet = ChoiceCard("HTN GUI Wallet or htnwallet", "A 24-word seed phrase made by this app or by htnwallet create.")
        self.source_web = ChoiceCard("HTN web wallet", "Its seed phrase, or a backup file exported from the web wallet with its password.")
        self.source_key = ChoiceCard("Private key", "Advanced. A single key in hex, such as one printed by genkeypair.")
        self.source_cards = [self.source_htnwallet, self.source_web, self.source_key]
        _group(self.source_cards)
        for card in self.source_cards:
            page.add(card, 10)
        back, nxt = page.finish("Back", "Next")
        back.clicked.connect(lambda: self.pages.setCurrentWidget(self.welcome))
        nxt.clicked.connect(self._source_next)
        self.pages.addWidget(page)

    def _source_next(self) -> None:
        if self.source_web.is_checked():
            self.source = "web"
            self.pages.setCurrentWidget(self.web_page)
        elif self.source_key.is_checked():
            self.source = "key"
            self.pages.setCurrentWidget(self.key_page)
        else:
            self.source = "htnwallet"
            self.pages.setCurrentWidget(self.phrase_page)

    def _build_phrase(self) -> None:
        page = self.phrase_page = _Page(
            "RESTORE  ·  STEP 2 OF 3",
            "Enter your seed phrase",
            "Type or paste your seed phrase, words separated by spaces. It's passed to htnwallet exactly as entered.",
        )
        self.phrase = page.add(_phrase_box())
        back, self.phrase_next = page.finish("Back", "Next")
        self.phrase_next.setEnabled(False)
        self.phrase.textChanged.connect(lambda: self.phrase_next.setEnabled(bool(self.phrase.toPlainText())))
        back.clicked.connect(lambda: self.pages.setCurrentWidget(self.source_page))
        self.phrase_next.clicked.connect(self._input_next)
        self.pages.addWidget(page)

    def _build_web(self) -> None:
        page = self.web_page = _Page(
            "RESTORE  ·  STEP 2 OF 3", "HTN web wallet", "Use its seed phrase, or the backup file the web wallet exported."
        )
        tabs = QFrame()
        tabs.setObjectName("sendTabs")
        tab_row = QHBoxLayout(tabs)
        tab_row.setContentsMargins(4, 4, 4, 4)
        tab_row.setSpacing(4)
        self.web_phrase_tab = QPushButton("Seed phrase")
        self.web_file_tab = QPushButton("Backup file")
        for tab in (self.web_phrase_tab, self.web_file_tab):
            tab.setObjectName("sendTab")
            tab.setCheckable(True)
            tab.setAutoExclusive(True)
            tab_row.addWidget(tab)
        self.web_phrase_tab.setChecked(True)
        page.add(tabs, 16)

        self.web_stack = QStackedWidget()
        phrase_panel = QWidget()
        phrase_layout = QVBoxLayout(phrase_panel)
        phrase_layout.setContentsMargins(0, 0, 0, 0)
        phrase_layout.setSpacing(4)
        phrase_layout.addWidget(_field_label("Seed phrase"))
        self.web_phrase = _phrase_box()
        phrase_layout.addWidget(self.web_phrase)
        phrase_layout.addWidget(label("Passed to htnwallet exactly as entered.", "muted"))
        phrase_layout.addStretch()
        file_panel = QWidget()
        file_layout = QVBoxLayout(file_panel)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(4)
        file_layout.addWidget(_field_label("Backup file"))
        file_row = QHBoxLayout()
        self.web_file = QLineEdit()
        file_row.addWidget(self.web_file)
        browse = QPushButton("Browse…")
        browse.setFixedWidth(BUTTON_WIDTH)
        browse.clicked.connect(self._choose_backup)
        file_row.addWidget(browse)
        file_layout.addLayout(file_row)
        file_layout.addSpacing(10)
        file_layout.addWidget(_field_label("Backup file password"))
        self.web_file_password = _password_box()
        file_layout.addWidget(self.web_file_password)
        file_layout.addWidget(label("The password you chose when exporting from the web wallet.", "muted"))
        file_layout.addStretch()
        self.web_stack.addWidget(phrase_panel)
        self.web_stack.addWidget(file_panel)
        self.web_stack.setFixedHeight(190)
        page.add(self.web_stack, 8)
        note = label(
            "The first 256 receive addresses and 256 change addresses are imported. That covers nearly every web wallet.",
            "note",
        )
        note.setWordWrap(True)
        page.add(note)

        back, self.web_next = page.finish("Back", "Next")
        self.web_phrase_tab.toggled.connect(lambda on: (self.web_stack.setCurrentIndex(0 if on else 1), self._web_ready()))
        for signal in (self.web_phrase.textChanged, self.web_file.textChanged, self.web_file_password.textChanged):
            signal.connect(self._web_ready)
        back.clicked.connect(lambda: self.pages.setCurrentWidget(self.source_page))
        self.web_next.clicked.connect(self._input_next)
        self._web_ready()
        self.pages.addWidget(page)

    def _web_ready(self) -> None:
        if self.web_phrase_tab.isChecked():
            ready = bool(self.web_phrase.toPlainText())
        else:
            ready = bool(self.web_file.text()) and bool(self.web_file_password.text())
        self.web_next.setEnabled(ready)

    def _choose_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Web wallet backup file", "", "All files (*)")
        if path:
            self.web_file.setText(path)

    def _build_key(self) -> None:
        page = self.key_page = _Page(
            "RESTORE  ·  STEP 2 OF 3", "Enter the private key", "A Schnorr private key in hex, such as one printed by genkeypair."
        )
        page.add(_field_label("Private key"))
        self.private_key = page.add(QLineEdit())
        self.private_key.setObjectName("word")
        back, self.key_next = page.finish("Back", "Next")
        self.key_next.setEnabled(False)
        self.private_key.textChanged.connect(lambda text: self.key_next.setEnabled(bool(text)))
        back.clicked.connect(lambda: self.pages.setCurrentWidget(self.source_page))
        self.key_next.clicked.connect(self._input_next)
        self.pages.addWidget(page)

    def _input_page(self) -> _Page:
        return {"htnwallet": self.phrase_page, "web": self.web_page, "key": self.key_page}[self.source]

    def _input_next(self) -> None:
        self._input_page().show_error("")
        self._open_password("RESTORE  ·  STEP 3 OF 3", "Restore")

    # ----- Password -----

    def _build_password(self) -> None:
        page = self.password_page = _Page(
            "",
            "Choose a password",
            "It encrypts the wallet on this PC and is asked for on every send. The app never stores it.",
        )
        page.add(_field_label("Password"))
        self.password = page.add(_password_box(), 12)
        page.add(_field_label("Type it again"))
        self.password_again = page.add(_password_box(), 6)
        self.password_match = page.add(label("✓  The passwords match", "ok"), 14)
        note = label("Forgot it later? Restore again from your seed phrase and choose a new one.", "note")
        note.setWordWrap(True)
        page.add(note)
        self.password_back, self.password_next = page.finish("Back", "Create")
        for box in (self.password, self.password_again):
            box.textChanged.connect(self._password_changed)
        self.password_back.clicked.connect(self._password_back_clicked)
        self.password_next.clicked.connect(self._password_next_clicked)
        self._password_changed()
        self.pages.addWidget(page)

    def _open_password(self, step: str, action_text: str) -> None:
        page = self.password_page
        page.step.setText(step)
        page.step.show()
        page.show_error("")
        self.password_next.setText(action_text)
        self.pages.setCurrentWidget(page)
        self.password.setFocus()

    def _password_changed(self) -> None:
        both = bool(self.password.text()) and bool(self.password_again.text())
        same = both and self.password.text() == self.password_again.text()
        self.password_match.setText("✓  The passwords match" if same else " ")
        self.password_next.setEnabled(both and not self.busy)

    def _password_back_clicked(self) -> None:
        self.pages.setCurrentWidget(self.welcome if self.mode == "create" else self._input_page())

    def _set_busy(self, busy: bool, button: QPushButton | None = None, text: str = "") -> None:
        self.busy = busy
        for widget in self.findChildren(QPushButton):
            widget.setEnabled(not busy)
        if button is not None and text:
            button.setText(text)
        if not busy:
            self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        """After being busy, put back the buttons that depend on what has been filled in."""
        self._password_changed()
        self._check_tools()
        self.phrase_next.setEnabled(bool(self.phrase.toPlainText()))
        self.key_next.setEnabled(bool(self.private_key.text()))
        self._web_ready()
        self.seed_next.setEnabled(self.seed_written.isChecked())

    def _password_next_clicked(self) -> None:
        page = self.password_page
        try:
            password = check_new_password(self.password.text(), self.password_again.text())
        except InvalidInput as error:
            page.show_error(str(error))
            return
        tool = self._tool()
        if tool is None or self.target is None:
            page.show_error("htnwallet.exe wasn't found. Go back and choose its folder.")
            return
        page.show_error("")
        network, target = self.draft.network, self.target
        action_text = self.password_next.text()
        self._set_busy(True, self.password_next, "Creating…" if self.mode == "create" else "Restoring…")

        if self.mode == "create":
            def work():
                create_wallet(tool, network, target, password)
                return read_seed_phrase(tool, network, target, password)
        else:
            work = self._restore_work(tool, network, target, password)

        def finished(result) -> None:
            self.created = target
            self._set_busy(False, self.password_next, action_text)
            self._clear_passwords()
            if self.mode == "create":
                self._show_seed(result)
            else:
                self._open_done()

        def failed(error: Exception) -> None:
            self._set_busy(False, self.password_next, action_text)
            if target.is_file() and self.created != target:
                self.created = target  # Made, but something after it failed.
            message = str(error) if isinstance(error, SetupError) else "Something went wrong while making the wallet"
            if self.mode == "restore" and message in (INVALID_PHRASE, INVALID_KEY, INVALID_BACKUP):
                self._clear_passwords()
                self.pages.setCurrentWidget(self._input_page())
                self._input_page().show_error(message)
            else:
                page.show_error(message)

        run_in_background(work, finished, failed)

    def _restore_work(self, tool: Path, network: str, target: Path, password: str):
        if self.source == "htnwallet":
            phrase = self.phrase.toPlainText()
            return lambda: restore_from_phrase(tool, network, target, password, phrase)
        if self.source == "key":
            key = self.private_key.text()
            return lambda: restore_private_key(tool, network, target, password, key)
        if self.web_phrase_tab.isChecked():
            phrase = self.web_phrase.toPlainText()
            return lambda: restore_web_wallet_phrase(tool, network, target, password, phrase)
        backup, backup_password = Path(self.web_file.text()), self.web_file_password.text()
        return lambda: restore_web_wallet_backup(tool, network, target, password, backup, backup_password)

    def _clear_passwords(self) -> None:
        for box in (self.password, self.password_again, self.web_file_password):
            box.clear()

    def _clear_secrets(self) -> None:
        """Nothing secret is kept once it has been handed to htnwallet, or once the wizard closes."""
        self._clear_passwords()
        for box in (self.phrase, self.web_phrase):
            box.clear()
        self.private_key.clear()
        self.words = []
        for word_label in getattr(self, "seed_labels", []):
            word_label.setText("")

    # ----- New wallet: the seed phrase, and checking it -----

    def _build_seed(self) -> None:
        page = self.seed_page = _Page(
            "NEW WALLET  ·  STEP 2 OF 3",
            "Write down your seed phrase",
            "These 24 words are your wallet. Anyone who has them can take your coins.",
        )
        warning = label(
            "Write them on paper, in order, and keep it somewhere safe. Don't take a screenshot or save them on "
            "this PC. If you lose them and this PC fails, the coins are gone for good.",
            "warnBox",
        )
        warning.setWordWrap(True)
        page.add(warning, 12)
        self.seed_grid = QGridLayout()
        self.seed_grid.setHorizontalSpacing(10)
        self.seed_grid.setVerticalSpacing(8)
        page.layout_.addLayout(self.seed_grid)
        page.layout_.addSpacing(12)
        row = QHBoxLayout()
        self.seed_written = QCheckBox("I've written down all 24 words, in order")
        row.addWidget(self.seed_written)
        row.addStretch()
        self.copy_button = QPushButton("  Copy words")
        self.copy_button.setIcon(copy_icon("#AEB8CC"))
        self.copy_button.setIconSize(QSize(14, 14))
        self.copy_button.setFixedWidth(BUTTON_WIDTH)
        self.copy_button.setAutoDefault(False)
        self.copy_button.clicked.connect(self._copy_words)
        row.addWidget(self.copy_button)
        page.layout_.addLayout(row)
        back, self.seed_next = page.finish("Back", "Next")
        back.hide()  # The wallet is made by now; going back would only make another.
        self.seed_written.toggled.connect(self.seed_next.setEnabled)
        self.seed_next.setEnabled(False)
        self.seed_next.clicked.connect(self._open_check)
        self.pages.addWidget(page)

    def _show_seed(self, words: list[str]) -> None:
        self.words = words
        self.seed_labels = []
        while self.seed_grid.count():
            item = self.seed_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                inner = item.layout()
                while inner.count():
                    child = inner.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
        rows = max(1, (len(words) + 3) // 4)
        for index, word in enumerate(words):
            cell = QHBoxLayout()
            cell.setSpacing(6)
            number = label(str(index + 1), "wordNo")
            number.setFixedWidth(16)
            number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            cell.addWidget(number)
            word_label = label(word, "seedWord")
            self.seed_labels.append(word_label)
            cell.addWidget(word_label, 1)
            self.seed_grid.addLayout(cell, index % rows, index // rows)
        count = len(words)
        self.seed_page.subtitle.setText(f"These {count} words are your wallet. Anyone who has them can take your coins.")
        self.seed_written.setText(f"I've written down all {count} words, in order")
        self.seed_written.setChecked(False)
        self.check_positions = sorted(random.sample(range(count), min(CHECK_WORDS, count)))
        self.pages.setCurrentWidget(self.seed_page)

    def _copy_words(self) -> None:
        QGuiApplication.clipboard().setText(" ".join(self.words))
        self.copy_button.setIcon(copy_icon(GREEN, copied=True))
        self.copy_button.setText("  Copied")
        self.copied_timer.start()

    def _copy_reset(self) -> None:
        self.copy_button.setIcon(copy_icon("#AEB8CC"))
        self.copy_button.setText("  Copy words")

    def _build_check(self) -> None:
        page = self.check_page = _Page(
            "NEW WALLET  ·  STEP 3 OF 3",
            "Check your seed phrase",
            "Type these three words from what you wrote down, to make sure it's right.",
        )
        self.check_rows = []
        for _ in range(CHECK_WORDS):
            row = QHBoxLayout()
            row.setSpacing(12)
            name = label("", "subtitle")
            name.setFixedWidth(70)
            row.addWidget(name)
            box = QLineEdit()
            box.setObjectName("word")
            box.setFixedWidth(220)
            row.addWidget(box)
            row.addStretch()
            page.layout_.addLayout(row)
            page.layout_.addSpacing(8)
            self.check_rows.append((name, box))
        back, finish = page.finish("Back", "Finish")
        back.clicked.connect(lambda: self.pages.setCurrentWidget(self.seed_page))
        finish.clicked.connect(self._check_finish)
        self.pages.addWidget(page)

    def _open_check(self) -> None:
        for (name, box), position in zip(self.check_rows, self.check_positions):
            name.setText(f"Word {position + 1}")
            box.clear()
        self.check_page.show_error("")
        self.pages.setCurrentWidget(self.check_page)
        self.check_rows[0][1].setFocus()

    def _check_finish(self) -> None:
        typed = [box.text() for _name, box in self.check_rows]
        expected = [self.words[position] for position in self.check_positions]
        if typed != expected:  # Compared exactly, and no hint which word is wrong.
            self.check_page.show_error(MISMATCH)
            return
        self.draft.keys_file = keys_file_setting(self.target, self.draft.network)
        self._finish(SAVED)  # htnwallet isn't started: the user checks Settings (such as the node) first.

    # ----- Restored: start htnwallet and show how it's going -----

    def _build_done(self) -> None:
        page = self.done_page = _Page("", "Wallet restored", "htnwallet is starting and reading your addresses from the network.")
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(8)
        status = QHBoxLayout()
        status.setSpacing(8)
        self.done_dot = StatusDot()
        status.addWidget(self.done_dot)
        self.done_status = label("", "dialogValue")
        self.done_status.setWordWrap(True)
        status.addWidget(self.done_status, 1)
        card_layout.addLayout(status)
        self.done_address = label("", "address")
        card_layout.addWidget(self.done_address)
        page.add(card, 14)
        note = label("Not the balance you expected? Choose Start over to try again; nothing is lost.", "note")
        note.setWordWrap(True)
        page.add(note)
        self.done_over, self.done_open = page.finish("Start over", "Open wallet")
        self.done_over.clicked.connect(self._start_over)
        self.done_open.clicked.connect(self._done_open)
        self.pages.addWidget(page)

    def _open_done(self) -> None:
        self.draft.keys_file = keys_file_setting(self.target, self.draft.network)
        self.done_ready = False
        self.done_address.setText("")
        self._done_status("Starting htnwallet…", AMBER)
        self.pages.setCurrentWidget(self.done_page)
        port = self.draft.daemon_port

        def stop_old():
            if self.app_owns_daemon and port_is_open(port):
                close_daemon(port)  # Only ever the htnwallet this app started.

        run_in_background(stop_old, lambda _result: self._done_start(), lambda error: self._done_start())

    def _done_start(self) -> None:
        tool = self._tool()
        if tool is None:
            self._done_status("htnwallet.exe wasn't found.", RED)
            return
        if port_is_open(self.draft.daemon_port):
            self._done_status(
                f"Something is already using {self.draft.daemon_address}, so htnwallet couldn't start. "
                "Open the wallet and press Start once it's free.",
                RED,
            )
            return
        if not is_official(self.draft.node_address):
            self._done_start_on(tool, None)
            return
        try:
            htnctl = find_tool(Tool.HTNCTL, tool_folders(self.draft))
        except ToolError as error:
            self._done_status(str(error), RED)
            return
        self._done_status("Finding the fastest node…", AMBER)

        def failed(error: Exception) -> None:
            if self.pages.currentWidget() is self.done_page and self.done_process is None:
                reason = str(error) if isinstance(error, NodeError) else "Couldn't reach the official RPC"
                self._done_status(f"{reason}. Open the wallet and press Start to try again.", RED)

        run_in_background(lambda: find_nodes(htnctl), lambda nodes: self._done_start_on(tool, nodes[0]), failed)

    def _done_start_on(self, tool: Path, node: Node | None) -> None:
        if not self.isVisible() or self.pages.currentWidget() is not self.done_page or self.done_process is not None:
            return  # Left the page, or the wizard, while the nodes were being timed.
        self._done_status("Starting htnwallet…", AMBER)
        try:
            self.done_process = start_daemon(
                tool,
                wallet_context(self.draft),
                node.address if node else self.draft.node_address,
                settings_path().parent / "daemon-start.log",
            )
        except ToolError as error:
            self._done_status(str(error), RED)
            return
        self.done_node = node
        self.app_owns_daemon = True
        self.done_since = time.monotonic()
        self.done_timer.start()

    def _done_status(self, text: str, colour: str) -> None:
        self.done_status.setText(text)
        self.done_dot.set_color(colour)

    def _done_poll(self) -> None:
        process = self.done_process
        if process is None or self.pages.currentWidget() is not self.done_page or self.done_polling:
            return
        if process.poll() is not None:
            reason = last_log_line(settings_path().parent / "daemon-start.log") or "htnwallet exited straight away"
            self._done_status(f"Could not start: {reason}. The node can be changed in Settings.", RED)
            self.done_process = None
            return
        if time.monotonic() - self.done_since > DONE_START_TIMEOUT_SECONDS and not self.done_ready:
            self._done_status("htnwallet is taking a while. You can open the wallet and it will carry on.", AMBER)
        tool = self._tool()
        context = wallet_context(self.draft)
        port = self.draft.daemon_port

        def work():
            if running_daemon_version(tool, context, port) is None:
                return None
            try:
                snapshot = read_wallet(tool, context, port, include_coins=False)
            except DaemonNotSynced:
                return "syncing"
            addresses = read_receive_addresses(tool, context, port)
            return snapshot, addresses

        def finished(result) -> None:
            self.done_polling = False
            if result is None:
                pass  # Still starting.
            elif result == "syncing":
                self._done_status("Syncing…  this can take a few minutes", AMBER)
            else:
                snapshot, addresses = result
                self.done_ready = True
                self._done_status(f"Ready  ·  balance {format_htn(snapshot.balance.available)} HTN", GREEN)
                first = addresses[0] if addresses else (
                    snapshot.balance.addresses[0].address if snapshot.balance.addresses else ""
                )
                self.done_address.setText(shorten_address(first))
                self.done_address.setToolTip(first)
            if self.pages.currentWidget() is self.done_page:
                self.done_timer.start()

        def failed(_error: Exception) -> None:
            self.done_polling = False
            if self.pages.currentWidget() is self.done_page:
                self.done_timer.start()

        self.done_polling = True
        run_in_background(work, finished, failed)

    def _stop_done_process(self) -> None:
        """End the htnwallet this wizard started on the last page, if it's still running."""
        self.done_timer.stop()
        process, self.done_process = self.done_process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    def _start_over(self) -> None:
        self._stop_done_process()
        # The restored wallet came from the seed phrase the user holds, so removing the file this wizard
        # just made loses nothing. No other file is ever deleted.
        if self.created is not None and self.created.is_file():
            try:
                self.created.unlink()
            except OSError:
                pass
        self.created = None
        self.draft.keys_file = self.settings.keys_file
        self.pages.setCurrentWidget(self.source_page)

    def _done_open(self) -> None:
        self.done_timer.stop()
        # It keeps running; the main window takes it over, and can still cancel it while it starts.
        self.started_process, self.done_process = self.done_process, None
        self.started_node = self.done_node if self.started_process is not None else None
        self._finish(STARTED)

    # ----- Use an existing wallet -----

    def _build_existing(self) -> None:
        page = self.existing = _Page(
            "EXISTING WALLET",
            "Use your existing wallet",
            "The app connects to htnwallet if it's running, or starts it with your wallet file. "
            "It never stops or changes an htnwallet it didn't start.",
        )
        self.connect_card = ChoiceCard("Connect to the running htnwallet", "")
        self.file_card = ChoiceCard(
            "Start htnwallet with a wallet file", "For when htnwallet isn't running, or to use a different wallet."
        )
        self.existing_cards = [self.connect_card, self.file_card]
        _group(self.existing_cards, self._existing_changed)
        page.add(self.connect_card, 8)
        self.connect_address = QComboBox()
        self.connect_address.setEditable(True)
        page.add(self.connect_address, 8)
        page.add(self.file_card, 12)

        self.file_panel = QWidget()
        panel = QVBoxLayout(self.file_panel)
        panel.setContentsMargins(0, 0, 0, 0)
        panel.setSpacing(4)
        panel.addWidget(_field_label("Wallet file"))
        row = QHBoxLayout()
        self.existing_file = QLineEdit()
        self.existing_file.textChanged.connect(self._existing_file_changed)
        row.addWidget(self.existing_file)
        browse = QPushButton("Browse…")
        browse.setFixedWidth(BUTTON_WIDTH)
        browse.clicked.connect(self._choose_existing_file)
        row.addWidget(browse)
        panel.addLayout(row)
        self.existing_file_note = label("", "ok")
        panel.addWidget(self.existing_file_note)
        panel.addSpacing(8)
        panel.addWidget(_field_label("Node (address:port)"))
        node_row = QHBoxLayout()
        self.existing_node = QLineEdit()
        node_row.addWidget(self.existing_node)
        official = QPushButton("Use Official RPC")
        official.setFixedWidth(BUTTON_WIDTH)
        official.clicked.connect(lambda: self.existing_node.setText(OFFICIAL_RPC))
        node_row.addWidget(official)
        panel.addLayout(node_row)
        self.existing_node_hint = official_rpc_hint()
        self.existing_node.textChanged.connect(lambda text: self.existing_node_hint.setVisible(is_official(text)))
        panel.addWidget(self.existing_node_hint)
        page.add(self.file_panel)

        back, self.existing_next = page.finish("Back", "Connect")
        back.clicked.connect(lambda: self.pages.setCurrentWidget(self.welcome))
        self.existing_next.clicked.connect(self._existing_next_clicked)
        self.pages.addWidget(page)

    def _prepare_existing(self) -> None:
        self.existing.show_error("")
        self.connect_address.clear()
        for daemon in self.daemons:
            self.connect_address.addItem(daemon.address, daemon)
        if len(self.daemons) == 1:
            only = self.daemons[0]
            self.connect_card.description.setText(f"{only.address}  ·  {only.started_text()}")
        elif self.daemons:
            self.connect_card.description.setText(f"{len(self.daemons)} found on this PC. Pick one below.")
        else:
            self.connect_card.description.setText("None found on this PC. Choose this to type its address.")
            self.connect_address.setEditText(self.draft.daemon_address)
        path = wallet_file(self.draft)
        self.existing_file.setText(str(path))
        self.existing_node.setText(self.draft.node_address)
        _select(self.existing_cards, self.connect_card if self.daemons else self.file_card)
        self._existing_changed()

    def _existing_changed(self) -> None:
        connect = self.connect_card.is_checked()
        self.connect_address.setVisible(connect and len(self.daemons) != 1)
        self.file_panel.setVisible(not connect)
        self.existing_next.setText("Connect" if connect else "Start")
        self.existing.show_error("")

    def _existing_file_changed(self, text: str) -> None:
        path = Path(text) if text else None
        if path and path.is_file():
            usual = path == wallet_file(dataclasses.replace(self.draft, keys_file=""))
            self.existing_file_note.setText("✓  Found in htnwallet's usual place" if usual else "✓  Found")
            self.existing_file_note.setObjectName("ok")
        else:
            self.existing_file_note.setText("No wallet file here.")
            self.existing_file_note.setObjectName("error")
        self.existing_file_note.style().unpolish(self.existing_file_note)
        self.existing_file_note.style().polish(self.existing_file_note)

    def _choose_existing_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Wallet file", "", "Wallet files (*.json);;All files (*)")
        if path:
            self.existing_file.setText(path)

    def _existing_next_clicked(self) -> None:
        page = self.existing
        if self.connect_card.is_checked():
            daemon = None
            if len(self.daemons) == 1:
                daemon = self.daemons[0]
                address = daemon.address
            else:
                address = self.connect_address.currentText()
                daemon = next((found for found in self.daemons if found.address == address), None)
            try:
                address = validate_daemon_address(address)
            except InvalidInput as error:
                page.show_error(str(error))
                return
            if not port_is_open(int(address.rsplit(":", 1)[1])):
                page.show_error(f"No htnwallet answers at {address}.")
                return
            self.draft.daemon_address = address
            if daemon is not None:
                # Sends need the same wallet file the daemon uses, read from its command line.
                self.draft.keys_file = daemon.keys_file
            self._finish(CONNECT)
            return

        path = Path(self.existing_file.text())
        if not path.is_file():
            page.show_error("Choose a wallet file that exists.")
            return
        try:
            node = validate_node_address(self.existing_node.text())
        except InvalidInput as error:
            page.show_error(str(error))
            return
        if self._outside_daemon_running():
            page.show_error(
                f"htnwallet is already running at {self.draft.daemon_address}. Choose Connect, or stop it first."
            )
            return
        self.draft.keys_file = keys_file_setting(path, self.draft.network)
        self.draft.node_address = node
        self._finish(START)

    # ----- Finishing -----

    def _finish(self, action: str) -> None:
        self.action = action
        for setting in dataclasses.fields(Settings):
            setattr(self.settings, setting.name, getattr(self.draft, setting.name))
        self._clear_secrets()
        self.busy = False
        super().accept()

    def reject(self) -> None:
        if self.busy:
            return
        self._stop_done_process()  # Leaving without finishing: undo the start, as Start over would.
        self._clear_secrets()
        super().reject()

    def closeEvent(self, event) -> None:
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
