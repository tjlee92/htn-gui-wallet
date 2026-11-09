import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from guiwallet.ui.background import run_in_background  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for(app, condition):
    deadline = time.time() + 5
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    return condition()


def test_results_reach_a_plain_function_on_the_main_thread(app):
    received = []

    def finished(result):  # A local function, which has no Qt object to keep it alive.
        received.append((result, threading.current_thread()))

    run_in_background(lambda: threading.current_thread(), finished, lambda _error: None)

    assert wait_for(app, lambda: received)
    worker_thread, callback_thread = received[0]
    assert worker_thread is not threading.main_thread()
    assert callback_thread is threading.main_thread()


def test_errors_reach_the_failure_callback_on_the_main_thread(app):
    received = []

    def work():
        raise ValueError("expected")

    run_in_background(
        work,
        lambda _result: pytest.fail("should not finish"),
        lambda error: received.append((error, threading.current_thread())),
    )

    assert wait_for(app, lambda: received)
    error, callback_thread = received[0]
    assert isinstance(error, ValueError)
    assert callback_thread is threading.main_thread()
