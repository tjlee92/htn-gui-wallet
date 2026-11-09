"""Auto-compound: starting it for one address, and finding every auto-compound running on this PC.

An auto-compound started by this app ends with the app. One started elsewhere, such as from a .bat
file, is found by reading htnwallet's command line. Only the From and To addresses and the interval
are taken from it; the password, which can be on that command line too, is never read into the app.
"""

import re
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from guiwallet.core.commands import WalletContext
from guiwallet.core.tools import TiedProcess, Tool, ToolError, start_tied
from guiwallet.core.validation import InvalidInput, validate_address, validate_password

COMMAND = "auto-compound"
DEFAULT_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 6  # htnwallet treats anything shorter, or no interval at all, as 60.
MAX_INTERVAL_SECONDS = 86_400
# A wrong password or an unreachable daemon ends it within about 3 seconds; its first result line, sooner,
# shows it got past both. A failure after this is still reported, by a banner.
START_CHECK_SECONDS = 4
_COMPOUNDED_ID = re.compile(r"COMPOUNDED .*/txs/([0-9a-fA-F]{64})")
_RESULT_LINE = re.compile(r"^\[\d\d:\d\d:\d\d\] ")  # Every result htnwallet prints starts [15:04:05].
PROGRAM_NAMES = {"htnwallet", "htnwallet.exe"}

# htnwallet's flags that take a value, by short and long name. Anything else is a switch such as -u.
_FROM, _TO, _INTERVAL = "from", "to", "interval"
_VALUE_FLAGS = {
    "a": _FROM, "from-address": _FROM,
    "t": _TO, "to-address": _TO,
    "c": _INTERVAL, "compound-rate": _INTERVAL,
    "f": None, "keys-file": None,
    "p": None, "password": None,
    "d": None, "daemonaddress": None,
    "l": None, "limit": None,
    "override-dag-params-file": None,
}

_ERRORS = (
    ("message authentication failed", "Wrong password, or the wallet file in Settings isn't the wallet "
                                      "htnwallet is running."),
    ("connecting to htnwallet daemon", "htnwallet isn't answering. Check that it's running."),
    ("reading keys file", "The wallet file in Settings couldn't be read."),
)


class CompoundError(Exception):
    """Auto-compound couldn't start. The message is safe to show the user."""


def validate_interval(text: str) -> int:
    try:
        seconds = int(text.strip())
    except ValueError:
        raise InvalidInput("Enter the interval as a whole number of seconds.") from None
    if not MIN_INTERVAL_SECONDS <= seconds <= MAX_INTERVAL_SECONDS:
        raise InvalidInput(f"The interval must be between {MIN_INTERVAL_SECONDS} and {MAX_INTERVAL_SECONDS:,} seconds.")
    return seconds


def compound_arguments(
    context: WalletContext, from_address: str, to_address: str, seconds: int, password: str
) -> list[str]:
    """The arguments for `htnwallet auto-compound`, the same as `-a <from> -t <to> -u -c <seconds>`."""
    return [
        f"--from-address={validate_address(from_address, context.network)}",
        f"--to-address={validate_address(to_address, context.network)}",
        "-u",
        f"--compound-rate={seconds}",
        f"--password={validate_password(password)}",
        *context.keys_flags(),
        *context.daemon_flags(),
    ]


@dataclass(frozen=True)
class RunningCompound:
    """An auto-compound running on this PC."""

    pid: int
    created: float  # When the process started, so that a reused process ID is never mistaken for it.
    from_addresses: tuple[str, ...]  # Empty means every address in the wallet.
    to_address: str | None
    interval: int
    started_here: bool = False
    compounded: int = 0  # Only known for one started here.

    def covers(self, address: str, wallet_addresses: Iterable[str]) -> bool:
        if self.from_addresses:
            return address in self.from_addresses
        # Without -a it compounds the whole wallet it was given; its To address says whether that's this one.
        return self.to_address in set(wallet_addresses)


def _flag(word: str) -> tuple[str, str | None] | None:
    """The name of the flag in `word`, and its value if attached. None if it isn't a flag."""
    if word.startswith("--"):
        name, sep, value = word[2:].partition("=")
        return name, value if sep else None
    if word.startswith("/") and len(word) > 1:  # Windows style, as in /c 60 or /to-address:hoosat:...
        name, sep, value = word[1:].partition(":")
        if not sep:
            name, sep, value = word[1:].partition("=")
        return name, value if sep else None
    if word.startswith("-") and len(word) > 1:
        name, rest = word[1], word[2:]
        if not rest:
            return name, None
        return name, rest[1:] if rest.startswith("=") else rest
    return None


def parse_compound_command(words: list[str]) -> tuple[tuple[str, ...], str | None, int] | None:
    """From an htnwallet command line: the From addresses, the To address and the interval.

    None if it isn't an auto-compound. Values of every other flag, the password included, are skipped.
    """
    if len(words) < 2 or words[1] != COMMAND:
        return None
    found: dict[str, list[str]] = {_FROM: [], _TO: [], _INTERVAL: []}
    index = 2
    while index < len(words):
        parsed = _flag(words[index])
        index += 1
        if parsed is None:
            continue
        name, value = parsed
        if name not in _VALUE_FLAGS:
            continue
        if value is None and index < len(words):
            value = words[index]
            index += 1
        kind = _VALUE_FLAGS[name]
        if kind and value is not None:
            found[kind].append(value)
    interval = DEFAULT_INTERVAL_SECONDS
    if found[_INTERVAL]:
        try:
            interval = int(found[_INTERVAL][-1])
        except ValueError:
            pass
        if interval < MIN_INTERVAL_SECONDS:
            interval = DEFAULT_INTERVAL_SECONDS
    to_address = found[_TO][-1] if found[_TO] else None
    return tuple(found[_FROM]), to_address, interval


@dataclass
class _Started:
    """An auto-compound this app started."""

    tied: TiedProcess | None
    from_address: str
    to_address: str
    interval: int
    started_at: float
    lines: list[str] = field(default_factory=list)
    compounded: int = 0
    answered: bool = False  # It has printed a result, so it got past the password and the daemon.
    new_ids: list[str] = field(default_factory=list)  # Transaction IDs of its compounds, not yet collected.
    lock: threading.Lock = field(default_factory=threading.Lock)

    def take_line(self, line: str) -> None:
        with self.lock:
            self.lines = [*self.lines[-19:], line]
            if _RESULT_LINE.match(line):
                self.answered = True
            if "COMPOUNDED" in line:
                self.compounded += 1
                if found := _COMPOUNDED_ID.search(line):
                    self.new_ids.append(found.group(1).lower())

    def take_ids(self) -> list[str]:
        with self.lock:
            ids, self.new_ids = self.new_ids, []
            return ids

    def last_line(self) -> str:
        with self.lock:
            return next((line for line in reversed(self.lines) if line.strip()), "")


def _friendly(line: str) -> str:
    for marker, message in _ERRORS:
        if marker in line:
            return message
    return line or "htnwallet auto-compound exited straight away."


class Compounders:
    """The auto-compounds this app has started. Call its methods from one thread at a time."""

    def __init__(self) -> None:
        self.started: dict[int, _Started] = {}
        self.leftover_ids: list[str] = []  # From ones that have ended, until collected.

    @staticmethod
    def launch(
        tool_path: Path, context: WalletContext, from_address: str, to_address: str, seconds: int, password: str
    ) -> _Started:
        """Start one and wait long enough to see that it didn't fail straight away.

        Slow: call it off the main thread, then hand the result to `add` on the main thread.
        """
        arguments = compound_arguments(context, from_address, to_address, seconds, password)
        # Made first, so that no line written before start_tied returns can be missed.
        started = _Started(None, from_address, to_address, seconds, time.time())
        try:
            tied = start_tied(tool_path, Tool.HTNWALLET, COMMAND, arguments, started.take_line)
        except ToolError as error:
            raise CompoundError(str(error)) from None
        started.tied = tied

        deadline = time.monotonic() + START_CHECK_SECONDS
        while time.monotonic() < deadline and tied.process.poll() is None and not started.answered:
            time.sleep(0.05)
        if tied.process.poll() is not None:
            if tied.reader:
                tied.reader.join(timeout=2)
            tied.stop()
            raise CompoundError(_friendly(started.last_line()))
        return started

    def add(self, started: _Started) -> int:
        self.started[started.tied.process.pid] = started
        return started.tied.process.pid

    def take_compound_ids(self) -> list[str]:
        """Transaction IDs of compounds sent since the last call, so History can leave them out."""
        ids, self.leftover_ids = self.leftover_ids, []
        return ids + [found for started in self.started.values() for found in started.take_ids()]

    def counts(self) -> dict[int, int]:
        """Each one's process ID and how many compounds it has sent: safe to pass to another thread."""
        return {pid: started.compounded for pid, started in self.started.items()}

    def ended(self) -> list[tuple[str, str]]:
        """Forget those that have exited by themselves. Returns each one's From address and its last words."""
        gone = []
        for pid, started in list(self.started.items()):
            if started.tied.process.poll() is not None:
                started.tied.stop()
                del self.started[pid]
                self.leftover_ids.extend(started.take_ids())
                gone.append((started.from_address, _friendly(started.last_line())))
        return gone

    def stop(self, pid: int) -> bool:
        """Stop one this app started. False if it isn't one of them."""
        started = self.started.pop(pid, None)
        if started is None:
            return False
        started.tied.stop()
        self.leftover_ids.extend(started.take_ids())
        return True

    def stop_all(self) -> None:
        for pid in list(self.started):
            self.stop(pid)

    def details(self, pid: int) -> tuple[int, float] | None:
        """How many compounds one started here has sent, and when it started."""
        started = self.started.get(pid)
        return (started.compounded, started.started_at) if started else None


def find_compounds(own: dict[int, int] | None = None) -> list[RunningCompound]:
    """Every auto-compound running on this PC, however it was started. `own` is `Compounders.counts()`."""
    own = own or {}
    found = []
    for process in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        if (process.info["name"] or "").lower() not in PROGRAM_NAMES:
            continue
        parsed = parse_compound_command(process.info["cmdline"] or [])
        if parsed is None:
            continue
        from_addresses, to_address, interval = parsed
        pid = process.info["pid"]
        started_here = pid in own
        compounded = own.get(pid, 0)
        found.append(
            RunningCompound(
                pid, process.info["create_time"] or 0.0, from_addresses, to_address, interval, started_here, compounded
            )
        )
    return found


def stop_outside_compound(compound: RunningCompound) -> None:
    """End an auto-compound started outside this app, after checking the process is still that one."""
    try:
        process = psutil.Process(compound.pid)
        same = (
            abs(process.create_time() - compound.created) < 1
            and (process.name() or "").lower() in PROGRAM_NAMES
            and parse_compound_command(process.cmdline()) is not None
        )
        if not same:
            return  # It has already ended, and the ID now belongs to something else.
        process.terminate()
        process.wait(timeout=5)
    except psutil.NoSuchProcess:
        return
    except psutil.TimeoutExpired:
        raise CompoundError("The auto-compound didn't stop in time.") from None
    except psutil.AccessDenied:
        raise CompoundError("Windows didn't allow this app to stop that auto-compound.") from None
