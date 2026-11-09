"""These run a real process (Python itself standing in for a wallet tool), so they
check what actually reaches a program, including on the Windows command line."""

import json
import logging
import sys
from pathlib import Path

import pytest

from guiwallet.core import tools
from guiwallet.core.tools import CommandNotAllowed, CommandTimedOut, Tool, ToolNotFound, find_tool, run_tool

PYTHON = Path(sys.executable)
SECRET = "hunter2-secret"


@pytest.fixture
def allow_python_c(monkeypatch):
    """Let `python -c <code>` through the allowlist for these tests."""
    monkeypatch.setitem(tools.ALLOWED_COMMANDS, Tool.HTNWALLET, {"-c"})


@pytest.mark.parametrize("password", ['-dash', "/slash", "two words", 'mid"quote', "trail\\", "a&b|c>d", "%PATH%", "é€"])
def test_arguments_reach_the_program_unchanged(allow_python_c, password):
    code = "import sys, json; sys.stdout.reconfigure(encoding='utf-8'); print(json.dumps(sys.argv[1:]))"
    result = run_tool(PYTHON, Tool.HTNWALLET, "-c", [code, f"--password={password}"])
    assert result.succeeded
    assert json.loads(result.stdout) == [f"--password={password}"]


@pytest.mark.parametrize(
    "tool, command",
    [
        (Tool.HTNWALLET, "sweep"),
        (Tool.HTNWALLET, "vote"),
        (Tool.HTNCTL, "Ban"),
        (Tool.HTNCTL, "SubmitTransaction"),
        (Tool.HTNCTL, "getinfo"),
    ],
)
def test_refuses_commands_not_on_the_list(tool, command):
    with pytest.raises(CommandNotAllowed):
        run_tool(PYTHON, tool, command)


def test_timeout_error_does_not_contain_the_password(allow_python_c):
    with pytest.raises(CommandTimedOut) as caught:
        run_tool(PYTHON, Tool.HTNWALLET, "-c", ["import time; time.sleep(30)", f"--password={SECRET}"], timeout=0.5)
    assert SECRET not in str(caught.value)
    assert caught.value.__suppress_context__


def test_arguments_are_not_logged(allow_python_c, caplog):
    with caplog.at_level(logging.DEBUG):
        run_tool(PYTHON, Tool.HTNWALLET, "-c", ["pass", f"--password={SECRET}"])
    assert SECRET not in caplog.text


def test_printing_a_result_does_not_show_its_output(allow_python_c):
    result = run_tool(PYTHON, Tool.HTNWALLET, "-c", [f"print('{SECRET}')"])
    assert SECRET in result.stdout
    assert SECRET not in repr(result)


def test_nonzero_exit_is_reported(allow_python_c):
    result = run_tool(PYTHON, Tool.HTNWALLET, "-c", ["import sys; sys.exit(1)"])
    assert not result.succeeded


def test_finds_a_tool_only_in_the_given_folders(tmp_path, monkeypatch):
    filename = "htnwallet.exe" if sys.platform == "win32" else "htnwallet"
    on_path = tmp_path / "on_path"
    on_path.mkdir()
    (on_path / filename).touch()
    monkeypatch.setenv("PATH", str(on_path))
    monkeypatch.chdir(on_path)

    with pytest.raises(ToolNotFound):
        find_tool(Tool.HTNWALLET, [tmp_path])
    assert find_tool(Tool.HTNWALLET, [tmp_path, on_path]) == (on_path / filename).resolve()
