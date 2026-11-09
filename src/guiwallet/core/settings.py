"""The user's saved preferences. Passwords and keys are never stored here."""

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from guiwallet.core.validation import InvalidInput, validate_daemon_address


@dataclass
class Settings:
    tools_folder: str = ""
    keys_file: str = ""  # Where the encrypted wallet file is. Empty means htnwallet's default.
    network: str = "mainnet"
    node_address: str = "localhost:42420"
    # htnwallet's own default, so the app finds a daemon the user started themselves.
    daemon_address: str = "127.0.0.1:4282"
    close_action: str = "ask"  # "ask", "exit" or "tray"
    hide_balances: bool = False
    history_source: str = "EU"  # The explorer Transaction History reads from: "EU" or "NZ".
    # Shows the htnwallet command (password masked) and its replies in the send window.
    show_debug: bool = False

    @property
    def daemon_port(self) -> int:
        return int(self.daemon_address.rsplit(":", 1)[1])


def settings_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "guiwallet" / "settings.json"


def default_keys_file(network: str) -> Path:
    """Where htnwallet keeps its wallet file unless told otherwise."""
    folder = "hoosat-testnet" if network == "testnet" else "hoosat-mainnet"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home())
        return base / "Htnwallet" / folder / "keys.json"
    return Path.home() / ".htnwallet" / folder / "keys.json"


def load_settings(path: Path | None = None) -> Settings:
    """Load saved settings, ignoring anything missing, unknown or of the wrong type."""
    path = path or settings_path()
    settings = Settings()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if not isinstance(saved, dict):
        return settings
    for setting in fields(Settings):
        value = saved.get(setting.name)
        if type(value) is type(getattr(settings, setting.name)):
            setattr(settings, setting.name, value)
    if settings.network not in ("mainnet", "testnet"):
        settings.network = "mainnet"
    if settings.close_action not in ("ask", "exit", "tray"):
        settings.close_action = "ask"
    if settings.history_source not in ("EU", "NZ"):
        settings.history_source = "EU"
    try:
        validate_daemon_address(settings.daemon_address)
    except InvalidInput:
        settings.daemon_address = Settings().daemon_address
    return settings


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Save by writing a new file and swapping it in, so a crash can't leave half a file."""
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    temporary.replace(path)
