"""The app's screens."""

from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from guiwallet.core.labels import MAX_LABEL_LENGTH

from guiwallet.core.compound import RunningCompound
from guiwallet.core.daemon import WalletSnapshot
from guiwallet.core.nodes import OFFICIAL_RPC, is_official
from guiwallet.core.send import SendRequest
from guiwallet.core.settings import Settings, default_keys_file, save_settings
from guiwallet.core.tools import sending_switched_off
from guiwallet.core.validation import (
    ADDRESS_PREFIXES,
    InvalidInput,
    validate_address,
    validate_amount,
    validate_daemon_address,
    validate_node_address,
)
from guiwallet.core.wallet import WalletBalance, format_htn, format_htn_cents, format_htn_exact
from guiwallet.ui.widgets import (
    HIDDEN_AMOUNT,
    ICON_GREEN,
    ICON_GREY,
    CopyButton,
    CopyIconButton,
    QrCode,
    address_text,
    card,
    cycle_icon,
    divider,
    label,
    official_rpc_hint,
    page_layout,
    shorten_address,
)

COMPOUND_RUNNING_STYLE = "background: #18243F; border-color: #8BE83A;"

UNIT_GREY = "#8D98B0"  # The same grey as the sidebar's "HTN".
CLOSE_ACTIONS = {"ask": "Ask me each time", "exit": "Exit the app", "tray": "Minimize to tray"}
HISTORY_SOURCES = {"EU": "Explorer EU", "NZ": "Explorer NZ"}


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()  # deleteLater alone can leave it visible for a moment.
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def _scroll_area(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    return scroll


SEND_TAB_WIDTH = 170  # Both tabs, so that they match.
ALL_SPENDABLE_STYLE = "color: #B5F27A; font-weight: 600; background: #0A0F1C; border-color: #8BE83A;"


class LabelEditor(QWidget):
    """The label under an address: grey text, or "Add a label…". Click to edit; Enter saves, Esc cancels."""

    changed = Signal(str, str)  # Address, new label ("" removes it).

    def __init__(self, address: str, name: str) -> None:
        super().__init__()
        self.address = address
        self.name = name
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.text = QLabel()
        self.text.setCursor(Qt.CursorShape.PointingHandCursor)
        self.text.setToolTip("Click to change the label. Only kept in this app.")
        self.text.mousePressEvent = lambda _event: self.edit()
        row.addWidget(self.text)
        self.box = QLineEdit()
        self.box.setObjectName("labelEdit")
        self.box.setMaxLength(MAX_LABEL_LENGTH)
        self.box.setFixedWidth(180)
        self.box.returnPressed.connect(self._save)
        self.box.editingFinished.connect(self._focus_left)
        row.addWidget(self.box)
        self.hint = label("Enter saves  ·  Esc cancels", "hint")
        row.addWidget(self.hint)
        row.addStretch()
        self._show_text()

    def _show_text(self) -> None:
        if self.name:
            self.text.setText(self.name)
            self.text.setObjectName("addressLabel")
        else:
            self.text.setText("Add a label…")
            self.text.setObjectName("addressLabelEmpty")
        self.text.style().unpolish(self.text)
        self.text.style().polish(self.text)
        self.text.show()
        self.box.hide()
        self.hint.hide()
        self.setMinimumWidth(0)

    def edit(self) -> None:
        self.box.setText(self.name)
        self.text.hide()
        self.box.show()
        self.hint.show()
        # Room for the box and the hint beside it, so the row widens instead of overlapping them.
        self.setMinimumWidth(self.box.width() + 10 + self.hint.sizeHint().width())
        self.box.setFocus()
        self.box.selectAll()

    def editing(self) -> bool:
        return self.box.isVisible()

    def _save(self) -> None:
        if not self.box.isVisible():
            return
        new_name = self.box.text()
        self.box.hide()  # Before the signal, so editingFinished doesn't save twice.
        if new_name != self.name:
            self.changed.emit(self.address, new_name)
        self._show_text()

    def _focus_left(self) -> None:
        if self.box.isVisible() and not self.box.hasFocus():
            self._save()  # Clicking elsewhere keeps what was typed.

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.box.isVisible():
            self.box.hide()
            self._show_text()
            return
        super().keyPressEvent(event)

    def set_name(self, name: str) -> None:
        self.name = name
        if not self.editing():
            self._show_text()


class WalletPage(QWidget):
    """The balance, and a table of addresses, each with a copy button and an auto-compound button."""

    COLUMNS = ("Addresses", "Pending Bal.", "Total Bal.", "UTXOs", "Compound")

    compound_clicked = Signal(str)  # The address whose auto-compound button was pressed.
    add_address_clicked = Signal()
    label_changed = Signal(str, str)  # Address, new label.

    def __init__(self) -> None:
        super().__init__()
        self.snapshot: WalletSnapshot | None = None
        self.labels: dict[str, str] = {}  # Shared with the main window, which saves them.
        self.label_editors: dict[str, LabelEditor] = {}
        self.extra_addresses: list[str] = []  # Handed out but holding nothing yet, such as a new one.
        self.balances_hidden = False
        self.rows: dict[str, tuple[QLabel, QLabel, QLabel]] = {}
        self.compound_buttons: dict[str, QPushButton] = {}
        self.compounds: list[RunningCompound] = []
        layout = page_layout(self, "Wallet", "Your balance and addresses")

        self.available = label("—", "cardValue")
        self.pending = label("—", "cardValue")
        self.coin_count = label("—", "cardValue")
        cards = QHBoxLayout()
        cards.setSpacing(14)
        cards.addWidget(self._stat_card("Available", self.available))
        cards.addWidget(self._stat_card("Pending", self.pending))
        cards.addWidget(self._stat_card("UTXOs", self.coin_count))
        layout.addLayout(cards)
        layout.addSpacing(12)

        panel = card()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(20, 14, 12, 14)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(10)
        self.table = QGridLayout()
        self.table.setHorizontalSpacing(28)
        self.table.setVerticalSpacing(9)
        self.table.setColumnStretch(0, 1)
        content_layout.addLayout(self.table)
        self.message = label("", "muted")
        self.message.setWordWrap(True)
        content_layout.addWidget(self.message)
        content_layout.addStretch()
        panel_layout.addWidget(_scroll_area(content))
        layout.addWidget(panel, stretch=1)

        self._add_column_headings()
        self.show_message("htnwallet isn't running. Press Start to run it.")

        # Beside the title, top right; placed in resizeEvent.
        self.add_button = QPushButton("+  Add new address", self)
        self.add_button.setFixedWidth(170)
        self.add_button.setToolTip("Make the next receiving address of this wallet")
        self.add_button.clicked.connect(self.add_address_clicked)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.add_button.move(self.width() - 32 - self.add_button.width(), 34)

    @staticmethod
    def _stat_card(title: str, value: QLabel) -> QFrame:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(label(title, "sectionLabel"))
        layout.addWidget(value)
        return frame

    def _add_column_headings(self) -> None:
        for column, name in enumerate(self.COLUMNS):
            alignment = Qt.AlignmentFlag.AlignLeft if column == 0 else Qt.AlignmentFlag.AlignRight
            self.table.addWidget(label(name, "sectionLabel"), 0, column, alignment)
        self.table.addWidget(divider(), 1, 0, 1, len(self.COLUMNS))

    def _set_addresses(self, addresses: list[str]) -> None:
        """Lay out a row per address. Only needed when the list of addresses changes."""
        clear_layout(self.table)
        self._add_column_headings()
        self.rows = {}
        self.compound_buttons = {}
        self.label_editors = {}
        row = 2
        for address in addresses:
            cell = QWidget()
            cell_column = QVBoxLayout(cell)
            cell_column.setContentsMargins(0, 0, 0, 0)
            cell_column.setSpacing(2)
            cell_layout = QHBoxLayout()
            cell_layout.setSpacing(6)
            text = label(shorten_address(address), "address")
            text.setToolTip(address)
            cell_layout.addWidget(text)
            cell_layout.addWidget(CopyIconButton(address))
            cell_layout.addStretch()
            cell_column.addLayout(cell_layout)
            editor = LabelEditor(address, self.labels.get(address, ""))
            editor.changed.connect(self.label_changed)
            cell_column.addWidget(editor)
            self.label_editors[address] = editor
            self.table.addWidget(cell, row, 0)
            values = (label("", "amount"), label("", "amount"), label("", "amount"))
            for column, value in enumerate(values, start=1):
                self.table.addWidget(value, row, column, Qt.AlignmentFlag.AlignRight)
            self.rows[address] = values
            button = QPushButton()
            button.setObjectName("compound")
            button.setFixedSize(38, 34)
            button.setIconSize(QSize(18, 18))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, address=address: self.compound_clicked.emit(address))
            self.table.addWidget(button, row, len(self.COLUMNS) - 1, Qt.AlignmentFlag.AlignRight)
            self.compound_buttons[address] = button
            self.table.addWidget(divider(), row + 1, 0, 1, len(self.COLUMNS))
            row += 2
        self._show_compounds()

    def show_compounds(self, compounds: list[RunningCompound]) -> None:
        """Mark each address that has an auto-compound running, wherever it was started."""
        self.compounds = compounds
        self._show_compounds()

    def compound_for(self, address: str) -> RunningCompound | None:
        """The auto-compound running for this address, preferring one this app started."""
        covering = [compound for compound in self.compounds if compound.covers(address, self.rows)]
        covering.sort(key=lambda compound: not compound.started_here)
        return covering[0] if covering else None

    def _show_compounds(self) -> None:
        for address, button in self.compound_buttons.items():
            compound = self.compound_for(address)
            if compound is None:
                button.setIcon(cycle_icon(ICON_GREY))
                button.setStyleSheet("")
                button.setToolTip("Start auto-compound")
                continue
            button.setIcon(cycle_icon(ICON_GREEN))
            button.setStyleSheet(COMPOUND_RUNNING_STYLE)
            target = "itself" if compound.to_address in (None, address) else shorten_address(compound.to_address)
            where = "" if compound.started_here else " (started outside this app)"
            button.setToolTip(
                f"Auto-compound running{where}: to {target} every {compound.interval} seconds. Click to stop."
            )

    def show_message(self, message: str) -> None:
        """Replace all figures with a message, so an old balance is never left on screen."""
        self.snapshot = None
        for value in (self.available, self.pending, self.coin_count):
            value.setText("—")
            value.setToolTip("")
        self._set_addresses([])
        self.message.setText(message)
        self.message.show()

    def set_balances_hidden(self, hidden: bool) -> None:
        self.balances_hidden = hidden
        if self.snapshot:
            self.show_snapshot(self.snapshot)

    def show_snapshot(self, snapshot: WalletSnapshot) -> None:
        self.snapshot = snapshot
        balance = snapshot.balance
        self._show_amount(self.available, balance.available)
        self._show_amount(self.pending, balance.pending)
        if snapshot.coins:
            self.coin_count.setText(f"{snapshot.coins.total:,}")

        # The numbers change in place, so that nothing moves on screen during a refresh.
        addresses = [entry.address for entry in balance.addresses]
        empty = [address for address in self.extra_addresses if address not in addresses]
        editing = any(editor.editing() for editor in self.label_editors.values())
        if addresses + empty != list(self.rows) and not editing:
            self._set_addresses(addresses + empty)
        self.message.setText("No funds in this wallet yet.")
        self.message.setVisible(not addresses and not empty)
        for address in empty:
            if address in self.rows:
                pending, total, utxos = self.rows[address]
                self._show_amount(pending, Decimal(0))
                self._show_amount(total, Decimal(0))
                utxos.setText("0" if snapshot.coins else "—")
        for entry in balance.addresses:
            if entry.address not in self.rows:
                continue  # Only while a label is being edited: the list catches up afterwards.
            pending, total, utxos = self.rows[entry.address]
            self._show_amount(pending, entry.pending)
            self._show_amount(total, entry.available + entry.pending)
            count = snapshot.coins.by_address.get(entry.address, 0) if snapshot.coins else 0
            utxos.setText(f"{count:,}" if snapshot.coins else "—")

    def show_labels(self) -> None:
        for address, editor in self.label_editors.items():
            editor.set_name(self.labels.get(address, ""))

    def _show_amount(self, widget: QLabel, amount: Decimal) -> None:
        # HTN follows each figure, small and grey like the sidebar's Current Balance; bigger on the cards.
        unit_size = 13 if widget.objectName() == "cardValue" else 11
        unit = f'<span style="font-size: {unit_size}px; color: {UNIT_GREY};">&nbsp;HTN</span>'
        if self.balances_hidden:
            widget.setText(HIDDEN_AMOUNT + unit)
            widget.setToolTip("")
            return
        widget.setText(format_htn(amount) + unit)
        widget.setToolTip(f"{format_htn_exact(amount)} HTN")  # The whole figure, on hover.


class SendPage(QWidget):
    """The send form, with a tab for each kind of send. Checking what was typed is all it does: the
    confirmation does the rest."""

    review_requested = Signal(object)

    def __init__(self, network: str) -> None:
        super().__init__()
        self.network = network
        self.from_addresses: list[str] = []
        self.receive_addresses: list[str] = []
        self.labels: dict[str, str] = {}  # Shared with the main window.
        self.spendable: dict[str, Decimal] = {}
        self.total_spendable: Decimal | None = None
        self.balances_hidden = False
        switched_off = sending_switched_off()
        self.switched_off = switched_off
        layout = page_layout(self, "Send HTN", "Confirm all details before sending. Transactions cannot be reversed.")

        tabs = QFrame()
        tabs.setObjectName("sendTabs")
        tabs_row = QHBoxLayout(tabs)
        tabs_row.setContentsMargins(4, 4, 4, 4)
        tabs_row.setSpacing(4)
        self.own_tab = QPushButton("Between my wallets")
        self.other_tab = QPushButton("To another wallet")
        self.tab_group = QButtonGroup(self)  # Nothing is chosen at first: the user has to pick one.
        for tab in (self.own_tab, self.other_tab):
            tab.setObjectName("sendTab")
            tab.setCheckable(True)
            tab.setFixedWidth(SEND_TAB_WIDTH)
            tab.toggled.connect(self._tab_toggled)
            self.tab_group.addButton(tab)
            tabs_row.addWidget(tab)
        layout.addWidget(tabs, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(10)

        # Nothing below the tabs shows until one is chosen.
        self.form = QWidget()
        form_layout = QVBoxLayout(self.form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(0)
        panel = card()
        panel.setEnabled(not switched_off)
        grid = QGridLayout(panel)
        grid.setContentsMargins(22, 20, 22, 20)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(14)
        self.source = QComboBox()
        self.source.addItem("Any address (recommended)", None)
        self.source.currentIndexChanged.connect(self._source_changed)
        self.recipient = QLineEdit()
        self.recipient.setPlaceholderText(f"{ADDRESS_PREFIXES[network]}:...")
        self.recipient.textChanged.connect(self._clear_error)
        self.own_source = QComboBox()
        self.own_source.setPlaceholderText("Choose a wallet")
        self.own_source.currentIndexChanged.connect(self._source_changed)
        self.own_target = QComboBox()
        self.own_target.setPlaceholderText("Choose a wallet")
        self.own_target.currentIndexChanged.connect(self._clear_error)
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("0.00")
        self.amount.textChanged.connect(self._amount_changed)
        # What the chosen From can spend, under the list. "Any address" shows the whole wallet's.
        self.available = label("", "muted")
        self.available.setStyleSheet("font-size: 12px;")
        self.send_all = QPushButton("Send all")
        self.send_all.setObjectName("sendAll")
        self.send_all.setCheckable(True)
        self.send_all.toggled.connect(self._send_all_toggled)
        amount_row = QHBoxLayout()
        amount_row.addWidget(self.amount)
        amount_row.addWidget(self.send_all)
        grid.addWidget(label("From", "sectionLabel"), 0, 0)
        from_column = QVBoxLayout()
        from_column.setSpacing(6)
        from_column.addWidget(self.source)
        from_column.addWidget(self.own_source)
        from_column.addWidget(self.available)
        grid.addLayout(from_column, 0, 1)
        grid.addWidget(label("To", "sectionLabel"), 1, 0)
        grid.addWidget(self.recipient, 1, 1)
        grid.addWidget(self.own_target, 1, 1)
        grid.addWidget(label("Amount", "sectionLabel"), 2, 0)
        grid.addLayout(amount_row, 2, 1)
        grid.setColumnStretch(1, 1)
        form_layout.addWidget(panel)
        form_layout.addSpacing(14)

        actions = QHBoxLayout()
        hint = "Sending is switched off during the pilot." if switched_off else (
            "Your password is required on the next step."
        )
        self.hint = label(hint, "muted")
        actions.addWidget(self.hint)
        self.error = label("", "error")
        self.error.setWordWrap(True)
        self.error.hide()
        actions.addWidget(self.error, stretch=1)
        actions.addStretch()
        self.review = QPushButton("Review")
        self.review.setObjectName("primary")
        self.review.setEnabled(not switched_off)
        self.review.clicked.connect(self._review_clicked)
        actions.addWidget(self.review)
        form_layout.addLayout(actions)
        layout.addWidget(self.form)
        self.form.hide()
        layout.addStretch()

    def show_balance(self, balance: WalletBalance) -> None:
        """Take in what each address can spend, which only touches the lists when something changed."""
        self.spendable = {entry.address: entry.available for entry in balance.addresses}
        self.total_spendable = balance.available
        self.set_from_addresses([entry.address for entry in balance.addresses])
        self._refresh_own_lists()
        self._show_all_spendable()
        self._show_available()

    def set_receive_addresses(self, addresses: list[str]) -> None:
        """`balance` can leave out an address holding nothing, so `show-addresses` fills the gaps."""
        self.receive_addresses = addresses
        self._refresh_own_lists()

    def forget_wallet(self) -> None:
        """htnwallet can't be read, so nothing it said earlier can be relied on."""
        self.spendable = {}
        self.total_spendable = None
        self.receive_addresses = []
        self.source.setCurrentIndex(0)  # A different wallet may come next: none of these is chosen.
        self.set_from_addresses([])
        self._refresh_own_lists()
        self._show_all_spendable()
        self._show_available()

    def set_balances_hidden(self, hidden: bool) -> None:
        self.balances_hidden = hidden
        self._refresh_own_lists()
        self._show_all_spendable()
        self._show_available()

    def labels_changed(self) -> None:
        """Show changed labels in the From lists, keeping what's chosen."""
        addresses, self.from_addresses = self.from_addresses, None
        self.set_from_addresses(addresses or [])
        self._refresh_own_lists()

    def set_from_addresses(self, addresses: list[str]) -> None:
        """Offer the wallet's addresses. Only touches the list when they change, keeping the choice."""
        if addresses == self.from_addresses:
            return
        chosen = self.source.currentData()
        self.from_addresses = addresses
        listed = list(addresses)
        if chosen is not None and chosen not in listed:
            # The chosen address has dropped out of htnwallet's balance list, such as when its coins
            # were just compounded away. It stays chosen, now with nothing to spend, rather than
            # quietly becoming "Any address", which would spend the wallet's other addresses.
            listed.append(chosen)
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem("Any address (recommended)", None)
        for address in listed:
            self.source.addItem(address_text(address, self.labels), address)
            self.source.setItemData(self.source.count() - 1, address, Qt.ItemDataRole.ToolTipRole)
        self.source.setCurrentIndex(max(0, self.source.findData(chosen)))
        self.source.blockSignals(False)

    def set_network(self, network: str) -> None:
        self.network = network
        self.recipient.setPlaceholderText(f"{ADDRESS_PREFIXES[network]}:...")

    def show_error(self, message: str) -> None:
        self.hint.hide()
        self.error.setText(message)
        self.error.show()

    def _clear_error(self) -> None:
        self.error.hide()
        self.hint.show()

    def chosen_spendable(self) -> Decimal | None:
        """What the chosen From can spend: one address's, or the whole wallet's for "Any address".
        None when no From is chosen or the balance isn't known."""
        if self.own_tab.isChecked():
            address = self.own_source.currentData()
            return None if address is None else self.spendable.get(address, Decimal(0))
        address = self.source.currentData()
        if address is None:
            return self.total_spendable
        return self.spendable.get(address, Decimal(0))

    def _show_available(self) -> None:
        spendable = self.chosen_spendable()
        if spendable is None:
            self.available.hide()
            return
        figure = HIDDEN_AMOUNT if self.balances_hidden else format_htn_cents(spendable)
        self.available.setText(f"Available: {figure} HTN")
        self.available.show()
        self._check_amount()

    def _amount_problem(self) -> str | None:
        """Why the typed amount can't be sent from the chosen From, or None. Only checks the balance."""
        if self.send_all.isChecked():
            return None
        spendable = self.chosen_spendable()
        try:
            amount = Decimal(validate_amount(self.amount.text()))
        except InvalidInput:
            return None  # Not a usable amount yet; Review says what's wrong with it.
        if spendable is None:
            return None
        if amount > spendable:
            return "The amount is more than the available balance."
        if amount == spendable:
            return "The fee must come out of the balance too. To send everything, use Send all."
        return None

    def _check_amount(self) -> None:
        """Refuse an amount the From can't cover, as it's typed: the error shows and Review is off."""
        problem = self._amount_problem()
        if problem:
            self.show_error(problem)
        elif self.error.text() in self._balance_errors():
            self._clear_error()
        self.review.setEnabled(not self.switched_off and problem is None)

    @staticmethod
    def _balance_errors() -> tuple[str, ...]:
        return (
            "The amount is more than the available balance.",
            "The fee must come out of the balance too. To send everything, use Send all.",
        )

    def _amount_changed(self) -> None:
        self._clear_error()
        self._check_amount()

    def clear(self) -> None:
        """Empty the form, so that a send can't be repeated by accident. The user picks a tab again."""
        self.recipient.clear()
        self.amount.clear()
        self.send_all.setChecked(False)
        self.source.setCurrentIndex(0)
        self.own_source.setCurrentIndex(-1)
        self.own_target.setCurrentIndex(-1)
        self._clear_error()
        self.review.setEnabled(not self.switched_off)
        self.available.hide()
        self.tab_group.setExclusive(False)  # An exclusive group refuses to leave nothing chosen.
        self.own_tab.setChecked(False)
        self.other_tab.setChecked(False)
        self.tab_group.setExclusive(True)
        self.form.hide()

    def _tab_toggled(self, checked: bool) -> None:
        if not checked:
            return
        own = self.own_tab.isChecked()
        self.source.setVisible(not own)
        self.recipient.setVisible(not own)
        self.own_source.setVisible(own)
        self.own_target.setVisible(own)
        # An amount or Send all chosen for one kind of send must not carry over to the other.
        self.send_all.setChecked(False)
        self.amount.clear()
        self._clear_error()
        self._show_available()
        self.form.show()

    def _refresh_own_lists(self) -> None:
        addresses = sorted(set(self.spendable) | set(self.receive_addresses))
        self._fill_own_list(self.own_source, addresses)
        chosen = self.own_source.currentData()
        # Sending to the same address would only pay a fee. If To was that address, it empties
        # rather than moving on to another one the user didn't pick.
        self._fill_own_list(self.own_target, [address for address in addresses if address != chosen])
        self.own_target.setPlaceholderText(
            "Choose a wallet" if self.own_target.count() else "No other address in this wallet"
        )

    def _fill_own_list(self, combo: QComboBox, addresses: list[str]) -> None:
        rows = [
            (f"{address_text(address, self.labels)}   ·   {self._amount_text(address)} HTN", address)
            for address in addresses
        ]
        if rows == [(combo.itemText(index), combo.itemData(index)) for index in range(combo.count())]:
            return  # Rebuilding would close a list the user has open.
        chosen = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for row, (text, address) in enumerate(rows):
            combo.addItem(text, address)
            combo.setItemData(row, address, Qt.ItemDataRole.ToolTipRole)
        combo.setCurrentIndex(combo.findData(chosen) if chosen else -1)
        combo.blockSignals(False)

    def _amount_text(self, address: str) -> str:
        if self.balances_hidden:
            return HIDDEN_AMOUNT
        return format_htn_exact(self.spendable.get(address, Decimal(0)))

    def _source_changed(self) -> None:
        self._refresh_own_lists()
        self._show_all_spendable()
        self._clear_error()
        self._show_available()

    def _show_all_spendable(self) -> None:
        """Say how much Send all covers: what the From address holds, or all of it for any address."""
        if not self.send_all.isChecked():
            return
        if self.own_tab.isChecked():
            address = self.own_source.currentData()
            known = address is not None
        else:
            address = self.source.currentData()
            known = address is not None or self.total_spendable is not None
        if not known:
            text = "All spendable HTN"
        elif self.balances_hidden:
            text = f"All spendable: {HIDDEN_AMOUNT} HTN"
        elif address is None:
            text = f"All spendable: {format_htn_exact(self.total_spendable)} HTN"
        else:
            text = f"All spendable: {self._amount_text(address)} HTN"
        # Every balance refresh passes through here, which mustn't wipe an error being shown.
        self.amount.blockSignals(True)
        self.amount.setText(text)
        self.amount.blockSignals(False)

    def _send_all_toggled(self, checked: bool) -> None:
        self.amount.clear()
        self.amount.setEnabled(not checked)
        self.amount.setStyleSheet(ALL_SPENDABLE_STYLE if checked else "")
        self._show_all_spendable()
        self._clear_error()
        self._check_amount()

    def _review_clicked(self) -> None:
        if self.tab_group.checkedButton() is None:
            return
        try:
            if self.own_tab.isChecked():
                from_address = self.own_source.currentData()
                if from_address is None:
                    raise InvalidInput("Choose the wallet to send from.")
                if self.own_target.currentData() is None:
                    raise InvalidInput("Choose the wallet to send to.")
                to_address = validate_address(self.own_target.currentData(), self.network)
            else:
                from_address = self.source.currentData()
                to_address = validate_address(self.recipient.text(), self.network)
            amount = None if self.send_all.isChecked() else validate_amount(self.amount.text())
            if problem := self._amount_problem():
                raise InvalidInput(problem)
        except InvalidInput as error:
            self.show_error(str(error))
            return
        self._clear_error()
        self.review_requested.emit(SendRequest(to_address, amount, from_address))


class _AddressRow(QFrame):
    """One address with a copy button. Clicking anywhere else on it shows its QR code."""

    clicked = Signal()

    def mousePressEvent(self, _event) -> None:
        self.clicked.emit()


class ReceivePage(QWidget):
    """A QR code for the selected address, and every address with a copy button."""

    def __init__(self, network: str) -> None:
        super().__init__()
        self.network = network
        self.addresses: list[str] = []
        self.current_address: str | None = None
        self.rows_by_address: dict[str, QFrame] = {}
        layout = page_layout(self, "Receive HTN", "Share an address, or let the other person scan its QR code")

        body = QHBoxLayout()
        body.setSpacing(18)

        content = QWidget()
        self.rows = QVBoxLayout(content)
        self.rows.setContentsMargins(0, 0, 8, 0)
        self.rows.setSpacing(8)
        self.rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        body.addWidget(_scroll_area(content), stretch=1)

        qr_panel = card()
        qr_layout = QVBoxLayout(qr_panel)
        qr_layout.setContentsMargins(18, 18, 18, 18)
        self.qr = QrCode()
        qr_layout.addWidget(self.qr, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.selected = label("", "address")
        self.selected.setFixedWidth(self.qr.width())
        self.selected.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        qr_layout.addWidget(self.selected)
        qr_layout.addStretch()
        self.qr_panel = qr_panel
        body.addWidget(qr_panel, alignment=Qt.AlignmentFlag.AlignTop)

        layout.addLayout(body, stretch=1)
        self.show_message("htnwallet isn't running. Press Start to run it.")

    def show_message(self, message: str) -> None:
        self.addresses = []
        self.current_address = None
        self.rows_by_address = {}
        clear_layout(self.rows)
        self.rows.addWidget(label(message, "muted"))
        self.qr.set_text(None)
        self.selected.setText("")
        self.qr_panel.setVisible(False)

    def show_addresses(self, addresses: list[str]) -> None:
        valid = []
        for address in addresses:
            try:
                valid.append(validate_address(address, self.network))
            except InvalidInput:
                continue  # Never offer something that isn't an address for this network.
        if not valid:
            self.show_message("This wallet has no receiving addresses yet. Use Add new address on the Wallet page.")
            return
        if valid == self.addresses:
            return

        previously_selected = self.current_address
        self.addresses = valid
        self.rows_by_address = {}
        clear_layout(self.rows)
        for address in valid:
            self.rows.addWidget(self._address_row(address))
        self.qr_panel.setVisible(True)
        self._select(previously_selected if previously_selected in valid else valid[0])

    def _address_row(self, address: str) -> QFrame:
        row = _AddressRow()
        row.setObjectName("card")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.clicked.connect(lambda: self._select(address))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 8, 10, 8)
        text = label(shorten_address(address), "address")
        text.setToolTip(address)
        layout.addWidget(text)
        layout.addStretch()
        layout.addWidget(CopyButton(address))
        self.rows_by_address[address] = row
        return row

    def _select(self, address: str) -> None:
        self.current_address = address
        self.qr.set_text(address)
        # An address has no spaces to wrap at, so it's split into lines for display only.
        self.selected.setText("\n".join(address[i : i + 34] for i in range(0, len(address), 34)))
        for known, row in self.rows_by_address.items():
            row.setObjectName("selectedCard" if known == address else "card")
            row.style().unpolish(row)
            row.style().polish(row)


class SettingsPage(QWidget):
    """Where the wallet tools and wallet file are, and how to connect."""

    saved = Signal()
    wizard_requested = Signal()

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        layout = page_layout(self, "Settings", "The app looks for htnwallet and htnctl beside itself first, then in the Main folder")

        panel = card()
        grid = QGridLayout(panel)
        grid.setContentsMargins(22, 18, 22, 18)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)

        self.tools_folder = QLineEdit(settings.tools_folder)
        self.tools_folder.setPlaceholderText("Folder containing htnwallet.exe and htnctl.exe")
        browse_tools = QPushButton("Browse…")
        browse_tools.clicked.connect(self._choose_tools_folder)

        self.default_keys_file = str(default_keys_file(settings.network))
        self.keys_file = QLineEdit(settings.keys_file or self.default_keys_file)
        self.keys_file.setPlaceholderText(self.default_keys_file)
        self.keys_file.textChanged.connect(self.keys_file.setToolTip)
        self.keys_file.setToolTip(self.keys_file.text())
        self.keys_file.setCursorPosition(0)  # Long paths show their start, not their end.
        self.browse_keys = QPushButton("Browse…")
        self.browse_keys.clicked.connect(self._choose_keys_file)

        self.node_address = QLineEdit(settings.node_address)
        self.node_address.setPlaceholderText("address:port")
        self.use_official = QPushButton("Use Official RPC")
        self.use_official.clicked.connect(lambda: self.node_address.setText(OFFICIAL_RPC))
        self.node_hint = official_rpc_hint()
        self.node_address.textChanged.connect(lambda text: self.node_hint.setVisible(is_official(text)))
        self.node_hint.setVisible(is_official(settings.node_address))
        self.daemon_address = QLineEdit(settings.daemon_address)
        self.daemon_address.setPlaceholderText("127.0.0.1:4282")
        self.close_action = QComboBox()
        for value, text in CLOSE_ACTIONS.items():
            self.close_action.addItem(text, value)
        self.history_source = QComboBox()
        for value, text in HISTORY_SOURCES.items():
            self.history_source.addItem(text, value)
        self.history_source.setCurrentIndex(max(0, self.history_source.findData(settings.history_source)))
        self.show_debug = QCheckBox("Show the htnwallet command and its replies in the send window")
        self.show_debug.setToolTip("For checking what's being sent to htnwallet. The password is always masked.")
        self.show_debug.setChecked(settings.show_debug)

        fields = [
            ("Main folder", self.tools_folder, browse_tools),
            ("Wallet file", self.keys_file, self.browse_keys),
            ("Node (address:port)", self.node_address, self.use_official),
            ("", self.node_hint, None),
            ("htnwallet (address:port)", self.daemon_address, None),
            ("Closing the window", self.close_action, None),
            ("Transaction History source", self.history_source, None),
            ("Debug details", self.show_debug, None),
        ]
        for row, (name, field, button) in enumerate(fields):
            if name:
                grid.addWidget(label(name, "sectionLabel"), row, 0)
            grid.addWidget(field, row, 1, 1, 1 if button else 2)
            if button:
                grid.addWidget(button, row, 2)
        grid.setColumnStretch(1, 1)
        layout.addWidget(panel)
        layout.addSpacing(10)

        actions = QHBoxLayout()
        self.status = label("", "muted")
        actions.addWidget(self.status)
        actions.addStretch()
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        actions.addWidget(save)
        layout.addLayout(actions)
        layout.addSpacing(10)

        wizard = card()
        wizard_row = QHBoxLayout(wizard)
        wizard_row.setContentsMargins(22, 16, 22, 16)
        wizard_row.setSpacing(16)
        wizard_text = QVBoxLayout()
        wizard_text.setSpacing(3)
        wizard_text.addWidget(label("Setup Wizard", "sectionLabel"))
        wizard_note = label(
            "Create a new wallet, restore one, or switch to another existing wallet or running htnwallet.", "muted"
        )
        wizard_note.setWordWrap(True)
        wizard_text.addWidget(wizard_note)
        wizard_row.addLayout(wizard_text, 1)
        self.wizard_button = QPushButton("Open Setup Wizard")
        self.wizard_button.setFixedWidth(150)
        self.wizard_button.clicked.connect(self.wizard_requested)
        wizard_row.addWidget(self.wizard_button)
        layout.addWidget(wizard)
        # The version is shown in the title bar.
        layout.addStretch()
        self.show_close_action()
        self.set_htnwallet_running(False)

    def set_htnwallet_running(self, running: bool) -> None:
        """These only apply when htnwallet starts, so they're locked while it's running."""
        for field in (self.keys_file, self.browse_keys, self.node_address, self.use_official, self.daemon_address):
            field.setEnabled(not running)
        if running:
            self.status.setText(
                "Stop htnwallet to change the greyed-out settings. "
                "A running htnwallet may be using a different wallet file."
            )
        else:
            self._show_wallet_file_note()

    def _show_wallet_file_note(self) -> None:
        path = Path(self.keys_file.text().strip() or self.default_keys_file)
        self.status.setText("" if path.is_file() else "No wallet file found at this location.")

    def show_settings(self) -> None:
        """Show the saved settings again, after the Setup Wizard changed them."""
        self.tools_folder.setText(self.settings.tools_folder)
        self.default_keys_file = str(default_keys_file(self.settings.network))
        self.keys_file.setText(self.settings.keys_file or self.default_keys_file)
        self.keys_file.setCursorPosition(0)
        self.node_address.setText(self.settings.node_address)
        self.daemon_address.setText(self.settings.daemon_address)
        self.history_source.setCurrentIndex(max(0, self.history_source.findData(self.settings.history_source)))
        self.show_debug.setChecked(self.settings.show_debug)
        self.show_close_action()

    def show_close_action(self) -> None:
        self.close_action.setCurrentIndex(self.close_action.findData(self.settings.close_action))

    def _choose_tools_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder containing htnwallet and htnctl")
        if folder:
            self.tools_folder.setText(folder)

    def _choose_keys_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Wallet file", "", "Wallet files (*.json);;All files (*)")
        if path:
            self.keys_file.setText(path)
            self.keys_file.setCursorPosition(0)
            self._show_wallet_file_note()

    def _save(self) -> None:
        try:
            node_address = validate_node_address(self.node_address.text() or Settings().node_address)
            daemon_address = validate_daemon_address(self.daemon_address.text() or Settings().daemon_address)
        except InvalidInput as error:
            self.status.setText(str(error))
            return
        self.settings.tools_folder = self.tools_folder.text().strip()
        # The default is saved as empty, so that htnwallet keeps choosing it for itself.
        keys_file = self.keys_file.text().strip()
        self.settings.keys_file = "" if keys_file == self.default_keys_file else keys_file
        self.settings.node_address = node_address
        self.settings.daemon_address = daemon_address
        self.settings.close_action = self.close_action.currentData()
        self.settings.history_source = self.history_source.currentData()
        self.settings.show_debug = self.show_debug.isChecked()
        try:
            save_settings(self.settings)
        except OSError as error:
            self.status.setText(f"Could not save settings: {error.strerror}")
            return
        self.status.setText("Saved.")
        self.saved.emit()
