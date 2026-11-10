"""Finding and running htnwallet and htnctl.

This is the only place the app starts another program, and it enforces three rules:

* Only commands on the lists below can run. Anything else is refused.
* Arguments go straight to the program, with no shell that could reinterpret them.
* Arguments are never logged or put in an error message, because they can hold the password.
"""

import ctypes
import logging
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)


class Tool(Enum):
    HTNWALLET = "htnwallet"
    HTNCTL = "htnctl"


ALLOWED_COMMANDS = {
    Tool.HTNWALLET: {
        "auto-compound",
        "balance",
        "get-daemon-version",
        "new-address",
        "send",
        "show-addresses",
        "start-daemon",
        "utxos",
        "version",
    },
    # Read-only on purpose: nothing here can change what a node does.
    Tool.HTNCTL: {
        "EstimateNetworkHashesPerSecond",
        "GetBlockCount",
        "GetBlockDagInfo",
        "GetCoinSupply",
        "GetConnectedPeerInfo",
        "GetCurrentNetwork",
        "GetInfo",
        "GetPeerAddresses",
        "GetSelectedTipHash",
        "GetTransactionStatus",
        "GetVirtualSelectedParentBlueScore",
    },
}

# Only the Setup Wizard runs these, and only through run_setup_command: they make a wallet, or (the
# dump) read back the seed phrase of the wallet it has just made. run_tool refuses them.
SETUP_COMMANDS = {"create", "dump-unencrypted-data", "import-web-wallet", "import-private-key"}

# PILOT: while the interface is being tried out, these are refused even though they're on the list
# above. `send` was taken off for the first real send tests. Add it back to switch sending off.
PILOT_BLOCKED_COMMANDS: set[str] = set()

# How long to wait for a command to exit, once it has been ended or has closed its output.
_EXIT_WAIT_SECONDS = 2

# Stops a console window from flashing up each time a command runs on Windows.
_HIDDEN_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

if sys.platform.startswith("linux"):
    _LIBC = ctypes.CDLL(None, use_errno=True)
    _PR_SET_PDEATHSIG = 1
    _SIGKILL = 9

    def _die_with_this_app() -> None:
        """Runs in the new process before htnwallet starts: Linux kills it when its starter goes away.

        Linux ties this to the starting thread, not the whole app, so it's only used where that
        thread waits for the command, is the main thread, or is kept for it (_popen_kept).
        """
        _LIBC.prctl(_PR_SET_PDEATHSIG, _SIGKILL)
else:
    _die_with_this_app = None


def _linux_tie(thread_waits: bool):
    """What to pass as preexec_fn: the Linux tie, when it can't end the command too early."""
    if _die_with_this_app is None:
        return None
    if thread_waits or threading.current_thread() is threading.main_thread():
        return _die_with_this_app
    return None


def _popen_kept(argv: list[str], **options) -> subprocess.Popen:
    """Start a long-running command from a thread of its own that stays until the command ends.

    On Linux the tie follows the starting thread, so a command started from a short-lived worker
    thread would lose it. This thread lives exactly as long as the command, so the command still
    ends if the app dies, and isn't ended while the app is fine. Elsewhere it's a plain Popen.
    """
    if _die_with_this_app is None:
        return subprocess.Popen(argv, **options)
    handed_over: queue.SimpleQueue = queue.SimpleQueue()

    def keep() -> None:
        try:
            process = subprocess.Popen(argv, preexec_fn=_die_with_this_app, **options)
        except BaseException as error:  # Handed to the caller, which raises it there.
            handed_over.put(error)
            return
        handed_over.put(process)
        process.wait()

    threading.Thread(target=keep, name="keeps-htnwallet", daemon=True).start()
    result = handed_over.get()
    if isinstance(result, BaseException):
        raise result
    return result


class ToolError(Exception):
    """A tool could not be run. The message never contains the command's arguments."""


class ToolNotFound(ToolError):
    pass


class CommandNotAllowed(ToolError):
    pass


class CommandTimedOut(ToolError):
    pass


@dataclass(frozen=True)
class CommandResult:
    tool: Tool
    command: str
    exit_code: int
    # Left out of repr so that printing a result can't expose a recovery phrase.
    stdout: str = field(repr=False)
    stderr: str = field(repr=False)

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class WatchedRun:
    """How a watched command went. `lines` holds stdout and stderr together, in the order written."""

    lines: tuple[str, ...] = field(repr=False)
    exit_code: int | None  # None if it was ended rather than finishing by itself.
    stopped_early: bool  # A line of its output asked for it to be ended.
    timed_out: bool
    still_running: bool  # It could not be ended, which should never happen.


def sending_switched_off() -> bool:
    return "send" in PILOT_BLOCKED_COMMANDS


def find_tool(tool: Tool, folders: list[Path]) -> Path:
    """Look for a tool in the given folders only.

    The system PATH is deliberately not searched: it could lead to a different program
    with the same name, and that program would be handed the wallet password.
    """
    filename = tool.value + (".exe" if sys.platform == "win32" else "")
    for folder in folders:
        candidate = folder / filename
        if candidate.is_file():
            return candidate.resolve()
    searched = ", ".join(str(folder) for folder in folders) or "no folders"
    raise ToolNotFound(f"{filename} was not found (looked in: {searched})")


def app_folder() -> Path | None:
    """The folder holding the built app, or None when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return None


def _command_line(
    tool_path: Path,
    tool: Tool,
    command: str,
    arguments: list[str] | None,
    trailing: list[str] | None,
) -> list[str]:
    if command not in ALLOWED_COMMANDS[tool]:
        raise CommandNotAllowed(f"{tool.value} {command} is not allowed from this app")
    if command in PILOT_BLOCKED_COMMANDS:
        raise CommandNotAllowed(f"{tool.value} {command} is switched off during the pilot")
    arguments = arguments or []
    trailing = trailing or []
    if tool is Tool.HTNWALLET:
        return [str(tool_path), command, *arguments, *trailing]
    return [str(tool_path), *arguments, command, *trailing]


def start_in_background(
    tool_path: Path,
    tool: Tool,
    command: str,
    arguments: list[str],
    log_path: Path,
) -> subprocess.Popen:
    """Start an allowed long-running command, hidden, with its output going to a log file.

    It isn't tied to this app, so it carries on running after the app exits.
    """
    argv = _command_line(tool_path, tool, command, arguments, None)
    log.info("Starting %s %s", tool.value, command)  # Never the arguments.
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("wb") as output:
            return subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                # Not the app's folder: a single-file build unpacks to a temporary one.
                cwd=tool_path.parent,
                creationflags=_HIDDEN_WINDOW,
            )
    except OSError as error:
        raise ToolError(f"Could not start {tool.value}: {error.strerror}") from None


def _end_with_this_app(process: subprocess.Popen) -> int | None:
    """Have Windows end the process if this app dies, even in a crash. Returns a handle to close later.

    On Linux the same is done with `_linux_tie` when the process starts.

    Without this, a command left behind by a crash carries on by itself with nobody watching it.
    """
    if sys.platform != "win32":
        return None

    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", ctypes.c_ulonglong * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    extended_limit_information = 9
    kill_on_job_close = 0x2000

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        log.warning("Could not tie a command to this app")
        return None
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = kill_on_job_close
    tied = kernel32.SetInformationJobObject(
        job, extended_limit_information, ctypes.byref(limits), ctypes.sizeof(limits)
    ) and kernel32.AssignProcessToJobObject(job, int(process._handle))
    if not tied:
        log.warning("Could not tie a command to this app")
        kernel32.CloseHandle(job)
        return None
    return job


def _close_job(job: int | None) -> None:
    if job:
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.wintypes.HANDLE(job))


@dataclass
class TiedProcess:
    """A long-running command that Windows ends together with this app, its output read line by line."""

    process: subprocess.Popen
    job: int | None
    on_line: Callable[[str], None]
    reader: threading.Thread | None = None

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.wait(timeout=_EXIT_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        _close_job(self.job)
        self.job = None


def start_tied(
    tool_path: Path,
    tool: Tool,
    command: str,
    arguments: list[str],
    on_line: Callable[[str], None],
) -> TiedProcess:
    """Start an allowed long-running command that ends with this app, even if the app crashes.

    `on_line` is called on a background thread for each line it writes, stdout and stderr together.
    """
    argv = _command_line(tool_path, tool, command, arguments, None)
    log.info("Starting %s %s", tool.value, command)  # Never the arguments.
    try:
        process = _popen_kept(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            cwd=tool_path.parent,
            creationflags=_HIDDEN_WINDOW,
        )
    # "from None" matters: Python's original errors include the full command line.
    except OSError as error:
        raise ToolError(f"Could not start {tool.value}: {error.strerror}") from None
    tied = TiedProcess(process, _end_with_this_app(process), on_line)

    def read_output() -> None:
        for line in process.stdout:
            on_line(line.rstrip("\r\n"))
        process.stdout.close()

    tied.reader = threading.Thread(target=read_output, daemon=True)
    tied.reader.start()
    return tied


def run_watched(
    tool_path: Path,
    tool: Tool,
    command: str,
    arguments: list[str],
    stop_on: Callable[[str], bool],
    timeout: float,
    new_limit: Callable[[str], float | None] | None = None,
) -> WatchedRun:
    """Run an allowed command, reading its output as it's written.

    The command is ended straight away if `stop_on` returns True for a line, or at `timeout`. Lines it
    had written before being ended are always included, so the caller knows how far it got.

    `new_limit` can give a line a new time limit, in seconds from that line, replacing the current one.
    """
    argv = _command_line(tool_path, tool, command, arguments, None)
    log.info("Running %s %s", tool.value, command)  # Never the arguments.
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            creationflags=_HIDDEN_WINDOW,
            preexec_fn=_linux_tie(thread_waits=True),
        )
    # "from None" matters: Python's original errors include the full command line.
    except OSError as error:
        raise ToolError(f"Could not start {tool.value}: {error.strerror}") from None
    job = _end_with_this_app(process)

    incoming: queue.SimpleQueue[str | None] = queue.SimpleQueue()

    def read_output() -> None:
        for line in process.stdout:
            incoming.put(line.rstrip("\r\n"))
        incoming.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    lines: list[str] = []
    stopped_early = timed_out = False
    line: str | None = ""
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                line = incoming.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                timed_out = True
                break
            if line is None:
                break
            lines.append(line)
            if stop_on(line):
                stopped_early = True
                break
            if new_limit and (seconds := new_limit(line)) is not None:
                deadline = time.monotonic() + seconds
    finally:
        ended_it = stopped_early or timed_out or process.poll() is None and line is not None
        if ended_it:
            process.kill()
        try:
            process.wait(timeout=_EXIT_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=_EXIT_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        reader.join(timeout=_EXIT_WAIT_SECONDS)
        still_running = process.poll() is None
        _close_job(job)

    # What it wrote just before it ended counts too.
    while True:
        try:
            line = incoming.get_nowait()
        except queue.Empty:
            break
        if line is not None:
            lines.append(line)
    if not reader.is_alive():
        process.stdout.close()

    exit_code = None if ended_it or still_running else process.returncode
    log.info("%s %s finished after %d lines of output", tool.value, command, len(lines))
    return WatchedRun(tuple(lines), exit_code, stopped_early, timed_out, still_running)


def run_tool(
    tool_path: Path,
    tool: Tool,
    command: str,
    arguments: list[str] | None = None,
    trailing: list[str] | None = None,
    timeout: float | None = 60,
) -> CommandResult:
    """Run an allowed command and wait for it to finish.

    htnwallet takes its command first, then flags. htnctl takes flags first, then the
    command, then the command's values. `arguments` are the flags and `trailing` the values.

    Always keep a `timeout` for htnctl: it ignores its own --timeout flag while it can't
    connect, so an unreachable node would otherwise leave it waiting indefinitely.
    """
    argv = _command_line(tool_path, tool, command, arguments, trailing)

    log.info("Running %s %s", tool.value, command)  # Never the arguments.
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=_HIDDEN_WINDOW,
            check=False,
        )
    # "from None" matters: Python's original errors include the full command line.
    except subprocess.TimeoutExpired:
        raise CommandTimedOut(f"{tool.value} {command} did not finish in time") from None
    except OSError as error:
        raise ToolError(f"Could not start {tool.value}: {error.strerror}") from None

    log.info("%s %s exited with code %d", tool.value, command, completed.returncode)
    return CommandResult(tool, command, completed.returncode, completed.stdout, completed.stderr)


def run_setup_command(
    tool_path: Path,
    command: str,
    arguments: list[str],
    stdin_text: str | None = None,
    timeout: float = 120,
) -> CommandResult:
    """Run one of the Setup Wizard's htnwallet commands and wait for it to finish.

    `stdin_text` is what htnwallet reads when it prompts, such as a seed phrase: it's written exactly as
    given, so htnwallet sees what the user typed, as on the command line. Without it htnwallet's input is
    empty, so a question it asks (such as whether to overwrite a file) is answered with nothing and refused.
    """
    if command not in SETUP_COMMANDS:
        raise CommandNotAllowed(f"htnwallet {command} is not a setup command")
    argv = [str(tool_path), command, *arguments]
    log.info("Running htnwallet %s", command)  # Never the arguments or the input.
    try:
        completed = subprocess.run(
            argv,
            input=stdin_text if stdin_text is not None else "",
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_HIDDEN_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise CommandTimedOut(f"htnwallet {command} did not finish in time") from None
    except OSError as error:
        raise ToolError(f"Could not start htnwallet: {error.strerror}") from None
    log.info("htnwallet %s exited with code %d", command, completed.returncode)
    return CommandResult(Tool.HTNWALLET, command, completed.returncode, completed.stdout, completed.stderr)
