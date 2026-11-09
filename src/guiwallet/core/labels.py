"""Names the user gives their addresses, such as "Mining rig".

Purely cosmetic: kept only in this app's labels.json beside settings.json. They're never sent anywhere
and never written to the wallet, so restoring the wallet elsewhere doesn't bring them back.
"""

import json
from pathlib import Path

from guiwallet.core.settings import settings_path

MAX_LABEL_LENGTH = 40


def labels_path() -> Path:
    return settings_path().parent / "labels.json"


def clean_label(text: str) -> str:
    """One line, no control characters, at most MAX_LABEL_LENGTH characters. Empty means no label."""
    text = "".join(character if character.isprintable() else " " for character in text)
    return " ".join(text.split())[:MAX_LABEL_LENGTH]


def load_labels(path: Path | None = None) -> dict[str, str]:
    try:
        saved = json.loads((path or labels_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict):
        return {}
    labels = {}
    for address, name in saved.items():
        if isinstance(address, str) and isinstance(name, str) and (name := clean_label(name)):
            labels[address] = name
    return labels


def save_labels(labels: dict[str, str], path: Path | None = None) -> None:
    """Written to a new file and swapped in, so a crash can't leave half a file."""
    path = path or labels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(labels, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def set_label(labels: dict[str, str], address: str, text: str) -> None:
    """Change one label in place; an empty one removes it."""
    name = clean_label(text)
    if name:
        labels[address] = name
    else:
        labels.pop(address, None)
