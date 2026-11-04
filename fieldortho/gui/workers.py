"""Background threads so the window never freezes."""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QThread, Signal


class Task(QThread):
    """Runs fn(task) in a thread. fn may call task.report(pct, text) and task.log(line)."""
    progress = Signal(float, str)
    line = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[["Task"], object], parent=None):
        super().__init__(parent)
        self.fn = fn
        self._cancel = False
        self.on_cancel: Callable[[], None] | None = None

    def report(self, pct: float, text: str = ""):
        self.progress.emit(float(pct), text)

    def log(self, text: str):
        self.line.emit(text)

    def cancel(self):
        self._cancel = True
        if self.on_cancel:
            self.on_cancel()

    @property
    def cancelled(self) -> bool:
        return self._cancel

    def run(self):
        try:
            self.done.emit(self.fn(self))
        except Exception as e:  # surfaced to the user in plain words
            self.line.emit(traceback.format_exc())
            self.failed.emit(str(e))
