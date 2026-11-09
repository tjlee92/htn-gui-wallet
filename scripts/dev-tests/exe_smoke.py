"""Opens a private copy of the built exe beside the fake htnwallet, with its own settings folder and
private port, so it can't find or reach the real wallet. It never presses anything, so it never sends.
Reports which commands ran, then closes only the copy it opened."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

SCRATCH = Path(__file__).parent
FAKE = SCRATCH / "fakewallet" / "bin"
FOLDER = SCRATCH / "exe-smoke"
PORT = 47951

shutil.rmtree(FOLDER, ignore_errors=True)
(FOLDER / "appdata" / "guiwallet").mkdir(parents=True)
shutil.copy(sys.argv[1], FOLDER / "guiwallet-sendtest.exe")
for name in ("htnwallet.exe", "htnctl.exe"):
    shutil.copy(FAKE / name, FOLDER / name)
(FOLDER / "appdata" / "guiwallet" / "settings.json").write_text(
    json.dumps({"daemon_address": f"127.0.0.1:{PORT}", "close_action": "exit"}), encoding="utf-8"
)
env = dict(os.environ, APPDATA=str(FOLDER / "appdata"))
log = FOLDER / "commands.log"

daemon = subprocess.Popen(
    [str(FOLDER / "htnwallet.exe"), "start-daemon", f"--listen=127.0.0.1:{PORT}", "--rpcserver=localhost"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
time.sleep(1)
process = subprocess.Popen([str(FOLDER / "guiwallet-sendtest.exe")], env=env)
launcher = psutil.Process(process.pid)
try:
    time.sleep(14)
    print("still running after 14s:", process.poll() is None)
    commands = log.read_text().split() if log.exists() else []
    print("commands run:", sorted(set(commands)))
    print("send ran:", "send" in commands)
finally:
    for child in launcher.children(recursive=True):
        child.kill()
    process.kill()
    process.wait()
    daemon.kill()
    daemon.wait()
files = sorted(p.name for p in (FOLDER / "appdata" / "guiwallet").iterdir())
print("files in its settings folder:", files)
