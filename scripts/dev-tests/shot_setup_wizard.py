"""Drives the real Setup Wizard against the fake htnwallet, on its own port and its own folders.

Nothing here touches a real wallet: APPDATA and LOCALAPPDATA point into a scratch folder, the tools
folder is the fake wallet's, and the daemon port is 47951. The only htnwallet this script ends is the
fake one it started on that port.
"""
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
SCRATCH = Path(os.environ.get("WIZARD_SCRATCH", HERE / "wizard-scratch"))
shutil.rmtree(SCRATCH, ignore_errors=True)
(SCRATCH / "appdata").mkdir(parents=True)
(SCRATCH / "localappdata").mkdir(parents=True)
os.environ["APPDATA"] = str(SCRATCH / "appdata")
os.environ["LOCALAPPDATA"] = str(SCRATCH / "localappdata")
OUT = Path(os.environ.get("WIZARD_SHOTS", SCRATCH / "shots"))
OUT.mkdir(parents=True, exist_ok=True)

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from guiwallet.core.daemon import close_daemon, port_is_open
from guiwallet.core.settings import Settings, default_keys_file
from guiwallet.core.setup import INVALID_PHRASE
from guiwallet.ui.main_window import MainWindow, State
from guiwallet.ui.setup_wizard import CONNECT, MISMATCH, SAVED, START, STARTED, ExistingWalletDialog, SetupWizard
from guiwallet.ui.theme import app_icon, apply_theme

FAKE = HERE / "fakewallet" / "bin"
PORT = 47951
PHRASE = (
    "abandon ability able about above absent absorb abstract absurd abuse access accident "
    "account accuse achieve acid acoustic acquire across act action actor actress actual"
)
app = QApplication(sys.argv)
apply_theme(app)
app.setWindowIcon(app_icon())
app.setQuitOnLastWindowClosed(False)
results: dict[str, bool] = {}


def wait(condition, seconds=15):
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return condition()


def settle(seconds=0.3):
    wait(lambda: False, seconds)


def shot(widget, name):
    settle(0.2)
    widget.grab().save(str(OUT / name))


def fresh_settings() -> Settings:
    return Settings(tools_folder=str(FAKE), daemon_address=f"127.0.0.1:{PORT}")


def open_wizard(settings, parent=None, owns=False) -> SetupWizard:
    wizard = SetupWizard(parent, settings, app_owns_daemon=owns)
    wizard.show()
    settle()
    return wizard


def answer_existing_dialog(accept: bool):
    """ExistingWalletDialog runs its own loop; press its button from a timer."""
    def press():
        for widget in app.topLevelWidgets():
            if isinstance(widget, ExistingWalletDialog) and widget.isVisible():
                results["warning dialog names the old file"] = "keys.json" in "".join(
                    label.text() for label in widget.findChildren(type(widget.error))
                )
                widget.grab().save(str(OUT / "6-existing-warning.png"))
                text = "Continue" if accept else "Cancel"
                next(b for b in widget.findChildren(QPushButton) if b.text() == text).click()
                return
        QTimer.singleShot(100, press)

    QTimer.singleShot(200, press)


default_file = default_keys_file("mainnet")
try:
    # ----- 1. Create a new wallet -----
    settings = fresh_settings()
    w = open_wizard(settings)
    # This PC may run a real htnwallet; then Use an existing wallet is preselected, as it should be.
    results["welcome: preselects what was found"] = w.existing_card.is_checked() == bool(w.daemons)
    results["welcome: htnwallet found, Next enabled"] = w.welcome_next.isEnabled() and w.tools_row.isHidden()
    shot(w, "1-welcome.png")
    w.create_card.clicked.emit()
    w.welcome_next.click()
    results["create: password page"] = w.pages.currentWidget() is w.password_page and w.password_next.text() == "Create"
    w.password.setText("pw1")
    w.password_again.setText("pw2")
    results["create: mismatch blocks"] = w.password_match.text().strip() == ""
    w.password_next.click()
    results["create: mismatch error"] = w.password_page.error.text() == "The passwords don't match"
    w.password_again.setText("pw1")
    results["create: match tick"] = "match" in w.password_match.text()
    w.password_next.click()
    results["create: seed shown"] = wait(lambda: w.pages.currentWidget() is w.seed_page)
    results["create: 24 words from htnwallet"] = [l.text() for l in w.seed_labels] == PHRASE.split()
    results["create: wallet file made"] = default_file.is_file()
    results["create: passwords cleared"] = w.password.text() == "" and w.password_again.text() == ""
    results["create: Next waits for the tick"] = not w.seed_next.isEnabled()
    shot(w, "2-seed.png")
    w.copy_button.click()
    results["create: Copy words"] = QGuiApplication.clipboard().text() == PHRASE and "Copied" in w.copy_button.text()
    QGuiApplication.clipboard().clear()
    w.seed_written.setChecked(True)
    w.seed_next.click()
    results["create: check page"] = w.pages.currentWidget() is w.check_page
    for _name, box in w.check_rows:
        box.setText("wrong")
    next(b for b in w.check_page.findChildren(QPushButton) if b.text() == "Finish").click()
    results["create: wrong words, one message"] = w.check_page.error.text() == MISMATCH
    shot(w, "3-check-wrong.png")
    words = PHRASE.split()
    for (_name, box), position in zip(w.check_rows, w.check_positions):
        box.setText(words[position])
    next(b for b in w.check_page.findChildren(QPushButton) if b.text() == "Finish").click()
    results["create: finishes without starting htnwallet"] = w.action == SAVED and not w.isVisible()
    results["create: nothing listening after create"] = not port_is_open(PORT)
    results["create: default file kept as empty setting"] = settings.keys_file == ""
    results["create: words forgotten"] = w.words == []

    # ----- 2. Create again with a wallet in place: warning, then keys-2.json -----
    settings = fresh_settings()
    w = open_wizard(settings)
    results["second run: Use existing preselected when a wallet file is found"] = w.existing_card.is_checked()
    w.create_card.clicked.emit()
    answer_existing_dialog(accept=False)
    w.welcome_next.click()
    results["warning: Cancel stays on welcome"] = w.pages.currentWidget() is w.welcome
    answer_existing_dialog(accept=True)
    w.welcome_next.click()
    results["warning: Continue goes on, to keys-2.json"] = (
        w.pages.currentWidget() is w.password_page and w.target == default_file.with_name("keys-2.json")
    )
    w.reject()
    results["original wallet untouched"] = default_file.is_file()

    # ----- 3. Restore from a seed phrase, done page, Start over, Open wallet -----
    settings = fresh_settings()
    w = open_wizard(settings)
    w.restore_card.clicked.emit()
    answer_existing_dialog(accept=True)
    w.welcome_next.click()
    results["restore: source page"] = w.pages.currentWidget() is w.source_page
    w.source_htnwallet.clicked.emit()
    next(b for b in w.source_page.findChildren(QPushButton) if b.text() == "Next").click()
    w.phrase.setPlainText(PHRASE.replace("abuse", "abuze"))
    w.phrase_next.click()
    results["restore: password page says Restore"] = w.password_next.text() == "Restore"
    w.password.setText("pw")
    w.password_again.setText("pw")
    w.password_next.click()
    results["restore: bad phrase back on phrase page"] = wait(
        lambda: w.pages.currentWidget() is w.phrase_page and w.phrase_page.error.text() == INVALID_PHRASE
    )
    results["restore: no file left by a failure"] = not default_file.with_name("keys-2.json").exists()
    shot(w, "4-phrase-invalid.png")
    w.phrase.setPlainText(PHRASE)
    w.phrase_next.click()
    w.password.setText("pw")
    w.password_again.setText("pw")
    w.password_next.click()
    results["restore: done page"] = wait(lambda: w.pages.currentWidget() is w.done_page)
    results["restore: htnwallet started and ready"] = wait(lambda: "Ready" in w.done_status.text(), 30)
    shot(w, "5-done.png")
    restored = default_file.with_name("keys-2.json")
    results["restore: file made"] = restored.is_file()
    w.done_over.click()
    results["start over: back to source, file removed, daemon ended"] = (
        w.pages.currentWidget() is w.source_page and not restored.exists() and wait(lambda: not port_is_open(PORT), 10)
    )
    next(b for b in w.source_page.findChildren(QPushButton) if b.text() == "Next").click()
    w.phrase.setPlainText(PHRASE)
    w.phrase_next.click()
    w.password.setText("pw")
    w.password_again.setText("pw")
    w.password_next.click()
    wait(lambda: w.pages.currentWidget() is w.done_page)
    wait(lambda: "Ready" in w.done_status.text(), 30)
    w.done_open.click()
    results["open wallet: STARTED, settings point at keys-2"] = w.action == STARTED and settings.keys_file == str(restored)
    results["open wallet: daemon left running"] = port_is_open(PORT)

    # ----- 4. Use an existing wallet: the fake htnwallet is running now -----
    settings = fresh_settings()
    w = open_wizard(settings)
    w.existing_card.clicked.emit()
    w.welcome_next.click()
    results["existing: connect chosen when one is running"] = w.connect_card.is_checked()
    shot(w, "7-existing.png")
    if len(w.daemons) != 1:  # The real htnwallet on this PC may be listed too; pick the fake one.
        w.connect_address.setEditText(f"127.0.0.1:{PORT}")
    w.existing_next.click()
    results["existing: connect"] = w.action == CONNECT and settings.daemon_address == f"127.0.0.1:{PORT}"
    results["existing: keys file read from its command line"] = settings.keys_file == str(restored)

    settings = fresh_settings()
    w = open_wizard(settings)
    w.existing_card.clicked.emit()
    w.welcome_next.click()
    w.file_card.clicked.emit()
    w.existing_next.click()
    results["existing file: refused while another htnwallet runs"] = "already running" in w.existing.error.text()
    w.reject()

    # Creating while an outside htnwallet runs on the port is refused too.
    settings = fresh_settings()
    w = open_wizard(settings)
    w.create_card.clicked.emit()
    w.welcome_next.click()
    results["create: refused while an outside htnwallet runs"] = "Stop it first" in w.welcome.error.text()
    w.reject()

    # ----- 5. Settings button and the main window taking over -----
    settings = fresh_settings()
    settings.keys_file = str(restored)
    window = MainWindow(settings)
    window.show()
    results["main: running"] = wait(lambda: window.state is State.RUNNING, 30)
    button = window.settings_page.wizard_button
    results["settings: Open Setup Wizard button"] = button.text() == "Open Setup Wizard" and button.isEnabled()
    window.after_wizard(CONNECT)
    results["main: after the wizard, running again"] = wait(lambda: window.state is State.RUNNING, 30)
    window.after_wizard(SAVED)  # Not owned by the app: left running, nothing started or stopped.
    settle(1)
    results["main: after create, banner says press Start"] = "press Start" in window.banner.text()
    results["main: after create, nothing was stopped"] = port_is_open(PORT)

    # ----- 6. Starting that never finishes can be cancelled -----
    close_daemon(PORT)
    results["main: not running once stopped"] = wait(lambda: window.state is State.NOT_RUNNING, 30)
    os.environ["FAKE_DAEMON"] = "hang"
    try:
        window.start_stop_button.click()
        results["start: button becomes Cancel"] = wait(
            lambda: window.state is State.STARTING and window.start_stop_button.text() == "Cancel"
            and window.start_stop_button.isEnabled(), 10
        )
        stuck = window.starting_process
        shot(window, "8-starting-cancel.png")
        window.start_stop_button.click()
        results["cancel: stopped trying"] = window.state is State.NOT_RUNNING and window.start_stop_button.text() == "Start"
        results["cancel: the starting htnwallet was ended"] = stuck is not None and wait(lambda: stuck.poll() is not None, 10)
        shot(window, "9-cancelled.png")
    finally:
        os.environ.pop("FAKE_DAEMON", None)
    window.really_exit = True
    window.close()
finally:
    try:
        close_daemon(PORT)  # Only the fake htnwallet on this script's own port.
    except Exception:
        pass

failed = [name for name, ok in results.items() if not ok]
for name, ok in results.items():
    print(("PASS " if ok else "FAIL ") + name)
print(f"{len(results) - len(failed)} of {len(results)} passed")
print("Pictures in", OUT)
