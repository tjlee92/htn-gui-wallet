"""Pictures of the real auto-compound feature against the fake htnwallet. Starts and stops only a fake
auto-compound, and refuses to stop anything that isn't the fake wallet's."""

import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QDialog

from guiwallet.core.commands import wallet_context
from guiwallet.core.daemon import port_is_open, start_daemon
from guiwallet.core.settings import Settings
from guiwallet.ui.compound_dialog import StartCompoundDialog, StopCompoundDialog
from guiwallet.ui.main_window import MainWindow, State
from guiwallet.ui.theme import app_icon, apply_theme

SCRATCH = Path(__file__).parent
FAKE = SCRATCH / "fakewallet" / "bin"
OUT = SCRATCH / "compound-shots"
OUT.mkdir(exist_ok=True)
PORT = 47943
FAKE_ADDRESSES = {
    "hoosat:qq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6",
    "hoosat:qr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s",
    "hoosat:qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2",
}

app = QApplication(sys.argv)
apply_theme(app)
app.setWindowIcon(app_icon())
app.setQuitOnLastWindowClosed(False)


def wait(condition, seconds=15):
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return condition()


def over(window, dialog, name):
    picture = window.grab()
    painter = QPainter(picture)
    painter.fillRect(picture.rect(), QColor(5, 8, 16, 150))
    top = dialog.grab()
    painter.drawPixmap(QPoint((picture.width() - top.width()) // 2, (picture.height() - top.height()) // 2), top)
    painter.end()
    picture.save(str(OUT / name))


results = {}
options = Settings(tools_folder=str(FAKE), daemon_address=f"127.0.0.1:{PORT}")
daemon = start_daemon(FAKE / "htnwallet.exe", wallet_context(options), "localhost", SCRATCH / "shot_compound.log")
try:
    wait(lambda: port_is_open(PORT), 10)
    window = MainWindow(options)
    window.show()
    results["running"] = wait(lambda: window.state is State.RUNNING and bool(window.wallet_page.rows))
    window.banner.hide()
    page = window.wallet_page
    first = list(page.rows)[0]
    assert first in FAKE_ADDRESSES
    results["nothing marked yet"] = page.compound_for(first) is None
    window.grab().save(str(OUT / "1-wallet.png"))

    # Start, through the real window: choose, type a password, press Start.
    def drive_start():
        dialog = next(w for w in app.topLevelWidgets() if isinstance(w, StartCompoundDialog) and w.isVisible())
        dialog.interval.setText("30")
        dialog.password.setText("fake")
        wait(lambda: False, 0.2)
        over(window, dialog, "2-start.png")
        dialog.start_button.click()
        wait(lambda: False, 0.3)
        over(window, dialog, "3-starting.png")

    QTimer.singleShot(300, drive_start)
    page.compound_buttons[first].click()  # Returns once the window has closed.
    results["started"] = wait(lambda: page.compound_for(first) is not None and page.compound_for(first).started_here, 20)
    compound = page.compound_for(first)
    print("seen:", compound)
    results["own and 30s"] = bool(compound and compound.started_here and compound.interval == 30)
    page.compound_buttons[first].setFocus()
    wait(lambda: False, 2.5)
    window._scan_compounds()
    wait(lambda: False, 1)
    window.grab().save(str(OUT / "4-running.png"))

    # Stop it again, through the window.
    def drive_stop():
        dialog = next(w for w in app.topLevelWidgets() if isinstance(w, StopCompoundDialog) and w.isVisible())
        over(window, dialog, "5-stop.png")
        assert window.wallet_page.compound_for(first).started_here  # Only ever the fake one this app started.
        dialog.stop_button.click()

    QTimer.singleShot(300, drive_stop)
    page.compound_buttons[first].click()
    results["stopped"] = wait(lambda: page.compound_for(first) is None, 10)
    results["none left"] = not window.compounders.started
    window.grab().save(str(OUT / "6-after.png"))

    window.really_exit = True
    window.hide()
finally:
    daemon.kill()
    daemon.wait()

print(results)
print("fake compound runs:", (FAKE / "compound-args.log").read_text().count("auto-compound"))
print("ALL PASSED" if all(results.values()) else "SOMETHING FAILED")
