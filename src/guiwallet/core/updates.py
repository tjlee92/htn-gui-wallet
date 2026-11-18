"""Checking GitHub for newer releases of this app and of HTND.

The app asks at startup and then once a day. Each check is one request per project to GitHub's public
API, for the release marked Latest there (drafts and pre-releases never are). Nothing about the wallet is
sent, and nothing is downloaded or installed: the user is only told, and the Download button opens the
release page in their browser.
"""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from guiwallet import __version__

APP_REPO = "tjlee92/htn-gui-wallet"
HTND_REPO = "HoosatNetwork/HTND"
APP_RELEASES = f"https://github.com/{APP_REPO}/releases"
HTND_RELEASES = f"https://github.com/{HTND_REPO}/releases"
API = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = f"HTN-GUI-Wallet/{__version__}"
REQUEST_TIMEOUT_SECONDS = 10
MAX_REPLY_BYTES = 1_000_000  # A release's details are a few kilobytes; anything bigger isn't one.
FIRST_CHECK_DELAY_SECONDS = 15  # After startup, so the check doesn't compete with reading the wallet.
CHECK_EVERY_SECONDS = 24 * 60 * 60

# "v2.19.1", "2.19.0-fabe17858" (htnwallet's own, with its commit) or "1.1.0": the numbers are what count.
_VERSION = re.compile(r"^v?(\d+(?:\.\d+){0,3})(?:[-+ ].*)?$")


class UpdateCheckError(Exception):
    """GitHub couldn't be asked, or its answer wasn't a release."""


@dataclass(frozen=True)
class Release:
    version: str  # Without the "v", such as "2.19.1".
    url: str  # Its page on GitHub, where the downloads are.


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """The numbers of a version, such as (2, 19, 0) for "2.19.0-fabe17858". None if it isn't one."""
    match = _VERSION.match((text or "").strip())
    if not match:
        return None
    numbers = tuple(int(part) for part in match.group(1).split("."))
    return numbers + (0,) * (3 - len(numbers)) if len(numbers) < 3 else numbers


def plain_version(text: str | None) -> str | None:
    """The version as people write it, such as "2.19.0" for htnwallet's "2.19.0-fabe17858"."""
    numbers = parse_version(text)
    return ".".join(str(number) for number in numbers) if numbers else None


def is_newer(latest: str | None, installed: str | None) -> bool:
    """Whether the latest release is a higher version than the one installed. False if either is unknown."""
    latest_numbers, installed_numbers = parse_version(latest), parse_version(installed)
    if latest_numbers is None or installed_numbers is None:
        return False
    return latest_numbers > installed_numbers


def latest_release(repo: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> Release:
    """Ask GitHub for a project's latest release. Raises UpdateCheckError if that doesn't work."""
    request = urllib.request.Request(
        API.format(repo=repo),
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_REPLY_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise UpdateCheckError(f"GitHub couldn't be reached: {error}") from None
    if len(body) > MAX_REPLY_BYTES:
        raise UpdateCheckError("GitHub's answer was too long to be a release.")
    try:
        reply = json.loads(body)
    except ValueError:
        raise UpdateCheckError("GitHub's answer wasn't a release.") from None
    if not isinstance(reply, dict):
        raise UpdateCheckError("GitHub's answer wasn't a release.")
    version = plain_version(reply.get("tag_name") if isinstance(reply.get("tag_name"), str) else None)
    if version is None:
        raise UpdateCheckError("GitHub's latest release has no version number.")
    page = reply.get("html_url")
    # Only ever open the project's own release pages, whatever the answer says.
    releases = f"https://github.com/{repo}/releases"
    url = page if isinstance(page, str) and page.startswith(releases + "/") else releases
    return Release(version, url)
