"""Starts the app."""

import sys

from PySide6.QtWidgets import QApplication, QDialog

from guiwallet.core.settings import load_settings, save_settings, settings_path
from guiwallet.core.setup import is_first_launch
from guiwallet.ui.main_window import APP_NAME, MainWindow
from guiwallet.ui.setup_wizard import SetupWizard
from guiwallet.ui.single_instance import SingleInstance
from guiwallet.ui.theme import app_icon, apply_theme


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    apply_theme(application)
    application.setWindowIcon(app_icon())
    # Closing the window may only minimize it to the tray, so the app decides when to exit.
    application.setQuitOnLastWindowClosed(False)
    instance = SingleInstance(settings_path().parent)
    if not instance.claim():
        return 0  # Already open: that copy has been asked to show its window.
    shown: dict = {}

    def show_existing() -> None:
        if "window" in shown:
            shown["window"].show_from_tray()
        elif "wizard" in shown:
            shown["wizard"].raise_()
            shown["wizard"].activateWindow()

    instance.on_show_requested(show_existing)
    settings = load_settings()
    action, process = None, None
    if is_first_launch():
        # Shown once: finishing it saves the settings, and saved settings mean it isn't the first launch.
        wizard = shown["wizard"] = SetupWizard(None, settings)
        if wizard.exec() != QDialog.DialogCode.Accepted or not wizard.action:
            return 0
        del shown["wizard"]
        action, process = wizard.action, wizard.started_process
        try:
            save_settings(settings)
        except OSError:
            pass  # The wizard will simply show again next time.
    window = shown["window"] = MainWindow(settings)
    window.show()
    if action:
        window.after_wizard(action, process=process)
    return application.exec()
