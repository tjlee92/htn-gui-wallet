"""Finding, starting and closing the htnwallet daemon.

The app identifies a daemon by the port it listens on, never by process name. Other
htnwallet.exe processes, such as a running auto-compound, must not be mistaken for it.
"""

import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil

from guiwallet.core.commands import (
    WalletContext,
    balance_arguments,
    daemon_arguments,
    start_daemon_arguments,
)
from guiwallet.core.tools import Tool, ToolError, run_tool, start_in_background
from guiwallet.core.wallet import WalletBalance, WalletCoins, parse_addresses, parse_balance, parse_coins

DAEMON_PROGRAM_NAMES = {"htnwallet", "htnwallet.exe"}
LOOPBACK_ADDRESSES = {"127.0.0.1", "::1", "0.0.0.0", "::"}


class DaemonError(Exception):
    """A daemon operation failed. The message is safe to show the user."""


class DaemonNotSynced(DaemonError):
    """The daemon is running, but hasn't finished syncing with its node yet."""


class DaemonUnavailable(DaemonError):
    """Nothing is answering on the daemon's port any more."""


@dataclass(frozen=True)
class WalletSnapshot:
    balance: WalletBalance
    coins: WalletCoins | None


def port_is_open(port: int, timeout: float = 1) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def running_daemon_version(tool_path: Path, context: WalletContext, port: int) -> str | None:
    """Return the version of the daemon on this port, or None if no daemon answers there."""
    if not port_is_open(port):
        return None
    try:
        result = run_tool(tool_path, Tool.HTNWALLET, "get-daemon-version", daemon_arguments(context), timeout=10)
    except ToolError:
        return None  # The port is taken, but whatever holds it didn't answer as a daemon.
    return result.stdout.strip() if result.succeeded else None


def start_daemon(tool_path: Path, context: WalletContext, node_address: str, log_path: Path) -> subprocess.Popen:
    """Start a hidden daemon. It carries on running after the app exits."""
    return start_in_background(
        tool_path,
        Tool.HTNWALLET,
        "start-daemon",
        start_daemon_arguments(context, node_address),
        log_path,
    )


def last_log_line(log_path: Path) -> str:
    """The daemon's closing message when it fails to start, cut down to a sensible length."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    meaningful = [line.strip() for line in lines if line.strip()]
    return meaningful[-1][:300] if meaningful else ""


def find_port_owner(port: int) -> psutil.Process | None:
    """Find the process listening on this port on this computer."""
    for connection in psutil.net_connections(kind="tcp"):
        if (
            connection.status == psutil.CONN_LISTEN
            and connection.laddr
            and connection.laddr.port == port
            and connection.laddr.ip in LOOPBACK_ADDRESSES
            and connection.pid
        ):
            return psutil.Process(connection.pid)
    return None


def daemon_node(port: int) -> str | None:
    """The node the htnwallet on this port was started with, read from its command line."""
    try:
        owner = find_port_owner(port)
        if owner is None or owner.name().lower() not in DAEMON_PROGRAM_NAMES:
            return None
        arguments = owner.cmdline()
    except (psutil.Error, OSError):
        return None
    for index, argument in enumerate(arguments):
        if argument.startswith("--rpcserver="):
            return argument.split("=", 1)[1]
        if argument in ("--rpcserver", "-s") and index + 1 < len(arguments):
            return arguments[index + 1]
    return None


def close_daemon(port: int) -> None:
    """Close the daemon on this port, and only that process.

    It's refused if something other than htnwallet owns the port. The CLI has no stop
    command, so this ends the process abruptly. The daemon resyncs on its next start.
    """
    try:
        owner = find_port_owner(port)
        if owner is None:
            return  # Already gone, which is what was wanted.
        if owner.name().lower() not in DAEMON_PROGRAM_NAMES:
            raise DaemonError(f"Port {port} is held by {owner.name()}, not htnwallet, so it wasn't closed")
        owner.terminate()
        owner.wait(timeout=10)
    except psutil.NoSuchProcess:
        return
    except psutil.AccessDenied:
        raise DaemonError("Windows didn't allow closing this htnwallet process") from None
    except psutil.TimeoutExpired:
        raise DaemonError("htnwallet didn't close in time") from None


def _run_wallet_command(tool_path: Path, command: str, arguments: list[str], port: int, timeout: float) -> str:
    result = run_tool(tool_path, Tool.HTNWALLET, command, arguments, timeout=timeout)
    if result.succeeded:
        return result.stdout
    error_lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    message = error_lines[-1] if error_lines else ""
    if "not synced yet" in message:
        raise DaemonNotSynced(message[:300])
    if not port_is_open(port):
        raise DaemonUnavailable("The wallet daemon has stopped")
    raise DaemonError(message[:300] or f"htnwallet {command} failed")


def read_wallet(tool_path: Path, context: WalletContext, port: int, include_coins: bool) -> WalletSnapshot:
    """Fetch the balance and, when asked, the coin list, which can take longer."""
    balance = parse_balance(_run_wallet_command(tool_path, "balance", balance_arguments(context), port, 30))
    coins = None
    if include_coins:
        coins = parse_coins(_run_wallet_command(tool_path, "utxos", daemon_arguments(context), port, 60))
    return WalletSnapshot(balance, coins)


def read_receive_addresses(tool_path: Path, context: WalletContext, port: int) -> list[str]:
    stdout = _run_wallet_command(tool_path, "show-addresses", daemon_arguments(context), port, 30)
    return parse_addresses(stdout)


def create_address(tool_path: Path, context: WalletContext, port: int) -> str:
    """Ask the daemon for the wallet's next receiving address. It can't be removed afterwards."""
    stdout = _run_wallet_command(tool_path, "new-address", daemon_arguments(context), port, 30)
    lines = [line.strip() for line in stdout.splitlines()]
    for index, line in enumerate(lines):
        if line == "New address:" and index + 1 < len(lines) and lines[index + 1]:
            return lines[index + 1]
    raise DaemonError("htnwallet didn't show the new address")
