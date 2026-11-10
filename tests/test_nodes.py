import json
import os
import socket
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from guiwallet.core import daemon, nodes  # noqa: E402
from guiwallet.core.nodes import (  # noqa: E402
    OFFICIAL_RPC,
    Node,
    NodeError,
    find_nodes,
    is_official,
    node_is_ready,
    official_ip_of,
    ping,
)
from guiwallet.core.settings import Settings, load_settings  # noqa: E402
from guiwallet.core.tools import CommandResult, CommandTimedOut, Tool  # noqa: E402

HTNCTL = Path("htnctl")


@pytest.mark.parametrize(
    "address", ["rpc.hoosat.network:42420", "RPC.Hoosat.Network:42420", " rpc.hoosat.network ", "rpc.hoosat.network."]
)
def test_the_official_rpc_is_recognised_however_typed(address):
    assert is_official(address)


@pytest.mark.parametrize(
    "address", ["localhost:42420", "rpc.hoosat.network:16110", "rpc.hoosat.network.evil.com:42420", "189.72.146.62:42420"]
)
def test_other_addresses_are_used_as_typed(address):
    assert not is_official(address)


def test_nodes_come_fastest_first_and_unusable_ones_are_dropped():
    timings = {"1.1.1.1:42420": 180.0, "2.2.2.2:42420": None, "3.3.3.3:42420": 64.0, "4.4.4.4:42420": 300.0}

    def prober(_htnctl, address):
        return Node(address, timings[address]) if timings[address] is not None else None

    found = find_nodes(HTNCTL, resolver=lambda: ["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"], prober=prober)
    assert [node.address for node in found] == ["3.3.3.3:42420", "1.1.1.1:42420", "4.4.4.4:42420"]


def test_no_usable_node_is_an_error_the_user_can_read():
    with pytest.raises(NodeError, match="No synced public node answered"):
        find_nodes(HTNCTL, resolver=lambda: ["1.1.1.1"], prober=lambda _htnctl, _address: None)


def test_a_failed_lookup_is_reported(monkeypatch):
    def fail(*_args, **_kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(nodes.socket, "getaddrinfo", fail)
    with pytest.raises(NodeError, match="Couldn't look up"):
        nodes.resolve()


def _getinfo(monkeypatch, *, exit_code=0, stdout="", raises=None):
    def run_tool(path, tool, command, arguments, timeout):
        assert tool is Tool.HTNCTL and command == "GetInfo" and "--rpcserver=1.1.1.1:42420" in arguments
        if raises:
            raise raises
        return CommandResult(tool, command, exit_code, stdout, "")

    monkeypatch.setattr(nodes, "run_tool", run_tool)


def _info(**fields):
    response = {"isSynced": True, "isUtxoIndexed": True, "error": None} | fields
    return json.dumps({"getInfoResponse": response})


def test_a_synced_node_with_the_utxo_index_is_ready(monkeypatch):
    _getinfo(monkeypatch, stdout=_info())
    assert node_is_ready(HTNCTL, "1.1.1.1:42420")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stdout": _info(isSynced=False)},
        {"stdout": _info(isUtxoIndexed=False)},
        {"stdout": _info(error={"message": "x"})},
        {"stdout": "not json"},
        {"stdout": "[]"},
        {"exit_code": 1, "stdout": _info()},
        {"raises": CommandTimedOut("htnctl GetInfo did not finish in time")},
    ],
)
def test_a_node_that_isnt_ready_or_doesnt_answer_is_skipped(monkeypatch, kwargs):
    _getinfo(monkeypatch, **kwargs)
    assert not node_is_ready(HTNCTL, "1.1.1.1:42420")


def test_ping_times_an_open_port_and_gives_none_for_a_closed_one():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert ping(f"127.0.0.1:{port}") is not None
    assert ping(f"127.0.0.1:{port}", timeout=1) is None
    assert ping("not an address") is None


def test_only_an_official_node_is_recognised_on_a_running_htnwallet():
    resolver = lambda: ["189.72.146.62", "51.89.232.58"]  # noqa: E731
    assert official_ip_of("189.72.146.62:42420", resolver) == "189.72.146.62"
    assert official_ip_of("189.72.146.62:16110", resolver) is None
    assert official_ip_of("localhost:42420", resolver) is None


class _Owner:
    def __init__(self, arguments, name="htnwallet.exe"):
        self.arguments, self._name = arguments, name

    def name(self):
        return self._name

    def cmdline(self):
        return self.arguments


@pytest.mark.parametrize(
    "arguments, expected",
    [
        (["htnwallet", "start-daemon", "--rpcserver=1.2.3.4:42420"], "1.2.3.4:42420"),
        (["htnwallet", "start-daemon", "-s", "localhost:42420"], "localhost:42420"),
        (["htnwallet", "start-daemon"], None),
    ],
)
def test_the_node_of_a_running_htnwallet_is_read_from_its_command_line(monkeypatch, arguments, expected):
    monkeypatch.setattr(daemon, "find_port_owner", lambda _port: _Owner(arguments))
    assert daemon.daemon_node(4282) == expected


def test_another_program_on_the_port_has_no_node(monkeypatch):
    monkeypatch.setattr(daemon, "find_port_owner", lambda _port: _Owner(["x", "--rpcserver=1.2.3.4:42420"], "x.exe"))
    assert daemon.daemon_node(4282) is None


def test_new_installs_use_the_official_rpc_and_saved_nodes_are_kept(tmp_path):
    assert Settings().node_address == OFFICIAL_RPC
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"node_address": "localhost:42420"}))  # As 1.0.1 saved it.
    assert load_settings(path).node_address == "localhost:42420"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_use_official_rpc_fills_in_the_node_and_shows_the_hint(app):
    from guiwallet.ui.pages import SettingsPage

    page = SettingsPage(Settings(node_address="localhost:42420"))
    assert page.node_hint.isHidden()
    page.use_official.click()
    assert page.node_address.text() == OFFICIAL_RPC
    assert not page.node_hint.isHidden()
    page.set_htnwallet_running(True)
    assert not page.use_official.isEnabled()
