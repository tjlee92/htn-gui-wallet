"""The Add new address window: an optional label, then the new address with a copy button."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

from guiwallet.core.labels import MAX_LABEL_LENGTH
from guiwallet.ui.compound_dialog import BUTTON_WIDTH, _FramedDialog
from guiwallet.ui.widgets import CopyIconButton, label


class AddAddressDialog(_FramedDialog):
    add_requested = Signal(str)  # The label typed, possibly empty.

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, "Add new address")
        self.setFixedWidth(640)  # Wide enough for a whole address on one line.
        self.intro = label("htnwallet makes the next address of this wallet. No password is needed.", "subtitle")
        self.intro.setWordWrap(True)
        self.layout_.addWidget(self.intro)
        self.layout_.addSpacing(4)
        self.label_title = label("Label (optional)", "subtitle")
        self.layout_.addWidget(self.label_title)
        self.label_box = QLineEdit()
        self.label_box.setMaxLength(MAX_LABEL_LENGTH)
        self.label_box.setPlaceholderText("For example: Exchange deposits")
        self.layout_.addWidget(self.label_box)
        self.label_note = label("Only for you: kept in this app, never sent anywhere. You can change it later.", "muted")
        self.label_note.setWordWrap(True)
        self.layout_.addWidget(self.label_note)
        self.layout_.addSpacing(4)
        self.warning = label("Addresses can't be removed once made.", "note")
        self.warning.setWordWrap(True)
        self.layout_.addWidget(self.warning)

        # Shown once the address exists.
        self.result_name = label("", "subtitle")
        self.result_row = QWidget()
        row = QHBoxLayout(self.result_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.result_address = label("", "fullAddress")
        self.result_address.setWordWrap(True)
        self.result_address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.result_address, 1)
        self.copy_button = CopyIconButton("")
        row.addWidget(self.copy_button, 0, Qt.AlignmentFlag.AlignTop)
        self.result_note = label("It's now in the list on the Wallet page, and on the Receive page.", "muted")
        self.result_note.setWordWrap(True)
        for widget in (self.result_name, self.result_row, self.result_note):
            self.layout_.addWidget(widget)
            widget.hide()

        self.cancel_button, self.add_button = self._buttons("Cancel", "Add")
        self.add_button.clicked.connect(self._add_clicked)
        self.done_button = QPushButton("Done")
        self.done_button.setObjectName("primary")
        self.done_button.setFixedWidth(BUTTON_WIDTH)
        self.done_button.setAutoDefault(False)
        self.done_button.clicked.connect(self.accept)
        # The buttons' row is the dialog's last layout; Done takes the place of Cancel and Add.
        self.layout_.itemAt(self.layout_.count() - 1).layout().addWidget(self.done_button)
        self.done_button.hide()
        self.label_box.returnPressed.connect(self._add_clicked)
        self.label_box.setFocus()

    def _add_clicked(self) -> None:
        if self.busy or not self.add_button.isVisible():
            return
        self.busy = True
        self.error.hide()
        for widget in (self.label_box, self.cancel_button, self.add_button):
            widget.setEnabled(False)
        self.add_button.setText("Adding…")
        self.add_requested.emit(self.label_box.text())

    def show_error(self, message: str) -> None:
        super().show_error(message)
        for widget in (self.label_box, self.cancel_button, self.add_button):
            widget.setEnabled(True)
        self.add_button.setText("Add")

    def show_added(self, address: str, name: str) -> None:
        self.busy = False
        self.title_label().setText("Address added")
        self.title_label().setObjectName("resultGood")
        self.title_label().style().unpolish(self.title_label())
        self.title_label().style().polish(self.title_label())
        for widget in (self.intro, self.label_title, self.label_box, self.label_note, self.warning,
                       self.cancel_button, self.add_button, self.error):
            widget.hide()
        self.result_name.setText(name)
        self.result_name.setVisible(bool(name))
        self.result_address.setText(address)
        self.copy_button.text_to_copy = address
        self.result_row.show()
        self.result_note.show()
        self.done_button.show()
        self.done_button.setFocus()
        self.adjustSize()

    def title_label(self):
        return self.layout_.itemAt(0).widget()
