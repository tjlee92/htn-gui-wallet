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
OUT = SCRATCH / "history-shots"
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


def tx(tx_id, minutes_ago, inputs=(), outputs=(), accepted=True, coinbase=False):
    return {
        "transaction_id": tx_id,
        "block_time": NOW - minutes_ago * MINUTE,
        "is_accepted": accepted,
        "subnetwork_id": "0100000000000000000000000000000000000000" if coinbase else "0" * 40,
        "inputs": [{"previous_outpoint_address": a, "previous_outpoint_amount": v} for a, v in inputs],
        "outputs": [{"script_public_key_address": a, "amount": v} for a, v in outputs],
    }


def chain_for_one():
    """35 days of a mining address: a reward every 30 minutes, compounds, and 20 real transactions."""
    found = []
    for minute in range(0, 35 * 24 * 60, 30):
        found.append(tx(f"reward{minute:07d}".ljust(64, "0"), minute, outputs=[(ONE, 13 * COIN)], coinbase=True))
    for minute in range(15, 35 * 24 * 60, 600):
        found.append(tx(f"compound{minute:07d}".ljust(64, "0"), minute, inputs=[(ONE, 50 * COIN)] * 3, outputs=[(ONE, 150 * COIN)]))
    for n in range(20):
        minute = 7 + n * 1900
        kind = n % 4
        if kind == 0:
            item = tx(f"pay{n:02d}".ljust(64, "a"), minute, inputs=[(STRANGER, 300 * COIN)], outputs=[(ONE, (250 + n) * COIN), (STRANGER, 40 * COIN)])
        elif kind == 1:
            item = tx(f"send{n:02d}".ljust(64, "b"), minute, inputs=[(ONE, 100 * COIN)], outputs=[(SHOP, (40 + n) * COIN // 4), (ONE, 50 * COIN)])
        elif kind == 2:
            item = tx(f"move{n:02d}".ljust(64, "c"), minute, inputs=[(ONE, 30 * COIN)], outputs=[(TWO, 15 * COIN), (ONE, 14 * COIN)])
        else:
            item = tx(f"in{n:02d}".ljust(64, "d"), minute, inputs=[(TWO, 130 * COIN)], outputs=[(ONE, 120 * COIN), (TWO, 9 * COIN)])
        found.append(item)
    found.append(tx("pending".ljust(64, "e"), 1, inputs=[(ONE, 5 * COIN)], outputs=[(SHOP, 1.5 * COIN), (ONE, 3 * COIN)], accepted=False))
    found.sort(key=lambda t: (t["block_time"], t["transaction_id"]), reverse=True)
    return found


CHAINS = {ONE: chain_for_one(), TWO: []}
requests_seen = []


class Explorer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        address = urllib.parse.unquote(parsed.path.split("/")[2])
        query = dict(urllib.parse.parse_qsl(parsed.query))
        requests_seen.append((address, query.get("before_block_time")))
        chain = CHAINS.get(address, [])
        if "before_block_time" in query:
            cut = (int(query["before_block_time"]), query.get("before_transaction_id", ""))
            chain = [t for t in chain if (t["block_time"], t["transaction_id"]) < cut]
        limit = int(query.get("limit", 50))
        page = chain[:limit]
        time.sleep(0.15)  # A little like a real server.
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if len(page) == limit:
            self.send_header("X-Next-Before-Block-Time", str(page[-1]["block_time"]))
            self.send_header("X-Next-Before-Transaction-Id", page[-1]["transaction_id"])
        self.end_headers()
        self.wfile.write(json.dumps(page).encode())


server = ThreadingHTTPServer(("127.0.0.1", EXPLORER_PORT), Explorer)
threading.Thread(target=server.serve_forever, daemon=True).start()
history.API_BASE = f"http://127.0.0.1:{EXPLORER_PORT}"

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
    results["building shows"] = wait(lambda: page.building.isVisible() and page.progress.value() > 0, 10)
    window.grab().save(str(OUT / "1-building.png"))
    results["size while building"] = (window.width(), window.height()) == (1000, 698)
    results["build finishes"] = wait(lambda: window.history_cache and window.history_cache.complete, 60)
    wait(lambda: False, 0.5)
    results["panel gone"] = not page.building.isVisible()
    visible = page.visible_rows()
    kinds = {row.kind.value for row in visible}
    results["no rewards or compounds"] = all(not row.transaction_id.startswith(("reward", "compound")) for row in visible)
    results["three kinds"] = kinds == {"Sent", "Received", "Internal"}
    results["30 days only"] = all(row.block_time >= NOW - 30 * 86_400_000 - 60_000 for row in visible)
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
    next(b for b in page.periods.buttons() if b.text() == "30 days").click()

    # A top-up reads only what's new: one request.
    before = len(requests_seen)
    window.history_cache.updated_at = 0
    window._history_refresh_if_shown()
    wait(lambda: not window.history_busy, 10)
    results["top-up is one request"] = len(requests_seen) - before == 1

    # Another address with nothing in it.
    page.address.setCurrentIndex(1)
    results["empty address"] = wait(lambda: window.history_cache.address == TWO and window.history_cache.complete, 10)
    wait(lambda: False, 0.3)
    window.grab().save(str(OUT / "6-empty.png"))

    # Back to the first: it comes from the cache, without rebuilding.
    before = len(requests_seen)
    page.address.setCurrentIndex(0)
    wait(lambda: not window.history_busy, 10)
    results["cached, not rebuilt"] = len(requests_seen) - before <= 1 and len(page.visible_rows()) == len(visible)
    print("explorer pages read for the first build:", pages_read)
    window.really_exit = True
    window.hide()
finally:
    daemon.kill()
    daemon.wait()
    server.shutdown()

print(results)
print("ALL PASSED" if all(results.values()) else "SOMETHING FAILED")
