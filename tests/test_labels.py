"""Address labels: kept only by the app, cleaned to one short line, and new-address via the fake htnwallet."""

import os
import subprocess
from pathlib import Path

import pytest

from guiwallet.core.commands import WalletContext
from guiwallet.core.daemon import create_address
from guiwallet.core.labels import MAX_LABEL_LENGTH, clean_label, load_labels, save_labels, set_label
from guiwallet.ui.widgets import address_text, shorten_address

FAKE_WALLET = Path(os.environ.get("FAKE_HTNWALLET", "scripts/dev-tests/fakewallet/bin/htnwallet.exe"))
ADDRESS = "hoosat:qq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6"


def test_labels_are_one_short_line():
    assert clean_label("  Mining\nrig\t ") == "Mining rig"
    assert clean_label("x" * 100) == "x" * MAX_LABEL_LENGTH
    assert clean_label("   ") == ""


def test_set_label_and_remove_it():
    labels = {}
    set_label(labels, ADDRESS, "Savings")
    assert labels == {ADDRESS: "Savings"}
    set_label(labels, ADDRESS, "  ")
    assert labels == {}


def test_labels_round_trip_and_bad_files_are_ignored(tmp_path):
    path = tmp_path / "labels.json"
    save_labels({ADDRESS: "Café"}, path)
    assert load_labels(path) == {ADDRESS: "Café"}
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_labels(path) == {}
    path.write_text('{"a": 5, "b": "ok"}', encoding="utf-8")
    assert load_labels(path) == {"b": "ok"}
    assert load_labels(tmp_path / "missing.json") == {}


def test_address_text_adds_the_label():
    assert address_text(ADDRESS) == shorten_address(ADDRESS)
    assert address_text(ADDRESS, {ADDRESS: "Savings"}) == f"{shorten_address(ADDRESS)}   ·   Savings"


@pytest.mark.skipif(not FAKE_WALLET.is_file(), reason="the fake htnwallet isn't built")
def test_new_address_is_read_from_htnwallet(tmp_path):
    port = 47954
    added = FAKE_WALLET.parent / "new-addresses.txt"
    added.unlink(missing_ok=True)
    daemon = subprocess.Popen([str(FAKE_WALLET), "start-daemon", f"--listen=127.0.0.1:{port}", "--rpcserver=localhost"])
    try:
        from guiwallet.core.daemon import port_is_open
        import time

        deadline = time.time() + 10
        while not port_is_open(port) and time.time() < deadline:
            time.sleep(0.05)
        context = WalletContext(network="mainnet", daemon_address=f"127.0.0.1:{port}")
        first = create_address(FAKE_WALLET, context, port)
        second = create_address(FAKE_WALLET, context, port)
        assert first.startswith("hoosat:qp01new") and second.startswith("hoosat:qp02new")
    finally:
        daemon.kill()
        daemon.wait()
        added.unlink(missing_ok=True)
