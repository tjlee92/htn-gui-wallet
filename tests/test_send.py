"""The send's decisions: above all, never saying "nothing was sent" when money may have moved."""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import psutil
import pytest

from guiwallet.core import tools
from guiwallet.core.commands import WalletContext
from guiwallet.core.send import (
    SendRequest,
    SendResult,
    _limit_after_line,
    command_preview,
    forget_send,
    interpret,
    interrupted_send_message,
    record_send_started,
    run_send,
)
from guiwallet.core.tools import CommandNotAllowed, Tool, WatchedRun, run_watched

PYTHON = Path(sys.executable)
ADDRESS = "hoosat:qq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6"
TRANSACTION_ID = "ab" * 32
CONTEXT = WalletContext("mainnet", "127.0.0.1:4282", Path("keys.json"))
BROADCASTING = "Broadcasting 1 transaction(s)"
SUCCESS_LINES = (
    BROADCASTING,
    "Broadcasted 1 transaction(s) (broadcasted 100.00% of the transactions so far)",
    "Broadcasted Transaction ID(s): ",
    f"\t{TRANSACTION_ID}",
)


def run(*lines, exit_code=None, stopped_early=False, timed_out=False, still_running=False):
    return WatchedRun(tuple(lines), exit_code, stopped_early, timed_out, still_running)


def test_a_printed_transaction_id_means_sent():
    outcome = interpret(run(*SUCCESS_LINES, exit_code=0))
    assert outcome.result is SendResult.SENT
    assert outcome.transaction_ids == (TRANSACTION_ID,)


def test_killed_after_printing_the_id_still_counts_as_sent():
    assert interpret(run(*SUCCESS_LINES, timed_out=True)).result is SendResult.SENT


@pytest.mark.parametrize(
    "line, expected",
    [
        ("Waiting for spendable UTXO.", "spendable"),
        ("Password decryption failed. Sometimes this is a result of...", "Wrong password"),
        ("Failed to sign unsigned transactions after 0 attempts: Public key doesn't match any of the transaction public keys", "wallet file"),
        ("Failed to create unsigned transactions after 0 attempts: the transaction spends 900 inputs and would have a mass of 1", "more coins"),
        ("Failed to create unsigned transactions after 0 attempts: wallet daemon is not synced yet, x", "syncing"),
        ("htnwallet daemon is not running — start it with `htnwallet start-daemon`", "isn't running"),
        ("Failed to create unsigned transactions after 0 attempts: decoded address is of unknown format: checksum", "address isn't valid"),
    ],
)
def test_failures_before_broadcast_mean_nothing_was_sent(line, expected):
    outcome = interpret(run(line, exit_code=1))
    assert outcome.result is SendResult.NOT_SENT
    assert expected in outcome.message


def test_timing_out_before_broadcast_means_nothing_was_sent():
    outcome = interpret(run(timed_out=True))
    assert outcome.result is SendResult.NOT_SENT
    assert "5 seconds" in outcome.message


@pytest.mark.parametrize(
    "the_run",
    [
        run(BROADCASTING, timed_out=True),
        run(BROADCASTING, "failed to broadcast transactions (0 of 1 were broadcast)", exit_code=1),
        run(BROADCASTING, exit_code=0),
        run(BROADCASTING, "Password decryption failed", exit_code=1),
        run("Broadcasting 2 transaction(s)", f"\t{TRANSACTION_ID}", exit_code=1),
    ],
)
def test_after_broadcast_without_every_id_it_may_have_been_sent(the_run):
    assert interpret(the_run).result is SendResult.MAY_HAVE_BEEN_SENT


def test_a_command_that_could_not_be_ended_is_never_reported_as_not_sent():
    assert interpret(run(timed_out=True, still_running=True)).result is SendResult.MAY_HAVE_BEEN_SENT


def test_the_preview_masks_the_password_and_matches_what_runs():
    request = SendRequest(ADDRESS, "1.5")
    preview = command_preview("htnwallet.exe", request, CONTEXT)
    assert "preview" not in preview
    assert "--password=••••••••" in preview
    real = request.arguments(CONTEXT, "secret")
    assert [line.strip() for line in preview.splitlines()[1:]] == [
        "--password=••••••••" if argument.startswith("--password=") else argument for argument in real
    ]


def test_send_all_and_from_address_reach_the_command():
    arguments = SendRequest(ADDRESS, None, ADDRESS).arguments(CONTEXT, "secret")
    assert "--send-all" in arguments
    assert f"--from-address={ADDRESS}" in arguments
    assert not any(argument.startswith("--send-amount") for argument in arguments)


# ----- The record of a send in progress -----


def test_a_send_that_finished_leaves_nothing_to_report(tmp_path):
    journal = tmp_path / "journal.json"
    assert interrupted_send_message(journal) is None
    record_send_started(journal, SendRequest(ADDRESS, "1.5"))
    forget_send(journal)
    assert interrupted_send_message(journal) is None


def test_a_send_that_never_finished_is_reported_without_any_password(tmp_path):
    journal = tmp_path / "journal.json"
    record_send_started(journal, SendRequest(ADDRESS, "1.5"))
    message = interrupted_send_message(journal)
    assert ADDRESS in message and "1.5 HTN" in message
    assert "password" not in journal.read_text(encoding="utf-8")


def test_a_damaged_record_still_warns(tmp_path):
    journal = tmp_path / "journal.json"
    journal.write_text("{half a fi", encoding="utf-8")
    assert "may have gone through" in interrupted_send_message(journal)


# ----- Running a real process: Python stands in for htnwallet -----


@pytest.fixture
def allow_python_c(monkeypatch):
    monkeypatch.setitem(tools.ALLOWED_COMMANDS, Tool.HTNWALLET, {"-c"})


def test_output_arrives_and_the_exit_code_is_kept(allow_python_c):
    code = "import sys; print('one'); print('two', file=sys.stderr, flush=True); sys.exit(3)"
    result = run_watched(PYTHON, Tool.HTNWALLET, "-c", [code], lambda _line: False, 20)
    assert set(result.lines) == {"one", "two"}
    assert result.exit_code == 3 and not result.timed_out and not result.stopped_early


def test_a_line_can_end_the_command_at_once(allow_python_c):
    code = "import time; print('stop now', flush=True); time.sleep(30)"
    began = time.monotonic()
    result = run_watched(PYTHON, Tool.HTNWALLET, "-c", [code], lambda line: line == "stop now", 20)
    assert time.monotonic() - began < 10
    assert result.stopped_early and result.exit_code is None and not result.still_running


def test_the_time_limit_ends_the_command_and_keeps_its_output(allow_python_c):
    code = "import time; print('got this far', flush=True); time.sleep(30)"
    began = time.monotonic()
    result = run_watched(PYTHON, Tool.HTNWALLET, "-c", [code], lambda _line: False, 2)
    assert time.monotonic() - began < 10
    assert result.timed_out and result.lines == ("got this far",) and not result.still_running


def test_broadcast_gets_a_minute_for_the_answer():
    assert _limit_after_line(BROADCASTING) == 60
    assert _limit_after_line("Broadcasted 1 transaction(s)") is None
    assert _limit_after_line("anything else") is None


def test_a_line_can_extend_the_time_limit(allow_python_c):
    code = "import time; print('Broadcasting', flush=True); time.sleep(2); print('the answer', flush=True)"
    result = run_watched(
        PYTHON, Tool.HTNWALLET, "-c", [code], lambda _line: False, 1.5, lambda line: 20 if line == "Broadcasting" else None
    )
    assert result.lines == ("Broadcasting", "the answer")
    assert not result.timed_out and result.exit_code == 0


def test_an_extended_limit_still_ends_the_command(allow_python_c):
    code = "import time; print('Broadcasting', flush=True); time.sleep(30)"
    began = time.monotonic()
    result = run_watched(PYTHON, Tool.HTNWALLET, "-c", [code], lambda _line: False, 20, lambda _line: 1)
    assert time.monotonic() - began < 10
    assert result.timed_out and not result.still_running


def test_a_real_send_is_refused_while_blocked(monkeypatch):
    monkeypatch.setattr(tools, "PILOT_BLOCKED_COMMANDS", {"send"})
    with pytest.raises(CommandNotAllowed):
        run_send(PYTHON, ["--to-address=x"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_the_command_dies_if_the_app_crashes(tmp_path):
    """A parent runs a long command, then is killed outright, as a crash or Task Manager would."""
    pid_file = tmp_path / "pid"
    parent = tmp_path / "parent.py"
    parent.write_text(
        textwrap.dedent(
            f"""
            import sys
            from pathlib import Path
            from guiwallet.core import tools
            tools.ALLOWED_COMMANDS[tools.Tool.HTNWALLET] = {{"-c"}}
            code = "import os, time; from pathlib import Path; Path({pid_file.as_posix()!r}).write_text(str(os.getpid())); time.sleep(120)"
            tools.run_watched(Path(sys.executable), tools.Tool.HTNWALLET, "-c", [code], lambda _line: False, 120)
            """
        ),
        encoding="utf-8",
    )
    source = Path(__file__).resolve().parent.parent / "src"
    process = subprocess.Popen([sys.executable, str(parent)], env={**os.environ, "PYTHONPATH": str(source)})
    try:
        deadline = time.monotonic() + 30
        while not (pid_file.exists() and pid_file.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)
        child = psutil.Process(int(pid_file.read_text()))
        assert child.is_running()
        process.kill()
        process.wait()
        child.wait(timeout=10)  # Raises if it's still alive.
    finally:
        if process.poll() is None:
            process.kill()
