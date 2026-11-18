"""The About page: what the app is, where its code and HTND's live, updates, and where to get help."""

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from guiwallet import __version__
from guiwallet.core.updates import APP_RELEASES, APP_REPO, HTND_RELEASES, HTND_REPO, Release, is_newer, plain_version
from guiwallet.ui import theme
from guiwallet.ui.theme import AMBER_TEXT, ASSETS
from guiwallet.ui.widgets import card, divider, label, page_layout

DISCORD_URL = "https://discord.gg/mtVfyFfMZq"
APP_URL = f"https://github.com/{APP_REPO}"
HTND_URL = f"https://github.com/{HTND_REPO}"
ISSUES_URL = f"{APP_URL}/issues"
LICENCE_URL = f"{APP_URL}/blob/main/LICENSE"
EXPLORERS = {"EU": "https://explorer-eu.hoosat.network/", "NZ": "https://explorer-nz.hoosat.network/"}
MUTED = "#8D98B0"
ERROR = "#F28B82"


def open_link(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


def _link_button(text: str, url: str, opens: Callable[[], str] | None = None) -> QPushButton:
    """A small button that opens a web page in the browser. The arrow says it leaves the app.
    `opens`, if given, picks the page when it's pressed; `url` is then only the fallback shown on hover."""
    button = QPushButton(f"{text}  ↗")
    button.setObjectName("smallButton")
    button.setFixedHeight(30)
    button.setToolTip(url)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(lambda: open_link(opens() if opens else url))
    return button


def _pill(text: str, url: str, logo: str, name: str) -> QPushButton:
    """A GitHub or Discord button in that site's own colours, with its logo."""
    button = QPushButton(f" {text}")
    button.setObjectName(name)
    button.setFixedHeight(30)
    button.setIcon(QIcon(QPixmap(str(ASSETS / logo))))
    button.setIconSize(QSize(15, 15))
    button.setToolTip(url)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(lambda: open_link(url))
    return button


def _row(title: str, text: QLabel | str, *buttons: QPushButton) -> QWidget:
    """One line of the list: a bold title with a line of text under it, and its buttons on the right."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 6, 0, 6)
    layout.setSpacing(10)
    words = QVBoxLayout()
    words.setSpacing(2)
    words.addWidget(label(title, "rowTitle"))
    words.addWidget(text if isinstance(text, QLabel) else label(text, "rowText"))
    layout.addLayout(words, 1)
    for button in buttons:
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def _span(text: str, colour: str, bold: bool = False) -> str:
    weight = " font-weight: 600;" if bold else ""
    return f'<span style="color: {colour};{weight}">{text}</span>'


class AboutPage(QWidget):
    check_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.app_release: Release | None = None
        self.htnd_release: Release | None = None
        layout = page_layout(self, "About", "Who makes this app, where the code lives, and where to get help")

        head = card()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(22, 20, 22, 20)
        head_layout.setSpacing(18)
        owl = QLabel()
        owl.setPixmap(theme.emblem(72))
        head_layout.addWidget(owl, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(3)
        words.addWidget(label("HTN GUI Wallet", "dialogTitle"))
        words.addWidget(label(f"Version {__version__}", "muted"))
        words.addSpacing(4)
        blurb = label(
            "A desktop wallet for Hoosat (HTN). It runs htnwallet from HTND for you, so you can send, receive "
            "and compound without the command line.",
            "subtitle",
        )
        blurb.setWordWrap(True)
        words.addWidget(blurb)
        words.addSpacing(10)
        pills = QHBoxLayout()
        pills.setSpacing(8)
        pills.addWidget(_pill("GUI Wallet", APP_URL, "github.png", "githubPill"))
        pills.addWidget(_pill("Official HTND", HTND_URL, "github.png", "githubPill"))
        pills.addWidget(_pill("Discord", DISCORD_URL, "discord.png", "discordPill"))
        pills.addStretch()
        words.addLayout(pills)
        head_layout.addLayout(words, 1)
        layout.addWidget(head)
        layout.addSpacing(12)

        heading = QWidget()
        heading_layout = QHBoxLayout(heading)
        heading_layout.setContentsMargins(0, 10, 0, 2)
        heading_layout.addWidget(label("VERSIONS", "sectionLabel"))
        self.checked = label("", "hint")
        heading_layout.addWidget(self.checked)
        heading_layout.addStretch()
        self.check_button = QPushButton("Check for updates")
        self.check_button.setObjectName("smallButton")
        self.check_button.setFixedSize(140, 30)
        self.check_button.clicked.connect(self.check_requested)
        heading_layout.addWidget(self.check_button)

        self.app_status = label("", "rowText")
        self.htnd_status = label("", "rowText")
        # Each opens the page of the release that was found.
        self.app_download = _link_button(
            "Download", APP_RELEASES, lambda: self.app_release.url if self.app_release else APP_RELEASES
        )
        self.htnd_download = _link_button(
            "Download", HTND_RELEASES, lambda: self.htnd_release.url if self.htnd_release else HTND_RELEASES
        )

        rows = [
            _row("HTN GUI Wallet", self.app_status, self.app_download),
            _row("HTND (htnwallet)", self.htnd_status, self.htnd_download),
            _row("Report a problem", "Bugs and feature requests go to the GitHub issues page",
                 _link_button("Open", ISSUES_URL)),
            _row("Block explorer", "Look up any address or transaction",
                 _link_button("EU", EXPLORERS["EU"]), _link_button("NZ", EXPLORERS["NZ"])),
            _row("Licence", "GNU GPL v3 or later. Free to use and share, with no warranty",
                 _link_button("Read", LICENCE_URL)),
        ]
        links = card()
        links_layout = QVBoxLayout(links)
        links_layout.setContentsMargins(22, 6, 22, 6)
        links_layout.setSpacing(0)
        links_layout.addWidget(heading)  # No line under the heading.
        for index, row in enumerate(rows):
            if index:
                links_layout.addWidget(divider())
            links_layout.addWidget(row)
        layout.addWidget(links)
        layout.addSpacing(12)

        privacy = label(
            "Your password, seed phrase and keys never leave this PC. The app has no servers, accounts, tracking "
            "or analytics.",
            "note",
        )
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        layout.addStretch()
        self.show_versions(None, None, None, False, False, False, None)

    def show_versions(
        self,
        app_release: Release | None,
        htnd_release: Release | None,
        htnd_installed: str | None,
        app_failed: bool,
        htnd_failed: bool,
        checking: bool,
        checked_at: datetime | None,
    ) -> None:
        """Show what's installed and, once GitHub has answered, whether there's anything newer."""
        self.app_release, self.htnd_release = app_release, htnd_release
        app_new = app_release is not None and is_newer(app_release.version, __version__)
        htnd_new = htnd_release is not None and is_newer(htnd_release.version, htnd_installed)
        theme.set_rich_text(self.app_status, self._status(__version__, app_release, app_new, app_failed, checking))
        installed = plain_version(htnd_installed)
        if installed is None and not checking and not htnd_failed:
            htnd_text = _span("Version unknown: start htnwallet to check HTND.", MUTED)
        else:
            htnd_text = self._status(installed, htnd_release, htnd_new, htnd_failed, checking)
        theme.set_rich_text(self.htnd_status, htnd_text)
        self.app_download.setEnabled(app_new)
        self.htnd_download.setEnabled(htnd_new)
        self.check_button.setEnabled(not checking)
        if checking:
            self.check_button.setText("Checking…")
        else:
            self.check_button.setText("Try again" if app_failed and htnd_failed else "Check for updates")
        self.checked.setText(f"  ·  Checked at {checked_at:%H:%M}" if checked_at and not checking else "")

    @staticmethod
    def _status(installed: str | None, release: Release | None, newer: bool, failed: bool, checking: bool) -> str:
        if checking:
            return _span("Checking GitHub…", MUTED)
        if failed:
            return _span("Could not reach GitHub.", ERROR, bold=True) + _span(
                " Check the internet connection and try again.", MUTED
            )
        text = _span(f"Installed: version {installed}", MUTED)
        if newer and release is not None:
            text += _span("  ·  ", MUTED) + _span(f"Version {release.version} is available.", AMBER_TEXT, bold=True)
        return text

