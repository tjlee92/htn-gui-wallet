"""The Transaction History page: one address at a time, filtered by type and by date, eight rows a page."""

import time
from datetime import datetime, timedelta
from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from guiwallet.core.history import KEEP_DAYS, Kind, Row, address_link, transaction_link
from guiwallet.core.wallet import WalletBalance, format_htn
from guiwallet.ui.compound_dialog import _FramedDialog
from guiwallet.ui.theme import AMBER, GREEN
from guiwallet.ui.widgets import HIDDEN_AMOUNT, address_text, card, divider, label, page_layout, shorten_address

ROWS_PER_PAGE = 8
RED = "#F0706A"
MUTED = "#8D98B0"
WHITE = "#E7ECF5"
KIND_FILTERS = ("All", "Sent", "Received", "Internal")
PERIODS = {"Today": None, "7 days": 7}
COLUMNS = ("Type", "Amount (HTN)", "Address", "Date", "Status", "Explorer")
# Fixed widths, so the columns don't move when the filter changes. The address column takes the rest.
COLUMN_WIDTHS = {0: 62, 1: 92, 3: 92, 4: 72, 5: 50}
PAGE_NUMBERS_SHOWN = 3  # The first few pages, then "…" and the last.


def _text(value: str, style: str = "font-size: 13px;") -> QLabel:
    widget = QLabel(value)
    widget.setStyleSheet(style)
    return widget


def _links(urls: dict[str, str], prefix: str = "") -> QLabel:
    html = " &nbsp; ".join(f'<a style="color: {GREEN};" href="{url}">{site}</a>' for site, url in urls.items())
    widget = _text(prefix + html)
    widget.setOpenExternalLinks(True)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    return widget


def format_amount(amount: Decimal, kind: Kind) -> str:
    sign = "−" if amount < 0 else "+"
    return f"{sign}{abs(amount):,.2f}"


def format_date(block_time_ms: int, now: datetime | None = None) -> str:
    moment = datetime.fromtimestamp(block_time_ms / 1000)
    now = now or datetime.now()
    clock = moment.strftime("%H:%M")
    if moment.date() == now.date():
        return f"Today, {clock}"
    return f"{moment.day} {moment.strftime('%b')}, {clock}"


def period_start_ms(period: str, now: datetime | None = None) -> int:
    now = now or datetime.now()
    days = PERIODS[period]
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) if days is None else now - timedelta(days=days)
    return int(start.timestamp() * 1000)


def filter_rows(rows: list[Row], kind_filter: str, period: str, now: datetime | None = None) -> list[Row]:
    start = period_start_ms(period, now)
    wanted = None if kind_filter == "All" else Kind(kind_filter)
    return [row for row in rows if row.block_time >= start and (wanted is None or row.kind is wanted)]


def page_numbers(page_count: int, current: int) -> list[int | None]:
    """The page buttons to show; None stands for "…"."""
    if page_count <= PAGE_NUMBERS_SHOWN + 1:
        return list(range(1, page_count + 1))
    first = list(range(1, PAGE_NUMBERS_SHOWN + 1))
    if current in first or current == page_count:
        return [*first, None, page_count]
    return [1, None, current, None, page_count]


class RefreshHistoryDialog(_FramedDialog):
    """Asks before the saved history of an address is thrown away and read again."""

    def __init__(self, parent: QWidget, address: str) -> None:
        super().__init__(parent, "Refresh transaction history?")
        text = label(
            f"The transactions of {shorten_address(address)} will be read again from the explorer, "
            "replacing the history saved on this computer. For a mining address this can take a minute.",
            "muted",
        )
        text.setWordWrap(True)
        text.setFixedWidth(420)
        self.layout_.addWidget(text)
        self.cancel_button, self.refresh_button = self._buttons("Cancel", "Refresh")
        self.refresh_button.clicked.connect(self.accept)


class HistoryPage(QWidget):
    address_changed = Signal(str)
    refresh_clicked = Signal(str)  # The address whose history should be read again.

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[Row] = []
        self.page = 1
        self.span: str | None = None  # How far back a capped history reaches, such as "2 days".
        self.balances_hidden = False
        self.balances: dict[str, Decimal] = {}
        layout = page_layout(
            self,
            "Transaction History",
            "Transactions of the selected address. Mining rewards and auto-compounds are excluded.",
        )

        self.address = QComboBox()
        self.address.setFixedWidth(400)
        self.address.setPlaceholderText("No addresses yet")
        self.address.currentIndexChanged.connect(self._address_chosen)
        self.refresh = QPushButton("Refresh")
        self.refresh.setToolTip("Read this address's transactions again from the explorer")
        self.refresh.clicked.connect(self._refresh_clicked)
        address_row = QHBoxLayout()
        address_row.setSpacing(10)
        address_row.addWidget(self.address)
        address_row.addWidget(self.refresh)
        address_row.addStretch()
        layout.addLayout(address_row)
        layout.addSpacing(10)

        controls = QHBoxLayout()
        controls.setSpacing(14)
        self.kinds = self._segments(controls, KIND_FILTERS, "All", 84)
        controls.addStretch()
        self.periods = self._segments(controls, tuple(PERIODS), "7 days", 76)
        layout.addLayout(controls)
        layout.addSpacing(10)

        panel = card()
        self.grid = QGridLayout(panel)
        self.grid.setContentsMargins(20, 14, 20, 14)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(10)
        for column, width in COLUMN_WIDTHS.items():
            self.grid.setColumnMinimumWidth(column, width)
        self.grid.setColumnStretch(2, 1)
        layout.addWidget(panel)
        self.message = _text("", f"font-size: 13px; color: {MUTED};")
        self.message.setWordWrap(True)
        layout.addSpacing(10)

        footer = QHBoxLayout()
        self.updated = _text("", f"font-size: 12px; color: {MUTED};")
        self.updated.setToolTip(f"{KEEP_DAYS} days are kept on this computer.")
        footer.addWidget(self.updated)
        # While the first build runs, its progress takes the place of "Updated", so the table keeps its rows.
        self.building = QWidget()
        build = QHBoxLayout(self.building)
        build.setContentsMargins(0, 0, 0, 0)
        build.setSpacing(8)
        self.building_text = _text("", f"font-size: 12px; color: {MUTED};")
        build.addWidget(self.building_text)
        self.progress = QProgressBar()
        self.progress.setRange(0, KEEP_DAYS)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(110, 6)
        self.progress.setStyleSheet(
            "QProgressBar { background: #1A2338; border: none; border-radius: 3px; }"
            f"QProgressBar::chunk {{ background: {GREEN}; border-radius: 3px; }}"
        )
        build.addWidget(self.progress)
        self.building.setToolTip(
            "The history of this address is read once, then only new transactions are fetched. Mining rewards "
            "are read and discarded, so this takes longest on mining addresses."
        )
        self.building.hide()
        footer.addWidget(self.building)
        footer.addStretch()
        self.pager = QWidget()
        self.pager_layout = QHBoxLayout(self.pager)
        self.pager_layout.setContentsMargins(0, 0, 0, 0)
        self.pager_layout.setSpacing(4)
        footer.addWidget(self.pager)
        footer.addStretch()
        self.older = QWidget()
        self.older_layout = QHBoxLayout(self.older)
        self.older_layout.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self.older)
        layout.addLayout(footer)
        layout.addWidget(self.message)
        layout.addStretch()

        self.kinds.buttonClicked.connect(lambda _button: self._filters_changed())
        self.periods.buttonClicked.connect(lambda _button: self._filters_changed())
        self._show_table()

    def _segments(self, row_layout: QHBoxLayout, names, chosen: str, width: int) -> QButtonGroup:
        frame = QFrame()
        frame.setObjectName("sendTabs")
        row = QHBoxLayout(frame)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        group = QButtonGroup(self)
        for name in names:
            button = QPushButton(name)
            button.setObjectName("sendTab")
            button.setCheckable(True)
            button.setFixedWidth(width)
            button.setChecked(name == chosen)
            group.addButton(button)
            row.addWidget(button)
        row_layout.addWidget(frame)
        return group

    # ----- What the main window tells it -----

    def current_address(self) -> str | None:
        return self.address.currentData()

    def labels_changed(self) -> None:
        self.set_addresses(getattr(self, "listed_addresses", []))

    def set_addresses(self, addresses: list[str], balance: WalletBalance | None = None) -> None:
        """Fill the address list, keeping the current choice. Each shows its balance."""
        self.listed_addresses = list(addresses)
        if balance is not None:
            self.balances = {entry.address: entry.available + entry.pending for entry in balance.addresses}
        current = self.current_address()
        wanted = [(address, self._address_text(address)) for address in addresses]
        existing = [(self.address.itemData(i), self.address.itemText(i)) for i in range(self.address.count())]
        if wanted == existing:
            return
        self.address.blockSignals(True)
        self.address.clear()
        for address, text in wanted:
            self.address.addItem(text, address)
            self.address.setItemData(self.address.count() - 1, address, Qt.ItemDataRole.ToolTipRole)
        index = self.address.findData(current) if current else -1
        self.address.setCurrentIndex(index if index >= 0 else (0 if addresses else -1))
        self.address.blockSignals(False)
        if self.current_address() != current:
            self._address_chosen()

    def _address_text(self, address: str) -> str:
        amount = self.balances.get(address)
        name = address_text(address, getattr(self, "labels", None))
        if amount is None:
            return name
        figure = HIDDEN_AMOUNT if self.balances_hidden else format_htn(amount)
        return f"{name}   ·   {figure} HTN"

    def set_balances_hidden(self, hidden: bool) -> None:
        self.balances_hidden = hidden
        addresses = [self.address.itemData(i) for i in range(self.address.count())]
        self.set_addresses(addresses)
        self._show_table()

    def show_rows(self, rows: list[Row], updated_at: float, keep_page: bool = True, span: str | None = None) -> None:
        """span is set when the first build stopped at its page cap: how far back the history reaches."""
        self.rows = rows
        self.span = span
        if not keep_page:
            self.page = 1
        updated = f"Updated {time.strftime('%H:%M', time.localtime(updated_at))}" if updated_at else ""
        self.updated.setText(f"Showing the last {span}  ·  {updated}" if span and updated else updated)
        self._show_table()

    def show_building(self, days: int | None, progress: int | None = None) -> None:
        """The first build's progress, in the footer: None hides it.

        progress (out of 30) can run ahead of days: a build that stops at its page cap fills the bar then."""
        building = days is not None
        self.building.setVisible(building)
        self.updated.setVisible(not building)
        if building:
            self.building_text.setText(f"Building… {days} of {KEEP_DAYS} days")
            self.progress.setValue(max(days, progress or 0))

    def show_message(self, message: str) -> None:
        self.message.setText(message)
        self.message.setVisible(bool(message))

    def forget(self) -> None:
        """No wallet to show, for example after htnwallet stopped."""
        self.rows = []
        self.span = None
        self.show_building(None)
        self.updated.setText("")
        self._show_table()

    # ----- Drawing -----

    def _choice(self, group: QButtonGroup) -> str:
        return group.checkedButton().text()

    def _address_chosen(self) -> None:
        self.page = 1
        self.rows = []
        self.span = None
        self.show_building(None)
        self.show_message("")
        self.updated.setText("")
        self._show_table()
        address = self.current_address()
        if address:
            self.address_changed.emit(address)

    def _refresh_clicked(self) -> None:
        address = self.current_address()
        if address:
            self.refresh_clicked.emit(address)

    def _filters_changed(self) -> None:
        self.page = 1
        self._show_table()

    def _go_to(self, page: int) -> None:
        self.page = page
        self._show_table()

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().setParent(None)

    def visible_rows(self) -> list[Row]:
        return filter_rows(self.rows, self._choice(self.kinds), self._choice(self.periods))

    def _show_table(self) -> None:
        self._clear(self.grid)
        for column, name in enumerate(COLUMNS):
            alignment = Qt.AlignmentFlag.AlignHCenter if column == 1 else Qt.AlignmentFlag.AlignLeft
            self.grid.addWidget(label(name, "sectionLabel"), 0, column, alignment)
        rows = self.visible_rows()
        per_page = self.rows_per_page()
        page_count = max(1, -(-len(rows) // per_page))
        self.page = min(self.page, page_count)
        start = (self.page - 1) * per_page
        shown = rows[start : start + per_page]
        grid_row = 1
        for row in shown:
            self.grid.addWidget(divider(), grid_row, 0, 1, len(COLUMNS))
            grid_row += 1
            tone = {Kind.SENT: RED, Kind.RECEIVED: GREEN}.get(row.kind, WHITE)
            self.grid.addWidget(_text(row.kind.value, f"font-size: 13px; color: {tone};"), grid_row, 0)
            amount = HIDDEN_AMOUNT if self.balances_hidden else format_amount(row.amount, row.kind)
            self.grid.addWidget(
                _text(amount, f"font-size: 13px; font-weight: 600; color: {tone};"),
                grid_row,
                1,
                Qt.AlignmentFlag.AlignHCenter,
            )
            other = label(shorten_address(row.other_address) if row.other_address else "—", "address")
            other.setToolTip(row.other_address)
            self.grid.addWidget(other, grid_row, 2)
            self.grid.addWidget(_text(format_date(row.block_time), f"font-size: 13px; color: {MUTED};"), grid_row, 3)
            status, colour = ("Confirmed", GREEN) if row.accepted else ("Pending", AMBER)
            self.grid.addWidget(_text(status, f"font-size: 13px; font-weight: 600; color: {colour};"), grid_row, 4)
            if row.accepted:
                links = {site: transaction_link(site, row.transaction_id) for site in ("NZ", "EU")}
                self.grid.addWidget(_links(links), grid_row, 5)
            else:
                self.grid.addWidget(_text("—"), grid_row, 5)
            grid_row += 1
        if not shown:
            self.grid.addWidget(divider(), grid_row, 0, 1, len(COLUMNS))
            empty = "No transactions in this period." if self.current_address() else "No address to show."
            self.grid.addWidget(_text(empty, f"font-size: 13px; color: {MUTED};"), grid_row + 1, 0, 1, len(COLUMNS))
        self._show_pager(len(rows), page_count, per_page)
        self._show_older()

    def rows_per_page(self) -> int:
        return ROWS_PER_PAGE

    def _show_pager(self, total: int, page_count: int, per_page: int) -> None:
        self._clear(self.pager_layout)
        self.pager.setVisible(page_count > 1)
        if page_count <= 1:
            return
        first = (self.page - 1) * per_page + 1
        last = min(total, self.page * per_page)
        self.pager_layout.addWidget(_text(f"{first}–{last} of {total:,}", f"font-size: 12px; color: {MUTED};"))
        self.pager_layout.addSpacing(8)
        self.pager_layout.addWidget(self._page_button("‹", self.page - 1, enabled=self.page > 1))
        for number in page_numbers(page_count, self.page):
            if number is None:
                self.pager_layout.addWidget(_text("…", f"font-size: 13px; color: {MUTED};"))
            else:
                self.pager_layout.addWidget(self._page_button(str(number), number, current=number == self.page))
        self.pager_layout.addWidget(self._page_button("›", self.page + 1, enabled=self.page < page_count))

    def _page_button(self, text: str, page: int, enabled: bool = True, current: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("pageButton")
        button.setFixedSize(30, 28)
        button.setEnabled(enabled)
        button.setProperty("current", current)
        if current:
            button.setStyleSheet(
                f"background: #1C2A47; color: #B5F27A; font-weight: 600; border-color: #1C2A47;"
            )
        button.clicked.connect(lambda _checked=False, page=page: self._go_to(page))
        return button

    def _show_older(self) -> None:
        self._clear(self.older_layout)
        address = self.current_address()
        if address:
            links = {site: address_link(site, address) for site in ("EU", "NZ")}
            prefix = "Older history on the explorer: &nbsp; " if self.span else "Older transactions: &nbsp; "
            older = _links(links, prefix)
            older.setStyleSheet(f"font-size: 12px; color: {MUTED};")
            self.older_layout.addWidget(older)
