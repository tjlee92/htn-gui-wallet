"""Only one copy of the app runs at a time.

Two copies would share the send journal, settings, labels and history files, and could confuse or
overwrite each other's. A second copy instead asks the first to show its window, then exits.

A lock file holds the claim. Qt notices a lock left behind by a copy that crashed (its process is
gone) and takes it over. The request to show the window is a small file the first copy watches.
"""

import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QLockFile, QObject

LOCK_NAME = "instance.lock"
SHOW_REQUEST_NAME = "show-window.request"


class SingleInstance(QObject):
    def __init__(self, folder: Path) -> None:
        super().__init__()
        folder.mkdir(parents=True, exist_ok=True)
        self.request = folder / SHOW_REQUEST_NAME
        self.lock = QLockFile(str(folder / LOCK_NAME))
        self.lock.setStaleLockTime(0)  # Judge a leftover lock only by whether its process still runs.
        self.watcher: QFileSystemWatcher | None = None

    def claim(self) -> bool:
        """True if this is the only copy. Otherwise asks the running copy to show itself."""
        if self.lock.tryLock(200):
            return True
        try:
            self.request.write_text(str(time.time()), encoding="utf-8")
        except OSError:
            pass  # At worst the running copy stays where it is.
        return False

    def on_show_requested(self, show: Callable[[], None]) -> None:
        """Call `show` whenever another copy is started."""
        try:
            self.request.write_text("", encoding="utf-8")  # It must exist to be watched.
        except OSError:
            return
        self.watcher = QFileSystemWatcher([str(self.request)], self)

        def changed(path: str) -> None:
            if path not in self.watcher.files():
                self.watcher.addPath(path)  # Some systems stop watching a file once it's rewritten.
            show()

        self.watcher.fileChanged.connect(changed)

    def release(self) -> None:
        self.lock.unlock()
