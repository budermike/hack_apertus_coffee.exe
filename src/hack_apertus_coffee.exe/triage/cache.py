"""Triage cache: a mirror of the crawler's data index for embeddings and Apertus decisions.

Layout (mirrors ``data/raw/<YYYY-MM>/<date>_<case>.json`` + ``data/raw/_index.json``)::

    data/processed/triage/_index.json                      one entry per judgement
    data/processed/triage/2026-05/2026-05-15_8C_123-2026.npy   chunk vectors
    data/processed/triage/2026-05/2026-05-15_8C_123-2026.json  Apertus decision

Rules:
- chunk vectors are reused while the judgement's text SHA-256 (same hash the
  crawler stores as ``fetch.text_sha256``) and the embedder fingerprint match;
- an Apertus decision is reused while its request key matches (a hash of model,
  temperature, max_tokens and the full prompt, which contains the text excerpt
  and the similar cases), so a new model, prompt or text triggers a new call;
- a changed text invalidates both.

The cache holds derived data only and is safe to delete at any time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

INDEX_NAME = "_index.json"
INDEX_VERSION = 1


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def entry_key(case_id: str, decision_date: str | None) -> str:
    """``2026-05-15_8C_123-2026``, the crawler's file-name convention."""
    safe_id = case_id.replace("/", "-")
    return f"{decision_date}_{safe_id}" if decision_date else f"undated_{safe_id}"


@dataclass
class CacheStats:
    embedding_hits: int = 0
    embedding_misses: int = 0
    decision_hits: int = 0
    decision_misses: int = 0

    def summary(self) -> str:
        return (
            f"embeddings {self.embedding_hits} cached / {self.embedding_misses} computed, "
            f"Apertus {self.decision_hits} cached / {self.decision_misses} called"
        )


class TriageCache:
    """Disk cache for chunk vectors and Apertus decisions, keyed like the crawl index."""

    def __init__(self, root: str | Path = "data/processed/triage") -> None:
        self.root = Path(root)
        self.index_path = self.root / INDEX_NAME
        self.stats = CacheStats()
        self._index = self._load_index()

    # ------------------------------------------------------------------ index

    def _load_index(self) -> dict[str, Any]:
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if data.get("version") == INDEX_VERSION and isinstance(data.get("items"), dict):
                    return data
                logger.warning("Ignoring triage cache index with unknown version: %s", self.index_path)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Ignoring unreadable triage cache index %s: %s", self.index_path, exc)
        return {"version": INDEX_VERSION, "items": {}}

    def save(self) -> None:
        payload = json.dumps(self._index, ensure_ascii=False, indent=2, sort_keys=True)
        _write_atomic(self.index_path, payload.encode("utf-8"))

    @property
    def items(self) -> dict[str, dict[str, Any]]:
        return self._index["items"]

    def _entry(self, case_id: str, decision_date: str | None, text_sha256: str) -> dict[str, Any]:
        """Get or create the index entry; a changed text drops cached vectors and decision."""
        key = entry_key(case_id, decision_date)
        entry = self.items.get(key)
        if entry is None or entry.get("text_sha256") != text_sha256:
            if entry is not None:
                logger.info("Text of %s changed; recomputing its cached triage data", case_id)
            entry = {
                "case_id": case_id,
                "decision_date": decision_date,
                "text_sha256": text_sha256,
            }
            self.items[key] = entry
        return entry

    def _path(self, decision_date: str | None, case_id: str, suffix: str) -> Path:
        month = decision_date[:7] if decision_date else "undated"
        return self.root / month / f"{entry_key(case_id, decision_date)}{suffix}"

    # ------------------------------------------------------------- embeddings

    def get_vectors(
        self, case_id: str, decision_date: str | None, text_sha256: str, fingerprint: str
    ) -> np.ndarray | None:
        entry = self.items.get(entry_key(case_id, decision_date))
        if not entry or entry.get("text_sha256") != text_sha256:
            return None
        emb = entry.get("embedding") or {}
        if emb.get("fingerprint") != fingerprint:
            return None
        path = self.root / emb.get("file", "")
        try:
            return np.load(path, allow_pickle=False)
        except (OSError, ValueError):
            return None

    def put_vectors(
        self,
        case_id: str,
        decision_date: str | None,
        text_sha256: str,
        fingerprint: str,
        vectors: np.ndarray,
    ) -> None:
        entry = self._entry(case_id, decision_date, text_sha256)
        path = self._path(decision_date, case_id, ".npy")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp.npy")
        np.save(tmp, np.asarray(vectors, dtype=np.float32), allow_pickle=False)
        tmp.replace(path)
        entry["embedding"] = {
            "fingerprint": fingerprint,
            "file": path.relative_to(self.root).as_posix(),
            "n_chunks": int(vectors.shape[0]),
            "updated_at": _now(),
        }

    # -------------------------------------------------------------- decisions

    def get_decision(
        self, case_id: str, decision_date: str | None, text_sha256: str, request_key: str
    ) -> dict[str, Any] | None:
        entry = self.items.get(entry_key(case_id, decision_date))
        if not entry or entry.get("text_sha256") != text_sha256:
            return None
        dec = entry.get("decision") or {}
        if dec.get("request_key") != request_key:
            return None
        try:
            return json.loads((self.root / dec["file"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, KeyError):
            return None

    def put_decision(
        self,
        case_id: str,
        decision_date: str | None,
        text_sha256: str,
        request_key: str,
        decision: dict[str, Any],
    ) -> None:
        entry = self._entry(case_id, decision_date, text_sha256)
        path = self._path(decision_date, case_id, ".json")
        _write_atomic(path, json.dumps(decision, ensure_ascii=False, indent=2).encode("utf-8"))
        entry["decision"] = {
            "request_key": request_key,
            "model": decision.get("model"),
            "file": path.relative_to(self.root).as_posix(),
            "updated_at": _now(),
        }
