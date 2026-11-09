import io
import json
import os
import time
from datetime import datetime
from decimal import Decimal

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from guiwallet.core import history  # noqa: E402
from guiwallet.core.compound import _Started  # noqa: E402
from guiwallet.core.history import (  # noqa: E402
    DAY_MS,
    Cache,
    HistoryError,
    Kind,
    Page,
    build_step,
    cache_path,
    load_cache,
    load_hidden_ids,
    rows_for,
    save_cache,
    save_hidden_ids,
    summarise,
    take_page,
    to_row,
    top_up,
)
from guiwallet.core.wallet import AddressBalance, WalletBalance  # noqa: E402
from guiwallet.ui.history_page import PERIODS, HistoryPage, filter_rows, format_amount, format_date, page_numbers  # noqa: E402
from guiwallet.ui.widgets import HIDDEN_AMOUNT  # noqa: E402

MINE = "hoosat:qq" + "a" * 61
OTHER_MINE = "hoosat:qq" + "c" * 61
STRANGER = "hoosat:qq" + "s" * 61
SHOP = "hoosat:qq" + "d" * 61
NOW_MS = 1_800_000_000_000
COIN = 100_000_000


def tx(tx_id, minutes_ago=1, inputs=(), outputs=(), accepted=True, coinbase=False):
    return {
        "transaction_id": tx_id,
        "block_time": NOW_MS - minutes_ago * 60_000,
        "is_accepted": accepted,
        "subnetwork_id": "0100000000000000000000000000000000000000" if coinbase else "0" * 40,
        "inputs": [{"previous_outpoint_address": a, "previous_outpoint_amount": v} for a, v in inputs],
        "outputs": [{"script_public_key_address": a, "amount": v} for a, v in outputs],
    }


@pytest.fixture(autouse=True)
def no_pauses(monkeypatch):
    monkeypatch.setattr(history, "REQUEST_GAP_SECONDS", 0)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ----- What a transaction is -----


def test_mining_rewards_are_dropped():
    assert summarise(tx("r", outputs=[(MINE, 13 * COIN)], coinbase=True), MINE) is None
    assert summarise(tx("r2", outputs=[(MINE, 13 * COIN)]), MINE) is None  # No inputs at all.


def test_a_compound_is_dropped():
    compound = tx("c", inputs=[(MINE, 5 * COIN), (MINE, 6 * COIN)], outputs=[(MINE, 11 * COIN - 2000)])
    assert summarise(compound, MINE) is None


def test_a_payment_in_is_received():
    entry = summarise(tx("p", inputs=[(STRANGER, 300 * COIN)], outputs=[(MINE, 250 * COIN), (STRANGER, 49 * COIN)]), MINE)
    row = to_row(entry, {MINE})
    assert row.kind is Kind.RECEIVED and row.amount == Decimal(250) and row.other_address == STRANGER


def test_a_payment_out_is_sent_without_the_change():
    entry = summarise(tx("s", inputs=[(MINE, 100 * COIN)], outputs=[(SHOP, 40 * COIN), (MINE, 59 * COIN)]), MINE)
    row = to_row(entry, {MINE})
    assert row.kind is Kind.SENT and row.amount == Decimal(-40) and row.other_address == SHOP


def test_moves_between_own_addresses_are_internal_both_ways():
    move = tx("m", inputs=[(MINE, 20 * COIN)], outputs=[(OTHER_MINE, 15 * COIN), (MINE, 4 * COIN)])
    out = to_row(summarise(move, MINE), {MINE, OTHER_MINE})
    arrived = to_row(summarise(move, OTHER_MINE), {MINE, OTHER_MINE})
    assert (out.kind, out.amount, out.other_address) == (Kind.INTERNAL, Decimal(-15), OTHER_MINE)
    assert (arrived.kind, arrived.amount, arrived.other_address) == (Kind.INTERNAL, Decimal(15), MINE)


def test_internal_is_decided_against_the_current_addresses():
    entry = summarise(tx("m", inputs=[(MINE, 20 * COIN)], outputs=[(OTHER_MINE, 15 * COIN)]), MINE)
    assert to_row(entry, {MINE}).kind is Kind.SENT
    assert to_row(entry, {MINE, OTHER_MINE}).kind is Kind.INTERNAL


def test_pending_comes_from_the_explorer():
    entry = summarise(tx("p", inputs=[(STRANGER, COIN)], outputs=[(MINE, COIN)], accepted=False), MINE)
    assert not to_row(entry, {MINE}).accepted


def test_compounds_this_app_started_are_hidden_by_id():
    cache = Cache(MINE)
    take_page(cache, Page([tx("ab", inputs=[(MINE, COIN)], outputs=[(OTHER_MINE, COIN)])], None), NOW_MS)
    assert len(rows_for(cache, {MINE, OTHER_MINE}, set())) == 1
    assert rows_for(cache, {MINE, OTHER_MINE}, {"ab"}) == []


def test_compound_ids_are_read_from_htnwallets_output():
    started = _Started(None, MINE, MINE, 60, 0)
    started.take_line(f"[15:04:05] COMPOUNDED → https://explorer-eu.hoosat.network/txs/{'AB' * 32}")
    started.take_line("[15:05:05] NOTHING TO COMPOUND, backing off for 5m")
    assert started.take_ids() == ["ab" * 32] and started.take_ids() == []


# ----- Reading 7 days -----


def payment(index, minutes_ago):
    return tx(f"t{index}", minutes_ago, inputs=[(STRANGER, COIN)], outputs=[(MINE, COIN)])


def test_a_page_past_30_days_ends_the_build_and_older_ones_are_not_kept():
    cache = Cache(MINE)
    page = Page([payment(1, 10), payment(2, 8 * 24 * 60)], next_cursor=(1, "x"))
    assert take_page(cache, page, NOW_MS)
    assert list(cache.entries) == ["t1"]


def test_build_steps_follow_the_cursor_until_done(monkeypatch):
    pages = [
        Page([payment(1, 5)], (NOW_MS - DAY_MS, "t1")),
        Page([payment(2, 2 * 24 * 60)], None),
    ]
    asked = []
    monkeypatch.setattr(history, "fetch_page", lambda address, cursor, base: asked.append(cursor) or pages.pop(0))
    cache = Cache(MINE)
    build_step(cache, NOW_MS)
    assert not cache.complete and cache.cursor == (NOW_MS - DAY_MS, "t1")
    build_step(cache, NOW_MS)
    assert cache.complete and set(cache.entries) == {"t1", "t2"} and asked == [None, (NOW_MS - DAY_MS, "t1")]


def reward(index, minutes_ago):
    return tx(f"r{index}", minutes_ago, outputs=[(MINE, 13 * COIN)], coinbase=True)


def fake_pages(monkeypatch, pages):
    asked = []

    def fetch(address, cursor=None, base=None, limit=None):
        asked.append((cursor, limit))
        return pages.pop(0)

    monkeypatch.setattr(history, "fetch_page", fetch)
    return asked


def kept(minutes_ago=60):
    """A finished cache holding one confirmed payment."""
    cache = Cache(MINE, complete=True)
    take_page(cache, Page([payment(0, minutes_ago)], None), NOW_MS)
    return cache


def test_the_first_build_stops_after_twenty_pages_and_says_how_far_it_reached(monkeypatch):
    pages = [Page([reward(n, n * 10)], (NOW_MS - (n + 1) * 10 * 60_000, f"r{n}")) for n in range(22)]
    asked = fake_pages(monkeypatch, pages)
    cache = Cache(MINE)
    while not cache.complete:
        build_step(cache, NOW_MS)
    assert len(asked) == history.MAX_BUILD_PAGES == 20
    assert cache.capped_at == NOW_MS - 200 * 60_000 and cache.cursor is None
    assert history.days_shown(cache, NOW_MS) == "3 hours"
    cache.capped_at = NOW_MS - 2 * DAY_MS - 5
    assert history.days_shown(cache, NOW_MS) == "2 days"


def test_a_build_that_reaches_30_days_is_not_capped(monkeypatch):
    fake_pages(monkeypatch, [Page([payment(1, 5)], (1, "t1")), Page([payment(2, 8 * 24 * 60)], (1, "t2"))])
    cache = Cache(MINE)
    build_step(cache, NOW_MS)
    build_step(cache, NOW_MS)
    assert cache.complete and cache.capped_at is None and history.days_shown(cache, NOW_MS) is None


def test_an_update_reads_one_short_page_and_stops_at_a_kept_transaction(monkeypatch):
    cache = kept()
    asked = fake_pages(monkeypatch, [Page([payment(3, 1), reward(1, 2), payment(0, 60)], (1, "t0"))])
    top_up(cache, NOW_MS)
    assert asked == [(None, 50)]
    assert set(cache.entries) == {"t0", "t3"}


def test_an_update_with_nothing_new_is_one_request(monkeypatch):
    cache = kept()
    asked = fake_pages(monkeypatch, [Page([payment(0, 60), payment(9, 90)], (1, "t9"))])
    top_up(cache, NOW_MS)
    assert len(asked) == 1


def test_an_update_reads_on_past_a_pending_transaction(monkeypatch):
    cache = kept(minutes_ago=5)
    pending = summarise(tx("p", 30, inputs=[(STRANGER, COIN)], outputs=[(MINE, COIN)], accepted=False), MINE)
    cache.entries["p"] = pending
    confirmed = tx("p", 30, inputs=[(STRANGER, COIN)], outputs=[(MINE, COIN)])
    asked = fake_pages(monkeypatch, [Page([payment(0, 5)], (1, "t0")), Page([confirmed, payment(8, 700)], None)])
    top_up(cache, NOW_MS)
    assert len(asked) == 2 and cache.entries["p"].accepted


def test_a_transaction_pending_for_over_an_hour_is_no_longer_watched():
    cache = kept()
    cache.entries["p"] = summarise(tx("p", 90, inputs=[(STRANGER, COIN)], outputs=[(MINE, COIN)], accepted=False), MINE)
    assert cache.pending_since(NOW_MS) is None
    cache.entries["q"] = summarise(tx("q", 20, inputs=[(STRANGER, COIN)], outputs=[(MINE, COIN)], accepted=False), MINE)
    assert cache.pending_since(NOW_MS) == NOW_MS - 20 * 60_000


def test_an_update_that_cannot_reach_what_is_kept_starts_afresh(monkeypatch):
    cache = kept(minutes_ago=600)
    cache.newest_time = NOW_MS - 600 * 60_000
    pages = [Page([payment(100 + n, n)], (NOW_MS - (n + 1) * 60_000, f"t{100 + n}")) for n in range(10)]
    fake_pages(monkeypatch, pages)
    top_up(cache, NOW_MS)
    assert "t0" not in cache.entries and len(cache.entries) == 10
    assert cache.capped_at == NOW_MS - 10 * 60_000


def test_the_cap_is_lifted_once_30_days_have_passed():
    cache = kept()
    cache.capped_at = NOW_MS - 8 * DAY_MS
    history.forget_old(cache, NOW_MS)
    assert cache.capped_at is None


class FakeResponse(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(json.dumps(body).encode())
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_fetch_page_reads_the_next_cursor_and_asks_for_input_addresses(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        return FakeResponse([payment(1, 1)], {"X-Next-Before-Block-Time": "123", "X-Next-Before-Transaction-Id": "ab"})

    monkeypatch.setattr(history.urllib.request, "urlopen", fake_urlopen)
    page = history.fetch_page(MINE)
    assert page.next_cursor == (123, "ab")
    assert "resolve_previous_outpoints=no" in seen["url"] and "limit=500" in seen["url"]
    assert seen["url"].startswith("https://api.explorer-eu.hoosat.network/addresses/hoosat:")


def test_requests_name_the_app_and_its_version(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["agent"] = request.get_header("User-agent")
        seen["url"] = request.full_url
        return FakeResponse([], {})

    monkeypatch.setattr(history.urllib.request, "urlopen", fake_urlopen)
    history.fetch_page(MINE, base=history.api_base("NZ"), limit=50)
    from guiwallet import __version__

    assert seen["agent"] == f"HTN-GUI-Wallet/{__version__}"
    assert seen["url"].startswith("https://api.explorer-nz.hoosat.network/") and "limit=50" in seen["url"]
    assert history.api_base("something else") == history.API_BASES["EU"]


@pytest.mark.parametrize("code", [429, 503])
def test_busy_answers_say_how_long_to_wait(monkeypatch, code):
    def fake_urlopen(request, timeout):
        raise history.urllib.error.HTTPError(request.full_url, code, "Busy", {"Retry-After": "120"}, io.BytesIO(b""))

    monkeypatch.setattr(history.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(HistoryError) as caught:
        history.fetch_page(MINE)
    assert caught.value.busy and caught.value.retry_after == 120


def test_fetch_page_failures_are_plain_messages(monkeypatch):
    def fail(request, timeout):
        raise history.urllib.error.URLError("no route")

    monkeypatch.setattr(history.urllib.request, "urlopen", fail)
    with pytest.raises(HistoryError, match="couldn't be reached"):
        history.fetch_page(MINE)


# ----- Kept on this computer -----


def test_cache_round_trip(tmp_path):
    cache = Cache(MINE, complete=False, cursor=(5, "x"), newest_time=9, updated_at=1.5, pages_read=3, capped_at=7)
    take_page(cache, Page([payment(1, 1)], None), NOW_MS)
    path = cache_path(tmp_path, "mainnet", MINE)
    save_cache(path, cache)
    loaded = load_cache(path, MINE)
    assert loaded.entries == cache.entries and loaded.cursor == (5, "x") and loaded.newest_time == NOW_MS - 60_000
    assert (loaded.pages_read, loaded.capped_at) == (3, 7)


def test_a_damaged_or_foreign_cache_starts_again(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_cache(path, MINE).entries == {}
    save_cache(path, Cache(OTHER_MINE))
    assert load_cache(path, MINE).address == MINE and not load_cache(path, MINE).complete


def test_hidden_ids_older_than_30_days_are_forgotten(tmp_path):
    save_hidden_ids(tmp_path, {"new": time.time(), "old": time.time() - 8 * 86_400})
    assert set(load_hidden_ids(tmp_path)) == {"new"}


# ----- The page -----


def rows(count, kind=Kind.RECEIVED):
    now = int(datetime.now().timestamp() * 1000)
    return [history.Row(f"t{i}", now - i * 60_000, kind, Decimal("1.5"), STRANGER, True) for i in range(count)]


def test_amounts_have_two_decimals_and_a_sign():
    assert format_amount(Decimal("-1.5"), Kind.SENT) == "−1.50"
    assert format_amount(Decimal("1000"), Kind.RECEIVED) == "+1,000.00"


def test_dates_say_today_or_the_day():
    now = datetime(2026, 11, 7, 21, 0)
    assert format_date(int(datetime(2026, 11, 7, 9, 12).timestamp() * 1000), now) == "Today, 09:12"
    assert format_date(int(datetime(2026, 10, 12, 8, 30).timestamp() * 1000), now) == "12 Oct, 08:30"


def test_filters_by_type_and_period():
    mixed = rows(2) + rows(1, Kind.SENT) + rows(1, Kind.INTERNAL)
    assert len(filter_rows(mixed, "All", "7 days")) == 4
    assert [row.kind for row in filter_rows(mixed, "Internal", "7 days")] == [Kind.INTERNAL]
    old = history.Row("o", int(datetime.now().timestamp() * 1000) - 8 * DAY_MS, Kind.SENT, Decimal(1), SHOP, True)
    assert filter_rows([old], "All", "7 days") == [] and filter_rows([old], "All", "Today") == []
    assert list(PERIODS) == ["Today", "7 days"]


def test_page_numbers():
    assert page_numbers(3, 1) == [1, 2, 3]
    assert page_numbers(8, 1) == [1, 2, 3, None, 8]
    assert page_numbers(8, 5) == [1, None, 5, None, 8]
    assert page_numbers(8, 8) == [1, 2, 3, None, 8]


def page_with(app, row_list):
    page = HistoryPage()
    balance = WalletBalance(Decimal(5), Decimal(0), (AddressBalance(MINE, Decimal(5), Decimal(0)),))
    page.set_addresses([MINE, OTHER_MINE], balance)
    page.show_rows(row_list, time.time())
    return page


def shown_amounts(page):
    return [w.text() for w in page.findChildren(QLabel) if w.text().startswith(("+", "−", HIDDEN_AMOUNT)) and w.isVisibleTo(page)]


def test_eight_rows_a_page_and_the_pager_moves(app):
    page = page_with(app, rows(20))
    assert len(page.visible_rows()) == 20 and not page.pager.isHidden()
    assert len([w for w in page.grid.parentWidget().findChildren(QLabel) if w.text() == "Received"]) == 8
    next_button = next(b for b in page.pager.findChildren(QPushButton) if b.text() == "›")
    next_button.click()
    assert page.page == 2
    page.kinds.buttons()[1].click()  # Sent: changing a filter goes back to page 1.
    assert page.page == 1


def test_no_pager_for_one_page(app):
    page = page_with(app, rows(3))
    assert page.pager.isHidden()


def test_all_is_the_default_filter_and_30_days_the_default_period(app):
    page = HistoryPage()
    assert page.kinds.checkedButton().text() == "All" and page.periods.checkedButton().text() == "7 days"
    assert [b.text() for b in page.kinds.buttons()] == ["All", "Sent", "Received", "Internal"]


def test_hidden_balances_hide_history_amounts(app):
    page = page_with(app, rows(2))
    page.set_balances_hidden(True)
    texts = [w.text() for w in page.grid.parentWidget().findChildren(QLabel)]
    assert HIDDEN_AMOUNT in texts and "+1.50" not in texts
    assert HIDDEN_AMOUNT in page.address.itemText(0)


def test_choosing_an_address_asks_for_its_history(app):
    page = page_with(app, [])
    asked = []
    page.address_changed.connect(asked.append)
    page.address.setCurrentIndex(1)
    assert asked == [OTHER_MINE]


def test_building_shows_in_the_footer_and_keeps_eight_rows(app):
    page = page_with(app, rows(20))
    page.show_building(5)
    assert page.rows_per_page() == 8 and page.building_text.text() == "Building… 5 of 7 days"
    assert not page.building.isHidden() and page.updated.isHidden()
    assert len([w for w in page.grid.parentWidget().findChildren(QLabel) if w.text() == "Received"]) == 8
    page.show_building(None)
    assert page.building.isHidden() and not page.updated.isHidden()


def test_a_capped_history_says_so_in_the_footer(app):
    page = page_with(app, rows(3))
    page.show_rows(rows(3), time.time(), span="2 days")
    assert page.updated.text().startswith("Showing the last 2 days  ·  Updated ")
    older = [w.text() for w in page.older.findChildren(QLabel)]
    assert any(text.startswith("Older history on the explorer:") for text in older)
    page.show_rows(rows(3), time.time())
    assert page.updated.text().startswith("Updated ")


def test_the_history_source_is_saved_and_checked(tmp_path):
    from guiwallet.core.settings import Settings, load_settings, save_settings

    path = tmp_path / "settings.json"
    assert load_settings(path).history_source == "EU"
    save_settings(Settings(history_source="NZ"), path)
    assert load_settings(path).history_source == "NZ"
    path.write_text(json.dumps({"history_source": "elsewhere"}), encoding="utf-8")
    assert load_settings(path).history_source == "EU"


def test_a_lower_search_limit_from_the_explorer_shrinks_the_batch(monkeypatch):
    sizes = []

    def fake_urlopen(request, timeout):
        ids = json.loads(request.data)["transactionIds"]
        sizes.append(len(ids))
        assert "resolve_previous_outpoints=light" in request.full_url and request.get_method() == "POST"
        if len(ids) > 20:
            body = io.BytesIO(b'{"detail":"Temporary issue: Transaction ids count is limited to 20 for light and full searches."}')
            raise history.urllib.error.HTTPError(request.full_url, 422, "Unprocessable", {}, body)
        return FakeResponse([], {})

    monkeypatch.setattr(history, "_search_size", 50)
    monkeypatch.setattr(history.urllib.request, "urlopen", fake_urlopen)
    history.search_transactions([f"{n:064x}" for n in range(50)])
    assert sizes == [50, 20, 20, 10] and history.search_size() == 20


# ----- Pages read without input addresses -----


def bare(transaction, spends=()):
    """A transaction as the explorer gives it with resolve_previous_outpoints=no: inputs name only the coin spent."""
    found = dict(transaction)
    found["inputs"] = [{"previous_outpoint_hash": h, "previous_outpoint_index": i} for h, i in spends]
    found["outputs"] = [dict(o, index=n) for n, o in enumerate(transaction["outputs"])]
    return found


def test_a_compound_of_known_rewards_is_worked_out_without_a_lookup():
    cache = Cache(MINE)
    rewards = [reward(n, 30 + n) for n in range(3)]
    compound = bare(tx("c1", 5, outputs=[(MINE, 39 * COIN)]), spends=[(r["transaction_id"], 0) for r in rewards])
    send = bare(tx("s1", 3, outputs=[(SHOP, 30 * COIN), (MINE, 8 * COIN)]), spends=[("c1", 0)])
    take_page(cache, Page([send, compound], None), NOW_MS)
    # The send spends the compound's coin, read on the same page; the compound's rewards aren't read yet.
    assert set(cache.unplaced) == {"c1"} and "s1" in cache.entries
    take_page(cache, Page(rewards, None), NOW_MS)
    assert cache.unplaced == {} and list(cache.entries) == ["s1"]
    row = to_row(cache.entries["s1"], {MINE})
    assert row.kind is Kind.SENT and row.amount == Decimal(-30) and row.other_address == SHOP


def test_a_payment_from_someone_else_is_looked_up(monkeypatch):
    cache = Cache(MINE)
    payment_in = tx("p1", 5, inputs=[(STRANGER, 60 * COIN)], outputs=[(MINE, 50 * COIN), (STRANGER, 9 * COIN)])
    take_page(cache, Page([bare(payment_in, spends=[("ab" * 32, 1)])], None), NOW_MS)
    assert set(cache.unplaced) == {"p1"}
    asked = []
    monkeypatch.setattr(history, "search_transactions", lambda ids, base=None: asked.append(ids) or [payment_in])
    assert history.look_up_unplaced(cache, None) == 1
    assert asked == [["p1"]] and cache.unplaced == {}
    row = to_row(cache.entries["p1"], {MINE})
    assert row.kind is Kind.RECEIVED and row.amount == Decimal(50) and row.other_address == STRANGER


def test_the_build_looks_up_what_is_left_once_it_ends(monkeypatch):
    payment_in = tx("p2", 5, inputs=[(STRANGER, COIN)], outputs=[(MINE, COIN)])
    fake_pages(monkeypatch, [Page([bare(payment_in, spends=[("cd" * 32, 0)])], None)])
    monkeypatch.setattr(history, "search_transactions", lambda ids, base=None: [payment_in])
    cache = Cache(MINE)
    build_step(cache, NOW_MS)
    assert cache.complete and "p2" in cache.entries and cache.unplaced == {}


def test_an_update_stops_at_a_reward_it_has_already_seen(monkeypatch):
    cache = Cache(MINE, complete=True)
    take_page(cache, Page([reward(1, 10)], None), NOW_MS)
    asked = fake_pages(monkeypatch, [Page([reward(2, 1), reward(1, 10)], (1, "r1")), Page([reward(3, 20)], None)])
    top_up(cache, NOW_MS)
    assert len(asked) == 1


def test_the_cache_keeps_unplaced_and_paid_coins(tmp_path):
    cache = Cache(MINE)
    take_page(cache, Page([bare(tx("u1", 5, outputs=[(MINE, COIN)]), spends=[("ef" * 32, 2)]), reward(4, 9)], None), NOW_MS)
    path = cache_path(tmp_path, "mainnet", MINE)
    save_cache(path, cache)
    loaded = load_cache(path, MINE)
    assert loaded.unplaced == cache.unplaced and loaded.own_outputs == cache.own_outputs


def test_a_cache_from_the_previous_version_is_rebuilt(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"version": 1, "address": MINE, "complete": True, "entries": []}), encoding="utf-8")
    assert not load_cache(path, MINE).complete


def test_another_422_is_an_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise history.urllib.error.HTTPError(request.full_url, 422, "Unprocessable", {}, io.BytesIO(b'{"detail":"bad"}'))

    monkeypatch.setattr(history.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(HistoryError, match="422"):
        history.fetch_page(MINE)
