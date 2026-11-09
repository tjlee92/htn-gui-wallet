"""Lists htnwallet processes: id, start time, which program and which subcommand. Never other arguments,
because they can hold a password."""

import datetime
import psutil

for p in psutil.process_iter(["pid", "name", "exe", "cmdline", "create_time", "ppid"]):
    if (p.info["name"] or "").lower() != "htnwallet.exe":
        continue
    words = p.info["cmdline"] or []
    started = datetime.datetime.fromtimestamp(p.info["create_time"]).strftime("%H:%M:%S")
    try:
        parent = psutil.Process(p.info["ppid"]).name()
    except psutil.Error:
        parent = "?"
    print(p.info["pid"], started, p.info["exe"], words[1] if len(words) > 1 else "", "parent:", parent)
print("now", datetime.datetime.now().strftime("%H:%M:%S"))
for p in psutil.process_iter(["pid", "name", "exe"]):
    if "guiwallet" in (p.info["name"] or "").lower():
        print("gui:", p.info["pid"], p.info["exe"])
