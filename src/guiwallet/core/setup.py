"""The Setup Wizard's work: first-launch detection, finding a running htnwallet, and making a wallet.

Every wallet is made by htnwallet itself. What the user types is handed over exactly as typed, the
same as on htnwallet's command line: nothing is trimmed, lowercased or checked here first.
"""

import datetime
from dataclasses import dataclass
from pathlib import Path

import psutil

from guiwallet.core.daemon import DAEMON_PROGRAM_NAMES, LOOPBACK_ADDRESSES
from guiwallet.core.settings import Settings, default_keys_file, settings_path
from guiwallet.core.tools import ToolError, app_folder, run_setup_command
from guiwallet.core.validation import InvalidInput, validate_password

# Shown for every failed seed phrase or key, whatever the reason: the user asked for no hints.
INVALID_PHRASE = "This seed phrase isn't valid. Check it against what you wrote down."
INVALID_KEY = "This private key isn't valid."
INVALID_BACKUP = "The backup file couldn't be opened. Check the file and its password."


class SetupError(Exception):
    """Making the wallet failed. The message is meant for the user and never holds a secret."""


@dataclass(frozen=True)
class RunningDaemon:
    address: str  # 127.0.0.1:port, where it listens.
    keys_file: str  # From its command line, or "" if it uses htnwallet's default.
    started: float  # When the process started, in seconds since 1970.

    def started_text(self) -> str:
        moment = datetime.datetime.fromtimestamp(self.started)
        day = "today" if moment.date() == datetime.date.today() else moment.strftime("%d %b")
        return f"started {day} at {moment:%H:%M}"


def is_first_launch(path: Path | None = None) -> bool:
    """No settings saved yet. The wizard saves them when it finishes, so it only opens itself once."""
    return not (path or settings_path()).exists()


def tool_folders(settings: Settings) -> list[Path]:
    """Where to look for the tools: beside the app first, then the Main folder in Settings."""
    folders = []
    if (own_folder := app_folder()) is not None:
        folders.append(own_folder)
    if settings.tools_folder:
        folders.append(Path(settings.tools_folder))
    return folders


def wallet_file(settings: Settings) -> Path:
    """The wallet file the app uses: the one in Settings, or htnwallet's own default."""
    return Path(settings.keys_file) if settings.keys_file else default_keys_file(settings.network)


def free_wallet_file(path: Path) -> Path:
    """A name beside `path` that isn't taken: keys.json, then keys-2.json, keys-3.json and so on.

    A wallet file is never overwritten; the wizard saves a new wallet under a free name instead.
    """
    if not path.exists():
        return path
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1


def keys_file_setting(path: Path, network: str) -> str:
    """What Settings stores for this file: empty for htnwallet's default, so htnwallet keeps choosing it."""
    return "" if path == default_keys_file(network) else str(path)


def _flag_value(arguments: list[str], long: str, short: str) -> str | None:
    """A flag's value in any of the forms htnwallet accepts: --long=X, --long X, -sX, -s X, /s X, /s:X."""
    for index, argument in enumerate(arguments):
        following = arguments[index + 1] if index + 1 < len(arguments) else None
        if argument.startswith(f"--{long}="):
            return argument.split("=", 1)[1]
        if argument in (f"--{long}", f"-{short}", f"/{short}", f"/{long}"):
            return following
        for prefix in (f"/{short}:", f"/{long}:"):
            if argument.startswith(prefix):
                return argument[len(prefix):]
        if argument.startswith(f"-{short}") and not argument.startswith("--") and len(argument) > 2:
            return argument[2:]
    return None


def find_running_daemons() -> list[RunningDaemon]:
    """htnwallet daemons running on this PC, with the address each listens on. Passwords are never read."""
    found = []
    # Names first: reading every process's command line takes seconds on Windows.
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info["name"] or "").lower() not in DAEMON_PROGRAM_NAMES:
                continue
            arguments = process.cmdline() or []
            if "start-daemon" not in arguments[1:]:
                continue
            ports = sorted(
                {
                    connection.laddr.port
                    for connection in process.net_connections(kind="tcp")
                    if connection.status == psutil.CONN_LISTEN
                    and connection.laddr
                    and connection.laddr.ip in LOOPBACK_ADDRESSES
                }
            )
            keys_file = _flag_value(arguments[1:], "keys-file", "f") or ""
            for port in ports:
                found.append(RunningDaemon(f"127.0.0.1:{port}", keys_file, process.create_time() or 0))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return sorted(found, key=lambda daemon: daemon.address)


def check_new_password(password: str, again: str) -> str:
    if password != again:
        raise InvalidInput("The passwords don't match")
    return validate_password(password)


def _base_arguments(keys_file: Path, password: str, network: str) -> list[str]:
    arguments = [f"--keys-file={keys_file}", f"--password={password}"]
    return arguments + (["--testnet"] if network == "testnet" else [])


def _run(tool: Path, command: str, arguments: list[str], stdin_text: str | None, keys_file: Path, failure: str):
    """Run a command that writes a new wallet file. `keys_file` must not exist: htnwallet would ask to
    overwrite it, and the empty answer it gets is a refusal, but it's checked here too."""
    if keys_file.exists():
        raise SetupError(f"A wallet file already exists at {keys_file}")
    try:
        result = run_setup_command(tool, command, arguments, stdin_text)
    except ToolError as error:
        raise SetupError(str(error)) from None
    if not result.succeeded or not keys_file.is_file():
        raise SetupError(failure)
    return result


def create_wallet(tool: Path, network: str, keys_file: Path, password: str) -> None:
    """htnwallet makes a new 24-word seed phrase and saves it, encrypted, in `keys_file`."""
    _run(tool, "create", _base_arguments(keys_file, password, network), None, keys_file,
         "htnwallet couldn't create the wallet")


def read_seed_phrase(tool: Path, network: str, keys_file: Path, password: str) -> list[str]:
    """The words of the wallet just made, read back once to show them. Never stored or logged."""
    arguments = [f"--keys-file={keys_file}", f"--password={password}", "--yes"]
    arguments += ["--testnet"] if network == "testnet" else []
    try:
        result = run_setup_command(tool, "dump-unencrypted-data", arguments)
    except ToolError as error:
        raise SetupError(str(error)) from None
    lines = result.stdout.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "Mnemonic #1:" and index + 1 < len(lines):
            words = lines[index + 1].split()
            if words:
                return words
    raise SetupError("htnwallet didn't show the new seed phrase")


def restore_from_phrase(tool: Path, network: str, keys_file: Path, password: str, phrase: str) -> None:
    """`create --import`: htnwallet reads the phrase on its input, exactly as typed."""
    _run(tool, "create", ["--import", *_base_arguments(keys_file, password, network)], phrase + "\n",
         keys_file, INVALID_PHRASE)


def restore_web_wallet_phrase(tool: Path, network: str, keys_file: Path, password: str, phrase: str) -> None:
    _run(tool, "import-web-wallet", _base_arguments(keys_file, password, network), phrase + "\n",
         keys_file, INVALID_PHRASE)


def restore_web_wallet_backup(
    tool: Path, network: str, keys_file: Path, password: str, backup_file: Path, backup_password: str
) -> None:
    # htnwallet can only ask for the backup's password in a terminal, so it goes as a flag, like --password.
    arguments = [
        *_base_arguments(keys_file, password, network),
        f"--export-file={backup_file}",
        f"--export-password={backup_password}",
    ]
    _run(tool, "import-web-wallet", arguments, None, keys_file, INVALID_BACKUP)


def restore_private_key(tool: Path, network: str, keys_file: Path, password: str, private_key: str) -> None:
    # Likewise: htnwallet only prompts for the key in a terminal.
    arguments = [*_base_arguments(keys_file, password, network), f"--private-key={private_key}"]
    _run(tool, "import-private-key", arguments, None, keys_file, INVALID_KEY)
