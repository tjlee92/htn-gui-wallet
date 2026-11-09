"""Drives the real Transaction History page against a fake explorer on this PC and the fake htnwallet.
Settings and caches go to a scratch folder, never the user's own. No real explorer or address is used."""

import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRATCH = Path(__file__).parent
os.environ["APPDATA"] = str(SCRATCH / "history-appdata")  # Before the app works out its settings folder.

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from guiwallet.core import history  # noqa: E402
from guiwallet.core.commands import wallet_context  # noqa: E402
from guiwallet.core.daemon import port_is_open, start_daemon  # noqa: E402
from guiwallet.core.settings import Settings, settings_path  # noqa: E402
from guiwallet.ui.main_window import MainWindow, State  # noqa: E402
from guiwallet.ui.theme import app_icon, apply_theme  # noqa: E402

FAKE = SCRATCH / "fakewallet" / "bin"
OUT = SCRATCH / "owner-shots"
OUT.mkdir(exist_ok=True)
PORT = 47947
EXPLORER_PORT = 47948
ONE = "hoosat:qq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6"
TWO = "hoosat:qr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s"
STRANGER = "hoosat:qrx9ty4nk8ms6rj1ue0hd5pqz3xw9lc5vb2ga7ty4nk8ms6rj1ue0hd5pqz3xw9lc5v"
SHOP = "hoosat:qp4m8ta2hw6xk3ly9rd5ce0s5ayr2zz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qza"
COIN = 100_000_000
NOW = int(time.time() * 1000)
MINUTE = 60_000


def tx(tx_id, minutes_ago, inputs=(), outputs=(), accepted=True, coinbase=False, spends=None):
    # spends: the coins each input spends (transaction ID, index). Unknown coins get made-up IDs.
    SPENDS[tx_id] = spends or [(f"{abs(hash((tx_id, n))):064x}"[-64:], 0) for n in range(len(inputs))]
    return {
        "transaction_id": tx_id,
        "block_time": NOW - minutes_ago * MINUTE,
        "is_accepted": accepted,
        "subnetwork_id": "0100000000000000000000000000000000000000" if coinbase else "0" * 40,
        "inputs": [{"previous_outpoint_address": a, "previous_outpoint_amount": v} for a, v in inputs],
        "outputs": [{"script_public_key_address": a, "amount": v} for a, v in outputs],
    }


SPENDS = {}


def reward_id(minute):
    return f"reward{minute:07d}".ljust(64, "0")


def chain_for_one():
    """35 days of a mining address: a reward every 5 minutes, compounds of the three rewards before them, and
    20 real transactions. 10 pages of 500 then reach about 17 days, so the cap shows."""
    found = []
    for minute in range(0, 35 * 24 * 60, 5):
        found.append(tx(reward_id(minute), minute, outputs=[(ONE, 13 * COIN)], coinbase=True))
    for minute in range(15, 35 * 24 * 60 - 30, 600):
        spends = [(reward_id(minute + 5 * k), 0) for k in (1, 2, 3)]
        found.append(tx(f"compound{minute:07d}".ljust(64, "0"), minute, inputs=[(ONE, 13 * COIN)] * 3, outputs=[(ONE, 39 * COIN)], spends=spends))
    for n in range(20):
        minute = 7 + n * 400
        kind = n % 4
        if kind == 0:
            item = tx(f"pay{n:02d}".ljust(64, "a"), minute, inputs=[(STRANGER, 300 * COIN)], outputs=[(ONE, (250 + n) * COIN), (STRANGER, 40 * COIN)])
        elif kind == 1:
            item = tx(f"send{n:02d}".ljust(64, "b"), minute, inputs=[(ONE, 13 * COIN)], outputs=[(SHOP, (40 + n) * COIN // 4), (ONE, 50 * COIN)], spends=[(reward_id(minute + 3), 0)])
        elif kind == 2:
            item = tx(f"move{n:02d}".ljust(64, "c"), minute, inputs=[(ONE, 13 * COIN)], outputs=[(TWO, 15 * COIN), (ONE, 14 * COIN)], spends=[(reward_id(minute + 3), 0)])
        else:
            item = tx(f"in{n:02d}".ljust(64, "d"), minute, inputs=[(TWO, 130 * COIN)], outputs=[(ONE, 120 * COIN), (TWO, 9 * COIN)])
        found.append(item)
    found.append(tx("pending".ljust(64, "e"), 1, inputs=[(ONE, 13 * COIN)], outputs=[(SHOP, 1.5 * COIN), (ONE, 3 * COIN)], accepted=False, spends=[(reward_id(5), 0)]))
    found.sort(key=lambda t: (t["block_time"], t["transaction_id"]), reverse=True)
    return found


CHAINS = {ONE: chain_for_one(), TWO: []}
requests_seen = []
searches = []
BY_ID = {t["transaction_id"]: t for t in CHAINS[ONE]}


def bare(t):
    """As the real explorer answers with resolve_previous_outpoints=no."""
    found = dict(t)
    found["inputs"] = [{"previous_outpoint_hash": h, "previous_outpoint_index": i} for h, i in SPENDS[t["transaction_id"]]]
    found["outputs"] = [dict(o, index=n) for n, o in enumerate(t["outputs"])]
    return found
stamps = []
agents = set()
busy_once = []


class Explorer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        address = urllib.parse.unquote(parsed.path.split("/")[2])
        query = dict(urllib.parse.parse_qsl(parsed.query))
        requests_seen.append((address, query.get("before_block_time")))
        stamps.append(time.time())
        agents.add(self.headers.get("User-Agent"))
        if busy_once:
            busy_once.pop()
            self.send_response(429)
            self.send_header("Retry-After", "90")
            self.end_headers()
            return
        chain = CHAINS.get(address, [])
        if "before_block_time" in query:
            cut = (int(query["before_block_time"]), query.get("before_transaction_id", ""))
            chain = [t for t in chain if (t["block_time"], t["transaction_id"]) < cut]
        light = query.get("resolve_previous_outpoints") == "light"
        limit = min(50 if light else 500, int(query.get("limit", 50)))  # Like the real explorer today.
        page = chain[:limit] if light else [bare(t) for t in chain[:limit]]
        time.sleep(0.15)  # A little like a real server.
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if len(page) == limit:
            self.send_header("X-Next-Before-Block-Time", str(page[-1]["block_time"]))
            self.send_header("X-Next-Before-Transaction-Id", page[-1]["transaction_id"])
        self.end_headers()
        self.wfile.write(json.dumps(page).encode())


def do_POST(self):
    stamps.append(time.time())
    agents.add(self.headers.get("User-Agent"))
    ids = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["transactionIds"]
    searches.append(len(ids))
    assert len(ids) <= 50 and "resolve_previous_outpoints=light" in self.path
    time.sleep(0.05)
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.end_headers()
    self.wfile.write(json.dumps([BY_ID[i] for i in ids if i in BY_ID]).encode())


Explorer.do_POST = do_POST
server = ThreadingHTTPServer(("127.0.0.1", EXPLORER_PORT), Explorer)
threading.Thread(target=server.serve_forever, daemon=True).start()
history.API_BASES["EU"] = f"http://127.0.0.1:{EXPLORER_PORT}"

app = QApplication(sys.argv)
apply_theme(app)
app.setWindowIcon(app_icon())
app.setQuitOnLastWindowClosed(False)


def wait(condition, seconds=15):
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return condition()


results = {}
options = Settings(tools_folder=str(FAKE), daemon_address=f"127.0.0.1:{PORT}")
print("settings folder:", settings_path().parent)
assert "history-appdata" in str(settings_path())
cache_folder = history.history_folder(settings_path())
for old in cache_folder.glob("*.json") if cache_folder.exists() else []:
    old.unlink()
daemon = start_daemon(FAKE / "htnwallet.exe", wallet_context(options), "localhost", SCRATCH / "shot_history.log")
try:
    wait(lambda: port_is_open(PORT), 10)
    window = MainWindow(options)
    window.show()
    results["running"] = wait(lambda: window.state is State.RUNNING and window.history_page.address.count() >= 2)
    window.banner.hide()
    nav = next(b for b in window.findChildren(QPushButton) if b.text() == "Transaction History")
    nav.click()
    page = window.history_page
    results["first address chosen"] = page.current_address() == ONE
    results["building shows"] = wait(lambda: page.building.isVisible() and page.progress.value() > 0, 20)
    window.grab().save(str(OUT / "1-building.png"))
    results["size while building"] = (window.width(), window.height()) == (1000, 698)
    results["build finishes"] = wait(lambda: window.history_cache and window.history_cache.complete, 60)
    results["all 7 days read, not capped"] = window.history_cache.capped_at is None and 3 <= len(requests_seen) <= 6
    print("pages for 7 days:", len(requests_seen))
    results["few lookups"] = 0 < len(searches) <= 2 and window.history_cache.unplaced == {}
    print("lookups:", searches)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    results["under 2 a second"] = min(gaps) >= 0.95
    print("gaps between requests:", [round(g, 2) for g in gaps])
    from guiwallet import __version__
    results["user agent"] = agents == {f"HTN-GUI-Wallet/{__version__}"}
    print("user agent:", agents)
    wait(lambda: False, 0.5)
    results["panel gone"] = not page.building.isVisible()
    visible = page.visible_rows()
    kinds = {row.kind.value for row in visible}
    results["no rewards or compounds"] = all(not row.transaction_id.startswith(("reward", "compound")) for row in visible)
    results["three kinds"] = kinds == {"Sent", "Received", "Internal"}
    results["7 days only"] = all(row.block_time >= NOW - 7 * 86_400_000 - 60_000 for row in visible)
    expected = {t["transaction_id"] for t in CHAINS[ONE] if t["block_time"] >= NOW - 7 * 86_400_000 and not t["transaction_id"].startswith(("reward", "compound"))}
    results["every payment of the 7 days found"] = expected == set(window.history_cache.entries)
    print("payments in 7 days:", len(expected), "found:", len(window.history_cache.entries))
    results["footer not capped"] = page.updated.text().startswith("Updated ")
    print("footer:", page.updated.text())
    results["pending first"] = visible[0].transaction_id.startswith("pending") and not visible[0].accepted
    results["pager shown"] = not page.pager.isHidden()
    window.grab().save(str(OUT / "2-all.png"))
    results["size after"] = (window.width(), window.height()) == (1000, 698)
    print("size:", window.width(), window.height())
    pages_read = len(requests_seen)

    next(b for b in page.pager.findChildren(QPushButton) if b.text() == "2").click()
    wait(lambda: False, 0.3)
    window.grab().save(str(OUT / "3-page2.png"))
    next(b for b in page.kinds.buttons() if b.text() == "Internal").click()
    wait(lambda: False, 0.3)
    results["internal only"] = {row.kind.value for row in page.visible_rows()} == {"Internal"} and page.page == 1
    window.grab().save(str(OUT / "4-internal.png"))
    next(b for b in page.kinds.buttons() if b.text() == "All").click()
    next(b for b in page.periods.buttons() if b.text() == "Today").click()
    wait(lambda: False, 0.3)
    window.grab().save(str(OUT / "5-today.png"))
    next(b for b in page.periods.buttons() if b.text() == "7 days").click()

    # Within a minute of the last read: no request. A minute later: one short request.
    before = len(requests_seen)
    window.history_last_request = 0
    window._history_refresh_if_shown()
    wait(lambda: not window.history_busy, 3)
    results["no request within a minute"] = len(requests_seen) == before
    window.history_cache.updated_at = 0
    window.history_last_request = 0
    window._history_refresh_if_shown()
    wait(lambda: not window.history_busy, 10)
    results["a minute later, one request"] = len(requests_seen) - before == 1
    # Leaving the page and coming back reads new ones at once, even inside the minute.
    before = len(requests_seen)
    next(b for b in window.findChildren(QPushButton) if b.text() == "Wallet").click()
    window.history_last_request = 0
    nav.click()
    wait(lambda: len(requests_seen) > before and not window.history_busy, 10)
    results["opening the page reads new ones"] = len(requests_seen) - before == 1
    # Refresh asks first; Cancel changes nothing; Refresh rebuilds from nothing.
    from PySide6.QtCore import QTimer
    from guiwallet.ui.history_page import RefreshHistoryDialog
    def answer(accept):
        def go():
            dialogs = [w for w in app.topLevelWidgets() if isinstance(w, RefreshHistoryDialog) and w.isVisible()]
            if dialogs:
                if accept:
                    dialogs[0].grab().save(str(OUT / "9-refresh-dialog.png"))
                    dialogs[0].refresh_button.click()
                else:
                    dialogs[0].cancel_button.click()
            else:
                QTimer.singleShot(50, go)
        QTimer.singleShot(100, go)
    before = len(requests_seen)
    answer(False)
    page.refresh.click()
    results["cancel keeps the cache"] = window.history_cache.complete and len(requests_seen) == before
    window.history_last_request = 0
    answer(True)
    page.refresh.click()
    results["refresh rebuilds"] = wait(lambda: window.history_cache.complete and len(requests_seen) - before >= 3, 40)
    window.history_last_request = 0
    window.grab().save(str(OUT / "10-after-refresh.png"))
    # A 429: it waits at least what the explorer asked, and says so.
    busy_once.append(True)
    window.history_cache.updated_at = 0
    window.history_last_request = 0
    window._history_refresh_if_shown()
    wait(lambda: not window.history_busy, 10)
    results["429 backs off"] = window.history_wait_until - time.time() >= 85 and "busy" in page.message.text()
    print("message:", page.message.text())
    window.grab().save(str(OUT / "7-busy.png"))
    before = len(requests_seen)
    window.history_last_request = 0
    window._history_refresh_if_shown()
    wait(lambda: False, 0.5)
    results["no retry while waiting"] = len(requests_seen) == before
    window.history_wait_until = 0
    window.history_failures = 0

    # Another address with nothing in it.
    window.history_page.show_message("")
    window.history_last_request = 0
    page.address.setCurrentIndex(1)
    results["empty address"] = wait(lambda: window.history_cache.address == TWO and window.history_cache.complete, 10)
    wait(lambda: False, 0.3)
    window.grab().save(str(OUT / "6-empty.png"))

    # Back to the first: it comes from the cache, without rebuilding.
    before = len(requests_seen)
    window.history_last_request = 0
    page.address.setCurrentIndex(0)
    wait(lambda: not window.history_busy, 10)
    results["cached, not rebuilt"] = len(requests_seen) - before <= 1 and len(page.visible_rows()) == len(visible)
    print("explorer pages read for the first build:", pages_read)
    settings_nav = next(b for b in window.findChildren(QPushButton) if b.text() == "Settings")
    settings_nav.click()
    wait(lambda: False, 0.3)
    window.grab().save(str(OUT / "8-settings.png"))
    results["size on settings"] = (window.width(), window.height()) == (1000, 698)
    window.really_exit = True
    window.hide()
finally:
    daemon.kill()
    daemon.wait()
    server.shutdown()

print(results)
print("ALL PASSED" if all(results.values()) else "SOMETHING FAILED")
