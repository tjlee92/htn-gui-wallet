"""Runs slow work off the main thread, so the window never freezes while a command runs."""

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot


class _MainThreadCaller(QObject):
    """Calls a function on the main thread, the only thread allowed to touch the window.

    It stays alive for the whole run of the app. A carrier that ended with its background
    task would be destroyed before its result was delivered, and the result would be lost.
    """

    call = Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object, object)
    def _run(self, function: Callable[[Any], None], value: Any) -> None:
        function(value)


_caller: _MainThreadCaller | None = None


class _Task(QRunnable):
    def __init__(
        self,
        caller: _MainThreadCaller,
        work: Callable[[], Any],
        on_finished: Callable[[Any], None],
        on_failed: Callable[[Exception], None],
    ) -> None:
        super().__init__()
        self.caller = caller
        self.work = work
        self.on_finished = on_finished
        self.on_failed = on_failed

    def run(self) -> None:
        try:
            result = self.work()
        except Exception as error:  # Reported to the screen that asked for the work.
            self.caller.call.emit(self.on_failed, error)
        else:
            self.caller.call.emit(self.on_finished, result)


def run_in_background(
    work: Callable[[], Any],
    on_finished: Callable[[Any], None],
    on_failed: Callable[[Exception], None],
) -> None:
    """Run `work` on another thread, then call one of the callbacks on the main thread.

    Call this from the main thread.
    """
    global _caller
    if _caller is None:
        _caller = _MainThreadCaller()
    QThreadPool.globalInstance().start(_Task(_caller, work, on_finished, on_failed))
