"""Transaction History: an address's recent transactions, read from the explorer API and kept on this computer.

The explorer lists an address's transactions newest first, at most 500 a page. It can't leave mining
rewards out, so they're read and discarded here, never stored (only which of their coins went to this address,
for a few days, to recognise the compounds that spend them). Compounds (an address paying only itself)
are discarded too. What's kept is a short summary of each remaining transaction; whether it shows as
Sent, Received or Internal is decided when it's shown, against the wallet's current addresses.

The first visit to an address reads back 7 days, page by page, but no more than twenty pages: a mining
address gets a reward almost every block, so 7 days can be a great many transactions. (The user chose 7 days
over 30 so the whole period is read; the owner had suggested about ten pages, and was told about twenty.) After that, an update
reads one short page and stops at the first transaction it already has.

The explorer's owners asked for this (November 2026): cursor paging, the ten-page cap, short updates, no
more than 2 requests a second, backing off on 429 and 503, and a User-Agent naming the app and its version.
They also said resolve_previous_outpoints=no is the cheaper call; pages are read that way.
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from enum import Enum
from pathlib import Path

from guiwallet import __version__

# Where history is read from. Both explorers offer the same API; the user chooses in Settings.
API_BASES = {"EU": "https://api.explorer-eu.hoosat.network", "NZ": "https://api.explorer-nz.hoosat.network"}
DEFAULT_SOURCE = "EU"
EXPLORER_LINKS = {"NZ": "https://explorer-nz.hoosat.network", "EU": "https://explorer-eu.hoosat.network"}
SCAN_PAGE_SIZE = 500  # The most the explorer gives in one page (without input addresses).
# How many transactions one search may look up with input addresses: 50 in November 2026 ("temporary"). If the
# explorer lowers it, it says so with a 422; the batch then shrinks to its limit until the app restarts.
SEARCH_BATCH = 50
_LIMIT_MESSAGE = re.compile(r"limited to (\d+) for light")
_search_size = SEARCH_BATCH
REQUEST_GAP_SECONDS = 1.0  # Between requests made in one go: the explorer allows 2 a second.
MAX_SEARCHES_PER_STEP = 20  # Lookups in one go; anything left waits for the next update.
OWN_OUTPUTS_KEEP_MS = 3 * 86_400_000  # How long coins paid to this address are remembered, once built.
KEEP_DAYS = 7
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
REQUEST_TIMEOUT_SECONDS = 30
MAX_BUILD_PAGES = 20  # A first build stops here, even short of 7 days; older history is on the explorer.
UPDATE_PAGE_SIZE = 50  # An update reads this many, newest first, and stops at the first one it already has.
# A pending transaction is watched (updates keep coming, and read back to it) for this long. One still
# pending after that was most likely never accepted; it isn't worth polling the explorer for.
PENDING_WATCH_MS = 60 * 60 * 1000
MAX_UPDATE_PAGES = 10  # Past this, an update gives up on joining what's kept and starts afresh.
CACHE_VERSION = 2  # 2: pages read without input addresses. An older cache is rebuilt.
COINBASE_SUBNETWORK = "0100000000000000000000000000000000000000"
SOMPI_PER_HTN = Decimal(100_000_000)
USER_AGENT = f"HTN-GUI-Wallet/{__version__}"


class HistoryError(Exception):
    """The explorer couldn't be read. The message is safe to show the user."""

    def __init__(self, message: str, busy: bool = False, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.busy = busy  # The explorer said 429 or 503: too many requests, or it's overloaded.
        self.retry_after = retry_after  # Seconds it asked the app to wait, when it said.


class Kind(Enum):
    SENT = "Sent"
    RECEIVED = "Received"
    INTERNAL = "Internal"


@dataclass(frozen=True)
class Entry:
    """What's kept of one transaction, from the point of view of one address."""

    transaction_id: str
    block_time: int  # Milliseconds since 1970.
    accepted: bool
    spent_from_here: int  # Sompi this address put in.
    paid_here: int  # Sompi paid back to this address: a payment in, or change.
    others_in: tuple[str, ...]  # The other addresses coins came from.
    others_out: tuple[tuple[str, int], ...]  # Every other address paid, with its sompi.


@dataclass(frozen=True)
class Row:
    """One line of the table."""

    transaction_id: str
    block_time: int
    kind: Kind
    amount: Decimal  # Signed, in HTN: negative when it left this address.
    other_address: str
    accepted: bool


@dataclass(frozen=True)
class Unplaced:
    """A transaction read without its inputs' addresses, until it's worked out or looked up."""

    transaction_id: str
    block_time: int
    accepted: bool
    inputs: tuple[tuple[str, int], ...]  # The coin each input spends: transaction ID and output index.
    outputs: tuple[tuple[str, int], ...]  # Each output's address and sompi.


@dataclass
class Cache:
    address: str
    entries: dict[str, Entry] = field(default_factory=dict)
    complete: bool = False  # The first build has reached 7 days back (or its page cap).
    cursor: tuple[int, str] | None = None  # Where the first build carries on from.
    newest_time: int = 0  # The newest block time read so far.
    updated_at: float = 0.0  # When the explorer was last read, in seconds since 1970.
    pages_read: int = 0  # Pages the first build has read, for its cap.
    # Set when the first build stopped at its cap: the block time it reached. Nothing older is kept.
    capped_at: int | None = None
    # Coins paid to this address that a later transaction may spend: transaction ID -> (block time, {index: sompi}).
    own_outputs: dict[str, tuple[int, dict[int, int]]] = field(default_factory=dict)
    unplaced: dict[str, Unplaced] = field(default_factory=dict)

    def oldest_wanted(self, now_ms: int) -> int:
        return now_ms - KEEP_DAYS * DAY_MS

    def copy(self) -> "Cache":
        return Cache(
            self.address,
            dict(self.entries),
            self.complete,
            self.cursor,
            self.newest_time,
            self.updated_at,
            self.pages_read,
            self.capped_at,
            dict(self.own_outputs),
            dict(self.unplaced),
        )

    def pending_since(self, now_ms: int) -> int | None:
        """The block time of the oldest pending transaction still worth watching, if any."""
        watched = now_ms - PENDING_WATCH_MS
        times = [e.block_time for e in self.entries.values() if not e.accepted and e.block_time >= watched]
        return min(times, default=None)


def summarise(transaction: dict, address: str) -> Entry | None:
    """Keep what matters of a transaction for this address. None for a reward or a compound."""
    inputs = transaction.get("inputs") or []
    if not inputs or transaction.get("subnetwork_id") == COINBASE_SUBNETWORK:
        return None  # A mining reward.
    spent_from_here = 0
    others_in: list[str] = []
    for item in inputs:
        source = item.get("previous_outpoint_address")
        if source == address:
            spent_from_here += int(item.get("previous_outpoint_amount") or 0)
        elif source and source not in others_in:
            others_in.append(source)
    paid_here = 0
    others_out: dict[str, int] = {}
    for item in transaction.get("outputs") or []:
        target = item.get("script_public_key_address")
        amount = int(item.get("amount") or 0)
        if target == address:
            paid_here += amount
        elif target:
            others_out[target] = others_out.get(target, 0) + amount
    if spent_from_here and not others_out and not others_in:
        return None  # A compound: this address paid only itself.
    if not spent_from_here and not paid_here:
        return None  # It doesn't touch this address after all.
    return Entry(
        transaction_id=transaction.get("transaction_id") or "",
        block_time=int(transaction.get("block_time") or 0),
        accepted=bool(transaction.get("is_accepted")),
        spent_from_here=spent_from_here,
        paid_here=paid_here,
        others_in=tuple(others_in),
        others_out=tuple(others_out.items()),
    )


def to_row(entry: Entry, own_addresses: set[str]) -> Row:
    """Sent, Received or Internal, against the wallet's addresses as they are now."""
    if entry.spent_from_here:
        # Money left this address. The recipient is the biggest output that isn't change.
        target, amount = max(entry.others_out, key=lambda pair: pair[1]) if entry.others_out else ("", 0)
        sent = sum(value for _, value in entry.others_out) or amount
        kind = Kind.INTERNAL if target in own_addresses else Kind.SENT
        return Row(entry.transaction_id, entry.block_time, kind, -Decimal(sent) / SOMPI_PER_HTN, target, entry.accepted)
    source = entry.others_in[0] if entry.others_in else ""
    kind = Kind.INTERNAL if source and source in own_addresses else Kind.RECEIVED
    return Row(
        entry.transaction_id, entry.block_time, kind, Decimal(entry.paid_here) / SOMPI_PER_HTN, source, entry.accepted
    )


def rows_for(cache: Cache, own_addresses: set[str], hidden_ids: set[str]) -> list[Row]:
    """Every row, newest first, leaving out compounds this app started (known by their IDs)."""
    entries = sorted(cache.entries.values(), key=lambda entry: (entry.block_time, entry.transaction_id), reverse=True)
    return [to_row(entry, own_addresses) for entry in entries if entry.transaction_id not in hidden_ids]


# ----- Reading the explorer -----
#
# Pages are read with resolve_previous_outpoints=no: the explorer then gives 500 a page (with "light" it
# allows only 50) and it's the cheaper call. Without input addresses, a transaction is placed like this:
# rewards are known by their subnetwork; a transaction spending only coins this address was paid (by a reward
# or a compound the scan has already seen) is this address spending, worked out here; whatever's left, mostly
# payments from other people, is looked up in batches with POST /transactions/search (light, 50 IDs a request).


@dataclass(frozen=True)
class Page:
    transactions: list[dict]
    next_cursor: tuple[int, str] | None


def search_size() -> int:
    return _search_size


def api_base(source: str) -> str:
    return API_BASES.get(source, API_BASES[DEFAULT_SOURCE])


class _BatchTooBig(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(limit)
        self.limit = limit


def _open(request: urllib.request.Request):
    """Send a request and read its JSON answer, turning every failure into a HistoryError."""
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response), response.headers
    except urllib.error.HTTPError as error:
        if error.code == 422:
            try:
                detail = error.read(2000).decode("utf-8", "replace")
            except OSError:
                detail = ""
            if found := _LIMIT_MESSAGE.search(detail):
                raise _BatchTooBig(int(found.group(1))) from None
        if error.code in (429, 503):
            raise HistoryError(
                "The explorer is busy.", busy=True, retry_after=_retry_after(error.headers)
            ) from None
        raise HistoryError(f"The explorer answered with an error ({error.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise HistoryError("The explorer couldn't be reached.") from None
    except ValueError:
        raise HistoryError("The explorer's answer couldn't be read.") from None


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def fetch_page(
    address: str, cursor: tuple[int, str] | None = None, base: str | None = None, limit: int | None = None
) -> Page:
    """One page of an address's transactions, newest first, without input addresses. Slow: off the main thread."""
    size = limit or SCAN_PAGE_SIZE
    query = {"limit": size, "resolve_previous_outpoints": "no"}
    if cursor:
        query["before_block_time"] = cursor[0]
        query["before_transaction_id"] = cursor[1]
    base = base or api_base(DEFAULT_SOURCE)
    url = f"{base}/addresses/{urllib.parse.quote(address, safe=':')}/full-transactions?{urllib.parse.urlencode(query)}"
    transactions, headers = _open(urllib.request.Request(url, headers=_headers()))
    if not isinstance(transactions, list):
        raise HistoryError("The explorer's answer couldn't be read.")
    next_time = headers.get("X-Next-Before-Block-Time")
    next_id = headers.get("X-Next-Before-Transaction-Id")
    next_cursor = None
    if next_time and next_id:
        try:
            next_cursor = (int(next_time), next_id)
        except ValueError:
            next_cursor = None
    if next_cursor is None and len(transactions) == size:
        last = transactions[-1]  # A full page without the headers: carry on from its last transaction.
        if last.get("block_time") and last.get("transaction_id"):
            next_cursor = (int(last["block_time"]), last["transaction_id"])
    return Page(transactions, next_cursor)


def search_transactions(ids: list[str], base: str | None = None) -> list[dict]:
    """Look transactions up by ID, with input addresses and amounts. At most search_size() IDs. Slow."""
    global _search_size
    base = base or api_base(DEFAULT_SOURCE)
    url = f"{base}/transactions/search?{urllib.parse.urlencode({'resolve_previous_outpoints': 'light'})}"
    body = json.dumps({"transactionIds": ids}).encode()
    headers = {**_headers(), "Content-Type": "application/json"}
    try:
        found, _ = _open(urllib.request.Request(url, data=body, headers=headers, method="POST"))
    except _BatchTooBig as error:
        # The explorer lowered its limit: remember it, and look these up again in smaller batches.
        _search_size = max(1, min(error.limit, len(ids) - 1))
        found = []
        for start in range(0, len(ids), _search_size):
            found += search_transactions(ids[start : start + _search_size], base)
        return found
    if not isinstance(found, list):
        raise HistoryError("The explorer's answer couldn't be read.")
    return found


def _retry_after(headers) -> float | None:
    """The wait a 429 or 503 asks for, in seconds. Only the plain number form is read."""
    try:
        value = float((headers or {}).get("Retry-After") or "")
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _is_reward(transaction: dict) -> bool:
    return not transaction.get("inputs") or transaction.get("subnetwork_id") == COINBASE_SUBNETWORK


def _as_transaction(item: Unplaced, sources: list[tuple[str, int]]) -> dict:
    """An unplaced transaction in the explorer's shape, with its inputs' addresses filled in."""
    return {
        "transaction_id": item.transaction_id,
        "block_time": item.block_time,
        "is_accepted": item.accepted,
        "inputs": [{"previous_outpoint_address": a, "previous_outpoint_amount": v} for a, v in sources],
        "outputs": [{"script_public_key_address": a, "amount": v} for a, v in item.outputs],
    }


def take_page(cache: Cache, page: Page, now_ms: int) -> bool:
    """Add a page to the cache. True when nothing older is needed: 7 days reached, or no more pages."""
    oldest_wanted = cache.oldest_wanted(now_ms)
    oldest_seen = None
    for transaction in page.transactions:
        transaction_id = transaction.get("transaction_id") or ""
        block_time = int(transaction.get("block_time") or 0)
        oldest_seen = block_time if oldest_seen is None else min(oldest_seen, block_time)
        cache.newest_time = max(cache.newest_time, block_time)
        if not transaction_id or (block_time and block_time < oldest_wanted):
            continue
        outputs = transaction.get("outputs") or []
        paid = {
            int(item.get("index", position)): int(item.get("amount") or 0)
            for position, item in enumerate(outputs)
            if item.get("script_public_key_address") == cache.address
        }
        if paid:
            cache.own_outputs[transaction_id] = (block_time, paid)
        if _is_reward(transaction):
            continue
        accepted = bool(transaction.get("is_accepted"))
        if transaction_id in cache.entries:  # Seen before: only its status can have changed.
            entry = cache.entries[transaction_id]
            if entry.accepted != accepted:
                cache.entries[transaction_id] = replace(entry, accepted=accepted)
            continue
        inputs = transaction.get("inputs") or []
        if all(item.get("previous_outpoint_address") for item in inputs):
            entry = summarise(transaction, cache.address)  # The answer already says where each input came from.
            if entry is not None:
                cache.entries[transaction_id] = entry
            continue
        cache.unplaced[transaction_id] = Unplaced(
            transaction_id,
            block_time,
            accepted,
            tuple((str(i.get("previous_outpoint_hash") or ""), int(i.get("previous_outpoint_index") or 0)) for i in inputs),
            tuple((str(o.get("script_public_key_address") or ""), int(o.get("amount") or 0)) for o in outputs),
        )
    place_known(cache)
    return page.next_cursor is None or (oldest_seen is not None and oldest_seen < oldest_wanted)


def place_known(cache: Cache) -> None:
    """Place each transaction whose every input spends a coin this address was paid. No request needed."""
    for transaction_id, item in list(cache.unplaced.items()):
        sources = []
        for spent, index in item.inputs:
            paid = cache.own_outputs.get(spent)
            if paid is None or index not in paid[1]:
                break
            sources.append((cache.address, paid[1][index]))
        else:
            entry = summarise(_as_transaction(item, sources), cache.address)
            if entry is not None:
                cache.entries[transaction_id] = entry
            del cache.unplaced[transaction_id]  # Placed: an entry, or a compound that's left out.


def look_up_unplaced(cache: Cache, base: str | None, pause=time.sleep, max_requests: int = MAX_SEARCHES_PER_STEP) -> int:
    """Look up what couldn't be placed, newest first, a batch a request. Returns the requests made. Slow."""
    requests = 0
    while cache.unplaced and requests < max_requests:
        batch = sorted(cache.unplaced.values(), key=lambda item: item.block_time, reverse=True)[:_search_size]
        pause(REQUEST_GAP_SECONDS)
        found = search_transactions([item.transaction_id for item in batch], base)
        requests += 1
        for transaction in found:
            entry = summarise(transaction, cache.address)
            if entry is not None:
                cache.entries[entry.transaction_id] = entry
        for item in batch:
            cache.unplaced.pop(item.transaction_id, None)  # One the explorer doesn't know is dropped, not retried.
    return requests


def days_read(cache: Cache, now_ms: int) -> int:
    """How many of the 7 days the first build has covered, for its progress bar."""
    if cache.complete:
        return KEEP_DAYS
    if cache.cursor is None:
        return 0
    return max(0, min(KEEP_DAYS, (now_ms - cache.cursor[0]) // DAY_MS))


def build_step(cache: Cache, now_ms: int, base: str | None = None, pause=time.sleep) -> None:
    """Read the next page of the first build; at its end, look up what's left. Slow: off the main thread."""
    page = fetch_page(cache.address, cache.cursor, base)
    done = take_page(cache, page, now_ms)
    cache.pages_read += 1
    if not done and cache.pages_read >= MAX_BUILD_PAGES:
        done = True
        cache.capped_at = page.next_cursor[0] if page.next_cursor else None
    cache.cursor = None if done else page.next_cursor
    if done:
        look_up_unplaced(cache, base, pause)
    cache.complete = done
    cache.updated_at = time.time()


def top_up(
    cache: Cache, now_ms: int, base: str | None = None, max_pages: int = MAX_UPDATE_PAGES, pause=time.sleep
) -> None:
    """Read what's new, a short page at a time, until a transaction already seen. Slow: off the main thread.

    A kept transaction that was still pending doesn't end it: the update reads on until it's past every
    pending one, so their status gets refreshed. If it reads max_pages without reaching anything seen, too
    much has happened since the last read to join the two; what's kept is dropped and the history starts
    again from what was just read, as a capped build would.
    """
    pending_since = cache.pending_since(now_ms)
    seen = set(cache.entries) | set(cache.own_outputs) | set(cache.unplaced)
    fresh = Cache(cache.address)
    fresh.entries = {key: entry for key, entry in cache.entries.items() if not entry.accepted}  # Status updates.
    cursor = None
    joined = not seen and cache.newest_time == 0
    for number in range(max_pages):
        if number:
            pause(REQUEST_GAP_SECONDS)
        page = fetch_page(cache.address, cursor, base, UPDATE_PAGE_SIZE)
        take_page(fresh, page, now_ms)
        reached_known = any(
            transaction.get("transaction_id") in seen
            and (pending_since is None or int(transaction.get("block_time") or 0) <= pending_since)
            for transaction in page.transactions
        )
        # Older than everything ever read: nothing more to find, even if nothing seen was met.
        reached_old = any(
            int(transaction.get("block_time") or 0) < min(cache.newest_time, pending_since or cache.newest_time)
            for transaction in page.transactions
        )
        if reached_known or reached_old or page.next_cursor is None:
            joined = True
            break
        cursor = page.next_cursor
    if joined:
        cache.entries.update(fresh.entries)
        cache.own_outputs.update(fresh.own_outputs)
        cache.unplaced.update({key: item for key, item in fresh.unplaced.items() if key not in cache.entries})
        cache.newest_time = max(cache.newest_time, fresh.newest_time)
    else:
        cache.entries = fresh.entries
        cache.own_outputs = fresh.own_outputs
        cache.unplaced = fresh.unplaced
        cache.newest_time = fresh.newest_time
        cache.capped_at = cursor[0] if cursor else None
    place_known(cache)
    if cache.unplaced:
        look_up_unplaced(cache, base, pause)
    cache.updated_at = time.time()


def forget_old(cache: Cache, now_ms: int) -> None:
    oldest_wanted = cache.oldest_wanted(now_ms)
    cache.entries = {key: entry for key, entry in cache.entries.items() if entry.block_time >= oldest_wanted}
    cache.unplaced = {key: item for key, item in cache.unplaced.items() if item.block_time >= oldest_wanted}
    if cache.complete:
        # Coins paid long ago are rarely what a new transaction spends; one that does is looked up instead.
        kept_from = now_ms - OWN_OUTPUTS_KEEP_MS
        cache.own_outputs = {key: paid for key, paid in cache.own_outputs.items() if paid[0] >= kept_from}
    if cache.capped_at is not None and cache.capped_at < oldest_wanted:
        cache.capped_at = None  # Thirty days have passed since the cap: everything wanted is kept now.


def days_shown(cache: Cache, now_ms: int) -> str | None:
    """How far back a capped history reaches, such as "2 days" or "5 hours". None when it isn't capped."""
    if cache.capped_at is None:
        return None
    span = max(0, now_ms - cache.capped_at)
    if span >= DAY_MS:
        days = span // DAY_MS
        return f"{days} day{'s' if days != 1 else ''}"
    hours = max(1, span // HOUR_MS)
    return f"{hours} hour{'s' if hours != 1 else ''}"


# ----- Kept on this computer -----


def history_folder(settings_file: Path) -> Path:
    return settings_file.parent / "history"


def cache_path(folder: Path, network: str, address: str) -> Path:
    return folder / f"{network}-{address.split(':')[-1]}.json"


def load_cache(path: Path, address: str) -> Cache:
    """The saved history of an address, or an empty one if there's none, it can't be read, or it's an older kind."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != CACHE_VERSION or data.get("address") != address:
            return Cache(address)
        entries = {}
        for item in data.get("entries", []):
            entry = Entry(
                transaction_id=item["transaction_id"],
                block_time=int(item["block_time"]),
                accepted=bool(item["accepted"]),
                spent_from_here=int(item["spent_from_here"]),
                paid_here=int(item["paid_here"]),
                others_in=tuple(item["others_in"]),
                others_out=tuple((pair[0], int(pair[1])) for pair in item["others_out"]),
            )
            entries[entry.transaction_id] = entry
        own_outputs = {
            key: (int(value[0]), {int(index): int(amount) for index, amount in value[1]})
            for key, value in data.get("own_outputs", {}).items()
        }
        unplaced = {}
        for item in data.get("unplaced", []):
            unplaced[item["transaction_id"]] = Unplaced(
                item["transaction_id"],
                int(item["block_time"]),
                bool(item["accepted"]),
                tuple((str(a), int(b)) for a, b in item["inputs"]),
                tuple((str(a), int(b)) for a, b in item["outputs"]),
            )
        cursor = tuple(data["cursor"]) if data.get("cursor") else None
        capped_at = data.get("capped_at")
        return Cache(
            address,
            entries,
            bool(data.get("complete")),
            (int(cursor[0]), str(cursor[1])) if cursor else None,
            int(data.get("newest_time") or 0),
            float(data.get("updated_at") or 0),
            int(data.get("pages_read") or 0),
            int(capped_at) if capped_at is not None else None,
            own_outputs,
            unplaced,
        )
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return Cache(address)


def save_cache(path: Path, cache: Cache) -> None:
    data = {
        "version": CACHE_VERSION,
        "address": cache.address,
        "complete": cache.complete,
        "cursor": list(cache.cursor) if cache.cursor else None,
        "newest_time": cache.newest_time,
        "updated_at": cache.updated_at,
        "pages_read": cache.pages_read,
        "capped_at": cache.capped_at,
        "entries": [asdict(entry) for entry in cache.entries.values()],
        "own_outputs": {key: [paid[0], list(paid[1].items())] for key, paid in cache.own_outputs.items()},
        "unplaced": [asdict(item) for item in cache.unplaced.values()],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    os.replace(temporary, path)  # Never a half-written file, even if the app stops mid-save.


def load_hidden_ids(folder: Path) -> dict[str, float]:
    """Transaction IDs of compounds this app started, with when each was seen."""
    try:
        data = json.loads((folder / "compounds.json").read_text(encoding="utf-8"))
        return {str(key): float(value) for key, value in data.items()}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def save_hidden_ids(folder: Path, ids: dict[str, float]) -> None:
    oldest = time.time() - KEEP_DAYS * 86_400
    kept = {key: seen for key, seen in ids.items() if seen >= oldest}
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / "compounds.tmp"
    temporary.write_text(json.dumps(kept), encoding="utf-8")
    os.replace(temporary, folder / "compounds.json")


def transaction_link(site: str, transaction_id: str) -> str:
    return f"{EXPLORER_LINKS[site]}/txs/{transaction_id}"


def address_link(site: str, address: str) -> str:
    return f"{EXPLORER_LINKS[site]}/addresses/{address}"
