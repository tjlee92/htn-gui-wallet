"""These use real sockets and processes, on ports chosen by the system, so they never
touch a daemon that's already running on this computer."""

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from guiwallet.core import daemon
from guiwallet.core.commands import WalletContext, start_daemon_arguments
from guiwallet.core.daemon import DaemonError, close_daemon, find_port_owner, port_is_open

LISTENER = """
import socket, time
server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen()
print(server.getsockname()[1], flush=True)
time.sleep(60)
"""


@pytest.fixture
def listening_process():
    process = subprocess.Popen([sys.executable, "-c", LISTENER], stdout=subprocess.PIPE, text=True)
    port = int(process.stdout.readline())
    yield process, port
    process.kill()
    process.wait()


def unused_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_port_check(listening_process):
    _, port = listening_process
    assert port_is_open(port)
    assert not port_is_open(unused_port())


def test_finds_the_process_that_owns_a_port(listening_process):
    process, port = listening_process
    owner = find_port_owner(port)
    # A virtual environment's python.exe on Windows is a launcher that starts the real
    # Python as its child, and the child is what holds the port.
    assert process.pid in (owner.pid, owner.ppid())
    assert find_port_owner(unused_port()) is None


def test_refuses_to_close_a_program_that_is_not_htnwallet(listening_process):
    process, port = listening_process
    with pytest.raises(DaemonError, match="not htnwallet"):
        close_daemon(port)
    time.sleep(0.2)
    assert process.poll() is None


def test_closes_only_the_port_owner(listening_process, monkeypatch):
    target, port = listening_process
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        program = Path(sys.executable).name.lower()
        monkeypatch.setattr(daemon, "DAEMON_PROGRAM_NAMES", {program})
        close_daemon(port)
        assert target.wait(timeout=5) is not None
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()


def test_closing_a_port_with_no_owner_does_nothing():
    close_daemon(unused_port())


def test_daemon_is_given_explicit_addresses():
    context = WalletContext("testnet", "127.0.0.1:4282", Path("wallet.json"))
    assert start_daemon_arguments(context, "node.example:42422") == [
        "--listen=127.0.0.1:4282",
        "--rpcserver=node.example:42422",
        "--keys-file=wallet.json",
        "--testnet",
    ]


def test_commands_never_run_when_nothing_is_listening():
    context = WalletContext("mainnet", "127.0.0.1:1")
    missing_program = Path("does-not-exist.exe")
    assert daemon.running_daemon_version(missing_program, context, unused_port()) is None
