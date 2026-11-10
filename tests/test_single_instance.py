import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from guiwallet.ui.single_instance import SingleInstance  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for(app, condition, seconds=3.0):
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    return condition()


def test_only_the_first_copy_runs_and_the_second_asks_it_to_show(app, tmp_path):
    first = SingleInstance(tmp_path)
    assert first.claim()
    shown = []
    first.on_show_requested(lambda: shown.append(True))
    second = SingleInstance(tmp_path)
    assert not second.claim()
    assert wait_for(app, lambda: shown)
    first.release()


def test_the_claim_is_free_again_once_the_first_copy_ends(app, tmp_path):
    first = SingleInstance(tmp_path)
    assert first.claim()
    first.release()
    assert SingleInstance(tmp_path).claim()


def test_the_folder_is_made_if_missing(app, tmp_path):
    folder = tmp_path / "new" / "guiwallet"
    assert SingleInstance(folder).claim()


def test_a_lock_left_by_a_crashed_copy_does_not_block_the_next_start(app, tmp_path):
    import subprocess
    import sys

    code = (
        "import sys, time; sys.path.insert(0, 'src');"
        "from guiwallet.ui.single_instance import SingleInstance;"
        f"s = SingleInstance(__import__('pathlib').Path(r'{tmp_path}'));"
        "print(s.claim(), flush=True); time.sleep(60)"
    )
    crashed = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert crashed.stdout.readline().strip() == "True"
    assert not SingleInstance(tmp_path).claim()  # Still running: it holds the lock.
    crashed.kill()
    crashed.wait()
    assert SingleInstance(tmp_path).claim()
