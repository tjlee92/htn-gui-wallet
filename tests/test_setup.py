"""The Setup Wizard's core: first launch, wallet file names, flag reading, and making wallets via the fake htnwallet."""

import os
from pathlib import Path

import pytest

from guiwallet.core import setup
from guiwallet.core.settings import Settings, default_keys_file
from guiwallet.core.setup import (
    INVALID_BACKUP,
    INVALID_KEY,
    INVALID_PHRASE,
    SetupError,
    check_new_password,
    create_wallet,
    free_wallet_file,
    is_first_launch,
    keys_file_setting,
    read_seed_phrase,
    restore_from_phrase,
    restore_private_key,
    restore_web_wallet_backup,
    restore_web_wallet_phrase,
    wallet_file,
)
from guiwallet.core.tools import CommandNotAllowed, Tool, run_setup_command, run_tool
from guiwallet.core.validation import InvalidInput

FAKE_WALLET = Path(os.environ.get("FAKE_HTNWALLET", "scripts/dev-tests/fakewallet/bin/htnwallet.exe"))
needs_fake = pytest.mark.skipif(not FAKE_WALLET.is_file(), reason="the fake htnwallet isn't built")
PHRASE = (
    "abandon ability able about above absent absorb abstract absurd abuse access accident "
    "account accuse achieve acid acoustic acquire across act action actor actress actual"
)


def test_first_launch_is_when_no_settings_are_saved(tmp_path):
    path = tmp_path / "settings.json"
    assert is_first_launch(path)
    path.write_text("{}", encoding="utf-8")
    assert not is_first_launch(path)


def test_free_wallet_file_never_takes_an_existing_name(tmp_path):
    keys = tmp_path / "keys.json"
    assert free_wallet_file(keys) == keys
    keys.write_text("x")
    assert free_wallet_file(keys) == tmp_path / "keys-2.json"
    (tmp_path / "keys-2.json").write_text("x")
    assert free_wallet_file(keys) == tmp_path / "keys-3.json"


def test_default_wallet_file_is_stored_as_empty():
    default = default_keys_file("mainnet")
    assert keys_file_setting(default, "mainnet") == ""
    other = default.with_name("keys-2.json")
    assert keys_file_setting(other, "mainnet") == str(other)
    assert wallet_file(Settings()) == default
    assert wallet_file(Settings(keys_file=str(other))) == other


@pytest.mark.parametrize(
    "arguments",
    [
        ["start-daemon", "--keys-file=C:\\w\\k.json"],
        ["start-daemon", "--keys-file", "C:\\w\\k.json"],
        ["start-daemon", "-f", "C:\\w\\k.json"],
        ["start-daemon", "-fC:\\w\\k.json"],
        ["start-daemon", "/f", "C:\\w\\k.json"],
        ["start-daemon", "/f:C:\\w\\k.json"],
    ],
)
def test_keys_file_is_read_from_every_flag_form(arguments):
    assert setup._flag_value(arguments, "keys-file", "f") == "C:\\w\\k.json"


def test_no_keys_flag_means_the_default():
    assert setup._flag_value(["start-daemon", "--listen=127.0.0.1:4282"], "keys-file", "f") is None


def test_new_password_must_match_and_be_usable():
    assert check_new_password("secret", "secret") == "secret"
    with pytest.raises(InvalidInput):
        check_new_password("secret", "secret2")
    with pytest.raises(InvalidInput):
        check_new_password("", "")


def test_setup_commands_only_run_through_the_setup_runner(tmp_path):
    for command in ("create", "dump-unencrypted-data", "import-web-wallet", "import-private-key"):
        with pytest.raises(CommandNotAllowed):
            run_tool(tmp_path / "htnwallet.exe", Tool.HTNWALLET, command)
    with pytest.raises(CommandNotAllowed):
        run_setup_command(tmp_path / "htnwallet.exe", "send", [])


@needs_fake
def test_create_then_read_the_new_seed_phrase(tmp_path):
    keys = tmp_path / "wallet" / "keys.json"
    create_wallet(FAKE_WALLET, "mainnet", keys, "pw")
    assert keys.is_file()
    assert read_seed_phrase(FAKE_WALLET, "mainnet", keys, "pw") == PHRASE.split()


@needs_fake
def test_an_existing_wallet_file_is_never_overwritten(tmp_path):
    keys = tmp_path / "keys.json"
    keys.write_text("original")
    with pytest.raises(SetupError):
        create_wallet(FAKE_WALLET, "mainnet", keys, "pw")
    assert keys.read_text() == "original"


@needs_fake
def test_restore_passes_the_phrase_exactly_as_typed(tmp_path):
    restore_from_phrase(FAKE_WALLET, "mainnet", tmp_path / "a.json", "pw", PHRASE)
    assert (tmp_path / "a.json").is_file()
    # Not tidied by the app: a capital or a double space reaches htnwallet as typed, and it refuses.
    for typed in (PHRASE.capitalize(), PHRASE.replace(" ", "  ", 1), PHRASE.replace("abuse", "abuze")):
        target = tmp_path / "b.json"
        with pytest.raises(SetupError) as failure:
            restore_from_phrase(FAKE_WALLET, "mainnet", target, "pw", typed)
        assert str(failure.value) == INVALID_PHRASE
        assert not target.exists()


@needs_fake
def test_web_wallet_and_private_key_restores(tmp_path):
    restore_web_wallet_phrase(FAKE_WALLET, "mainnet", tmp_path / "w.json", "pw", PHRASE)
    backup = tmp_path / "backup.txt"
    backup.write_text("encrypted")
    restore_web_wallet_backup(FAKE_WALLET, "mainnet", tmp_path / "b.json", "pw", backup, "backup")
    with pytest.raises(SetupError) as failure:
        restore_web_wallet_backup(FAKE_WALLET, "mainnet", tmp_path / "c.json", "pw", backup, "wrong")
    assert str(failure.value) == INVALID_BACKUP
    restore_private_key(FAKE_WALLET, "mainnet", tmp_path / "k.json", "pw", "ab" * 32)
    with pytest.raises(SetupError) as failure:
        restore_private_key(FAKE_WALLET, "mainnet", tmp_path / "l.json", "pw", "abc")
    assert str(failure.value) == INVALID_KEY
