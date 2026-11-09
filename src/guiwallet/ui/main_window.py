"""The main window: the sidebar, Start and Stop for htnwallet, and the system tray.

There is no connection to open. The app just runs htnwallet commands, so it regularly checks
whether a daemon answers, and shows whatever it finds. That also picks up a daemon that
starts or stops at any time.
"""

import dataclasses
import math
import subprocess
import time
from decimal import Decimal
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsBlurEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from guiwallet import __version__
from guiwallet.core.commands import wallet_context
from guiwallet.core.compound import (
    CompoundError,
    Compounders,
    RunningCompound,
    find_compounds,
    stop_outside_compound,
)
from guiwallet.core.history import (
    Cache,
    KEEP_DAYS,
    MAX_BUILD_PAGES,
    HistoryError,
    api_base,
    build_step,
    cache_path,
    days_read,
    days_shown,
    forget_old,
    history_folder,
    load_cache,
    load_hidden_ids,
    rows_for,
    save_cache,
    save_hidden_ids,
    top_up,
)
from guiwallet.core.daemon import (
    DaemonError,
    create_address,
    DaemonNotSynced,
    DaemonUnavailable,
    WalletSnapshot,
    close_daemon,
    last_log_line,
    port_is_open,
    read_receive_addresses,
    read_wallet,
    running_daemon_version,
    start_daemon,
)
from guiwallet.core.send import (
    SEND_DELAY_SECONDS,
    SendOutcome,
    SendRequest,
    SendResult,
    command_preview,
    forget_send,
    interrupted_send_message,
    record_send_started,
    run_send,
    send_journal_path,
)
from guiwallet.core.labels import load_labels, save_labels, set_label
from guiwallet.core.settings import Settings, save_settings, settings_path
from guiwallet.core.setup import tool_folders
from guiwallet.core.tools import Tool, ToolError, find_tool
from guiwallet.core.wallet import WalletOutputError, format_htn, format_htn_exact
from guiwallet.ui.address_dialog import AddAddressDialog
from guiwallet.ui.background import run_in_background
from guiwallet.ui.compound_dialog import StartCompoundDialog, StopCompoundDialog
from guiwallet.ui.history_page import HistoryPage, RefreshHistoryDialog
from guiwallet.ui.pages import ReceivePage, SendPage, SettingsPage, WalletPage
from guiwallet.ui.send_dialog import SendDialog
from guiwallet.ui.setup_wizard import CONNECT, SAVED, START, STARTED, SetupWizard
from guiwallet.ui.theme import AMBER, GREEN, RED, app_icon
from guiwallet.ui.widgets import (
    HIDDEN_AMOUNT,
    EyeButton,
    ResizeGrip,
    StatusDot,
    TitleBar,
    label,
    same_width,
    shorten_address,
)

APP_NAME = "HTN GUI Wallet"
EXPLORERS = {
    "explorer-nz.hoosat.network": "https://explorer-nz.hoosat.network/",
    "explorer-eu.hoosat.network": "https://explorer-eu.hoosat.network/",
}
SIDEBAR_WIDTH = 230
SIDEBAR_MARGIN = 16
BALANCE_CARD_MARGIN = 14
BALANCE_UNIT_GAP = 6
BALANCE_HEIGHT = 34
BALANCE_FONT_SIZES = (25, 22, 20, 18, 16, 14)  # Largest first; the first that fits is used.
GRIP_EDGE = 5  # How close to the window's edge, in pixels, dragging resizes it.
GRIP_CORNER = 14
REFRESH_SECONDS = 5  # The balance is read this often.
DETAILS_EVERY_N_REFRESHES = 4  # The daemon's version and the addresses rarely change: every 20 seconds.
COINS_EVERY_N_REFRESHES = 3  # The coin list can be long, so it's fetched less often: every 15 seconds.
DAEMON_START_TIMEOUT_SECONDS = 120
BANNER_SECONDS = 8
NOT_RUNNING_MESSAGE = "htnwallet isn't running. Press Start to run it."
SENDING_MESSAGE = "A send is in progress. Please wait a few seconds for it to finish."
# While Transaction History is on screen, new transactions are read on opening it and then this often.
HISTORY_REFRESH_SECONDS = 60
HISTORY_CHECK_SECONDS = 5  # How often the app looks whether an update is due. Looking costs the explorer nothing.
HISTORY_PAGE_GAP_MS = 1000  # Between pages of a first build: the explorer allows 2 requests a second.
HISTORY_BACKOFF_SECONDS = (60, 900)  # After a failure: 1 minute, doubling each time, at most 15 minutes.
BLUR_RADIUS = 8  # While the send window is open, the main window behind it can't be used.


class State(Enum):
    CHECKING = auto()  # Just opened, and the first check hasn't finished yet.
    NOT_RUNNING = auto()
    STARTING = auto()
    RUNNING = auto()


class CloseDialog(QDialog):
    """Asks whether closing the window should exit the app or minimize it to the tray."""

    def __init__(self, parent: QWidget, daemon_running: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.choice: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Exit the app, or keep it in the system tray?"))
        if daemon_running:
            layout.addWidget(label("htnwallet will keep running in the background either way.", "muted"))
        self.remember = QCheckBox("Don't ask again (you can change this in Settings)")
        layout.addWidget(self.remember)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        exit_button = QPushButton("Exit")
        exit_button.clicked.connect(lambda: self._choose("exit"))
        buttons.addWidget(exit_button)
        tray = QPushButton("Minimize to tray")
        tray.setObjectName("primary")
        tray.setDefault(True)
        tray.clicked.connect(lambda: self._choose("tray"))
        buttons.addWidget(tray)
        same_width(cancel, exit_button, tray)
        layout.addLayout(buttons)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.state = State.NOT_RUNNING
        self.started_by_app = False
        self.starting_process: subprocess.Popen | None = None
        self.starting_since = 0.0
        self.checking = False
        self.check_count = 0
        self.really_exit = False
        self.sending = False
        self.waiting_send = None  # (dialog, tool, request, arguments) during the wait before a send.
        self.wait_deadline = 0.0
        self.reported_interrupted_send = False
        self.compounders = Compounders()  # The auto-compounds this app started. They end with it.
        self.scanning_compounds = False
        self.daemon_version: str | None = None  # Shown beside "htnwallet" in the status corner.
        self.history_cache: Cache | None = None
        self.history_busy = False
        self.history_generation = 0  # Goes up when another address is chosen; older results are ignored.
        self.history_poll_now = False  # Set on opening the page or choosing an address: read new ones at once.
        self.history_wait_until = 0.0  # After a failure, nothing is asked of the explorer before this.
        self.history_failures = 0
        self.history_last_request = 0.0  # Requests are kept at least HISTORY_PAGE_GAP_MS apart, whatever asks.
        self.hidden_ids = load_hidden_ids(history_folder(settings_path()))  # Compounds this app started.
        self.receive_addresses: list[str] = []
        # Goes up whenever the user presses Start or Stop. A check that began before that is
        # out of date when it finishes, so its result is ignored.
        self.generation = 0

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        # The app draws its own title bar and rounded corners. Leaving out the maximize hint is
        # what stops Windows maximizing it, including by dragging it to the top of the screen.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(1000, 698)

        self.labels = load_labels()  # The user's names for addresses; one dict shared by every page.
        self.wallet_page = WalletPage()
        self.wallet_page.labels = self.labels
        self.wallet_page.compound_clicked.connect(self._compound_clicked)
        self.wallet_page.add_address_clicked.connect(self._add_address_clicked)
        self.wallet_page.label_changed.connect(self._label_changed)
        self.receive_page = ReceivePage(settings.network)
        self.settings_page = SettingsPage(settings)
        self.settings_page.saved.connect(self._settings_saved)
        self.settings_page.wizard_requested.connect(self._open_wizard)
        self.send_page = SendPage(settings.network)
        self.send_page.labels = self.labels
        self.send_page.review_requested.connect(self._review_send)
        self.history_page = HistoryPage()
        self.history_page.labels = self.labels
        self.history_page.address_changed.connect(self._history_address_changed)
        self.history_page.refresh_clicked.connect(self._history_refresh_clicked)
        screens = {
            "Wallet": self.wallet_page,
            "Send": self.send_page,
            "Receive": self.receive_page,
            "Transaction History": self.history_page,
            "Settings": self.settings_page,
        }

        self.stack = QStackedWidget()
        for screen in screens.values():
            self.stack.addWidget(screen)

        # Floats over the bottom of the page, so showing it doesn't move anything.
        self.banner = label("", "banner")
        self.banner.setParent(self.stack)
        self.banner.setWordWrap(True)
        self.banner.hide()
        self.banner_timer = QTimer(self, singleShot=True, interval=BANNER_SECONDS * 1000)
        self.banner_timer.timeout.connect(self.banner.hide)

        self.title_bar = TitleBar(APP_NAME, app_icon(), version=__version__)
        self.title_bar.minimize_button.clicked.connect(self.showMinimized)
        self.title_bar.close_button.clicked.connect(self.close)

        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._build_sidebar(screens))
        body.addWidget(self.stack, stretch=1)

        root = QFrame()
        root.setObjectName("root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(1, 1, 1, 1)  # Keeps everything inside the window's outline.
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        layout.addLayout(body, stretch=1)
        self.setCentralWidget(root)
        self.grips = self._build_grips()

        self.tray = self._build_tray()
        self.current_total: Decimal | None = None
        self.wallet_page.set_balances_hidden(self.settings.hide_balances)
        self.send_page.set_balances_hidden(self.settings.hide_balances)
        self.history_page.set_balances_hidden(self.settings.hide_balances)

        self.check_timer = QTimer(self, interval=REFRESH_SECONDS * 1000)
        self.check_timer.timeout.connect(self._check)
        self.start_timer = QTimer(self, singleShot=True, interval=1000)
        self.start_timer.timeout.connect(self._check_starting_daemon)
        self.wait_timer = QTimer(self, interval=200)
        self.wait_timer.timeout.connect(self._wait_tick)
        # Runs even while the window is hidden, so an auto-compound that stops by itself is noticed.
        self.compound_timer = QTimer(self, interval=REFRESH_SECONDS * 1000)
        self.compound_timer.timeout.connect(self._scan_compounds)
        self.compound_timer.start()
        self.history_timer = QTimer(self, interval=HISTORY_CHECK_SECONDS * 1000)
        self.history_timer.timeout.connect(self._history_refresh_if_shown)
        self.history_timer.start()

        self._show_not_running("htnwallet not running")
        self.state = State.CHECKING
        self._update_status("Checking for htnwallet…", AMBER, self.settings.daemon_address, "Start", False)
        self.check_timer.start()

    def showEvent(self, event) -> None:
        # Checks are skipped while the window is hidden, so the first one has to happen here,
        # once it's visible, rather than while it's being built.
        super().showEvent(event)
        self._refresh_everything()
        if not self.reported_interrupted_send:
            self.reported_interrupted_send = True
            QTimer.singleShot(0, self._report_interrupted_send)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        was_minimized = event.type() == QEvent.Type.WindowStateChange and (
            event.oldState() & Qt.WindowState.WindowMinimized
        )
        if was_minimized and not self.isMinimized():
            self._refresh_everything()  # Checks are paused while minimized, so catch up at once.

    def _refresh_everything(self) -> None:
        self.check_count = 0
        self._check()
        self._scan_compounds()

    # ----- Building the window -----

    def _build_sidebar(self, screens: dict[str, QWidget]) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(SIDEBAR_MARGIN, 24, SIDEBAR_MARGIN, 16)
        layout.setSpacing(4)

        balance_card = QFrame()
        balance_card.setObjectName("card")
        card_layout = QVBoxLayout(balance_card)
        card_layout.setContentsMargins(BALANCE_CARD_MARGIN, 12, BALANCE_CARD_MARGIN, 12)
        card_layout.setSpacing(4)
        heading_row = QHBoxLayout()
        heading_row.addWidget(label("Current Balance", "sectionLabel"))
        heading_row.addStretch()
        self.eye_button = EyeButton()
        self.eye_button.setChecked(self.settings.hide_balances)
        self.eye_button.toggled.connect(self._set_balances_hidden)
        heading_row.addWidget(self.eye_button)
        card_layout.addLayout(heading_row)
        balance_row = QHBoxLayout()
        balance_row.setSpacing(BALANCE_UNIT_GAP)
        self.balance = label("—", "balance")
        self.balance.setFixedHeight(BALANCE_HEIGHT)  # Smaller text mustn't move the menu below.
        balance_row.addWidget(self.balance)
        self.balance_unit = label("HTN", "unit")
        balance_row.addWidget(self.balance_unit)
        balance_row.addStretch()
        card_layout.addLayout(balance_row)
        layout.addWidget(balance_card)
        layout.addSpacing(20)

        self.navigation = QButtonGroup(self)
        for index, name in enumerate(screens):
            button = QPushButton(name)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setChecked(index == 0)
            self.navigation.addButton(button, index)
            layout.addWidget(button)
        self.navigation.idClicked.connect(self.stack.setCurrentIndex)
        self.navigation.idClicked.connect(lambda _index: self._history_opened())

        layout.addStretch()
        status_row = QHBoxLayout()
        self.status_dot = StatusDot()
        status_row.addWidget(self.status_dot)
        self.status_text = label("", "muted")
        self.status_text.setWordWrap(True)
        status_row.addWidget(self.status_text, stretch=1)
        layout.addLayout(status_row)
        self.status_detail = label("", "muted")
        self.status_detail.setWordWrap(True)
        layout.addWidget(self.status_detail)
        layout.addSpacing(8)
        self.start_stop_button = QPushButton("Start")
        self.start_stop_button.clicked.connect(self._start_stop_clicked)
        layout.addWidget(self.start_stop_button)
        return sidebar

    def _build_grips(self) -> dict[Qt.Edge, ResizeGrip]:
        left, right, top, bottom = (
            Qt.Edge.LeftEdge,
            Qt.Edge.RightEdge,
            Qt.Edge.TopEdge,
            Qt.Edge.BottomEdge,
        )
        sideways = Qt.CursorShape.SizeHorCursor
        upright = Qt.CursorShape.SizeVerCursor
        falling = Qt.CursorShape.SizeFDiagCursor
        rising = Qt.CursorShape.SizeBDiagCursor
        return {
            edges: ResizeGrip(self, edges, cursor)
            for edges, cursor in (
                (left, sideways),
                (right, sideways),
                (top, upright),
                (bottom, upright),
                (top | left, falling),
                (bottom | right, falling),
                (top | right, rising),
                (bottom | left, rising),
            )
        }

    def _place_grips(self) -> None:
        width, height = self.width(), self.height()
        edge, corner = GRIP_EDGE, GRIP_CORNER
        places = {
            Qt.Edge.LeftEdge: (0, corner, edge, height - 2 * corner),
            Qt.Edge.RightEdge: (width - edge, corner, edge, height - 2 * corner),
            Qt.Edge.TopEdge: (corner, 0, width - 2 * corner, edge),
            Qt.Edge.BottomEdge: (corner, height - edge, width - 2 * corner, edge),
            Qt.Edge.TopEdge | Qt.Edge.LeftEdge: (0, 0, corner, corner),
            Qt.Edge.TopEdge | Qt.Edge.RightEdge: (width - corner, 0, corner, corner),
            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: (0, height - corner, corner, corner),
            Qt.Edge.BottomEdge | Qt.Edge.RightEdge: (width - corner, height - corner, corner, corner),
        }
        for edges, grip in self.grips.items():
            grip.setGeometry(*places[edges])
            grip.raise_()

    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(app_icon(), self)
        tray.setToolTip(APP_NAME)

        menu = QMenu()
        open_action = QAction("Open HTN Wallet", menu)
        open_action.triggered.connect(self._show_from_tray)
        menu.addAction(open_action)
        explorers = menu.addMenu("HTN Explorer")
        for name, url in EXPLORERS.items():
            action = QAction(name, explorers)
            action.triggered.connect(lambda _checked=False, target=url: QDesktopServices.openUrl(QUrl(target)))
            explorers.addAction(action)
        exit_action = QAction("Exit", menu)
        exit_action.triggered.connect(self._exit_app)
        menu.addAction(exit_action)

        self.tray_menu = menu  # Qt doesn't keep the menu alive by itself.
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        tray.show()
        return tray

    # ----- Checking on htnwallet and reading the wallet -----

    def _check(self) -> None:
        """Ask whether a daemon answers and, if it does, read the wallet."""
        if self.checking or self.sending or self.state is State.STARTING:
            return  # A send has the daemon to itself: its time limit is short.
        if not self.isVisible() or self.isMinimized():
            return
        try:
            tool = find_tool(Tool.HTNWALLET, tool_folders(self.settings))
            find_tool(Tool.HTNCTL, tool_folders(self.settings))
        except ToolError as error:
            self._show_not_running(f"{error}. Set the Main folder in Settings.", tools_found=False)
            return

        self.checking = True
        generation = self.generation
        context = wallet_context(self.settings)
        port = self.settings.daemon_port
        previous_coins = self.wallet_page.snapshot.coins if self.wallet_page.snapshot else None
        # Anything not known yet is fetched straight away, whatever the count says.
        running = self.state is State.RUNNING
        include_details = not running or self.check_count % DETAILS_EVERY_N_REFRESHES == 0
        include_coins = previous_coins is None or self.check_count % COINS_EVERY_N_REFRESHES == 0
        self.check_count += 1

        def work():
            version = None
            if include_details:
                version = running_daemon_version(tool, context, port)
                if version is None:
                    return None
            elif not port_is_open(port):
                return None
            snapshot = read_wallet(tool, context, port, include_coins)
            if snapshot.coins is None and previous_coins is not None:
                snapshot = WalletSnapshot(snapshot.balance, previous_coins)
            addresses = read_receive_addresses(tool, context, port) if include_details else None
            return version, snapshot, addresses

        run_in_background(
            work,
            lambda result: self._check_finished(generation, result),
            lambda error: self._check_failed(generation, error),
        )

    def _is_current(self, generation: int) -> bool:
        self.checking = False
        return generation == self.generation and self.state is not State.STARTING

    def _check_finished(self, generation: int, result) -> None:
        if not self._is_current(generation):
            return
        if result is None:
            if self.state is State.RUNNING:
                self._show_not_running("htnwallet stopped")
            elif self.state is State.CHECKING:
                self._show_not_running("htnwallet not running")
            return  # Already not running: keep showing the reason, such as a failed start.
        version, snapshot, addresses = result
        self._show_running(version)
        self.status_text.setText(self._running_text())
        self.status_dot.set_color(GREEN)
        self.current_total = snapshot.balance.available + snapshot.balance.pending
        self._show_current_total()
        if addresses is not None:
            self.receive_page.show_addresses(addresses)
            self.send_page.set_receive_addresses(addresses)
            self.receive_addresses = addresses
        self.wallet_page.extra_addresses = self.receive_addresses  # So a new, empty address is listed too.
        self.wallet_page.show_snapshot(snapshot)
        self.send_page.show_balance(snapshot.balance)
        # History lists every address: those with a balance, then any others the wallet has handed out.
        listed = [entry.address for entry in snapshot.balance.addresses]
        listed += [address for address in self.receive_addresses if address not in listed]
        self.history_page.set_addresses(listed, snapshot.balance)

    def _check_failed(self, generation: int, error: Exception) -> None:
        if not self._is_current(generation):
            return
        if isinstance(error, DaemonNotSynced):
            self._show_running(None)
            self.status_text.setText(f"{self._running_text()} · syncing…")
            self.status_dot.set_color(AMBER)
            self._clear_figures("htnwallet is still syncing with the node.")
        elif isinstance(error, DaemonUnavailable):
            self._show_not_running("htnwallet stopped")
        elif isinstance(error, (DaemonError, WalletOutputError, ToolError)):
            self._show_running(None)
            self._clear_figures(str(error))
        else:
            self._show_running(None)
            self._clear_figures("Could not read the wallet.")

    def _running_text(self) -> str:
        """"htnwallet 2.18.3" once its version is known: it's read every fourth refresh."""
        return f"htnwallet {self.daemon_version}" if self.daemon_version else "htnwallet"

    def _show_running(self, version: str | None) -> None:
        if version:
            self.daemon_version = version
        if self.state is not State.RUNNING:
            self.state = State.RUNNING
            self.check_count = 1
            if not self.started_by_app:
                detail = f" (version {version})" if version else ""
                self._show_banner(f"htnwallet is already running{detail}, so the app is using it.")
        self._update_status(self._running_text(), GREEN, self.settings.daemon_address, "Stop", True)
        self.settings_page.set_htnwallet_running(True)

    def _show_not_running(self, reason: str, tools_found: bool = True) -> None:
        self.state = State.NOT_RUNNING
        self.started_by_app = False
        self.starting_process = None
        self.start_timer.stop()
        self.daemon_version = None  # Whatever starts next may be another version.
        self._update_status(reason, RED, self.settings.daemon_address, "Start", tools_found)
        self.settings_page.set_htnwallet_running(False)
        self._clear_figures(NOT_RUNNING_MESSAGE)

    def _update_status(self, text: str, color: str, detail: str, button: str, enabled: bool) -> None:
        self.status_text.setText(text)
        self.status_dot.set_color(color)
        self.status_detail.setText(detail)
        self.start_stop_button.setText(button)
        self.start_stop_button.setEnabled(enabled and not self.sending)

    def _set_balances_hidden(self, hidden: bool) -> None:
        self.settings.hide_balances = hidden
        self.wallet_page.set_balances_hidden(hidden)
        self.send_page.set_balances_hidden(hidden)
        self.history_page.set_balances_hidden(hidden)
        self._show_current_total()
        self._save_quietly()

    def _show_current_total(self) -> None:
        if self.current_total is None:
            self._show_balance("—")
        elif self.settings.hide_balances:
            self._show_balance(HIDDEN_AMOUNT)
        else:
            self._show_balance(format_htn(self.current_total), f"{format_htn_exact(self.current_total)} HTN")

    def _show_balance(self, text: str, tooltip: str = "") -> None:
        # A cut-off amount would show the wrong number, so long amounts get smaller text.
        room = (
            SIDEBAR_WIDTH
            - 2 * (SIDEBAR_MARGIN + BALANCE_CARD_MARGIN)
            - BALANCE_UNIT_GAP
            - self.balance_unit.sizeHint().width()
        )
        font = QFont("Segoe UI")
        font.setWeight(QFont.Weight.DemiBold)
        for size in BALANCE_FONT_SIZES:
            font.setPixelSize(size)
            if QFontMetrics(font).horizontalAdvance(text) <= room:
                break
        self.balance.setStyleSheet(f"QLabel#balance {{ font-size: {size}px; }}")
        self.balance.setText(text)
        self.balance.setToolTip(tooltip)

    def _clear_figures(self, message: str) -> None:
        self.current_total = None
        self._show_current_total()
        self.wallet_page.show_message(message)
        self.receive_page.show_message(message)
        self.send_page.forget_wallet()

    def _show_banner(self, message: str) -> None:
        self.banner.setText(message)
        self._place_banner()
        self.banner.show()
        self.banner.raise_()
        self.banner_timer.start()

    def _place_banner(self) -> None:
        width = self.stack.width() - 64
        height = self.banner.heightForWidth(width)
        self.banner.setGeometry(32, self.stack.height() - height - 16, width, height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_banner()
        self._place_grips()

    # ----- Start and Stop -----

    def _start_stop_clicked(self) -> None:
        self._cancel_waiting_send()
        if self.sending:
            self._show_banner(SENDING_MESSAGE)
        elif self.state is State.NOT_RUNNING:
            self._start()
        elif self.state is State.STARTING:
            self._cancel_start()
        elif self.state is State.RUNNING:
            self._stop()

    def _daemon_log_path(self) -> Path:
        return settings_path().parent / "daemon-start.log"

    def _start(self) -> None:
        self.generation += 1
        try:
            tool = find_tool(Tool.HTNWALLET, tool_folders(self.settings))
            self.starting_process = start_daemon(
                tool, wallet_context(self.settings), self.settings.node_address, self._daemon_log_path()
            )
        except ToolError as error:
            self._show_not_running(str(error))
            return
        self.starting_since = time.monotonic()
        self.state = State.STARTING
        # htnwallet can keep retrying a node it can't reach, so starting can always be cancelled.
        self._update_status("Starting htnwallet…", AMBER, self.settings.node_address, "Cancel", True)
        self.settings_page.set_htnwallet_running(True)
        self.start_timer.start()

    def _cancel_start(self) -> None:
        """End the htnwallet this app is starting. Only that process: the app started it."""
        self.generation += 1
        process = self.starting_process
        self.starting_process = None
        if process is not None and process.poll() is None:
            process.kill()
        self._show_not_running("Start cancelled. Check the node in Settings, then press Start.")

    def _check_starting_daemon(self) -> None:
        process = self.starting_process
        if process is None or self.state is not State.STARTING:
            return
        if process.poll() is not None:
            reason = last_log_line(self._daemon_log_path()) or "htnwallet exited straight away"
            self._show_not_running(f"Could not start: {reason}")
            return
        if time.monotonic() - self.starting_since > DAEMON_START_TIMEOUT_SECONDS:
            process.kill()  # The app started this one, so it's safe to end it.
            self._show_not_running("Could not start: htnwallet didn't respond in time")
            return

        generation = self.generation
        tool = find_tool(Tool.HTNWALLET, tool_folders(self.settings))
        context = wallet_context(self.settings)
        port = self.settings.daemon_port
        run_in_background(
            lambda: running_daemon_version(tool, context, port),
            lambda version: self._start_check_finished(generation, version),
            lambda _error: self.start_timer.start(),
        )

    def _start_check_finished(self, generation: int, version: str | None) -> None:
        if generation != self.generation or self.state is not State.STARTING:
            return
        if version is None:
            self.start_timer.start()
            return
        self.starting_process = None
        self.started_by_app = True
        self.state = State.NOT_RUNNING  # _show_running moves it on, and _check reads the wallet.
        self._show_running(version)
        self.checking = False
        self.check_count = 0
        self._check()

    def _stop(self) -> None:
        if not self.started_by_app:
            answer = QMessageBox.warning(
                self,
                "Close htnwallet?",
                "This htnwallet was started outside this app, and other programs may depend on "
                "it, such as a running auto-compound.\n\nClose it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.generation += 1
        self.start_stop_button.setEnabled(False)
        port = self.settings.daemon_port
        run_in_background(
            lambda: close_daemon(port),
            lambda _result: self._show_not_running("htnwallet not running"),
            self._stop_failed,
        )

    def _stop_failed(self, error: Exception) -> None:
        self.start_stop_button.setEnabled(True)
        self._show_banner(str(error) if isinstance(error, DaemonError) else "Could not close htnwallet")

    # ----- Sending -----

    def _review_send(self, request: SendRequest) -> None:
        if self.state is not State.RUNNING:
            self.send_page.show_error("htnwallet isn't running, so nothing can be sent yet.")
            return
        try:
            tool = find_tool(Tool.HTNWALLET, tool_folders(self.settings))
        except ToolError as error:
            self.send_page.show_error(str(error))
            return
        context = wallet_context(self.settings)
        dialog = SendDialog(
            self, request, command_preview(tool.name, request, context), show_debug=self.settings.show_debug
        )
        dialog.send_requested.connect(lambda password: self._send(dialog, tool, request, password))
        dialog.cancel_requested.connect(self._cancel_waiting_send)
        dialog.send_now_requested.connect(self._send_now)
        # Python must keep hold of the effect while it's in use, or it's deleted and nothing blurs.
        blur = QGraphicsBlurEffect(blurRadius=BLUR_RADIUS)
        self.centralWidget().setGraphicsEffect(blur)
        try:
            dialog.exec()
        finally:
            self.centralWidget().setGraphicsEffect(None)
        # Once money may have moved the form is emptied, so the same send can't be repeated by accident.
        if dialog.outcome and dialog.outcome.result is not SendResult.NOT_SENT:
            self.send_page.clear()

    def _send(self, dialog: SendDialog, tool: Path, request: SendRequest, password: str) -> None:
        """Start the wait. htnwallet doesn't run until it's over, so until then there's nothing to undo."""
        try:
            arguments = request.arguments(wallet_context(self.settings), password)
        except ValueError as error:
            dialog.show_outcome(SendOutcome(SendResult.NOT_SENT, str(error)))
            return
        self.waiting_send = (dialog, tool, request, arguments)
        self.wait_deadline = time.monotonic() + SEND_DELAY_SECONDS
        dialog.show_waiting(math.ceil(SEND_DELAY_SECONDS))
        self.wait_timer.start()

    def _wait_tick(self) -> None:
        if self.waiting_send is None:
            self.wait_timer.stop()
            return
        seconds_left = self.wait_deadline - time.monotonic()
        if seconds_left > 0:
            self.waiting_send[0].show_countdown(math.ceil(seconds_left))
            return
        self.wait_timer.stop()
        waiting_send, self.waiting_send = self.waiting_send, None
        self._run_send(*waiting_send)

    def _send_now(self) -> None:
        """The user has confirmed it, so the rest of the wait is skipped."""
        if self.waiting_send is not None:
            self.wait_deadline = 0.0
            self._wait_tick()

    def _cancel_waiting_send(self) -> None:
        """Called on Cancel, and on anything else that ends the wait. Nothing has run, so nothing was sent."""
        if self.waiting_send is None:
            return
        self.wait_timer.stop()
        dialog = self.waiting_send[0]
        self.waiting_send = None
        dialog.show_cancelled()

    def _run_send(self, dialog: SendDialog, tool: Path, request: SendRequest, arguments: list[str]) -> None:
        try:
            record_send_started(send_journal_path(settings_path()), request)
        except OSError:
            dialog.show_outcome(SendOutcome(SendResult.NOT_SENT, "The app couldn't save its note of this send."))
            return

        self.sending = True
        self.start_stop_button.setEnabled(False)
        dialog.show_sending()
        run_in_background(
            lambda: run_send(tool, arguments),
            lambda outcome: self._send_finished(dialog, outcome),
            lambda error: self._send_failed(dialog, error),
        )

    def _send_failed(self, dialog: SendDialog, error: Exception) -> None:
        if isinstance(error, ToolError):  # htnwallet never started.
            self._send_finished(dialog, SendOutcome(SendResult.NOT_SENT, str(error)))
        else:
            self._send_finished(
                dialog,
                SendOutcome(
                    SendResult.MAY_HAVE_BEEN_SENT,
                    "Something went wrong in the app during this send, so it can't tell whether it went "
                    "through. Check your balance and the recipient's before trying again, or you could "
                    "pay twice.",
                ),
            )

    def _send_finished(self, dialog: SendDialog, outcome: SendOutcome) -> None:
        self.sending = False
        try:
            forget_send(send_journal_path(settings_path()))
        except OSError:
            pass  # At worst the next start warns about a send that has already been reported.
        self.start_stop_button.setEnabled(self.state in (State.RUNNING, State.NOT_RUNNING))
        dialog.show_outcome(outcome)
        self._refresh_everything()

    def _report_interrupted_send(self) -> None:
        journal = send_journal_path(settings_path())
        message = interrupted_send_message(journal)
        if message is None:
            return
        QMessageBox.warning(self, "A send may have gone through", message)
        try:
            forget_send(journal)
        except OSError:
            pass

    # ----- Auto-compound -----

    def _exec_blurred(self, dialog: QDialog) -> None:
        # Python must keep hold of the effect while it's in use, or it's deleted and nothing blurs.
        blur = QGraphicsBlurEffect(blurRadius=BLUR_RADIUS)
        self.centralWidget().setGraphicsEffect(blur)
        try:
            dialog.exec()
        finally:
            self.centralWidget().setGraphicsEffect(None)

    def _scan_compounds(self) -> None:
        if self.scanning_compounds:
            return
        self.scanning_compounds = True
        own = self.compounders.counts()
        run_in_background(lambda: find_compounds(own), self._compounds_found, self._compound_scan_failed)

    def _compound_scan_failed(self, _error: Exception) -> None:
        self.scanning_compounds = False

    def _compounds_found(self, compounds: list[RunningCompound]) -> None:
        self.scanning_compounds = False
        for address, reason in self.compounders.ended():
            self._show_banner(f"Auto-compound for {shorten_address(address)} stopped: {reason}")
        new_ids = self.compounders.take_compound_ids()
        if new_ids:
            self._remember_compounds(new_ids)
        # One started while the scan was running is still this app's own.
        counts = self.compounders.counts()
        compounds = [
            dataclasses.replace(compound, started_here=True, compounded=counts[compound.pid])
            if compound.pid in counts
            else compound
            for compound in compounds
        ]
        self.wallet_page.show_compounds(compounds)

    def _compound_clicked(self, address: str) -> None:
        compound = self.wallet_page.compound_for(address)
        if compound is not None:
            self._open_stop_compound(compound)
            return
        if self.state is not State.RUNNING:
            self._show_banner("htnwallet isn't running, so auto-compound can't start yet.")
            return
        try:
            tool = find_tool(Tool.HTNWALLET, tool_folders(self.settings))
        except ToolError as error:
            self._show_banner(str(error))
            return
        dialog = StartCompoundDialog(self, address, list(self.wallet_page.rows))
        context = wallet_context(self.settings)

        def start(to_address: str, seconds: int, password: str) -> None:
            run_in_background(
                lambda: Compounders.launch(tool, context, address, to_address, seconds, password),
                lambda started: self._compound_started(dialog, started),
                lambda error: dialog.show_error(
                    str(error) if isinstance(error, (CompoundError, ValueError)) else "Could not start auto-compound."
                ),
            )

        dialog.start_requested.connect(start)
        self._exec_blurred(dialog)

    def _compound_started(self, dialog: StartCompoundDialog, started) -> None:
        self.compounders.add(started)
        dialog.busy = False
        dialog.accept()
        self._scan_compounds()

    def _open_stop_compound(self, compound: RunningCompound) -> None:
        details = self.compounders.details(compound.pid) if compound.started_here else None
        dialog = StopCompoundDialog(self, compound, details)

        def stop() -> None:
            if compound.started_here and self.compounders.stop(compound.pid):
                self._remember_compounds(self.compounders.take_compound_ids())
                self._compound_stopped(dialog)
                return
            run_in_background(
                lambda: stop_outside_compound(compound),
                lambda _result: self._compound_stopped(dialog),
                lambda error: dialog.show_error(
                    str(error) if isinstance(error, CompoundError) else "Could not stop the auto-compound."
                ),
            )

        dialog.stop_requested.connect(stop)
        self._exec_blurred(dialog)

    def _compound_stopped(self, dialog: StopCompoundDialog) -> None:
        dialog.busy = False
        dialog.accept()
        self._scan_compounds()

    # ----- Transaction History -----

    def _history_own_addresses(self) -> set[str]:
        page = self.history_page
        return {page.address.itemData(index) for index in range(page.address.count())}

    def _remember_compounds(self, ids: list[str]) -> None:
        if not ids:
            return
        now = time.time()
        for found in ids:
            self.hidden_ids[found] = now
        try:
            save_hidden_ids(history_folder(settings_path()), self.hidden_ids)
        except OSError:
            pass  # At worst a compound shows in History after the app restarts.
        self._history_show()

    def _history_path(self, address: str) -> Path:
        return cache_path(history_folder(settings_path()), self.settings.network, address)

    def _history_show(self, keep_page: bool = True) -> None:
        cache = self.history_cache
        if cache is None or cache.address != self.history_page.current_address():
            return
        rows = rows_for(cache, self._history_own_addresses(), set(self.hidden_ids))
        now_ms = int(time.time() * 1000)
        self.history_page.show_rows(rows, cache.updated_at, keep_page, days_shown(cache, now_ms))
        if cache.complete:
            self.history_page.show_building(None)
        else:
            pages_progress = cache.pages_read * KEEP_DAYS // MAX_BUILD_PAGES
            self.history_page.show_building(days_read(cache, now_ms), pages_progress)

    def _history_address_changed(self, address: str) -> None:
        self.history_generation += 1
        self.history_busy = False  # A step for the old address may still finish; its result is ignored.
        self.history_cache = load_cache(self._history_path(address), address)
        self._history_show(keep_page=False)
        self.history_poll_now = True
        self._history_refresh_if_shown()

    def _history_opened(self) -> None:
        if self.stack.currentWidget() is self.history_page:
            self.history_poll_now = True
            self._history_refresh_if_shown()

    def _history_refresh_clicked(self, address: str) -> None:
        dialog = RefreshHistoryDialog(self, address)
        self._exec_blurred(dialog)
        if dialog.result() != QDialog.DialogCode.Accepted or address != self.history_page.current_address():
            return
        # Start again from nothing. A wait the explorer asked for (429) still holds.
        self.history_generation += 1
        self.history_busy = False
        self.history_cache = Cache(address)
        try:
            self._history_path(address).unlink(missing_ok=True)
        except OSError:
            pass  # The rebuilt history overwrites it anyway.
        self.history_page.show_message("")
        self._history_show(keep_page=False)
        self._history_refresh_if_shown()

    def _history_due(self, cache: Cache) -> bool:
        """Whether the explorer should be asked now: a build, a fresh visit, or a minute since the last read."""
        if not cache.complete or self.history_poll_now:
            return True
        return time.time() - cache.updated_at >= HISTORY_REFRESH_SECONDS - 1

    def _history_refresh_if_shown(self) -> None:
        cache = self.history_cache
        shown = self.stack.currentWidget() is self.history_page and self.isVisible() and not self.isMinimized()
        if cache is None or not shown or self.history_busy or time.time() < self.history_wait_until:
            return
        if not self._history_due(cache):
            return
        too_soon = self.history_last_request + HISTORY_PAGE_GAP_MS / 1000 - time.time()
        if too_soon > 0:
            QTimer.singleShot(int(too_soon * 1000) + 1, self._history_refresh_if_shown)
            return
        self._history_step()

    def _history_step(self) -> None:
        """Read the next page of a first build, or what's new since the last read."""
        cache = self.history_cache
        if cache is None or self.history_busy:
            return
        self.history_busy = True
        self.history_poll_now = False
        self.history_last_request = time.time()
        generation = self.history_generation
        balance = self.history_page.balances.get(cache.address)
        base = api_base(self.settings.history_source)
        # The work happens on a copy, so the page never reads the cache while it's being changed.
        working = cache.copy()
        now_ms = int(time.time() * 1000)

        def work() -> Cache:
            if working.complete:
                top_up(working, now_ms, base)
            else:
                build_step(working, now_ms, base)
            forget_old(working, now_ms)
            return working

        run_in_background(
            work,
            lambda result: self._history_stepped(generation, result, balance),
            lambda error: self._history_failed(generation, error),
        )

    def _history_stepped(self, generation: int, cache: Cache, balance: Decimal | None) -> None:
        if generation != self.history_generation:
            return
        self.history_busy = False
        self.history_failures = 0
        self.history_wait_until = 0.0
        self.history_cache = cache
        try:
            save_cache(self._history_path(cache.address), cache)
        except OSError:
            pass  # It's rebuilt next time; nothing else depends on it.
        self.history_page.show_message("")
        self._history_show()
        if not cache.complete:
            QTimer.singleShot(HISTORY_PAGE_GAP_MS, self._history_refresh_if_shown)

    def _history_failed(self, generation: int, error: Exception) -> None:
        if generation != self.history_generation:
            return
        self.history_busy = False
        self.history_failures += 1
        shortest, longest = HISTORY_BACKOFF_SECONDS
        delay = min(longest, shortest * 2 ** (self.history_failures - 1))
        if isinstance(error, HistoryError) and error.retry_after:
            delay = max(delay, min(error.retry_after, longest))
        self.history_wait_until = time.time() + delay
        reason = str(error) if isinstance(error, HistoryError) else "The history couldn't be read."
        again = time.strftime("%H:%M", time.localtime(self.history_wait_until))
        self.history_page.show_message(f"{reason} Trying again at {again}.")

    # ----- Preferences -----

    def _settings_saved(self) -> None:
        self.receive_page.network = self.settings.network
        self.send_page.set_network(self.settings.network)
        if self.state is State.NOT_RUNNING:
            self._show_not_running("htnwallet not running")
            self._check()

    # ----- Addresses and their labels -----

    def _add_address_clicked(self) -> None:
        if self.state is not State.RUNNING:
            self._show_banner("Start htnwallet first: it makes the new address.")
            return
        dialog = AddAddressDialog(self)

        def add(name: str) -> None:
            try:
                tool = find_tool(Tool.HTNWALLET, tool_folders(self.settings))
            except ToolError as error:
                dialog.show_error(str(error))
                return
            context = wallet_context(self.settings)
            port = self.settings.daemon_port
            run_in_background(
                lambda: create_address(tool, context, port),
                lambda address: self._address_added(dialog, address, name),
                lambda error: dialog.show_error(
                    str(error) if isinstance(error, (DaemonError, ToolError)) else "htnwallet couldn't make an address"
                ),
            )

        dialog.add_requested.connect(add)
        self._exec_blurred(dialog)

    def _address_added(self, dialog: AddAddressDialog, address: str, name: str) -> None:
        if name:
            self._label_changed(address, name)
        dialog.show_added(address, self.labels.get(address, ""))
        if address not in self.receive_addresses:
            self.receive_addresses = [*self.receive_addresses, address]
        self.check_count = 0  # Read the addresses again at once, so every page lists it.
        self._check()

    def _label_changed(self, address: str, text: str) -> None:
        set_label(self.labels, address, text)
        try:
            save_labels(self.labels)
        except OSError as error:
            self._show_banner(f"Could not save the label: {error.strerror}")
        self.wallet_page.show_labels()
        self.send_page.labels_changed()
        self.history_page.labels_changed()

    def _open_wizard(self) -> None:
        self._cancel_waiting_send()
        if self.sending:
            self._show_banner(SENDING_MESSAGE)
            return
        owns = self.started_by_app and self.state is State.RUNNING
        wizard = SetupWizard(self, self.settings, app_owns_daemon=owns)
        wizard.move(self.frameGeometry().topLeft())
        if wizard.exec() == QDialog.DialogCode.Accepted and wizard.action:
            self.after_wizard(wizard.action, app_owned=owns, process=wizard.started_process)

    def after_wizard(self, action: str, app_owned: bool = False, process: subprocess.Popen | None = None) -> None:
        """Use what the Setup Wizard chose: its settings are already in self.settings."""
        self._save_quietly()
        self.settings_page.show_settings()
        self.receive_page.network = self.settings.network
        self.send_page.set_network(self.settings.network)
        self.generation += 1  # Anything read from the old wallet or address is out of date.
        self.checking = False
        self.check_count = 0
        port = self.settings.daemon_port
        if action == START:
            if app_owned and port_is_open(port):
                self.start_stop_button.setEnabled(False)
                # The old htnwallet this app started has to go before one with the new file can start.
                run_in_background(lambda: close_daemon(port), lambda _result: self._start(), self._stop_failed)
            else:
                self._start()
            return
        if action == SAVED:
            message = "Wallet created. Check Settings, such as the node, then press Start to run htnwallet."
            if app_owned and port_is_open(port):
                # The htnwallet this app started still has the old wallet open; it doesn't fit the new settings.
                self.start_stop_button.setEnabled(False)
                run_in_background(
                    lambda: close_daemon(port),
                    lambda _result: (self._show_not_running("htnwallet not running"), self._show_banner(message)),
                    self._stop_failed,
                )
            else:
                self.state = State.NOT_RUNNING
                self._show_not_running("htnwallet not running")
                self._show_banner(message)
            self.started_by_app = False
            return
        if action == STARTED and process is not None and process.poll() is None:
            # The wizard's htnwallet may still be starting: watch it as if Start had been pressed, Cancel included.
            self.starting_process = process
            self.starting_since = time.monotonic()
            self.state = State.STARTING
            self._update_status("Starting htnwallet…", AMBER, self.settings.node_address, "Cancel", True)
            self.settings_page.set_htnwallet_running(True)
            self.start_timer.start()
            return
        self.started_by_app = action == STARTED
        self.state = State.NOT_RUNNING
        self._show_not_running("htnwallet not running")
        self.state = State.CHECKING
        self._update_status("Checking for htnwallet…", AMBER, self.settings.daemon_address, "Start", False)
        self._check()

    def _save_quietly(self) -> None:
        try:
            save_settings(self.settings)
        except OSError:
            pass  # Remembering a preference isn't worth interrupting the user over.

    # ----- Closing the window, and the tray -----

    def closeEvent(self, event) -> None:
        self._cancel_waiting_send()
        if self.sending:
            event.ignore()
            self._show_banner(SENDING_MESSAGE)
            return
        if self.really_exit or self.tray is None:
            event.accept()
            self._exit_app()
            return

        action = self.settings.close_action
        if action == "ask":
            dialog = CloseDialog(self, self.state is State.RUNNING)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                event.ignore()
                return
            action = dialog.choice
            if dialog.remember.isChecked():
                self.settings.close_action = action
                self.settings_page.show_close_action()
                self._save_quietly()

        if action == "tray":
            event.ignore()
            self.hide()
        else:
            event.accept()
            self._exit_app()

    def _exit_app(self) -> None:
        """Exit the app. htnwallet is deliberately left exactly as it is."""
        self._cancel_waiting_send()
        if self.sending:
            self._show_banner(SENDING_MESSAGE)
            self._show_from_tray()
            return
        self.really_exit = True
        self.compounders.stop_all()  # Windows would end them anyway; this does it straight away.
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._check()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_from_tray()
