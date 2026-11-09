"""Send page with the balance under From, on the fake wallet. Nothing is sent: Review is never clicked."""
import os, sys, time
from pathlib import Path
SCRATCH = Path(__file__).parent
os.environ["APPDATA"] = str(SCRATCH / "history-appdata")
from PySide6.QtWidgets import QApplication, QPushButton
from guiwallet.core.commands import wallet_context
from guiwallet.core.daemon import port_is_open, start_daemon
from guiwallet.core.settings import Settings
from guiwallet.ui.main_window import MainWindow, State
from guiwallet.ui.theme import app_icon, apply_theme
FAKE = SCRATCH / "fakewallet" / "bin"; OUT = SCRATCH / "send-shots"; OUT.mkdir(exist_ok=True)
PORT = 47949
app = QApplication(sys.argv); apply_theme(app); app.setWindowIcon(app_icon()); app.setQuitOnLastWindowClosed(False)
def wait(c, s=15):
    d = time.time() + s
    while not c() and time.time() < d:
        app.processEvents(); time.sleep(0.02)
    app.processEvents(); return c()
options = Settings(tools_folder=str(FAKE), daemon_address=f"127.0.0.1:{PORT}")
daemon = start_daemon(FAKE / "htnwallet.exe", wallet_context(options), "localhost", SCRATCH / "shot_send.log")
r = {}
try:
    wait(lambda: port_is_open(PORT), 10)
    w = MainWindow(options); w.show()
    s = w.send_page
    r["running"] = wait(lambda: w.state is State.RUNNING and s.total_spendable is not None)
    w.banner.hide()
    next(b for b in w.findChildren(QPushButton) if b.text() == "Send").click()
    s.other_tab.click(); wait(lambda: False, 0.3)
    r["any address"] = s.available.text() == "Available: 1,234.56 HTN"
    w.grab().save(str(OUT / "1-any.png"))
    s.own_tab.click()
    r["hidden until chosen"] = s.available.isHidden()
    s.own_source.setCurrentIndex(s.own_source.findData(next(a for a, v in s.spendable.items() if str(v) .startswith("210"))))
    s.own_target.setCurrentIndex(0)
    s.amount.setText("150"); wait(lambda: False, 0.3)
    r["ok amount"] = s.available.text() == "Available: 210.46 HTN" and s.review.isEnabled() and s.error.isHidden()
    w.grab().save(str(OUT / "2-ok.png"))
    s.amount.setText("500"); wait(lambda: False, 0.3)
    r["too much"] = not s.review.isEnabled() and "more than" in s.error.text() and not s.error.isHidden()
    w.grab().save(str(OUT / "3-too-much.png"))
    s.amount.setText("210.46"); wait(lambda: False, 0.3)
    r["exactly all"] = not s.review.isEnabled() and "Send all" in s.error.text()
    w.grab().save(str(OUT / "4-exact.png"))
    s.amount.setText("20"); wait(lambda: False, 0.2)
    r["back to ok"] = s.review.isEnabled() and s.error.isHidden()
    r["size"] = (w.width(), w.height()) == (1000, 698)
    w.really_exit = True; w.hide()
finally:
    daemon.kill(); daemon.wait()
print(r); print("ALL PASSED" if all(r.values()) else "SOMETHING FAILED")
