"""Exiting while an auto-compound started outside the app is running: cancel, Leave running, Stop and exit.

The "outside" auto-compound is the fake htnwallet, started here. The app is only shown that one process,
so a real auto-compound on this PC (such as the user's .bat) is never seen, let alone stopped. The app
never quits for real, and settings are never saved.
"""

import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import guiwallet.ui.main_window as main_window
from guiwallet.core.compound import find_compounds
from guiwallet.core.settings import Settings
from guiwallet.ui import theme
from guiwallet.ui.compound_dialog import OutsideCompoundsDialog
from guiwallet.ui.main_window import MainWindow

HERE = Path(__file__).parent
FAKE = HERE / "fakewallet" / "bin" / "htnwallet.exe"
OUT = HERE / "theme-scratch"
OUT.mkdir(exist_ok=True)
ADDRESS = "hoosat:qq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6"

app = QApplication(sys.argv)
theme.apply_theme(app, theme.DARK)
app.setQuitOnLastWindowClosed(False)
quits = []
QApplication.quit = staticmethod(lambda: quits.append(True))  # Never really quit.
main_window.save_settings = lambda *_args, **_kwargs: None
outside: list[subprocess.Popen] = []
# Only ever show the app the fake auto-compound started here.
main_window.find_compounds = lambda own=None: [
    compound for compound in find_compounds(own) if compound.pid in {process.pid for process in outside}
]
results: dict[str, bool] = {}


def wait(condition, seconds=10):
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return condition()


def start_outside() -> subprocess.Popen:
    process = subprocess.Popen(
        [str(FAKE), "auto-compound", f"--from-address={ADDRESS}", f"--to-address={ADDRESS}", "-u", "--compound-rate=30"],
        stdout=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    outside.append(process)
    wait(lambda: any(c.pid == process.pid for c in main_window.find_compounds()), 5)
    return process


def answer(action: str, picture: str | None = None):
    """Press a button in the exit question once it shows."""

    def press():
        dialog = next((w for w in app.topLevelWidgets() if isinstance(w, OutsideCompoundsDialog) and w.isVisible()), None)
        if dialog is None:
            QTimer.singleShot(50, press)
            return
        if picture:
            dialog.grab().save(str(OUT / picture))
        {"cancel": dialog.reject, "leave": dialog.leave_button.click, "stop": dialog.stop_button.click}[action]()

    QTimer.singleShot(300, press)


window = MainWindow(Settings(tools_folder=str(FAKE.parent), daemon_address="127.0.0.1:47963"))
window.check_timer.stop()
window.show()
wait(lambda: False, 0.5)
try:
    results["no question without an outside compound"] = window._exit_app() and quits == [True]
    window.really_exit, quits[:] = False, []

    first = start_outside()
    answer("cancel", "exit-question-dark.png")
    results["closing the question cancels the exit"] = not window._exit_app() and not quits
    results["cancel leaves it running"] = first.poll() is None

    answer("leave")
    results["Leave running exits"] = window._exit_app() and quits == [True]
    results["Leave running leaves it running"] = first.poll() is None
    window.really_exit, quits[:] = False, []

    theme.set_mode(theme.LIGHT_MODE)
    answer("stop", "exit-question-light.png")
    results["Stop and exit exits"] = window._exit_app() and quits == [True]
    results["Stop and exit stopped it"] = wait(lambda: first.poll() is not None, 6)
finally:
    for process in outside:
        if process.poll() is None:
            process.kill()
    window.really_exit = True
    window.hide()

for name, passed in results.items():
    print(("PASS " if passed else "FAIL ") + name)
print("ALL PASSED" if all(results.values()) else "SOME FAILED")
