"""Pictures of 1.2's light and dark modes, the About page with updates found, and the auto-compound
notice on the Send page, from the real window against the fake wallet on a private port.

Settings are never saved (the real settings.json is left alone) and GitHub is never asked: the update
check is given made-up releases. Review is never pressed, so nothing is sent.
"""

import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication

import guiwallet.ui.main_window as main_window
from guiwallet.core.commands import wallet_context
from guiwallet.core.compound import RunningCompound
from guiwallet.core.daemon import port_is_open, start_daemon
from guiwallet.core.settings import Settings
from guiwallet.core.updates import Release
from guiwallet.ui import theme
from guiwallet.ui.address_dialog import AddAddressDialog
from guiwallet.ui.compound_dialog import StartCompoundDialog
from guiwallet.ui.main_window import MainWindow, State
from guiwallet.ui.setup_wizard import SetupWizard

HERE = Path(__file__).parent
FAKE = HERE / "fakewallet" / "bin"
OUT = HERE / "theme-scratch"  # Git-ignored, like the other *-scratch folders.
OUT.mkdir(parents=True, exist_ok=True)
PORT = 47962

main_window.save_settings = lambda *_args, **_kwargs: None  # Never touch the real settings.json.
RELEASES = {
    main_window.APP_REPO: Release("1.3.0", "https://github.com/tjlee92/htn-gui-wallet/releases/tag/v1.3.0"),
    main_window.HTND_REPO: Release("99.0.0", "https://github.com/HoosatNetwork/HTND/releases/tag/v99.0.0"),
}
main_window.latest_release = lambda repo: RELEASES[repo]

app = QApplication(sys.argv)
theme.apply_theme(app, theme.DARK)
app.setQuitOnLastWindowClosed(False)
results: dict[str, bool] = {}


def wait(condition, seconds=15):
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return condition()


def settle(seconds=0.35):
    wait(lambda: False, seconds)


def shot(widget, name):
    settle()
    widget.grab().save(str(OUT / name))


def over(window, dialog, name):
    dialog.show()
    settle(0.4)
    picture = window.grab()
    painter = QPainter(picture)
    painter.fillRect(picture.rect(), QColor(5, 8, 16, 150) if theme.mode() == theme.DARK else QColor(20, 28, 45, 90))
    top = dialog.grab()
    painter.drawPixmap(QPoint((picture.width() - top.width()) // 2, (picture.height() - top.height()) // 2), top)
    painter.end()
    picture.save(str(OUT / name))
    dialog.hide()


options = Settings(tools_folder=str(FAKE), daemon_address=f"127.0.0.1:{PORT}")
daemon = start_daemon(FAKE / "htnwallet.exe", wallet_context(options), "localhost", HERE / "shot-theme.log")
try:
    wait(lambda: port_is_open(PORT), 10)
    window = MainWindow(options)
    window.show()
    results["running"] = wait(lambda: window.state is State.RUNNING and window.balance.text() != "—")
    results["htnwallet version read"] = wait(lambda: window.htnd_version is not None, 30)
    window.check_timer.stop()
    window.banner.hide()
    nav = window.navigation

    results["badge hidden before a check"] = window.update_badge.isHidden()
    window._check_updates()
    results["two updates found"] = wait(lambda: not window.checking_updates, 5) and window.update_count() == 2
    results["badge shows 2"] = window.update_badge.isVisible() and window.update_badge.text() == "2"
    results["app download enabled"] = window.about_page.app_download.isEnabled()

    addresses = list(window.wallet_page.rows)
    compound = RunningCompound(pid=1, created=0.0, from_addresses=(addresses[0],), to_address=None, interval=60)
    window.wallet_page.show_compounds([compound])
    window.send_page.show_compounds([compound])
    window.compound_timer.stop()  # Its scan would find no real auto-compound and clear the made-up one.

    for mode in (theme.DARK, theme.LIGHT_MODE):
        if theme.mode() != mode:
            window.title_bar.theme_button.click()  # The real button, switching the open window.
        results[f"{mode}: mode switched"] = theme.mode() == mode and window.settings.theme == mode
        nav.button(0).click()
        shot(window, f"{mode}-1-wallet.png")
        nav.button(1).click()
        page = window.send_page
        page.other_tab.click()
        page.source.setCurrentIndex(page.source.findData(addresses[0]))
        page.amount.setText("500")
        results[f"{mode}: notice shown"] = page.notice_holder.isVisible()
        shot(window, f"{mode}-2-send-notice.png")
        page.clear()
        # Transaction History is left out: it would ask the real explorer about the fake wallet's addresses.
        nav.button(4).click()
        shot(window, f"{mode}-4-settings.png")
        nav.button(5).click()
        shot(window, f"{mode}-5-about.png")
        over(window, StartCompoundDialog(window, addresses[1], addresses), f"{mode}-6-compound.png")
        over(window, AddAddressDialog(window), f"{mode}-7-add-address.png")
        wizard = SetupWizard(None, options)
        wizard.show()
        shot(wizard, f"{mode}-8-wizard.png")
        wizard.pages.setCurrentIndex(6)
        shot(wizard, f"{mode}-9-wizard-seed.png")
        wizard.reject()

    # Switching from the wizard switches the main window too, and the setting follows.
    wizard = SetupWizard(window, window.settings)
    wizard.show()
    settle()
    wizard.findChild(main_window.TitleBar).theme_button.click()
    results["wizard switch reaches the main window"] = theme.mode() == theme.DARK and window.settings.theme == "dark"
    results["wizard draft keeps the mode"] = wizard.draft.theme == "dark"
    wizard.reject()

    window.really_exit = True
    window.hide()
finally:
    daemon.kill()
    daemon.wait()

for name, passed in results.items():
    print(("PASS " if passed else "FAIL ") + name)
print("ALL PASSED" if all(results.values()) else "SOME FAILED")
print("send ran:", "send" in (FAKE / "commands.log").read_text().split())
print(f"Pictures in {OUT}")
