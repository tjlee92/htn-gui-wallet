"""The official public RPC: picking the fastest of its nodes, and timing the one in use.

rpc.hoosat.network has several nodes behind one name, and the order its addresses come back in
changes from one lookup to the next. htnwallet connects to the first address that answers, so the
app looks them all up itself, keeps the synced ones and gives htnwallet the fastest.
"""

import json
import socket
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from guiwallet.core.tools import Tool, ToolError, run_tool

OFFICIAL_HOST = "rpc.hoosat.network"
OFFICIAL_PORT = 42420
OFFICIAL_RPC = f"{OFFICIAL_HOST}:{OFFICIAL_PORT}"

PING_TIMEOUT_SECONDS = 3
PINGS_PER_PROBE = 2  # The quicker of two: the first connection can include a slow start on either side.
GETINFO_TIMEOUT_SECONDS = 8


class NodeError(Exception):
    """No node could be used. The message is safe to show the user."""


@dataclass(frozen=True)
class Node:
    address: str  # ip:port, as htnwallet is given it.
    latency_ms: float

    @property
    def ip(self) -> str:
        return self.address.rsplit(":", 1)[0]


def is_official(address: str) -> bool:
    """True for the official RPC however it's typed: any case, with or without :42420."""
    address = address.strip().lower().rstrip(".")
    host, _, port = address.partition(":")
    return host.rstrip(".") == OFFICIAL_HOST and port in ("", str(OFFICIAL_PORT))


def resolve(host: str = OFFICIAL_HOST) -> list[str]:
    """Every IPv4 address the name has, in the order given, without repeats."""
    try:
        answers = socket.getaddrinfo(host, OFFICIAL_PORT, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        raise NodeError(f"Couldn't look up {host}. Check the internet connection.") from None
    addresses: list[str] = []
    for *_, sockaddr in answers:
        if sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])
    if not addresses:
        raise NodeError(f"{host} has no addresses")
    return addresses


def ping(address: str, timeout: float = PING_TIMEOUT_SECONDS) -> float | None:
    """How long a connection to ip:port takes to open, in milliseconds, or None if it doesn't."""
    host, _, port = address.rpartition(":")
    started = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return (time.perf_counter() - started) * 1000
    except (OSError, ValueError):
        return None


def node_is_ready(htnctl: Path, address: str) -> bool:
    """The node says it's synced and keeps the UTXO index, which htnwallet needs for balances."""
    try:
        # -a: a public node may run a newer version than this htnctl; GetInfo reads the same either way.
        result = run_tool(
            htnctl, Tool.HTNCTL, "GetInfo", ["-a", f"--rpcserver={address}", f"--timeout={GETINFO_TIMEOUT_SECONDS}"],
            timeout=GETINFO_TIMEOUT_SECONDS + 4,
        )
    except ToolError:
        return False
    if not result.succeeded:
        return False
    try:
        info = json.loads(result.stdout).get("getInfoResponse") or {}
    except (ValueError, AttributeError):
        return False
    return info.get("isSynced") is True and info.get("isUtxoIndexed") is True and not info.get("error")


def probe(htnctl: Path, address: str) -> Node | None:
    times = [ms for ms in (ping(address) for _ in range(PINGS_PER_PROBE)) if ms is not None]
    if not times or not node_is_ready(htnctl, address):
        return None
    return Node(address, min(times))


def find_nodes(
    htnctl: Path,
    resolver: Callable[[], list[str]] = resolve,
    prober: Callable[[Path, str], Node | None] = probe,
) -> list[Node]:
    """The official RPC's usable nodes, fastest first. Every node is asked at the same time."""
    addresses = [f"{ip}:{OFFICIAL_PORT}" for ip in resolver()]
    with ThreadPoolExecutor(max_workers=len(addresses)) as pool:
        found = [node for node in pool.map(lambda address: prober(htnctl, address), addresses) if node]
    if not found:
        raise NodeError("No synced public node answered")
    return sorted(found, key=lambda node: node.latency_ms)


def official_ip_of(rpcserver: str, resolver: Callable[[], list[str]] = resolve) -> str | None:
    """If an htnwallet's --rpcserver is one of the official RPC's nodes, return its IP."""
    host, _, port = rpcserver.rpartition(":")
    if port != str(OFFICIAL_PORT):
        return None
    try:
        return host if host in resolver() else None
    except NodeError:
        return None
