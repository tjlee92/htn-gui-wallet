import json
import urllib.error

import pytest

from guiwallet.core import updates
from guiwallet.core.updates import (
    Release,
    UpdateCheckError,
    is_newer,
    latest_release,
    parse_version,
    plain_version,
)


def test_versions_are_read_from_tags_and_from_htnwallet():
    assert parse_version("v2.19.1") == (2, 19, 1)
    assert parse_version("2.19.0-fabe17858") == (2, 19, 0)
    assert parse_version("1.1.0") == (1, 1, 0)
    assert parse_version("v2.20") == (2, 20, 0)
    assert parse_version("0.0.0-sample") == (0, 0, 0)


def test_things_that_are_not_versions_give_none():
    for text in (None, "", "latest", "version two", "v", "2.x.1"):
        assert parse_version(text) is None


def test_plain_version_drops_the_commit():
    assert plain_version("2.19.0-fabe17858") == "2.19.0"
    assert plain_version("v1.2.0") == "1.2.0"
    assert plain_version("nonsense") is None


def test_only_a_higher_version_counts_as_newer():
    assert is_newer("2.19.1", "2.19.0-fabe17858")
    assert is_newer("1.10.0", "1.9.9")  # Compared as numbers, not as text.
    assert not is_newer("2.19.0", "2.19.0-fabe17858")  # Same version, whatever the commit.
    assert not is_newer("1.1.0", "1.2.0")
    assert not is_newer(None, "1.1.0")
    assert not is_newer("1.2.0", None)


class _Reply:
    def __init__(self, body: bytes):
        self.body = body

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _answer(monkeypatch, body, seen=None):
    def urlopen(request, timeout):
        if seen is not None:
            seen.append(request)
        if isinstance(body, Exception):
            raise body
        return _Reply(body if isinstance(body, bytes) else json.dumps(body).encode())

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)


def test_latest_release_reads_the_tag_and_page(monkeypatch):
    seen = []
    page = "https://github.com/HoosatNetwork/HTND/releases/tag/v2.19.1"
    _answer(monkeypatch, {"tag_name": "v2.19.1", "name": "v2.19.1 [OPTIONAL]", "html_url": page}, seen)
    assert latest_release("HoosatNetwork/HTND") == Release("2.19.1", page)
    assert seen[0].full_url == "https://api.github.com/repos/HoosatNetwork/HTND/releases/latest"
    assert seen[0].get_header("User-agent").startswith("HTN-GUI-Wallet/")


def test_a_page_outside_the_project_is_never_used(monkeypatch):
    _answer(monkeypatch, {"tag_name": "v1.2.0", "html_url": "https://example.com/malware"})
    assert latest_release("tjlee92/htn-gui-wallet").url == "https://github.com/tjlee92/htn-gui-wallet/releases"


@pytest.mark.parametrize(
    "body",
    [
        urllib.error.HTTPError("u", 404, "Not Found", {}, None),  # Such as while the repo is private.
        urllib.error.URLError("offline"),
        TimeoutError(),
        b"<html>not json</html>",
        b"[]",
        {"message": "API rate limit exceeded"},
        {"tag_name": "nightly"},
        b"x" * (updates.MAX_REPLY_BYTES + 1),
    ],
)
def test_anything_but_a_release_is_an_error(monkeypatch, body):
    _answer(monkeypatch, body)
    with pytest.raises(UpdateCheckError):
        latest_release("HoosatNetwork/HTND")
