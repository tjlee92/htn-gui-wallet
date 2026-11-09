"""Pictures of the real Send page, against the fake wallet on a private port. Review is never pressed."""

import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication, QPushButton

from guiwallet.core.commands import wallet_context
from guiwallet.core.daemon import port_is_open, start_daemon
from guiwallet.core.settings import Settings
from guiwallet.ui.main_window import MainWindow, State
from guiwallet.ui.theme import apply_theme

SCRATCH = Path(__file__).parent
FAKE = SCRATCH / "fakewallet" / "bin"
OUT = SCRATCH / "shots"
OUT.mkdir(exist_ok=True)
PORT = 47951

app = QApplication(sys.argv)
apply_theme(app)
app.setQuitOnLastWindowClosed(False)


def wait(condition, seconds=15):
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return condition()


options = Settings(tools_folder=str(FAKE), daemon_address=f"127.0.0.1:{PORT}")
daemon = start_daemon(FAKE / "htnwallet.exe", wallet_context(options), "localhost", SCRATCH / "shot-tabs.log")
try:
    wait(lambda: port_is_open(PORT), 10)
    window = MainWindow(options)
    window.show()
    print("read:", wait(lambda: window.state is State.RUNNING and window.balance.text() != "—"))
    window.check_timer.stop()
    window.banner.hide()
    for button in window.findChildren(QPushButton):
        if button.text() == "Send":
            button.click()
    page = window.send_page
    wait(lambda: page.own_source.count() > 0, 5)
    print("own list:", [page.own_source.itemText(i) for i in range(page.own_source.count())])

    def shot(name):
        wait(lambda: False, 0.3)
        window.grab().save(str(OUT / name))

    shot("none.png")
    page.own_tab.click()
    shot("own-empty.png")
    page.own_source.setCurrentIndex(0)
    page.own_target.setCurrentIndex(0)
    page.amount.setText("1.5")
    shot("own.png")
    page.send_all.setChecked(True)
    shot("own-all.png")
    page.other_tab.click()
    page.send_all.setChecked(True)
    shot("other-all.png")
    window.really_exit = True
    window.hide()
finally:
    daemon.kill()
    daemon.wait()

print("send ran:", "send" in (FAKE / "commands.log").read_text().split())
