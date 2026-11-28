"""Server-side background queue for generating DE/FR summaries (#68)."""

from __future__ import annotations

from collections import deque
import logging
import threading
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def _ui_key(case_id: str) -> str:
    return case_id.replace("/", "_").strip()


class SummaryQueue:
    """Serialises on-demand summary generation in one daemon worker thread (#68)."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._pending: deque[str] = deque()
        self._running: str | None = None
        self._done: set[str] = set()
        self._failed: dict[str, str] = {}
        self._stop = False
        self._worker: threading.Thread | None = None

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._stop = False
            self._worker = threading.Thread(target=self._run, name="summary-queue", daemon=True)
            self._worker.start()

    def _is_stored(self, key: str) -> bool:
        try:
            status, _ = self.backend.get_summary(key)
            return status == 200
        except Exception:
            return False

    def enqueue(self, case_ids: Iterable[str], *, priority: bool = False) -> dict[str, Any]:
        """Queue ``case_ids`` for background generation; stored summaries go straight to ``done``."""
        keys: list[str] = []
        seen: set[str] = set()
        for raw in case_ids:
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("caseIds must contain non-empty strings")
            key = _ui_key(raw)
            if key not in seen:
                seen.add(key)
                keys.append(key)

        already_done: set[str] = set()
        for key in keys:
            if self._is_stored(key):
                already_done.add(key)

        with self._cond:
            for key in already_done:
                self._failed.pop(key, None)
            self._done.update(already_done)
            to_add = [k for k in keys if k not in already_done]
            if priority:
                to_add = list(reversed(to_add))
            for key in to_add:
                if key == self._running:
                    continue
                self._failed.pop(key, None)
                if priority:
                    if key in self._pending:
                        self._pending.remove(key)
                    self._pending.appendleft(key)
                elif key not in self._pending:
                    self._pending.append(key)
            if self._pending:
                self._ensure_worker()
                self._cond.notify_all()
        return self.status(keys)

    def cancel(self, case_id: str) -> bool:
        """Remove a pending case from the queue (cannot abort an active LLM call)."""
        key = _ui_key(case_id)
        with self._lock:
            if key in self._pending:
                self._pending.remove(key)
                return True
            return False

    def status(self, case_ids: Iterable[str] | None = None) -> dict[str, Any]:
        """Snapshot of running, queued, done and failed cases (optionally filtered to ``case_ids``)."""
        scope: list[str] | None = None
        if case_ids is not None:
            scope = []
            seen: set[str] = set()
            for raw in case_ids:
                if not isinstance(raw, str) or not raw.strip():
                    raise ValueError("ids must contain non-empty strings")
                key = _ui_key(raw)
                if key not in seen:
                    seen.add(key)
                    scope.append(key)

        with self._lock:
            running = [self._running] if self._running else []
            queued = list(self._pending)
            done = set(self._done)
            failed = dict(self._failed)

        if scope is None:
            return {
                "running": running,
                "queued": queued,
                "done": sorted(done),
                "failed": failed,
                "total": len(done) + len(failed) + len(queued) + len(running),
            }

        scope_set = set(scope)
        for key in scope:
            if key not in done and key not in running and key not in queued and self._is_stored(key):
                done.add(key)
                failed.pop(key, None)
                with self._lock:
                    self._failed.pop(key, None)
                    self._done.add(key)
        return {
            "running": [k for k in running if k in scope_set],
            "queued": [k for k in queued if k in scope_set],
            "done": [k for k in scope if k in done],
            "failed": {k: failed[k] for k in scope if k in failed},
            "total": len(scope),
        }

    def stop(self, timeout: float = 2.0) -> None:
        with self._cond:
            self._stop = True
            worker = self._worker
            self._cond.notify_all()
        if worker is not None:
            worker.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._stop and not self._pending:
                    self._cond.wait(timeout=0.5)
                if self._stop:
                    return
                key = self._pending.popleft()
                self._running = key
            try:
                http_status, payload = self.backend.create_summary(key)
                with self._lock:
                    if http_status == 200:
                        self._failed.pop(key, None)
                        self._done.add(key)
                    else:
                        msg = payload.get("error") if isinstance(payload, dict) else f"status {http_status}"
                        self._failed[key] = str(msg)
            except Exception as exc:
                logger.exception("Queued summary failed for %s", key)
                with self._lock:
                    self._failed[key] = str(exc)
            finally:
                with self._cond:
                    self._running = None
