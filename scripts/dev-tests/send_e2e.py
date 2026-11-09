"""Drives the real send form and confirmation against the fake htnwallet on a private port.

`send` is unblocked here, in this process only, and only the fake wallet is ever found: the Main folder
points at it. It holds no wallet and sends nothing. The fake's "send" acts out each way a send can go.
"""

import os
import sys
import time
from pathlib import Path

import psutil
from PySide6.QtCore import QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

SCRATCH = Path(__file__).parent
FAKE = SCRATCH / "fakewallet" / "bin"
MOCKUPS = Path("T:/hoosatmain/guiwallet/mockups")
PORT = 47941
ADDRESS = "hoosat:qq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6"
PASSWORD = "sample-only-password"
ARGS_LOG = FAKE / "send-args.log"
ARGS_LOG.unlink(missing_ok=True)

from guiwallet.core import settings as settings_module
from guiwallet.core import tools
from guiwallet.core.commands import wallet_context
from guiwallet.core.daemon import port_is_open, start_daemon
from guiwallet.core.send import SendResult
from guiwallet.core.settings import Settings
from guiwallet.ui import main_window as mw
from guiwallet.ui.main_window import MainWindow, State
from guiwallet.ui.send_dialog import SendDialog
from guiwallet.ui.theme import app_icon, apply_theme

tools.PILOT_BLOCKED_COMMANDS.add("send")  # To check how the page looks when it's switched off.
check_blocked = tools.sending_switched_off()

settings_file = SCRATCH / "send-e2e" / "settings.json"
settings_file.parent.mkdir(exist_ok=True)
journal = settings_file.parent / "send-in-progress.json"
journal.unlink(missing_ok=True)
mw.settings_path = lambda: settings_file
mw.save_settings = lambda s: settings_module.save_settings(s, settings_file)

app = QApplication(sys.argv)
apply_theme(app)
app.setWindowIcon(app_icon())
app.setQuitOnLastWindowClosed(False)
# Only ones that have been running a while: another copy of the app starts short-lived commands all the time.
real_pids = {
    p.pid for p in psutil.process_iter(["name", "create_time"])
    if (p.info["name"] or "").lower() == "htnwallet.exe" and time.time() - p.info["create_time"] > 300
}
failures = []


def wait(condition, seconds=15):
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return condition()


def check(name, passed):
    print(("PASS " if passed else "FAIL ") + name)
    if not passed:
        failures.append(name)


def fake_processes():
    return [
        p for p in psutil.process_iter(["name", "exe", "cmdline"])
        if (p.info["name"] or "").lower() == "htnwallet.exe"
        and str(FAKE).lower() in (p.info["exe"] or "").lower()
        and "send" in (p.info["cmdline"] or [])[:2]
    ]


def with_main_window(window, dialog, name):
    """The picture a user would see: the send window over the main window."""
    picture = window.grab()
    painter = QPainter(picture)
    shot = dialog.grab()
    painter.drawPixmap((picture.width() - shot.width()) // 2, (picture.height() - shot.height()) // 2, shot)
    painter.end()
    picture.save(str(MOCKUPS / name))


def options():
    return Settings(tools_folder=str(FAKE), daemon_address=f"127.0.0.1:{PORT}", close_action="exit", show_debug=True)


# The block as shipped.
check("blocked: reported as switched off", check_blocked)
blocked_window = MainWindow(options())
check("blocked: form and Review switched off",
      not blocked_window.send_page.review.isEnabled() and "pilot" in blocked_window.send_page.hint.text())
blocked_window.check_timer.stop()
blocked_window.deleteLater()

tools.PILOT_BLOCKED_COMMANDS.discard("send")  # In this process only.
check("the wait is a minute as shipped", mw.SEND_DELAY_SECONDS == 60)
from guiwallet.core import send as send_module
check("limits as shipped: 5 then 60", send_module.SEND_TIMEOUT_SECONDS == 5 and send_module.BROADCAST_REPLY_SECONDS == 60)
send_module.BROADCAST_REPLY_SECONDS = 5
mw.SEND_DELAY_SECONDS = 0.3  # Short for most scenarios; the real minute is run once below.

daemon = start_daemon(FAKE / "htnwallet.exe", wallet_context(options()), "localhost", SCRATCH / "send-e2e" / "daemon.log")
window = None
try:
    wait(lambda: port_is_open(PORT), 10)
    window = MainWindow(options())
    window.show()
    check("wallet reads", wait(lambda: window.state is State.RUNNING and window.balance.text() != "—"))
    window.navigation.button(1).click()
    page = window.send_page
    wait(lambda: page.source.count() == 4, 5)
    check("From lists the wallet's addresses", page.source.count() == 4 and page.source.itemData(0) is None)

    # What the form refuses, before any confirmation.
    page.other_tab.setChecked(True); page.recipient.setText("kaspa:qq")
    page.amount.setText("1")
    page.review.click()
    check("form: wrong address refused", page.error.isVisible() and "hoosat:" in page.error.text())
    page.other_tab.setChecked(True); page.recipient.setText(ADDRESS)
    page.amount.setText("0")
    page.review.click()
    check("form: zero refused", "more than zero" in page.error.text())
    page.amount.setText("1.123456789")
    page.review.click()
    check("form: too many decimals refused", "decimal" in page.error.text())
    page.send_all.setChecked(True)
    check("form: Send all empties and locks the amount", not page.amount.isEnabled() and "All spendable" in page.amount.text())
    page.send_all.setChecked(False)

    def scenario(mode, click_send=True, during=None, password=PASSWORD, screenshot=None, shot_during=None):
        """Fill in the form and go through the confirmation. Returns the dialog and elapsed seconds."""
        os.environ["FAKE_SEND"] = mode
        page.other_tab.setChecked(True); page.recipient.setText(ADDRESS)
        page.amount.setText("1.5")
        found = {}

        def drive():
            dialog = app.activeModalWidget()
            if not isinstance(dialog, SendDialog):
                QTimer.singleShot(30, drive)
                return
            found["dialog"] = dialog
            if not click_send:
                dialog.reject()
                return
            dialog.password.setText(password)
            dialog.send_button.click()
            reached_send()

        def reached_send():
            dialog = found["dialog"]
            if not dialog.busy and dialog.outcome is None:
                QTimer.singleShot(5, reached_send)
                return
            found["began"] = time.monotonic()
            if dialog.busy:
                if during:
                    during(dialog)
                if shot_during:
                    wait(lambda: False, 0.05)
                    dialog.grab().save(str(MOCKUPS / shot_during))
            finish()

        def finish():
            dialog = found["dialog"]
            if dialog.outcome is None:
                QTimer.singleShot(30, finish)
                return
            found["seconds"] = time.monotonic() - found["began"]
            wait(lambda: False, 0.1)
            if screenshot:
                dialog.grab().save(str(MOCKUPS / screenshot))
            dialog.accept()

        QTimer.singleShot(30, drive)
        page.review.click()  # Blocks until the dialog closes.
        return found.get("dialog"), found.get("seconds")

    # Cancelling.
    before = ARGS_LOG.read_text() if ARGS_LOG.exists() else ""
    dialog, _ = scenario("ok", click_send=False)
    check("cancel: nothing ran", (ARGS_LOG.read_text() if ARGS_LOG.exists() else "") == before)
    check("cancel: form kept", page.recipient.text() == ADDRESS)

    # Success.
    during_checks = {}

    def while_sending(dialog):
        during_checks["busy"] = dialog.busy
        during_checks["stop off"] = not window.start_stop_button.isEnabled()
        during_checks["journal"] = journal.exists()
        dialog.reject()
        during_checks["escape ignored"] = dialog.isVisible()
        dialog.close()
        during_checks["close ignored"] = dialog.isVisible()
        window.close()
        during_checks["window stays"] = window.isVisible()
        window._exit_app()
        during_checks["exit refused"] = not window.really_exit and window.isVisible()
        window._start_stop_clicked()
        during_checks["stop refused"] = window.state is State.RUNNING and "send is in progress" in window.banner.text()

    dialog, seconds = scenario("ok", during=while_sending, screenshot="send-result-sent.png", shot_during="send-sending.png")
    outcome = dialog.outcome
    check("ok: reported as sent", outcome.result is SendResult.SENT)
    check("ok: transaction ID shown", outcome.transaction_ids == ("ab" * 32,))
    for name, passed in during_checks.items():
        check(f"during a send: {name}", passed)
    check("ok: record of the send removed", not journal.exists())
    check("ok: form emptied, so it can't be repeated", page.recipient.text() == "" and page.amount.text() == "")
    check("ok: Stop works again", wait(lambda: window.start_stop_button.isEnabled(), 5))

    # What reached the command.
    arguments = ARGS_LOG.read_text(encoding="utf-8").splitlines()
    check("arguments: exact", arguments[:1] == ["send"] and f"--to-address={ADDRESS}" in arguments
          and f"--password={PASSWORD}" in arguments and "--send-amount=1.5" in arguments
          and f"--daemonaddress=127.0.0.1:{PORT}" in arguments)
    check("password not left in the dialog", dialog.password.text() == "")

    # Failing before broadcast.
    for mode, expected, picture in (
        ("waiting", "spendable", None),
        ("badaddress", "address isn't valid", "send-result-not-sent.png"),
        ("wrongpassword", "Wrong password", None),
    ):
        dialog, seconds = scenario(mode, screenshot=picture)
        check(f"{mode}: Nothing was sent", dialog.outcome.result is SendResult.NOT_SENT)
        check(f"{mode}: explained", expected in dialog.outcome.message)
        check(f"{mode}: stopped at the first failure, under 1.5s ({seconds:.2f}s)", seconds < 1.5)
        check(f"{mode}: form kept for another try", page.recipient.text() == ADDRESS)
        check(f"{mode}: command is gone", wait(lambda: not fake_processes(), 3))

    # The 5 second limit, before and after broadcast.
    dialog, seconds = scenario("hang")
    check(f"hang: ended at the limit ({seconds:.2f}s)", 4.8 < seconds < 6.5)
    check("hang: Nothing was sent", dialog.outcome.result is SendResult.NOT_SENT and "5 seconds" in dialog.outcome.message)
    check("hang: command is gone", wait(lambda: not fake_processes(), 3))

    dialog, seconds = scenario("hangafter", screenshot="send-result-maybe.png")
    check(f"hangafter: ended at the limit ({seconds:.2f}s)", 4.8 < seconds < 6.5)
    check("hangafter: may have been sent", dialog.outcome.result is SendResult.MAY_HAVE_BEEN_SENT)
    check("hangafter: form emptied", page.recipient.text() == "")
    check("hangafter: command is gone", wait(lambda: not fake_processes(), 3))

    dialog, _ = scenario("broadcastfail")
    check("broadcast error: may have been sent", dialog.outcome.result is SendResult.MAY_HAVE_BEEN_SENT)

    # Debug box and password never carry the secret.
    text_on_screen = "\n".join(w.text() for w in dialog.findChildren(mw.QLabel))
    check("nothing on screen holds the password", PASSWORD not in text_on_screen)

    # The confirmation itself, as it's really drawn.
    os.environ["FAKE_SEND"] = "ok"
    page.other_tab.setChecked(True); page.recipient.setText(ADDRESS)
    page.amount.setText("1.5")
    wait(lambda: False, 0.1)
    window.grab().save(str(MOCKUPS / "send-form.png"))
    shots = {}

    def confirm_shot():
        dialog = app.activeModalWidget()
        if not isinstance(dialog, SendDialog):
            QTimer.singleShot(30, confirm_shot)
            return
        dialog.password.setText(PASSWORD)
        wait(lambda: False, 0.15)
        with_main_window(window, dialog, "send-confirm.png")
        labels = {w.objectName(): w for w in dialog.findChildren(mw.QLabel) if w.isVisible()}
        shots["same size"] = dialog.cancel_button.size() == dialog.send_button.size()
        shots["address one line"] = labels["fullAddress"].height() < 30 and labels["fullAddress"].width() >= labels["fullAddress"].sizeHint().width()
        shots["masked"] = "••••••••" in labels["debugText"].text() and PASSWORD not in labels["debugText"].text()
        dialog.password.clear()
        dialog.reject()

    QTimer.singleShot(30, confirm_shot)
    page.review.click()
    check("buttons: Confirm pair exactly the same size", shots.get("same size"))
    check("confirmation: address stays on one line", shots.get("address one line"))
    check("confirmation: command shown with password masked", shots.get("masked"))
    page.clear()

    # The wait: each way out of it means nothing ran.
    def send_runs():
        return ARGS_LOG.read_text(encoding="utf-8").count("--to-address=")

    def during_wait(action, seconds=30):
        """Reach the wait, do `action`, and report what happened."""
        os.environ["FAKE_SEND"] = "ok"
        page.other_tab.setChecked(True); page.recipient.setText(ADDRESS)
        page.amount.setText("1.5")
        seen = {"runs": send_runs()}

        def drive():
            dialog = app.activeModalWidget()
            if not isinstance(dialog, SendDialog):
                QTimer.singleShot(30, drive)
                return
            seen["dialog"] = dialog
            dialog.password.setText(PASSWORD)
            dialog.send_button.click()
            seen["waiting"] = dialog.waiting and dialog.waiting_view.isVisible()
            seen["no record yet"] = not journal.exists()
            seen["stop allowed"] = window.start_stop_button.isEnabled() and not window.sending
            action(dialog)
            seen["outcome"] = dialog.outcome
            dialog.accept()

        QTimer.singleShot(30, drive)
        page.review.click()
        return seen

    mw.SEND_DELAY_SECONDS = 30
    for name, action in (
        ("Cancel send", lambda d: d.cancel_send_button.click()),
        ("the X", lambda d: d.title_bar.close_button.click()),
        ("Esc", lambda d: d.reject()),
        ("closing the dialog", lambda d: d.close()),
        ("Stop pressed", lambda d: window._cancel_waiting_send()),
    ):
        seen = during_wait(action)
        check(f"wait: {name}: was waiting, no record, Stop not blocked",
              seen["waiting"] and seen["no record yet"] and seen["stop allowed"])
        check(f"wait: {name}: Nothing was sent", seen["outcome"].result is SendResult.NOT_SENT and "cancelled" in seen["outcome"].message)
        check(f"wait: {name}: htnwallet never ran", send_runs() == seen["runs"])
        check(f"wait: {name}: form kept", page.recipient.text() == ADDRESS)

    # The window's own title bar and the blur.
    looks = {}

    def look_at_it():
        dialog = app.activeModalWidget()
        if not isinstance(dialog, SendDialog):
            QTimer.singleShot(30, look_at_it)
            return
        looks["blurred"] = window.centralWidget().graphicsEffect() is not None
        looks["main window locked"] = app.activeModalWidget() is dialog and dialog.isModal()
        looks["no Windows frame"] = bool(dialog.windowFlags() & mw.Qt.WindowType.FramelessWindowHint)
        looks["only an X"] = [b.toolTip() for b in dialog.title_bar.findChildren(QPushButton)] == ["Close"]
        dialog.title_bar.close_button.click()  # Before any send: just closes.
        looks["X closes the confirmation"] = wait(lambda: not dialog.isVisible(), 2)

    runs = send_runs()
    QTimer.singleShot(30, look_at_it)
    page.review.click()
    for name, passed in looks.items():
        check(f"look: {name}", passed)
    check("look: blur removed afterwards", window.centralWidget().graphicsEffect() is None)
    check("look: nothing ran", send_runs() == runs)

    # Pressing X during the wait: says so, then closes itself.
    cancelled = {}

    def x_during_wait():
        dialog = app.activeModalWidget()
        if not isinstance(dialog, SendDialog):
            QTimer.singleShot(30, x_during_wait)
            return
        dialog.password.setText(PASSWORD)
        dialog.send_button.click()
        wait(lambda: False, 0.1)
        began = time.monotonic()
        dialog.title_bar.close_button.click()
        wait(lambda: False, 0.2)
        words = [w.text() for w in dialog.result_view.findChildren(mw.QLabel)]
        cancelled["says so"] = "Transaction cancelled" in words and "No funds were sent." in words
        cancelled["still open"] = dialog.isVisible()
        with_main_window(window, dialog, "send-cancelled.png")
        cancelled["closed by itself"] = wait(lambda: not dialog.isVisible(), 5)
        cancelled["seconds"] = time.monotonic() - began

    page.other_tab.setChecked(True); page.recipient.setText(ADDRESS)
    page.amount.setText("1.5")
    runs = send_runs()
    QTimer.singleShot(30, x_during_wait)
    page.review.click()
    check("X: says the transaction was cancelled and no funds were sent", cancelled["says so"])
    check("X: stays up for a moment", cancelled["still open"])
    check(f"X: closes by itself ({cancelled['seconds']:.1f}s)", cancelled["closed by itself"] and 2 < cancelled["seconds"] < 3.5)
    check("X: htnwallet never ran", send_runs() == runs)
    check("X: form kept", page.recipient.text() == ADDRESS)
    check("X: blur removed", window.centralWidget().graphicsEffect() is None)

    # Send now: skips the rest of the wait, once only.
    now = {}
    sizes = {}

    def press_send_now():
        dialog = app.activeModalWidget()
        if not isinstance(dialog, SendDialog):
            QTimer.singleShot(30, press_send_now)
            return
        dialog.password.setText(PASSWORD)
        dialog.send_button.click()
        now["waiting first"] = dialog.waiting and send_runs() == runs
        now["keyboard can't reach it"] = dialog.send_now_button.focusPolicy() == mw.Qt.FocusPolicy.NoFocus
        now["focus on Cancel send"] = app.focusWidget() is dialog.cancel_send_button or not app.focusWidget()
        wait(lambda: False, 0.1)
        sizes["Processing"] = (dialog.cancel_send_button.size(), dialog.send_now_button.size())
        began = time.monotonic()
        dialog.send_now_button.click()
        dialog.send_now_button.click()  # A double click must not send twice.
        now["same size"] = None
        now["went straight on"] = dialog.busy
        now["buttons gone"] = not dialog.send_now_button.isVisible() and not dialog.cancel_send_button.isVisible()
        wait(lambda: dialog.outcome is not None, 10)
        now["seconds"] = time.monotonic() - began
        now["outcome"] = dialog.outcome
        dialog.accept()

    mw.SEND_DELAY_SECONDS = 30
    os.environ["FAKE_SEND"] = "ok"
    page.other_tab.setChecked(True); page.recipient.setText(ADDRESS)
    page.amount.setText("1.5")
    runs = send_runs()
    QTimer.singleShot(30, press_send_now)
    page.review.click()
    wait(lambda: not window.sending, 3)
    check("buttons: Processing pair exactly the same size", sizes["Processing"][0] == sizes["Processing"][1])
    check("Send now: waits until pressed", now["waiting first"])
    check("Send now: keyboard can't press it", now["keyboard can't reach it"])
    check("Send now: focus stays on Cancel send", now["focus on Cancel send"])
    check("Send now: starts at once", now["went straight on"])
    check("Send now: can't cancel after it", now["buttons gone"])
    check(f"Send now: finished without the wait ({now['seconds']:.1f}s)", now["seconds"] < 3)
    check("Send now: sent", now["outcome"].result is SendResult.SENT)
    check("Send now: a double click sends only once", send_runs() == runs + 1)
    check("Send now: timer stopped", not window.wait_timer.isActive())

    # Balance reads carry on during the wait, then the whole real minute with nothing else touching it.
    mw.SEND_DELAY_SECONDS = 60
    real = {}
    os.environ["FAKE_SEND"] = "ok"
    page.other_tab.setChecked(True); page.recipient.setText(ADDRESS)
    page.amount.setText("1.5")
    runs_before = send_runs()
    commands_log = FAKE / "commands.log"

    def real_minute():
        dialog = app.activeModalWidget()
        if not isinstance(dialog, SendDialog):
            QTimer.singleShot(30, real_minute)
            return
        dialog.password.setText(PASSWORD)
        real["began"] = time.monotonic()
        dialog.send_button.click()
        wait(lambda: False, 2.2)
        real["countdown"] = dialog.countdown.text()
        real["ran early"] = send_runs() != runs_before
        with_main_window(window, dialog, "send-processing.png")
        reads = commands_log.read_text().split().count("balance")
        wait(lambda: False, 12)
        real["reads during wait"] = commands_log.read_text().split().count("balance") - reads
        real["still not run at 14s"] = send_runs() == runs_before
        wait(lambda: dialog.busy or dialog.outcome is not None, 60)
        real["seconds"] = time.monotonic() - real["began"]
        wait(lambda: dialog.outcome is not None, 10)
        real["outcome"] = dialog.outcome
        dialog.accept()

    QTimer.singleShot(30, real_minute)
    page.review.click()
    print("      countdown:", real["countdown"], " send began at", round(real["seconds"], 1), "seconds")
    check("real wait: counts down", real["countdown"] in ("Sending in 0:58", "Sending in 0:57"))
    check("real wait: htnwallet not run early", not real["ran early"] and real["still not run at 14s"])
    check("real wait: balance keeps updating", real["reads during wait"] >= 2)
    check("real wait: sends after a minute", 59.5 < real["seconds"] < 61.5)
    check("real wait: then sent", real["outcome"].result is SendResult.SENT and send_runs() == runs_before + 1)
    mw.SEND_DELAY_SECONDS = 0.3

    # A crash during a send: the next start warns once.
    from guiwallet.core.send import SendRequest, record_send_started

    record_send_started(journal, SendRequest(ADDRESS, "1.5"))
    seen = {}

    def answer_warning():
        box = app.activeModalWidget()
        if isinstance(box, QMessageBox):
            seen["text"] = box.text()
            box.accept()
        else:
            QTimer.singleShot(30, answer_warning)

    QTimer.singleShot(30, answer_warning)
    reopened = MainWindow(options())
    reopened.show()
    wait(lambda: "text" in seen, 5)
    check("after a crash: warned", "may have gone through" in seen.get("text", "") and ADDRESS in seen.get("text", ""))
    check("after a crash: record removed once told", wait(lambda: not journal.exists(), 3))
    reopened.check_timer.stop()
    reopened.really_exit = True
    reopened.hide()

    # Logs and output.
    log_text = (SCRATCH / "send-e2e" / "daemon.log").read_text(errors="replace") if (SCRATCH / "send-e2e" / "daemon.log").exists() else ""
    check("password not in the daemon log", PASSWORD not in log_text)
finally:
    if window:
        window.check_timer.stop()
        window.really_exit = True
        window.hide()
    daemon.kill()
    daemon.wait()

still = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "htnwallet.exe"}
check("real htnwallet processes untouched", real_pids <= still or not real_pids)
print("FAILED:" if failures else "ALL PASSED", failures if failures else "")
