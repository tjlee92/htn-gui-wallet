import os
import time
from decimal import Decimal
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from guiwallet.core.commands import WalletContext  # noqa: E402
from guiwallet.core.compound import (  # noqa: E402
    CompoundError,
    Compounders,
    RunningCompound,
    compound_arguments,
    find_compounds,
    parse_compound_command,
    validate_interval,
)
from guiwallet.core.daemon import WalletSnapshot  # noqa: E402
from guiwallet.core.tools import ALLOWED_COMMANDS, Tool  # noqa: E402
from guiwallet.core.validation import InvalidInput  # noqa: E402
from guiwallet.core.wallet import AddressBalance, WalletBalance, WalletCoins  # noqa: E402
from guiwallet.ui.compound_dialog import StartCompoundDialog, StopCompoundDialog  # noqa: E402
from guiwallet.ui.pages import WalletPage  # noqa: E402
from guiwallet.ui.widgets import HIDDEN_AMOUNT  # noqa: E402
from guiwallet.ui.widgets import CopyIconButton  # noqa: E402

ONE = "hoosat:qq" + "a" * 61
TWO = "hoosat:qq" + "p" * 61
OTHER_WALLET = "hoosat:qq" + "z" * 61
CONTEXT = WalletContext("mainnet", "localhost:8082")
EXE = "C:\\Hoosat\\htnwallet.exe"
# Built from scripts/dev-tests/fakewallet; the tests that run it are skipped without it.
FAKE_WALLET = Path(os.environ.get("FAKE_HTNWALLET", "scripts/dev-tests/fakewallet/bin/htnwallet.exe"))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


PARENTS = []  # Keeps each window's parent alive for the test: Qt deletes a window with its parent.


def parent():
    widget = QWidget()
    PARENTS.append(widget)
    return widget


# ----- Reading another program's command line -----


def test_reads_the_users_own_command_line():
    assert parse_compound_command([EXE, "auto-compound", "-a", ONE, "-t", TWO, "-u", "-c", "30"]) == ((ONE,), TWO, 30)


def test_reads_attached_and_long_forms():
    words = [EXE, "auto-compound", f"--from-address={ONE}", f"-t{TWO}", "--compound-rate", "90"]
    assert parse_compound_command(words) == ((ONE,), TWO, 90)


def test_reads_windows_style_flags():
    assert parse_compound_command([EXE, "auto-compound", "/a", ONE, f"/to-address:{TWO}", "/c:45"]) == ((ONE,), TWO, 45)


def test_reads_several_from_addresses():
    assert parse_compound_command([EXE, "auto-compound", "-a", ONE, "-a", TWO, "-t", TWO])[0] == (ONE, TWO)


def test_never_takes_the_password_for_an_address():
    words = [EXE, "auto-compound", "-p", "-a", "--password", "secret", "-t", TWO]
    from_addresses, to_address, _ = parse_compound_command(words)
    assert "secret" not in from_addresses and to_address == TWO
    assert from_addresses == ()  # "-a" was the password there, not a flag.


def test_a_missing_or_too_short_interval_is_sixty_seconds_like_htnwallet():
    assert parse_compound_command([EXE, "auto-compound", "-t", TWO])[2] == 60
    assert parse_compound_command([EXE, "auto-compound", "-t", TWO, "-c", "3"])[2] == 60
    assert parse_compound_command([EXE, "auto-compound", "-t", TWO, "/c", "12:30:01.55"])[2] == 60


def test_other_commands_are_not_auto_compounds():
    assert parse_compound_command([EXE, "start-daemon", "-a", ONE]) is None
    assert parse_compound_command([EXE]) is None
    assert parse_compound_command([]) is None


def test_which_addresses_an_auto_compound_covers():
    wallet = [ONE, TWO]
    assert RunningCompound(1, 0, (ONE,), ONE, 60).covers(ONE, wallet)
    assert not RunningCompound(1, 0, (ONE,), ONE, 60).covers(TWO, wallet)
    assert RunningCompound(1, 0, (), TWO, 60).covers(ONE, wallet)  # No -a: the whole wallet.
    assert not RunningCompound(1, 0, (), OTHER_WALLET, 60).covers(ONE, wallet)  # Another wallet's.


def test_finding_compounds_never_fails_on_this_machine():
    for compound in find_compounds():
        assert compound.pid > 0 and compound.interval >= 6


# ----- Starting one -----


def test_arguments_match_the_users_command():
    arguments = compound_arguments(CONTEXT, ONE, TWO, 60, "pw")
    assert arguments[:5] == [f"--from-address={ONE}", f"--to-address={TWO}", "-u", "--compound-rate=60", "--password=pw"]


def test_arguments_refuse_a_bad_address():
    with pytest.raises(InvalidInput):
        compound_arguments(CONTEXT, "nonsense", TWO, 60, "pw")


def test_auto_compound_is_an_allowed_command():
    assert "auto-compound" in ALLOWED_COMMANDS[Tool.HTNWALLET]


@pytest.mark.parametrize("text", ["5", "86401", "abc", ""])
def test_interval_out_of_range_is_refused(text):
    with pytest.raises(InvalidInput):
        validate_interval(text)


def test_interval_in_range():
    assert validate_interval(" 6 ") == 6 and validate_interval("86400") == 86400


needs_fake = pytest.mark.skipif(not FAKE_WALLET.is_file(), reason="fake htnwallet not built")


@needs_fake
def test_wrong_password_is_reported_and_nothing_keeps_running(monkeypatch):
    monkeypatch.setenv("FAKE_COMPOUND", "wrongpassword")
    with pytest.raises(CompoundError, match="Wrong password"):
        Compounders.launch(FAKE_WALLET.resolve(), CONTEXT, ONE, ONE, 60, "pw")


@needs_fake
def test_a_started_one_counts_its_compounds_and_stops(monkeypatch):
    monkeypatch.delenv("FAKE_COMPOUND", raising=False)
    own = Compounders()
    pid = own.add(Compounders.launch(FAKE_WALLET.resolve(), CONTEXT, ONE, TWO, 60, "pw"))
    try:
        found = {compound.pid: compound for compound in find_compounds(own.counts())}
        assert found[pid].started_here and found[pid].from_addresses == (ONE,) and found[pid].to_address == TWO
        assert own.details(pid)[0] >= 1
        assert own.ended() == []
    finally:
        assert own.stop(pid)
    time.sleep(0.5)
    assert pid not in {compound.pid for compound in find_compounds()}


# ----- The Wallet page -----


def wallet_page(app):
    page = WalletPage()
    balance = WalletBalance(
        Decimal(10), Decimal(0), (AddressBalance(ONE, Decimal(8), Decimal(0)), AddressBalance(TWO, Decimal(2), Decimal(0)))
    )
    page.show_snapshot(WalletSnapshot(balance, WalletCoins(3, {ONE: 2, TWO: 1})))
    return page


def test_wallet_figures_are_followed_by_htn_but_the_coin_count_is_not(app):
    page = wallet_page(app)
    assert page.available.text().startswith("10<") and page.available.text().endswith("HTN</span>")
    pending, total, utxos = page.rows[ONE]
    assert pending.text().startswith("0<") and total.text().startswith("8<") and "HTN" in total.text()
    assert utxos.text() == "2" and page.coin_count.text() == "3"
    page.set_balances_hidden(True)
    assert page.available.text().startswith(HIDDEN_AMOUNT) and "HTN" in page.available.text()


def test_each_address_has_a_copy_button_that_copies_it_in_full(app):
    page = wallet_page(app)
    buttons = page.findChildren(CopyIconButton)
    assert [button.text_to_copy for button in buttons] == [ONE, TWO]
    buttons[1].click()
    assert QGuiApplication.clipboard().text() == TWO


def test_compound_buttons_show_what_is_running(app):
    page = wallet_page(app)
    assert "Start auto-compound" == page.compound_buttons[ONE].toolTip()
    page.show_compounds([RunningCompound(5, 0, (ONE,), ONE, 30)])
    assert "outside this app" in page.compound_buttons[ONE].toolTip()
    assert "every 30 seconds" in page.compound_buttons[ONE].toolTip()
    assert page.compound_buttons[TWO].toolTip() == "Start auto-compound"
    page.show_compounds([])
    assert page.compound_buttons[ONE].toolTip() == "Start auto-compound"


def test_a_whole_wallet_compound_marks_every_row(app):
    page = wallet_page(app)
    page.show_compounds([RunningCompound(5, 0, (), TWO, 60)])
    assert all("running" in button.toolTip() for button in page.compound_buttons.values())


def test_the_app_prefers_its_own_compound_for_a_row(app):
    page = wallet_page(app)
    outside = RunningCompound(5, 0, (), TWO, 60)
    own = RunningCompound(6, 0, (ONE,), ONE, 60, started_here=True)
    page.show_compounds([outside, own])
    assert page.compound_for(ONE) is own and page.compound_for(TWO) is outside


def test_pressing_a_compound_button_names_its_address(app):
    page = wallet_page(app)
    pressed = []
    page.compound_clicked.connect(pressed.append)
    page.compound_buttons[TWO].click()
    assert pressed == [TWO]


def test_figures_refresh_without_losing_the_compound_marks(app):
    page = wallet_page(app)
    page.show_compounds([RunningCompound(5, 0, (TWO,), TWO, 60)])
    page.show_message("htnwallet stopped")
    page.show_snapshot(wallet_page(app).snapshot)
    assert "running" in page.compound_buttons[TWO].toolTip()


# ----- The windows -----


def test_start_window_sends_the_choice(app):
    dialog = StartCompoundDialog(parent(), ONE, [ONE, TWO])
    assert dialog.target.currentData() == ONE  # Same address first.
    assert not dialog.start_button.isEnabled()
    asked = []
    dialog.start_requested.connect(lambda *values: asked.append(values))
    dialog.target.setCurrentIndex(1)
    dialog.interval.setText("90")
    dialog.password.setText("pw")
    dialog.start_button.click()
    assert asked == [(TWO, 90, "pw")]
    assert dialog.busy and not dialog.cancel_button.isEnabled()


def test_start_window_refuses_a_short_interval_and_recovers_after_an_error(app):
    dialog = StartCompoundDialog(parent(), ONE, [ONE])
    asked = []
    dialog.start_requested.connect(lambda *values: asked.append(values))
    dialog.interval.setText("3")
    dialog.password.setText("pw")
    dialog.start_button.click()
    assert asked == [] and not dialog.error.isHidden()
    dialog.interval.setText("60")
    dialog.start_button.click()
    dialog.show_error("Wrong password")
    assert not dialog.busy and dialog.start_button.isEnabled() and dialog.start_button.text() == "Start"


def test_stop_window_says_where_it_was_started(app):
    outside = StopCompoundDialog(parent(), RunningCompound(5, 0, (ONE,), ONE, 60), None)
    texts = [widget.text() for widget in outside.findChildren(QWidget) if hasattr(widget, "text")]
    assert "Outside this application" in texts and "Same address" in texts
    own = StopCompoundDialog(parent(), RunningCompound(6, 0, (ONE,), TWO, 60, True, 3), (3, time.time()))
    texts = [widget.text() for widget in own.findChildren(QWidget) if hasattr(widget, "text")]
    assert any(text.startswith("3 transactions since") for text in texts) and TWO in texts
    stops = []
    own.stop_requested.connect(lambda: stops.append(True))
    own.stop_button.click()
    assert stops == [True] and own.busy
    assert isinstance(own.stop_button, QPushButton)


@needs_fake
def test_start_finishes_as_soon_as_htnwallet_prints_a_result(monkeypatch):
    monkeypatch.delenv("FAKE_COMPOUND", raising=False)
    began = time.monotonic()
    started = Compounders.launch(FAKE_WALLET.resolve(), CONTEXT, ONE, ONE, 60, "pw")
    try:
        assert started.answered and time.monotonic() - began < 2
    finally:
        started.tied.stop()


def test_only_htnwallet_result_lines_count_as_an_answer():
    from guiwallet.core.compound import _RESULT_LINE

    assert _RESULT_LINE.match("[15:04:05] COMPOUNDED → https://explorer-eu.hoosat.network/txs/ab")
    assert _RESULT_LINE.match("[15:04:05] NOTHING TO COMPOUND, backing off for 5m")
    assert not _RESULT_LINE.match("Hoosat Auto-Compounder STARTED → 1 compound tx every 60 seconds")
    assert not _RESULT_LINE.match("wrong password: chacha20poly1305: message authentication failed")
