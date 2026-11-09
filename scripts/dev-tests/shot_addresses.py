"""Add new address and labels, in the real window, on the fake htnwallet (port 47955, scratch folders)."""
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
SCRATCH = HERE / "addresses-scratch"
shutil.rmtree(SCRATCH, ignore_errors=True)
(SCRATCH / "appdata").mkdir(parents=True)
(SCRATCH / "localappdata").mkdir(parents=True)
os.environ["APPDATA"] = str(SCRATCH / "appdata")
os.environ["LOCALAPPDATA"] = str(SCRATCH / "localappdata")
OUT = SCRATCH / "shots"
OUT.mkdir()

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from guiwallet.core.commands import wallet_context
from guiwallet.core.daemon import close_daemon, port_is_open, start_daemon
from guiwallet.core.labels import labels_path, load_labels
from guiwallet.core.settings import Settings
from guiwallet.ui.address_dialog import AddAddressDialog
from guiwallet.ui.main_window import MainWindow, State
from guiwallet.ui.theme import app_icon, apply_theme

FAKE = HERE / "fakewallet" / "bin"
PORT = 47955
THREE = "hoosat:qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2"
(FAKE / "new-addresses.txt").unlink(missing_ok=True)
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


settings = Settings(tools_folder=str(FAKE), daemon_address=f"127.0.0.1:{PORT}")
start_daemon(FAKE / "htnwallet.exe", wallet_context(settings), "localhost", SCRATCH / "daemon.log")
try:
    wait(lambda: port_is_open(PORT), 10)
    window = MainWindow(settings)
    window.show()
    page = window.wallet_page
    results["running"] = wait(lambda: window.state is State.RUNNING and page.snapshot is not None, 30)
    wait(lambda: False, 1)
    window.banner.hide()
    results["button top right"] = page.add_button.isVisible() and page.add_button.x() > page.width() - 250
    results["every row has Add a label"] = all(e.text.text() == "Add a label…" for e in page.label_editors.values())
    rows_before = list(page.rows)

    seen = {}

    def drive_dialog():
        dialog = next((w for w in app.topLevelWidgets() if isinstance(w, AddAddressDialog) and w.isVisible()), None)
        if dialog is None:
            QTimer.singleShot(100, drive_dialog)
            return
        dialog.grab().save(str(OUT / "1-ask.png"))
        dialog.label_box.setText("Exchange deposits")
        dialog.add_button.click()
        wait(lambda: dialog.done_button.isVisible(), 15)
        seen["address"] = dialog.result_address.text()
        seen["name"] = dialog.result_name.text()
        dialog.grab().save(str(OUT / "2-added.png"))
        dialog.done_button.click()

    QTimer.singleShot(200, drive_dialog)
    page.add_button.click()
    new = seen.get("address", "")
    results["dialog: new address shown"] = new.startswith("hoosat:qp01new")
    results["dialog: label shown"] = seen.get("name") == "Exchange deposits"
    results["label saved to labels.json"] = load_labels(labels_path()).get(new) == "Exchange deposits"
    results["wallet list: new empty address listed"] = wait(lambda: new in page.rows, 30)
    results["wallet list: its label"] = new in page.label_editors and page.label_editors[new].text.text() == "Exchange deposits"
    results["wallet list: others kept"] = all(address in page.rows for address in rows_before)
    window.grab().save(str(OUT / "3-listed.png"))

    editor = page.label_editors[THREE]
    editor.text.mousePressEvent(None)
    results["click label: edit box"] = editor.editing() and editor.hint.isVisible()
    editor.box.setText("Cold storage")
    window.grab().save(str(OUT / "4-editing.png"))
    QTest.keyClick(editor.box, Qt.Key.Key_Return)
    results["Enter saves"] = not editor.editing() and window.labels.get(THREE) == "Cold storage"
    editor.edit()
    editor.box.setText("Something else")
    QTest.keyClick(editor.box, Qt.Key.Key_Escape)
    results["Esc cancels"] = not editor.editing() and window.labels.get(THREE) == "Cold storage"
    send = window.send_page
    results["send: From list shows the label"] = any(
        "Cold storage" in send.source.itemText(i) for i in range(send.source.count())
    )
    history = window.history_page
    results["history: address list shows the label"] = wait(lambda: any(
        "Exchange deposits" in history.address.itemText(i) for i in range(history.address.count())
    ), 30)
    editor.edit()
    editor.box.setText("")
    QTest.keyClick(editor.box, Qt.Key.Key_Return)
    results["empty label removes it"] = THREE not in window.labels and editor.text.text() == "Add a label…"
    window.grab().save(str(OUT / "5-after.png"))

    close_daemon(PORT)
    results["stopped"] = wait(lambda: window.state is State.NOT_RUNNING, 30)
    page.add_button.click()
    results["not running: banner instead"] = "Start htnwallet first" in window.banner.text()
    window.really_exit = True
    window.close()
finally:
    try:
        close_daemon(PORT)  # Only the fake htnwallet on this script's own port.
    except Exception:
        pass
    (FAKE / "new-addresses.txt").unlink(missing_ok=True)

failed = [name for name, ok in results.items() if not ok]
for name, ok in results.items():
    print(("PASS " if ok else "FAIL ") + name)
print(f"{len(results) - len(failed)} of {len(results)} passed")
print("Pictures in", OUT)
