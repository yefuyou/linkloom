"""Bounded ownership of background work that must finish before resource close."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
from threading import Event, Lock, Thread, current_thread
from time import monotonic
from typing import Callable


DEFAULT_SHUTDOWN_TIMEOUT = 10.0


class BackgroundWorkStopped(RuntimeError):
    """The owner has stopped accepting new work."""


class BackgroundShutdownTimeout(RuntimeError):
    def __init__(self, task_ids):
        self.task_ids = tuple(sorted(set(task_ids)))
        super().__init__("Background shutdown timed out; unfinished tasks: " + ", ".join(self.task_ids))


def shutdown_deadline(timeout=DEFAULT_SHUTDOWN_TIMEOUT):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError("Shutdown timeout must be finite and non-negative.")
    return monotonic() + timeout


@contextmanager
def shutdown_lock(lock, deadline, task_id):
    if not lock.acquire(timeout=max(0.0, deadline - monotonic())):
        raise BackgroundShutdownTimeout((task_id,))
    try:
        yield
    finally:
        lock.release()


@dataclass
class _Task:
    done: Event
    thread: Thread | None = None


class BackgroundTasks:
    """Admission and thread registration share one lock; waiting never holds it."""
    def __init__(self):
        self._lock = Lock()
        self._accepting = True
        self._tasks: dict[str, _Task] = {}

    def _prune_finished(self):
        # A completion Event can be set before the thread has actually returned.
        finished = [task_id for task_id, task in self._tasks.items()
                    if task.done.is_set() and (task.thread is None or not task.thread.is_alive())]
        for task_id in finished:
            self._tasks.pop(task_id)

    def stop_accepting(self):
        with self._lock:
            self._accepting = False

    @contextmanager
    def operation(self, task_id):
        task = _Task(Event())
        with self._lock:
            self._prune_finished()
            if task_id in self._tasks:
                raise ValueError("A background task with this ID is already active: " + task_id)
            if not self._accepting:
                raise BackgroundWorkStopped("Background owner is shutting down; new work is rejected.")
            self._tasks[task_id] = task
        try:
            yield
        finally:
            task.done.set()
            with self._lock:
                if self._tasks.get(task_id) is task:
                    self._tasks.pop(task_id, None)

    def start(self, task_id: str, target: Callable[[], None]):
        task = _Task(Event())
        def execute():
            try:
                target()
            finally:
                task.done.set()
        task.thread = Thread(target=execute, name=task_id, daemon=True)
        with self._lock:
            self._prune_finished()
            if task_id in self._tasks:
                raise ValueError("A background task with this ID is already active: " + task_id)
            if not self._accepting:
                raise BackgroundWorkStopped("Background owner is shutting down; new work is rejected.")
            self._tasks[task_id] = task
            try:
                # Register and start atomically so shutdown never joins an unstarted thread.
                task.thread.start()
            except BaseException:
                task.done.set()
                self._tasks.pop(task_id, None)
                raise
        return task.thread

    def pending_ids(self):
        with self._lock:
            self._prune_finished()
            return tuple(task_id for task_id, task in self._tasks.items()
                         if not task.done.is_set() or (task.thread is not None and task.thread.is_alive()))

    def wait(self, deadline):
        with self._lock:
            self._prune_finished()
            tasks = tuple(self._tasks.items())
        for task_id, task in tasks:
            remaining = max(0.0, deadline - monotonic())
            if task.thread is current_thread():
                raise BackgroundShutdownTimeout(self.pending_ids())
            if task.thread is not None:
                task.thread.join(remaining)
            else:
                task.done.wait(remaining)
        pending = self.pending_ids()
        if pending:
            raise BackgroundShutdownTimeout(pending)
