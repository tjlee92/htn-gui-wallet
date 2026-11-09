import json

from guiwallet.core.settings import Settings, load_settings, save_settings


def test_saved_settings_load_back(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    save_settings(Settings(tools_folder="T:/tools", network="testnet"), path)
    loaded = load_settings(path)
    assert loaded.tools_folder == "T:/tools" and loaded.network == "testnet"


def test_missing_or_broken_files_give_defaults(tmp_path):
    assert load_settings(tmp_path / "missing.json") == Settings()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert load_settings(broken) == Settings()


def test_bad_values_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"network": "evilnet", "daemon_address": 4282, "unknown": 1, "tools_folder": "ok"}))
    loaded = load_settings(path)
    assert loaded.network == "mainnet"
    assert loaded.daemon_address == Settings().daemon_address
    assert loaded.tools_folder == "ok"


def test_hidden_balances_are_remembered_and_off_by_default(tmp_path):
    assert Settings().hide_balances is False
    path = tmp_path / "settings.json"
    save_settings(Settings(hide_balances=True), path)
    assert load_settings(path).hide_balances is True


def test_a_bad_hidden_balances_value_is_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"hide_balances": "yes"}))
    assert load_settings(path).hide_balances is False


def test_settings_have_nowhere_to_hold_secrets():
    names = set(Settings.__dataclass_fields__)
    assert not any(word in name for name in names for word in ("password", "mnemonic", "secret", "private"))


def test_bad_close_action_and_port_fall_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"close_action": "explode", "daemon_address": "192.168.0.5:4282"}))
    loaded = load_settings(path)
    assert loaded.close_action == "ask"
    assert loaded.daemon_address == "127.0.0.1:4282"
    assert loaded.daemon_port == 4282


def test_debug_details_are_off_unless_switched_on(tmp_path):
    from guiwallet.core.settings import Settings, load_settings, save_settings

    path = tmp_path / "settings.json"
    assert Settings().show_debug is False
    save_settings(Settings(show_debug=True), path)
    assert load_settings(path).show_debug is True
    path.write_text('{"show_debug": "yes"}', encoding="utf-8")
    assert load_settings(path).show_debug is False
