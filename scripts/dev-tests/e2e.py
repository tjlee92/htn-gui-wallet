"""Drives the real window against a fake htnwallet.exe on a private port."""

import sys
import time
from pathlib import Path

import psutil
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QMessageBox, QPushButton

SCRATCH = Path(__file__).parent
FAKE = SCRATCH / "fakewallet" / "bin"
OUT = SCRATCH / "e2e-shots"
OUT.mkdir(exist_ok=True)
PORT = 47931
COMMAND_LOG = FAKE / "commands.log"
COMMAND_LOG.unlink(missing_ok=True)

from guiwallet.core import settings as settings_module
from guiwallet.core.commands import wallet_context
from guiwallet.core.daemon import find_port_owner, port_is_open, start_daemon
from guiwallet.core.settings import Settings
from guiwallet.ui import main_window as mw
from guiwallet.ui.main_window import MainWindow, State
from guiwallet.ui.pages import SendPage
from guiwallet.ui.theme import app_icon, apply_theme

# Keep the test away from the user's real settings file.
settings_file = SCRATCH / "e2e-settings" / "settings.json"
mw.settings_path = lambda: settings_file
mw.save_settings = lambda settings: settings_module.save_settings(settings, settings_file)

app = QApplication(sys.argv)
apply_theme(app)
app.setWindowIcon(app_icon())
app.setQuitOnLastWindowClosed(False)
real_pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "htnwallet.exe"}


def wait(condition, seconds=20):
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return condition()


def check(name, passed):
    print(("PASS " if passed else "FAIL ") + name)


def settings(**overrides):
    return Settings(tools_folder=str(FAKE), daemon_address=f"127.0.0.1:{PORT}", **overrides)


def grab(window, name):
    app.processEvents()
    window.grab().save(str(OUT / f"{name}.png"))


def click_start_stop(window):
    window.start_stop_button.click()


# 1. Nothing running.
window = MainWindow(settings())
check("opening: says it's checking", window.status_text.text().startswith("Checking") and not window.start_stop_button.isEnabled())
window.show()
check("nothing running: settles quickly by itself", wait(lambda: window.state is State.NOT_RUNNING, 3))
check("nothing running: balance blank", window.balance.text() == "—")
check("nothing running: button says Start", window.start_stop_button.text() == "Start")
check("nothing running: settings editable", window.settings_page.node_address.isEnabled())
grab(window, "app-not-running")

# The window's own title bar and corners.
import ctypes
WS_MAXIMIZEBOX, WS_MINIMIZEBOX, WS_THICKFRAME, WS_SYSMENU = 0x10000, 0x20000, 0x40000, 0x80000
style = ctypes.windll.user32.GetWindowLongW(int(window.winId()), -16)
check("frame: Windows has no maximize for it", not style & WS_MAXIMIZEBOX)
check("frame: taskbar can still minimize it", bool(style & WS_MINIMIZEBOX))
check("frame: Alt+F4 menu kept", bool(style & WS_SYSMENU))
check("frame: only minimize and close", {b.toolTip() for b in window.title_bar.findChildren(QPushButton)} == {"Minimize", "Close"})
check("frame: named", any(l.text() == "HTN GUI Wallet" for l in window.title_bar.findChildren(QLabel)))
image = window.grab().toImage()
last_x, last_y = image.width() - 1, image.height() - 1
check("frame: all four corners cut away",
      all(image.pixelColor(x, y).alpha() == 0 for x, y in ((0, 0), (last_x, 0), (0, last_y), (last_x, last_y))))
check("frame: middle of the window solid", image.pixelColor(image.width() // 2, image.height() // 2).alpha() == 255)
check("frame: eight resize grips around the window", len(window.grips) == 8 and all(g.isVisible() for g in window.grips.values()))
window.resize(1100, 760)
wait(lambda: False, 0.1)
right = window.grips[Qt.Edge.RightEdge].geometry()
bottom = window.grips[Qt.Edge.BottomEdge].geometry()
check("frame: grips follow a new size", right.right() == window.width() - 1 and bottom.bottom() == window.height() - 1)
window.resize(1000, 698)
window.title_bar.minimize_button.click()
check("frame: minimize button works", wait(lambda: window.isMinimized(), 3))
window.showNormal()
check("frame: comes back from minimized", wait(lambda: not window.isMinimized() and window.isVisible(), 3))
wait(lambda: not window.checking, 5)

# 2. A failed start reports why, even if clicked while a check is in flight.
window.settings.node_address = "unreachable"
window._check()
click_start_stop(window)
check("failed start: reason shown", wait(lambda: "Could not start" in window.status_text.text()))
print("     ", window.status_text.text())
window._check()
wait(lambda: not window.checking, 5)
check("failed start: reason stays after the next check", "Could not start" in window.status_text.text())
window.settings.node_address = "localhost"

# 3. A successful start.
click_start_stop(window)
check("start: running", wait(lambda: window.state is State.RUNNING))
check("start: counted as started by the app", window.started_by_app)
check("start: no 'already running' message", window.banner.isHidden())
check("start: balance", wait(lambda: window.balance.text() == "1,236"))
check("start: coins", wait(lambda: window.wallet_page.coin_count.text() == "148"))
check("start: pending stays on the Wallet page", window.wallet_page.pending.text() == "2")
check("card: no pending line beside the balance", not hasattr(window, "pending"))
check("card: named Current Balance", any(l.text() == "Current Balance" for l in window.findChildren(QLabel)))
menu_top = window.navigation.button(0).mapTo(window, QPoint(0, 0)).y()
for amount in ("0", "1,236", "999,999", "12,345,678", "1,234,567,890", "17,000,000,000", "999,999,999,999,999"):
    window._show_balance(amount, "x")
    app.processEvents()
    wait(lambda: False, 0.05)
    check(f"card: {amount} fits in full", window.balance.width() >= window.balance.sizeHint().width()
          and window.balance_unit.geometry().right() <= window.balance_unit.parentWidget().contentsRect().right())
    check(f"card: {amount} leaves the menu in place", window.navigation.button(0).mapTo(window, QPoint(0, 0)).y() == menu_top)
window._show_balance("1,236", "x")
rows = window.wallet_page.rows
check("table: three addresses in a stable order", list(rows) == sorted(rows) and len(rows) == 3)
first = next(iter(rows))
check("table: columns filled", [w.text() for w in rows[first]] == ["2", "1,002", "96"])
kept = dict(rows)
window._check()
wait(lambda: not window.checking, 10)
check("table: same widgets after a refresh, so nothing moves", all(window.wallet_page.rows[a] is kept[a] for a in kept))
check("settings locked while running", not window.settings_page.node_address.isEnabled()
      and not window.settings_page.daemon_address.isEnabled() and not window.settings_page.keys_file.isEnabled()
      and not window.settings_page.browse_keys.isEnabled())
def amount_labels(w):
    labels = [w.balance, w.wallet_page.available, w.wallet_page.pending]
    for pending_value, total_value, _ in w.wallet_page.rows.values():
        labels += [pending_value, total_value]
    return labels


shown = [a.text() for a in amount_labels(window)]
check("eye: starts showing balances", not window.eye_button.isChecked() and "••••" not in shown)
check("eye: says what it will do", window.eye_button.toolTip() == "Hide balances")
window.eye_button.click()
check("eye: every amount hidden", all(a.text() == "••••" for a in amount_labels(window)))
check("eye: no exact amount left in a tooltip", all(a.toolTip() == "" for a in amount_labels(window)))
check("eye: UTXO counts stay", window.wallet_page.coin_count.text() == "148"
      and [r[2].text() for r in window.wallet_page.rows.values()] == ["96", "41", "11"])
check("eye: says what it will do when hidden", window.eye_button.toolTip() == "Show balances")
check("eye: choice saved", '"hide_balances": true' in settings_file.read_text())
window._check()
wait(lambda: not window.checking, 10)
check("eye: still hidden after a refresh", all(a.text() == "••••" for a in amount_labels(window)))
reopened = MainWindow(settings(hide_balances=True))
reopened.show()
check("eye: reopening stays hidden", wait(lambda: reopened.state is State.RUNNING and reopened.balance.text() == "••••")
      and reopened.eye_button.isChecked())
wait(lambda: reopened.wallet_page.coin_count.text() != "—", 10)
check("eye: reopened amounts hidden", all(a.text() == "••••" and a.toolTip() == "" for a in amount_labels(reopened)))
reopened.really_exit = True
reopened.hide()
window.eye_button.click()
check("eye: showing brings every amount back", [a.text() for a in amount_labels(window)] == shown)
check("eye: exact amount back in the tooltip", window.balance.toolTip().endswith(" HTN"))
check("eye: showing is saved too", '"hide_balances": false' in settings_file.read_text())
grab(window, "app-wallet")
window.navigation.button(1).click()
send_page = window.stack.currentWidget()
check("send: is the send screen", isinstance(send_page, SendPage))
check("send: every input is switched off", all(not w.isEnabled() for w in send_page.findChildren(QLineEdit)))
check("send: every button is switched off", all(not b.isEnabled() for b in send_page.findChildren(QPushButton)))
grab(window, "app-send")
window.navigation.button(2).click()
check("receive: addresses", wait(lambda: len(window.receive_page.addresses) == 2))
grab(window, "app-receive")
window.navigation.button(4).click()
grab(window, "app-settings-running")
window.navigation.button(0).click()

# How often each command runs: the real timer, with nothing triggered by hand.
def command_counts():
    words = COMMAND_LOG.read_text().split()
    return {name: words.count(name) for name in ("balance", "get-daemon-version", "show-addresses", "utxos")}


wait(lambda: not window.checking, 5)
window.check_timer.start()
before = command_counts()
wait(lambda: False, 41)
now = command_counts()
ran = {name: now[name] - before[name] for name in now}
print("      in 41 seconds:", ran)
check("polling: balance about every 5 seconds", 7 <= ran["balance"] <= 9)
check("polling: version about every 20 seconds", 1 <= ran["get-daemon-version"] <= 3)
check("polling: addresses about every 20 seconds", 1 <= ran["show-addresses"] <= 3)
check("polling: coin list at most once in 41 seconds", ran["utxos"] <= 1)
check("polling: figures still there between detail reads", window.balance.text() == "1,236"
      and window.wallet_page.coin_count.text() == "148" and len(window.receive_page.addresses) == 2)

wait(lambda: not window.checking, 5)
window.showMinimized()
wait(lambda: window.isMinimized(), 3)
wait(lambda: not window.checking, 5)
before = command_counts()
wait(lambda: False, 11)
check("minimized: nothing runs", command_counts() == before)
window.showNormal()
wait(lambda: not window.isMinimized(), 3)
check("restored: reads everything straight away", wait(lambda: all(command_counts()[n] > before[n] for n in before), 3))
wait(lambda: not window.checking, 5)

# 4. Stop one the app started: no prompt, and only that process closes.
owner = find_port_owner(PORT).pid
QMessageBox.warning = lambda *a, **k: (check("stop: no prompt for its own", False), QMessageBox.StandardButton.Cancel)[1]
click_start_stop(window)
check("stop: not running", wait(lambda: window.state is State.NOT_RUNNING))
check("stop: figures cleared", window.balance.text() == "—")
check("stop: process gone", wait(lambda: not psutil.pid_exists(owner), 5))
check("stop: port released", not port_is_open(PORT))
check("stop: settings editable again", window.settings_page.node_address.isEnabled())
window.really_exit = True
window.hide()

# 5. Already running: picked up without pressing anything, and the user is told.
log = SCRATCH / "e2e-settings" / "external.log"
external = start_daemon(FAKE / "htnwallet.exe", wallet_context(settings()), "localhost", log)
wait(lambda: port_is_open(PORT), 5)
window = MainWindow(settings())
window.show()
check("detect: running by itself within 3 seconds", wait(lambda: window.state is State.RUNNING, 3))
check("detect: not counted as started by the app", not window.started_by_app)
check("detect: tells the user", "already running" in window.banner.text() and not window.banner.isHidden())
print("     ", window.banner.text())
wait(lambda: window.balance.text() == "1,236")
grab(window, "app-already-running")

# 6. Stop on one the app didn't start warns first.
answers = []
QMessageBox.warning = lambda *a, **k: (answers.append("cancel"), QMessageBox.StandardButton.Cancel)[1]
click_start_stop(window)
wait(lambda: False, 0.3)
check("external stop: warned", answers == ["cancel"])
check("external stop: Cancel keeps it", external.poll() is None and window.state is State.RUNNING)
QMessageBox.warning = lambda *a, **k: (answers.append("yes"), QMessageBox.StandardButton.Yes)[1]
click_start_stop(window)
check("external stop: Yes closes it", wait(lambda: external.poll() is not None, 10))
check("external stop: not running", wait(lambda: window.state is State.NOT_RUNNING))

# 7. A daemon that appears and disappears on its own is picked up by the regular check.
started = start_daemon(FAKE / "htnwallet.exe", wallet_context(settings()), "localhost", log)
wait(lambda: port_is_open(PORT), 5)
window._check()
check("appears later: picked up", wait(lambda: window.state is State.RUNNING and window.balance.text() != "—"))
started.kill()
started.wait()
window._check()
check("dies: not running", wait(lambda: window.state is State.NOT_RUNNING))
check("dies: old balance removed", window.balance.text() == "—")

# 8. Closing the window.
started = start_daemon(FAKE / "htnwallet.exe", wallet_context(settings()), "localhost", log)
wait(lambda: port_is_open(PORT), 5)
window._check()
wait(lambda: window.state is State.RUNNING)


def answer_dialog():
    dialog = app.activeModalWidget()
    if isinstance(dialog, QDialog):
        dialog.remember.setChecked(True)
        dialog._choose("tray")


QTimer.singleShot(400, answer_dialog)
window.title_bar.close_button.click()
wait(lambda: False, 0.8)
check("close: tray hides the window", not window.isVisible() and not window.really_exit)
check("close: choice remembered", window.settings.close_action == "tray")
check("close: choice saved", '"close_action": "tray"' in settings_file.read_text())
check("close: daemon untouched", started.poll() is None)
window.settings.close_action = "exit"
window.show()
window.close()
wait(lambda: False, 0.3)
check("exit: daemon still running", started.poll() is None and port_is_open(PORT))
started.kill()
started.wait()

# What was actually run, and what wasn't touched.
wait(lambda: False, 0.5)
commands = sorted(set(COMMAND_LOG.read_text().split()))
print("COMMANDS RUN:", commands)
allowed = {"balance", "get-daemon-version", "show-addresses", "start-daemon", "utxos", "version"}
check("only read-only commands and start-daemon ran", set(commands) <= allowed)
still = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "htnwallet.exe"}
print("real htnwallet before", sorted(real_pids), "after", sorted(still))
check("real htnwallet processes untouched", bool(real_pids) and real_pids == still)
