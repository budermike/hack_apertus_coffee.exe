"""Tests for the server-side summary generation queue (#68)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hack_apertus_coffee_exe.summary.queue import SummaryQueue


def test_queue_serialises_jobs_tracks_priority_and_cancels():
    started = []
    gate = threading.Event()

    class FakeBackend:
        def __init__(self):
            self.stored = {"8C_3_2026"}

        def get_summary(self, key):
            if key in self.stored:
                return 200, {"id": key}
            return 404, {"error": "no summary stored"}

        def create_summary(self, key):
            started.append(key)
            if key == "8C_1_2026":
                gate.wait(timeout=2)
            if key == "8C_BAD_2026":
                return 404, {"error": "judgement not found"}
            self.stored.add(key)
            return 200, {"id": key}

    q = SummaryQueue(FakeBackend())
    try:
        snap = q.enqueue(["8C_1/2026", "8C_2/2026", "8C_3/2026", "8C_4/2026", "8C_BAD/2026"])
        assert "8C_3_2026" in snap["done"]  # already stored -> never queued

        # Promote 8C_4_2026 ahead of 8C_2_2026 while 8C_1_2026 is running, and cancel 8C_2_2026
        time.sleep(0.02)
        q.enqueue(["8C_4/2026"], priority=True)
        assert q.cancel("8C_2_2026") is True
        gate.set()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            cur = q.status(["8C_1_2026", "8C_3_2026", "8C_4_2026", "8C_BAD_2026"])
            if not cur["running"] and not cur["queued"]:
                break
            time.sleep(0.02)

        assert started == ["8C_1_2026", "8C_4_2026", "8C_BAD_2026"]
        assert set(cur["done"]) == {"8C_1_2026", "8C_3_2026", "8C_4_2026"}
        assert cur["failed"] == {"8C_BAD_2026": "judgement not found"}
    finally:
        q.stop()


def test_queue_rejects_invalid_case_ids():
    q = SummaryQueue(SimpleNamespace(get_summary=Mock(return_value=(404, {})), create_summary=Mock()))
    with pytest.raises(ValueError, match="non-empty"):
        q.enqueue(["  "])
