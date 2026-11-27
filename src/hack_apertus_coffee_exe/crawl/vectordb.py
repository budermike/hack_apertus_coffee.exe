"""Local persistent Vector-DB + FTS5 index for BGer judgements (#5, #8, #9, #30, #37).

Persists metadata in SQLite (WAL mode), FTS5 full-text index in a separate
rebuildable SQLite file, and L2-normalized chunk vectors in compressed NumPy
archives under ``data/processed/vectordb/``:

    data/processed/vectordb/metadata.sqlite3          metadata + case aliases (~2 MB)
    data/processed/vectordb/fts.sqlite3               FTS5 full-text index (local)
    data/processed/vectordb/embedder_meta.json        persisted embedder config/vocab
    data/processed/vectordb/vectors/<YYYY-MM>/<doc_key>.npz   per-judgement chunks
    data/processed/vectordb/search_index.npz          consolidated fast cosine index

Default embedder: ``intfloat/multilingual-e5-small`` (MIT open-weights, 384-dim,
DE/FR/IT, via ``hack_apertus_coffee_exe.embed``). Pluggable ``Embedder`` also
supports ``TfidfEmbedder`` and custom dense embedders for offline unit tests.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from hack_apertus_coffee_exe.crawl.crawl import CASE_ID_RE, safe_case_id, sha256
from hack_apertus_coffee_exe.embed import (
    MultilingualEmbedder,
    chunk_judgement,
    chunk_text,
    get_default_embedder,
    l2_normalize,
)
from hack_apertus_coffee_exe.triage.embeddings import Embedder, TfidfEmbedder
from hack_apertus_coffee_exe.triage.reference_corpus import load_reference_corpus

DEFAULT_DB_DIR = "data/processed/vectordb"
DEFAULT_RAW_DIR = "data/raw"
DEFAULT_JUDGEMENTS_DIR = "judgements"
DEFAULT_REPORT_PATH = "results/tables/vectordb_report.csv"
FTS_TOKEN_RE = re.compile(r"[0-9a-zA-ZäöüÄÖÜàâéèêëîïôùûüçÀÂÉÈÊËÎÏÔÙÛÜÇìòù]+")
ISO_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_FALLBACK_TFIDF_SEEDS = (
    "Unfallversicherung UVG Art. 6 Suva Kausalzusammenhang Körperschädigung Berufskrankheit",
    "Assurance-accidents LAA Suva lien de causalité lésion corporelle maladie professionnelle",
    "Assicurazione contro gli infortuni LAINF Suva infortunio malattia professionale",
)


def _atomic_tmp_path(target: Path, extra_suffix: str = "") -> Path:
    return target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp{extra_suffix}")


def load_reference_case_ids(judgements_dir: str | Path = DEFAULT_JUDGEMENTS_DIR) -> set[str]:
    """Collect positive Suva reference case_ids from Export_*.html and Excel lists."""
    base = Path(judgements_dir)
    if not base.exists():
        return set()

    ref_ids: set[str] = set()
    if list(base.glob("Export_*.html")):
        for case in load_reference_corpus(base):
            ref_ids.add(case.case_id)

    space_case_re = re.compile(r"\b(\d{1,2}[A-Z])\s+(\d+/\d{4})\b")
    bare_bger_re = re.compile(r"\bBGer-Urteil\s+(\d+/\d{4})\b")
    for xlsx_path in sorted(base.glob("*.xlsx")):
        with zipfile.ZipFile(xlsx_path) as archive:
            for name in archive.namelist():
                if name.endswith(".xml"):
                    xml_text = archive.read(name).decode("utf-8", errors="ignore")
                    ref_ids.update(CASE_ID_RE.findall(xml_text))
                    for reg, num in space_case_re.findall(xml_text):
                        ref_ids.add(f"{reg}_{num}")
                    for num in bare_bger_re.findall(xml_text):
                        matched_chamber = [cid for cid in ref_ids if cid.endswith(f"_{num}")]
                        ref_ids.update(matched_chamber or [f"8C_{num}"])
    return ref_ids


def build_tfidf_embedder(judgements_dir: str | Path = DEFAULT_JUDGEMENTS_DIR) -> TfidfEmbedder:
    """Fit the multilingual word + char-ngram TF-IDF embedder (with offline fallback)."""
    base = Path(judgements_dir)
    embedder = TfidfEmbedder()
    if base.exists() and list(base.glob("Export_*.html")):
        cases = load_reference_corpus(base)
        embedder.fit([case.text_for_language(None) for case in cases])
    else:
        embedder.fit(list(_FALLBACK_TFIDF_SEEDS))
    return embedder


def embedder_fingerprint(embedder: Embedder, max_words: int = 350, overlap_words: int = 50) -> str:
    """Stable cache key covering embedder identity, chunking parameters, and vocabulary."""
    parts = [embedder.name, f"ctx_chunk={max_words}/{overlap_words}"]
    if isinstance(embedder, TfidfEmbedder):
        digest = hashlib.sha256()
        digest.update("\n".join(sorted(embedder.vocab_)).encode("utf-8"))
        digest.update(np.asarray(embedder.idf_, dtype=np.float32).tobytes())
        parts.append(f"vocab={digest.hexdigest()[:16]}")
    return "|".join(parts)


def _tfidf_sparse_chunks(
    chunks: Sequence[str], embedder: TfidfEmbedder
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[int, int]]:
    """Compute exact L2-normalized TF-IDF vectors directly in CSR sparse format."""
    dim = len(embedder.vocab_)
    if not chunks:
        return (
            np.zeros(1, dtype=np.int32),
            np.zeros(0, dtype=np.int32),
            np.zeros(0, dtype=np.float32),
            (0, dim),
        )

    indptr = [0]
    all_indices: list[np.ndarray] = []
    all_values: list[np.ndarray] = []
    vocab = embedder.vocab_
    idf = embedder.idf_

    for text in chunks:
        feats = embedder._featurize(text)
        cols: list[int] = []
        vals: list[float] = []
        for term, count in feats.items():
            col = vocab.get(term)
            if col is not None:
                cols.append(col)
                vals.append((1.0 + math.log(count)) * float(idf[col]))
        if cols:
            idx_arr = np.asarray(cols, dtype=np.int32)
            val_arr = np.asarray(vals, dtype=np.float32)
            order = np.argsort(idx_arr)
            idx_arr = idx_arr[order]
            val_arr = val_arr[order]
            norm = float(np.linalg.norm(val_arr))
            if norm > 1e-12:
                val_arr = val_arr / norm
            all_indices.append(idx_arr)
            all_values.append(val_arr)
            indptr.append(indptr[-1] + len(idx_arr))
        else:
            indptr.append(indptr[-1])

    indices = np.concatenate(all_indices) if all_indices else np.zeros(0, dtype=np.int32)
    data = np.concatenate(all_values) if all_values else np.zeros(0, dtype=np.float32)
    return np.asarray(indptr, dtype=np.int32), indices, data, (len(chunks), dim)


def csr_to_dense(
    indptr: np.ndarray, indices: np.ndarray, data: np.ndarray, shape: tuple[int, int]
) -> np.ndarray:
    """Materialize a CSR matrix into a dense float32 array (n_chunks, dim)."""
    n_rows, n_cols = int(shape[0]), int(shape[1])
    out = np.zeros((n_rows, n_cols), dtype=np.float32)
    for row in range(n_rows):
        start, end = int(indptr[row]), int(indptr[row + 1])
        if start < end:
            out[row, indices[start:end]] = data[start:end]
    return out


def _csr_to_csc(
    n_rows: int, n_cols: int, indptr: np.ndarray, indices: np.ndarray, data: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert CSR chunk matrix to CSC inverted index for millisecond cosine search."""
    nnz = len(indices)
    if nnz == 0:
        return np.zeros(n_cols + 1, dtype=np.int32), np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.float32)
    row_ids = np.repeat(np.arange(n_rows, dtype=np.int32), np.diff(indptr))
    order = np.argsort(indices, kind="mergesort")
    sorted_cols = indices[order]
    csc_indices = row_ids[order]
    csc_data = data[order]
    counts = np.bincount(sorted_cols, minlength=n_cols).astype(np.int32)
    csc_indptr = np.zeros(n_cols + 1, dtype=np.int32)
    np.cumsum(counts, out=csc_indptr[1:])
    return csc_indptr, csc_indices, csc_data


def _build_fts_match_expr(raw_query: str) -> str:
    """Convert user query (e.g. 'Art. 6 UVG') into a safe FTS5 phrase/AND expression."""
    stripped = raw_query.strip()
    if not stripped:
        return ""
    tokens = FTS_TOKEN_RE.findall(stripped)
    if not tokens:
        return ""
    if stripped.startswith('"') and stripped.endswith('"') and len(tokens) > 1:
        return '"' + " ".join(tokens) + '"'
    return " AND ".join(f'"{tok}"' for tok in tokens)


def quarter_of(iso_date: str) -> str:
    """Return 'YYYY-QN' for an ISO date 'YYYY-MM-DD'."""
    year = iso_date[:4]
    month = int(iso_date[5:7])
    return f"{year}-Q{(month - 1) // 3 + 1}"


class VectorDB:
    """Disk-persisted vector & FTS5 database with metadata filtering and chunk-to-case search."""

    def __init__(
        self,
        db_dir: str | Path = DEFAULT_DB_DIR,
        embedder: Embedder | None = None,
        judgements_dir: str | Path = DEFAULT_JUDGEMENTS_DIR,
        max_words: int = 350,
        overlap_words: int = 50,
    ) -> None:
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_path = self.db_dir / "metadata.sqlite3"
        self.fts_path = self.db_dir / "fts.sqlite3"
        self.embedder_meta_path = self.db_dir / "embedder_meta.json"
        self.search_index_path = self.db_dir / "search_index.npz"
        self.max_words = max_words
        self.overlap_words = overlap_words

        if embedder is not None:
            self.embedder = embedder
            self._persist_embedder_meta(embedder)
        else:
            self.embedder = self._load_persisted_or_default_embedder()
            if not self.embedder_meta_path.exists():
                self._persist_embedder_meta(self.embedder)

        self.fingerprint = embedder_fingerprint(self.embedder, max_words, overlap_words)
        self._search_cache: dict[str, Any] | None = None
        self._init_sqlite()

    def _persist_embedder_meta(self, emb: Embedder) -> None:
        if isinstance(emb, TfidfEmbedder):
            if not emb.vocab_:
                return
            payload: dict[str, Any] = {
                "kind": "tfidf",
                "name": emb.name,
                "use_char_ngrams": emb.use_char_ngrams,
                "char_ngram_range": list(emb.char_ngram_range),
                "vocab": emb.vocab_,
                "idf": emb.idf_.tolist(),
            }
        elif isinstance(emb, MultilingualEmbedder):
            payload = {
                "kind": "multilingual",
                "name": emb.name,
                "model_name": emb.model_name,
                "dim": emb.dim,
            }
        else:
            if self.embedder_meta_path.exists():
                return
            payload = {
                "kind": "custom",
                "name": emb.name,
                "dim": int(getattr(emb, "dim", 0)),
            }
        tmp = _atomic_tmp_path(self.embedder_meta_path)
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.embedder_meta_path)

    def _load_persisted_or_default_embedder(self) -> Embedder:
        if self.embedder_meta_path.exists():
            saved = json.loads(self.embedder_meta_path.read_text(encoding="utf-8"))
            kind = saved.get("kind")
            if kind == "tfidf":
                emb = TfidfEmbedder(
                    use_char_ngrams=saved["use_char_ngrams"],
                    char_ngram_range=tuple(saved["char_ngram_range"]),
                )
                emb.vocab_ = {k: int(v) for k, v in saved["vocab"].items()}
                emb.idf_ = np.asarray(saved["idf"], dtype=np.float32)
                return emb
            if kind == "multilingual":
                return get_default_embedder(saved.get("model_name"))
        return get_default_embedder()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _connect_fts(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.fts_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_fts_sqlite(self) -> None:
        with self._connect_fts() as conn:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS judgements_fts USING fts5(
                    doc_key UNINDEXED,
                    case_id,
                    subject,
                    text,
                    tokenize='unicode61'
                );
                """
            )

    def _init_sqlite(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS judgements (
                    doc_key TEXT PRIMARY KEY,
                    id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    case_ids TEXT NOT NULL,
                    decision_date TEXT NOT NULL,
                    language TEXT,
                    division TEXT,
                    subject TEXT,
                    is_reference INTEGER NOT NULL DEFAULT 0,
                    is_negative_reference INTEGER NOT NULL DEFAULT 0,
                    manual_reference INTEGER NOT NULL DEFAULT 0,
                    url TEXT,
                    text_sha256 TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    n_chunks INTEGER NOT NULL,
                    vector_file TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_decision_date ON judgements(decision_date);
                CREATE INDEX IF NOT EXISTS idx_id ON judgements(id);
                CREATE INDEX IF NOT EXISTS idx_case_id ON judgements(case_id);
                CREATE INDEX IF NOT EXISTS idx_language ON judgements(language);
                CREATE INDEX IF NOT EXISTS idx_division ON judgements(division);
                CREATE INDEX IF NOT EXISTS idx_is_reference ON judgements(is_reference);
                CREATE INDEX IF NOT EXISTS idx_is_neg_ref ON judgements(is_negative_reference);

                CREATE TABLE IF NOT EXISTS case_lookup (
                    alias TEXT NOT NULL,
                    doc_key TEXT NOT NULL,
                    decision_date TEXT NOT NULL,
                    PRIMARY KEY (alias, doc_key)
                );
                CREATE INDEX IF NOT EXISTS idx_lookup_alias ON case_lookup(alias);
                """
            )
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(judgements)")}
            if "is_negative_reference" not in cols:
                conn.execute("ALTER TABLE judgements ADD COLUMN is_negative_reference INTEGER NOT NULL DEFAULT 0")
            if "manual_reference" not in cols:
                conn.execute("ALTER TABLE judgements ADD COLUMN manual_reference INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def doc_key_for(record: dict[str, Any]) -> str:
        doc_id = record.get("id") or safe_case_id(record["case_id"])
        return f"{record['decision_date']}_{doc_id}"

    def _vector_rel_path(self, decision_date: str, doc_key: str) -> str:
        month = decision_date[:7] if len(decision_date) >= 7 else "undated"
        return f"vectors/{month}/{doc_key}.npz"

    def _save_dense_file(self, vecs: np.ndarray, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = _atomic_tmp_path(dest, ".npz")
        np.savez_compressed(tmp, format="dense", dense=np.asarray(vecs, dtype=np.float32))
        tmp.replace(dest)

    def _save_sparse_file(
        self, chunks: Sequence[str], dest: Path, embedder: TfidfEmbedder
    ) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = _atomic_tmp_path(dest, ".npz")
        indptr, indices, data, shape = _tfidf_sparse_chunks(chunks, embedder)
        np.savez_compressed(
            tmp,
            format="csr",
            indptr=indptr,
            indices=indices,
            data=data,
            shape=np.asarray(shape, dtype=np.int32),
        )
        tmp.replace(dest)
        return int(shape[0])

    def load_vectors(self, doc_key: str) -> np.ndarray:
        """Load a judgement's chunk vectors as a dense (n_chunks, D) float32 matrix."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT vector_file FROM judgements WHERE doc_key = ?", (doc_key,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown doc_key: {doc_key}")
        with np.load(self.db_dir / row["vector_file"], allow_pickle=False) as npz:
            fmt = str(npz["format"])
            if fmt == "csr":
                shape = tuple(int(x) for x in npz["shape"])
                return csr_to_dense(npz["indptr"], npz["indices"], npz["data"], shape)
            return np.asarray(npz["dense"], dtype=np.float32)

    def ingest_records(
        self,
        records: Sequence[dict[str, Any]],
        reference_ids: set[str] | None = None,
        chunk_batch_size: int = 512,
        rebuild_index: bool = True,
    ) -> dict[str, int]:
        """Embed and upsert judgements in fast batches; unchanged SHA-256 entries are cached.

        ``rebuild_index=False`` is for callers that ingest in many small batches (ensure_period):
        the search index is only dropped, and rebuilt once by the caller or on the next search.
        """
        stats = {"embedded": 0, "cached": 0, "total": len(records)}

        with self._connect() as conn:
            existing = {
                row["doc_key"]: row
                for row in conn.execute(
                    """
                    SELECT doc_key, text_sha256, fingerprint, vector_file,
                           is_reference, is_negative_reference, manual_reference,
                           subject, division, language, case_ids, url
                    FROM judgements
                    """
                )
            }

            to_embed: list[tuple[dict[str, Any], str, str, list[str], str, int, int, int, str, list[str]]] = []
            for record in records:
                case_id = record["case_id"]
                doc_id = record.get("id") or safe_case_id(case_id)
                case_ids = record.get("case_ids") or [case_id]
                decision_date = record["decision_date"]
                doc_key = f"{decision_date}_{doc_id}"
                text = record.get("text") or ""
                text_hash = (record.get("fetch") or {}).get("text_sha256") or sha256(text)
                division = record.get("division") or record.get("department")
                subject = record.get("subject")
                language = record.get("language")
                url = record.get("url")
                rel_vec = self._vector_rel_path(decision_date, doc_key)
                vec_path = self.db_dir / rel_vec

                prev = existing.get(doc_key)
                if reference_ids is None:
                    desired_ref = int(prev["is_reference"]) if prev else 0
                else:
                    corpus_ref = int(bool(set(case_ids) & reference_ids))
                    desired_ref = int(prev["is_reference"]) if (prev and prev["manual_reference"] == 1) else corpus_ref

                desired_neg = int(prev["is_negative_reference"]) if prev else 0
                manual_ref = int(prev["manual_reference"]) if prev else 0

                if (
                    prev is not None
                    and prev["text_sha256"] == text_hash
                    and prev["fingerprint"] == self.fingerprint
                    and vec_path.exists()
                ):
                    case_ids_json = json.dumps(case_ids, ensure_ascii=False)
                    if (
                        int(prev["is_reference"]) != desired_ref
                        or prev["subject"] != subject
                        or prev["division"] != division
                        or prev["language"] != language
                        or prev["case_ids"] != case_ids_json
                        or prev["url"] != url
                    ):
                        conn.execute(
                            """
                            UPDATE judgements
                            SET is_reference = ?, subject = ?, division = ?, language = ?, case_ids = ?, url = ?
                            WHERE doc_key = ?
                            """,
                            (desired_ref, subject, division, language, case_ids_json, url, doc_key),
                        )
                        if prev["case_ids"] != case_ids_json:
                            conn.execute("DELETE FROM case_lookup WHERE doc_key = ?", (doc_key,))
                            aliases = {doc_key, doc_id, case_id}
                            for cid in case_ids:
                                aliases.add(cid)
                                aliases.add(safe_case_id(cid))
                            conn.executemany(
                                "INSERT OR REPLACE INTO case_lookup (alias, doc_key, decision_date) VALUES (?, ?, ?)",
                                [(alias, doc_key, decision_date) for alias in aliases],
                            )
                    stats["cached"] += 1
                    continue

                chunks = chunk_judgement(
                    text,
                    division=division,
                    subject=subject,
                    max_words=self.max_words,
                    overlap_words=self.overlap_words,
                )
                to_embed.append(
                    (record, doc_key, doc_id, case_ids, text_hash, desired_ref, desired_neg, manual_ref, rel_vec, chunks)
                )

        if not to_embed:
            if rebuild_index and not self.search_index_path.exists():
                self.rebuild_search_index()
            return stats

        is_tfidf = isinstance(self.embedder, TfidfEmbedder)

        self._init_fts_sqlite()
        batch_start = 0
        while batch_start < len(to_embed):
            batch_items = []
            batch_chunks: list[str] = []
            idx = batch_start
            while idx < len(to_embed) and (not batch_items or len(batch_chunks) + len(to_embed[idx][9]) <= chunk_batch_size):
                batch_items.append(to_embed[idx])
                batch_chunks.extend(to_embed[idx][9])
                idx += 1
            batch_start = idx

            dense_batch: np.ndarray | None = None
            if not is_tfidf and batch_chunks:
                dense_batch = l2_normalize(self.embedder.embed(batch_chunks))

            chunk_cursor = 0
            fts_rows: list[tuple[str, str, str, str]] = []
            with self._connect() as conn:
                for (
                    record,
                    doc_key,
                    doc_id,
                    case_ids,
                    text_hash,
                    is_ref,
                    is_neg_ref,
                    manual_ref,
                    rel_vec,
                    chunks,
                ) in batch_items:
                    vec_path = self.db_dir / rel_vec
                    n_c = len(chunks)
                    if is_tfidf:
                        self._save_sparse_file(chunks, vec_path, self.embedder)
                    else:
                        if n_c > 0 and dense_batch is not None:
                            doc_vecs = dense_batch[chunk_cursor : chunk_cursor + n_c]
                            chunk_cursor += n_c
                        else:
                            dim = int(getattr(self.embedder, "dim", 0))
                            doc_vecs = np.zeros((0, dim), dtype=np.float32)
                        self._save_dense_file(doc_vecs, vec_path)

                    division = record.get("division") or record.get("department")
                    subject = record.get("subject")
                    text = record.get("text") or ""
                    decision_date = record["decision_date"]
                    case_id = record["case_id"]

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO judgements (
                            doc_key, id, case_id, case_ids, decision_date, language,
                            division, subject, is_reference, is_negative_reference,
                            manual_reference, url, text_sha256, fingerprint, n_chunks, vector_file
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            doc_key,
                            doc_id,
                            case_id,
                            json.dumps(case_ids, ensure_ascii=False),
                            decision_date,
                            record.get("language"),
                            division,
                            subject,
                            is_ref,
                            is_neg_ref,
                            manual_ref,
                            record.get("url"),
                            text_hash,
                            self.fingerprint,
                            n_c,
                            rel_vec,
                        ),
                    )
                    conn.execute("DELETE FROM case_lookup WHERE doc_key = ?", (doc_key,))
                    aliases = {doc_key, doc_id, case_id}
                    for cid in case_ids:
                        aliases.add(cid)
                        aliases.add(safe_case_id(cid))
                    conn.executemany(
                        "INSERT OR REPLACE INTO case_lookup (alias, doc_key, decision_date) VALUES (?, ?, ?)",
                        [(alias, doc_key, decision_date) for alias in aliases],
                    )
                    fts_rows.append((doc_key, " ".join(case_ids), subject or "", text))
                    stats["embedded"] += 1

            with self._connect_fts() as fts_conn:
                fts_conn.executemany("DELETE FROM judgements_fts WHERE doc_key = ?", [(r[0],) for r in fts_rows])
                fts_conn.executemany(
                    "INSERT INTO judgements_fts (doc_key, case_id, subject, text) VALUES (?, ?, ?, ?)",
                    fts_rows,
                )

        if rebuild_index:
            self.rebuild_search_index()
        else:  # a stale index would hide the new judgements; a missing one is rebuilt on demand
            self.search_index_path.unlink(missing_ok=True)
            self._search_cache = None
        return stats

    def ingest_directory(
        self,
        raw_dir: str | Path = DEFAULT_RAW_DIR,
        judgements_dir: str | Path = DEFAULT_JUDGEMENTS_DIR,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, int]:
        """Ingest crawled JSON files from data/raw/ (optionally filtered by date span)."""
        base = Path(raw_dir)
        files = sorted(p for p in base.rglob("*.json") if not p.name.startswith("_"))
        records: list[dict[str, Any]] = []
        for path in files:
            rec = json.loads(path.read_text(encoding="utf-8"))
            d = rec.get("decision_date") or ""
            if date_from and d < date_from:
                continue
            if date_to and d > date_to:
                continue
            records.append(rec)
        ref_ids = load_reference_case_ids(judgements_dir) if Path(judgements_dir).exists() else None
        return self.ingest_records(records, reference_ids=ref_ids)

    def rebuild_search_index(self) -> None:
        """Build consolidated chunk-to-judgement index for rows matching current fingerprint."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_key, vector_file, n_chunks
                FROM judgements
                WHERE fingerprint = ?
                ORDER BY decision_date, doc_key
                """,
                (self.fingerprint,),
            ).fetchall()

        doc_keys = [r["doc_key"] for r in rows]
        tmp = _atomic_tmp_path(self.search_index_path, ".npz")

        if isinstance(self.embedder, TfidfEmbedder):
            dim = len(self.embedder.vocab_)
            indptr_parts = [np.zeros(1, dtype=np.int32)]
            idx_parts: list[np.ndarray] = []
            dat_parts: list[np.ndarray] = []
            chunk_docs: list[np.ndarray] = []
            offset = 0
            for doc_idx, row in enumerate(rows):
                with np.load(self.db_dir / row["vector_file"], allow_pickle=False) as npz:
                    ip = npz["indptr"]
                    n_c = len(ip) - 1
                    if n_c > 0:
                        indptr_parts.append(ip[1:] + offset)
                        idx_parts.append(npz["indices"])
                        dat_parts.append(npz["data"])
                        chunk_docs.append(np.full(n_c, doc_idx, dtype=np.int32))
                        offset = int(indptr_parts[-1][-1])

            indptr = np.concatenate(indptr_parts)
            indices = np.concatenate(idx_parts) if idx_parts else np.zeros(0, dtype=np.int32)
            data = np.concatenate(dat_parts) if dat_parts else np.zeros(0, dtype=np.float32)
            chunk_to_doc = np.concatenate(chunk_docs) if chunk_docs else np.zeros(0, dtype=np.int32)
            csc_indptr, csc_indices, csc_data = _csr_to_csc(len(chunk_to_doc), dim, indptr, indices, data)

            np.savez_compressed(
                tmp,
                format="csc",
                doc_keys=np.asarray(doc_keys),
                chunk_to_doc=chunk_to_doc,
                csc_indptr=csc_indptr,
                csc_indices=csc_indices,
                csc_data=csc_data,
                dim=np.int32(dim),
            )
        else:
            dense_parts: list[np.ndarray] = []
            chunk_docs = []
            for doc_idx, row in enumerate(rows):
                with np.load(self.db_dir / row["vector_file"], allow_pickle=False) as npz:
                    vecs = np.asarray(npz["dense"], dtype=np.float32)
                if vecs.ndim == 2 and vecs.shape[0] > 0:
                    dense_parts.append(vecs)
                    chunk_docs.append(np.full(vecs.shape[0], doc_idx, dtype=np.int32))
            dim = int(dense_parts[0].shape[1]) if dense_parts else int(getattr(self.embedder, "dim", 0))
            dense_all = np.vstack(dense_parts) if dense_parts else np.zeros((0, dim), dtype=np.float32)
            chunk_to_doc = np.concatenate(chunk_docs) if chunk_docs else np.zeros(0, dtype=np.int32)
            np.savez_compressed(
                tmp,
                format="dense",
                doc_keys=np.asarray(doc_keys),
                chunk_to_doc=chunk_to_doc,
                dense=dense_all,
            )

        tmp.replace(self.search_index_path)
        self._search_cache = None

    def _load_search_index(self) -> dict[str, Any]:
        if self._search_cache is None:
            if not self.search_index_path.exists():
                self.rebuild_search_index()
            with np.load(self.search_index_path, allow_pickle=False) as npz:
                self._search_cache = {k: npz[k] for k in npz.files}
        return self._search_cache

    def rebuild_fts_index(self, raw_dir: str | Path = DEFAULT_RAW_DIR) -> int:
        """Build or rebuild the separate fts.sqlite3 full-text index from crawled raw JSONs."""
        self._init_fts_sqlite()
        base = Path(raw_dir)
        files = sorted(p for p in base.rglob("*.json") if not p.name.startswith("_"))
        fts_rows: list[tuple[str, str, str, str]] = []
        for path in files:
            rec = json.loads(path.read_text(encoding="utf-8"))
            doc_key = self.doc_key_for(rec)
            case_ids = rec.get("case_ids") or [rec["case_id"]]
            fts_rows.append((doc_key, " ".join(case_ids), rec.get("subject") or "", rec.get("text") or ""))
        with self._connect_fts() as fts_conn:
            fts_conn.execute("DELETE FROM judgements_fts")
            fts_conn.executemany(
                "INSERT INTO judgements_fts (doc_key, case_id, subject, text) VALUES (?, ?, ?, ?)",
                fts_rows,
            )
        return len(fts_rows)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "doc_key": row["doc_key"],
            "id": row["id"],
            "case_id": row["case_id"],
            "case_ids": json.loads(row["case_ids"]),
            "decision_date": row["decision_date"],
            "language": row["language"],
            "division": row["division"],
            "subject": row["subject"],
            "is_reference": bool(row["is_reference"]),
            "is_negative_reference": bool(row["is_negative_reference"]),
            "url": row["url"],
            "text_sha256": row["text_sha256"],
            "n_chunks": row["n_chunks"],
        }

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0])

    def resolve_doc_keys(self, identifier: str) -> list[str]:
        """Resolve a case_id / safe id / doc_key to all matching doc_keys (newest first)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT doc_key FROM case_lookup WHERE alias = ? ORDER BY decision_date DESC, doc_key DESC",
                (identifier,),
            ).fetchall()
        if not rows:
            raise KeyError(f"Case not found in VectorDB: {identifier}")
        return [str(r["doc_key"]) for r in rows]

    def resolve_doc_key(self, identifier: str) -> str:
        """Resolve a case_id ('8C_736/2025'), safe id ('8C_736_2025'), or doc_key to the latest doc_key."""
        return self.resolve_doc_keys(identifier)[0]

    def get(self, identifier: str, *, include_vectors: bool = False) -> dict[str, Any]:
        """Fetch metadata (and optionally chunk vectors) for a judgement by case_id / id / doc_key."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT j.*
                FROM case_lookup c
                JOIN judgements j ON j.doc_key = c.doc_key
                WHERE c.alias = ?
                ORDER BY j.decision_date DESC, j.doc_key DESC
                LIMIT 1
                """,
                (identifier,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Case not found in VectorDB: {identifier}")
        item = self._row_to_dict(row)
        if include_vectors:
            with np.load(self.db_dir / row["vector_file"], allow_pickle=False) as npz:
                if str(npz["format"]) == "csr":
                    shape = tuple(int(x) for x in npz["shape"])
                    item["vectors"] = csr_to_dense(npz["indptr"], npz["indices"], npz["data"], shape)
                else:
                    item["vectors"] = np.asarray(npz["dense"], dtype=np.float32)
        return item

    def set_reference_flags(
        self,
        identifier: str,
        *,
        is_reference: bool | None = None,
        is_negative_reference: bool | None = None,
    ) -> dict[str, Any]:
        """Update positive/negative reference status from lawyer Ampel feedback (#28, #40)."""
        doc_keys = self.resolve_doc_keys(identifier)
        with self._connect() as conn:
            latest_row = conn.execute("SELECT * FROM judgements WHERE doc_key = ?", (doc_keys[0],)).fetchone()
            pos = int(is_reference) if is_reference is not None else int(latest_row["is_reference"])
            neg = (
                int(is_negative_reference)
                if is_negative_reference is not None
                else int(latest_row["is_negative_reference"])
            )
            if is_reference is True and is_negative_reference is None:
                neg = 0
            elif is_negative_reference is True and is_reference is None:
                pos = 0
            placeholders = ",".join("?" for _ in doc_keys)
            conn.execute(
                f"""
                UPDATE judgements
                SET is_reference = ?, is_negative_reference = ?, manual_reference = 1
                WHERE doc_key IN ({placeholders})
                """,
                [pos, neg, *doc_keys],
            )
            updated = conn.execute("SELECT * FROM judgements WHERE doc_key = ?", (doc_keys[0],)).fetchone()
        return self._row_to_dict(updated)

    def _filter_sql(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        language: str | None = None,
        division: str | None = None,
        is_reference: bool | None = None,
        is_negative_reference: bool | None = None,
        table_alias: str = "",
    ) -> tuple[list[str], list[Any]]:
        prefix = f"{table_alias}." if table_alias else ""
        clauses: list[str] = []
        params: list[Any] = []
        if date_from is not None:
            clauses.append(f"{prefix}decision_date >= ?")
            params.append(date_from)
        if date_to is not None:
            clauses.append(f"{prefix}decision_date <= ?")
            params.append(date_to)
        if language is not None:
            clauses.append(f"{prefix}language = ?")
            params.append(language)
        if division is not None:
            clauses.append(f"{prefix}division = ?")
            params.append(division)
        if is_reference is not None:
            clauses.append(f"{prefix}is_reference = ?")
            params.append(int(is_reference))
        if is_negative_reference is not None:
            clauses.append(f"{prefix}is_negative_reference = ?")
            params.append(int(is_negative_reference))
        return clauses, params

    def query(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        language: str | None = None,
        division: str | None = None,
        is_reference: bool | None = None,
        is_negative_reference: bool | None = None,
        include_vectors: bool = False,
    ) -> list[dict[str, Any]]:
        """Filter judgements by date range and metadata; optionally attach their chunk vectors."""
        clauses, params = self._filter_sql(
            date_from=date_from,
            date_to=date_to,
            language=language,
            division=division,
            is_reference=is_reference,
            is_negative_reference=is_negative_reference,
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM judgements {where} ORDER BY decision_date, doc_key"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        results = [self._row_to_dict(r) for r in rows]
        if include_vectors:
            for item in results:
                item["vectors"] = self.load_vectors(item["doc_key"])
        return results

    def fulltext_search(
        self,
        query_text: str,
        *,
        top_k: int = 20,
        date_from: str | None = None,
        date_to: str | None = None,
        language: str | None = None,
        division: str | None = None,
        is_reference: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Exact keyword/article search via SQLite FTS5 (#30, e.g. 'Art. 6 UVG')."""
        match_expr = _build_fts_match_expr(query_text)
        if not match_expr:
            return []

        self._init_fts_sqlite()
        with self._connect_fts() as fts_conn:
            fts_count = int(fts_conn.execute("SELECT COUNT(*) FROM judgements_fts").fetchone()[0])
        if fts_count == 0 and self.count() > 0 and Path(DEFAULT_RAW_DIR).exists():
            self.rebuild_fts_index(DEFAULT_RAW_DIR)

        with self._connect_fts() as fts_conn:
            fts_rows = fts_conn.execute(
                """
                SELECT doc_key,
                       -bm25(judgements_fts) AS bm25_score,
                       snippet(judgements_fts, 3, '[', ']', ' … ', 20) AS snippet
                FROM judgements_fts
                WHERE judgements_fts MATCH ?
                ORDER BY bm25(judgements_fts) ASC
                """,
                (match_expr,),
            ).fetchall()

        if not fts_rows:
            return []

        has_filters = any(v is not None for v in (date_from, date_to, language, division, is_reference))
        if has_filters:
            allowed_clauses, allowed_params = self._filter_sql(
                date_from=date_from,
                date_to=date_to,
                language=language,
                division=division,
                is_reference=is_reference,
            )
            with self._connect() as conn:
                allowed = {
                    r["doc_key"]: self._row_to_dict(r)
                    for r in conn.execute(
                        f"SELECT * FROM judgements WHERE {' AND '.join(allowed_clauses)}",
                        allowed_params,
                    )
                }
            picked = [r for r in fts_rows if r["doc_key"] in allowed][:top_k]
            return [
                {
                    **allowed[r["doc_key"]],
                    "bm25_score": round(float(r["bm25_score"]), 4),
                    "snippet": r["snippet"],
                }
                for r in picked
            ]

        picked = fts_rows[:top_k]
        placeholders = ",".join("?" for _ in picked)
        with self._connect() as conn:
            by_key = {
                r["doc_key"]: self._row_to_dict(r)
                for r in conn.execute(
                    f"SELECT * FROM judgements WHERE doc_key IN ({placeholders})",
                    [r["doc_key"] for r in picked],
                )
            }
        return [
            {
                **by_key[r["doc_key"]],
                "bm25_score": round(float(r["bm25_score"]), 4),
                "snippet": r["snippet"],
            }
            for r in picked
            if r["doc_key"] in by_key
        ]

    def _rank_against_index(
        self,
        *,
        query_dense: np.ndarray | None = None,
        query_csr: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
        top_k: int = 10,
        exclude_doc_keys: set[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        language: str | None = None,
        division: str | None = None,
        is_reference: bool | None = None,
        is_negative_reference: bool | None = None,
    ) -> list[dict[str, Any]]:
        idx = self._load_search_index()
        doc_keys = [str(k) for k in idx["doc_keys"]]
        chunk_to_doc = idx["chunk_to_doc"]
        n_docs = len(doc_keys)
        n_total_chunks = len(chunk_to_doc)
        if n_docs == 0 or n_total_chunks == 0:
            return []

        fmt = str(idx["format"])
        if fmt == "csc" and query_csr is not None:
            q_ip, q_idx, q_dat = query_csr
            n_q = len(q_ip) - 1
            if n_q == 0 or len(q_idx) == 0:
                return []
            csc_ip, csc_idx, csc_dat = idx["csc_indptr"], idx["csc_indices"], idx["csc_data"]
            best_chunk_scores = np.full(n_total_chunks, -np.inf, dtype=np.float32)
            for row in range(n_q):
                row_start, row_end = int(q_ip[row]), int(q_ip[row + 1])
                if row_start == row_end:
                    continue
                scores = np.zeros(n_total_chunks, dtype=np.float32)
                touched = np.zeros(n_total_chunks, dtype=bool)
                for pos in range(row_start, row_end):
                    col = int(q_idx[pos])
                    weight = float(q_dat[pos])
                    start, end = int(csc_ip[col]), int(csc_ip[col + 1])
                    if start < end:
                        target_rows = csc_idx[start:end]
                        scores[target_rows] += weight * csc_dat[start:end]
                        touched[target_rows] = True
                scores[~touched] = -np.inf
                np.maximum(best_chunk_scores, scores, out=best_chunk_scores)
        else:
            if query_dense is None or query_dense.ndim != 2 or query_dense.shape[0] == 0:
                return []
            dense_mat = idx["dense"]
            if dense_mat.ndim != 2 or dense_mat.shape[0] == 0 or dense_mat.shape[1] != query_dense.shape[1]:
                return []
            sims = dense_mat @ query_dense.T
            best_chunk_scores = sims.max(axis=1)

        doc_scores = np.full(n_docs, -np.inf, dtype=np.float32)
        np.maximum.at(doc_scores, chunk_to_doc, best_chunk_scores)

        has_filters = any(
            v is not None
            for v in (date_from, date_to, language, division, is_reference, is_negative_reference)
        )
        allowed: set[str] | None = None
        if has_filters:
            allowed = {
                r["doc_key"]
                for r in self.query(
                    date_from=date_from,
                    date_to=date_to,
                    language=language,
                    division=division,
                    is_reference=is_reference,
                    is_negative_reference=is_negative_reference,
                    include_vectors=False,
                )
            }
        excluded = exclude_doc_keys or set()

        ranked_indices = np.argsort(-doc_scores, kind="stable")
        chosen: list[tuple[str, float]] = []
        for i in ranked_indices:
            dk = doc_keys[int(i)]
            score = float(doc_scores[int(i)])
            if not np.isfinite(score) or dk in excluded:
                continue
            if allowed is not None and dk not in allowed:
                continue
            chosen.append((dk, round(float(np.clip(score, -1.0, 1.0)), 4)))
            if len(chosen) >= top_k:
                break

        if not chosen:
            return []

        placeholders = ",".join("?" for _ in chosen)
        with self._connect() as conn:
            by_key = {
                r["doc_key"]: self._row_to_dict(r)
                for r in conn.execute(
                    f"SELECT * FROM judgements WHERE doc_key IN ({placeholders})",
                    [dk for dk, _ in chosen],
                )
            }
        return [{**by_key[dk], "similarity": sim} for dk, sim in chosen if dk in by_key]

    def similar_to_text(
        self,
        text: str,
        *,
        top_k: int = 10,
        is_query: bool = True,
        exclude_doc_keys: set[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        language: str | None = None,
        division: str | None = None,
        is_reference: bool | None = None,
        is_negative_reference: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search for free-text queries (#30) or full judgement passages."""
        chunks = chunk_text(text, max_words=self.max_words, overlap_words=self.overlap_words)
        if not chunks:
            return []

        if isinstance(self.embedder, TfidfEmbedder):
            q_ip, q_idx, q_dat, _ = _tfidf_sparse_chunks(chunks, self.embedder)
            return self._rank_against_index(
                query_csr=(q_ip, q_idx, q_dat),
                top_k=top_k,
                exclude_doc_keys=exclude_doc_keys,
                date_from=date_from,
                date_to=date_to,
                language=language,
                division=division,
                is_reference=is_reference,
                is_negative_reference=is_negative_reference,
            )

        if is_query and len(chunks) == 1 and hasattr(self.embedder, "embed_queries"):
            q_vecs = l2_normalize(self.embedder.embed_queries(chunks))
        else:
            q_vecs = l2_normalize(self.embedder.embed(chunks))
        return self._rank_against_index(
            query_dense=q_vecs,
            top_k=top_k,
            exclude_doc_keys=exclude_doc_keys,
            date_from=date_from,
            date_to=date_to,
            language=language,
            division=division,
            is_reference=is_reference,
            is_negative_reference=is_negative_reference,
        )

    def similar_to_case(
        self,
        identifier: str,
        *,
        top_k: int = 10,
        date_from: str | None = None,
        date_to: str | None = None,
        language: str | None = None,
        division: str | None = None,
        is_reference: bool | None = None,
        is_negative_reference: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Find the top-k most similar judgements to an ingested case (#11, #15, #37)."""
        doc_keys = self.resolve_doc_keys(identifier)
        doc_key = doc_keys[0]
        with self._connect() as conn:
            row = conn.execute(
                "SELECT vector_file FROM judgements WHERE doc_key = ?", (doc_key,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown doc_key: {doc_key}")

        with np.load(self.db_dir / row["vector_file"], allow_pickle=False) as npz:
            if str(npz["format"]) == "csr":
                return self._rank_against_index(
                    query_csr=(npz["indptr"], npz["indices"], npz["data"]),
                    top_k=top_k,
                    exclude_doc_keys=set(doc_keys),
                    date_from=date_from,
                    date_to=date_to,
                    language=language,
                    division=division,
                    is_reference=is_reference,
                    is_negative_reference=is_negative_reference,
                )
            q_vecs = np.asarray(npz["dense"], dtype=np.float32)
        return self._rank_against_index(
            query_dense=q_vecs,
            top_k=top_k,
            exclude_doc_keys=set(doc_keys),
            date_from=date_from,
            date_to=date_to,
            language=language,
            division=division,
            is_reference=is_reference,
            is_negative_reference=is_negative_reference,
        )

    def score_candidates(
        self,
        *,
        date_from: str,
        date_to: str,
        ref_date_from: str | None = None,
        ref_date_to: str | None = None,
        top_k: int = 3,
        negative_weight: float = 0.3,
        hybrid: bool = True,
    ) -> list[dict[str, Any]]:
        """Fast leakage-safe batch cosine scoring of a period against prior references (#11, #21, #28).

        ``ref_date_from`` limits the positive references to a window (eval #21: Q4/2025 only).
        """
        effective_ref_end = ref_date_to
        if effective_ref_end is None:
            from datetime import date, timedelta

            effective_ref_end = (date.fromisoformat(date_from) - timedelta(days=1)).isoformat()

        candidates = self.query(date_from=date_from, date_to=date_to, include_vectors=True)
        pos_refs = self.query(
            date_from=ref_date_from, date_to=effective_ref_end, is_reference=True, include_vectors=True
        )
        neg_refs = self.query(
            date_from=ref_date_from, date_to=effective_ref_end, is_negative_reference=True, include_vectors=True
        )

        if not candidates or not pos_refs:
            return []

        cand_ids = {cid for c in candidates for cid in c["case_ids"]}
        pos_refs = [r for r in pos_refs if not (set(r["case_ids"]) & cand_ids)]
        neg_refs = [r for r in neg_refs if not (set(r["case_ids"]) & cand_ids)]

        valid_pos_refs = [r for r in pos_refs if r["vectors"].shape[0] > 0]
        if not valid_pos_refs:
            return []
        pos_refs = valid_pos_refs
        pos_chunks_list = [r["vectors"] for r in pos_refs]
        pos_chunk_to_ref = np.concatenate(
            [np.full(r["vectors"].shape[0], i, dtype=np.int32) for i, r in enumerate(pos_refs)]
        )
        pos_mat = np.vstack(pos_chunks_list)
        pos_doc_means = l2_normalize(np.vstack([r["vectors"].mean(axis=0) for r in pos_refs]))

        neg_mat: np.ndarray | None = None
        if neg_refs:
            neg_parts = [r["vectors"] for r in neg_refs if r["vectors"].shape[0] > 0]
            if neg_parts:
                neg_mat = np.vstack(neg_parts)

        fts_domain_keys: set[str] = set()
        if hybrid:
            # fts.sqlite3 is not in Git. fulltext_search rebuilds it when it is missing or empty,
            # so a fresh clone gets the same scores as a machine that already has the index.
            for kw in ("UVG", "LAA", "LAINF", "Suva"):
                for hit in self.fulltext_search(kw, top_k=5000, date_from=date_from, date_to=date_to):
                    fts_domain_keys.add(hit["doc_key"])

        subject_keywords = ("unfall", "accident", "infortun", "uvg", "laa", "lainf")
        scored: list[dict[str, Any]] = []
        for cand in candidates:
            c_vecs = cand.pop("vectors")
            if c_vecs.shape[0] == 0:
                scored.append({**cand, "max_similarity": 0.0, "top_k_mean_similarity": 0.0, "score": 0.0, "similar_cases": []})
                continue

            chunk_sims = c_vecs @ pos_mat.T
            best_per_pos_chunk = chunk_sims.max(axis=0)
            per_ref_sim = np.full(len(pos_refs), -np.inf, dtype=np.float32)
            np.maximum.at(per_ref_sim, pos_chunk_to_ref, best_per_pos_chunk)

            k = min(top_k, len(pos_refs))
            top_idx = np.argsort(-per_ref_sim, kind="stable")[:k]
            top_vals = [float(per_ref_sim[i]) for i in top_idx if np.isfinite(per_ref_sim[i])]
            max_sim = top_vals[0] if top_vals else 0.0
            mean_sim = float(sum(top_vals) / len(top_vals)) if top_vals else 0.0

            if hybrid:
                cand_mean = l2_normalize(c_vecs.mean(axis=0))
                doc_mean_sims = (cand_mean @ pos_doc_means.T).ravel()
                doc_top_k = np.sort(doc_mean_sims)[-k:]
                doc_mean_sim = float(doc_top_k.mean()) if doc_top_k.size else 0.0
                base_sim = 0.25 * doc_mean_sim + 0.75 * mean_sim

                domain_bonus = 0.0
                if any(cid.startswith("8C_") for cid in cand["case_ids"]):
                    domain_bonus += 0.05
                subj_low = (cand.get("subject") or "").lower()
                if any(kw in subj_low for kw in subject_keywords):
                    domain_bonus += 0.08
                if cand["doc_key"] in fts_domain_keys:
                    domain_bonus += 0.06
                neg_penalty = max(0.0, float((c_vecs @ neg_mat.T).max())) * negative_weight if (neg_mat is not None and neg_mat.shape[0] > 0) else 0.0
                # Normalize [0, 1.19] -> [0, 100] so top candidates keep full cosine ordering without clamping at 100.
                raw_score = max(0.0, (base_sim + domain_bonus - neg_penalty) / 1.19) * 100.0
            else:
                neg_penalty = max(0.0, float((c_vecs @ neg_mat.T).max())) * negative_weight if (neg_mat is not None and neg_mat.shape[0] > 0) else 0.0
                raw_score = max(0.0, mean_sim - neg_penalty) * 100.0
            similar_cases = [
                {
                    "case_id": pos_refs[int(i)]["case_id"],
                    "id": pos_refs[int(i)]["id"],
                    "decision_date": pos_refs[int(i)]["decision_date"],
                    "subject": pos_refs[int(i)]["subject"],
                    "similarity": round(float(per_ref_sim[int(i)]), 4),
                }
                for i in top_idx
                if np.isfinite(per_ref_sim[int(i)])
            ]
            scored.append(
                {
                    **cand,
                    "max_similarity": round(max_sim, 4),
                    "top_k_mean_similarity": round(mean_sim, 4),
                    "score": round(min(100.0, raw_score), 2),
                    "similar_cases": similar_cases,
                }
            )

        scored.sort(key=lambda r: -r["score"])
        return scored

    def missing_reference_cases(self, judgements_dir: str | Path = DEFAULT_JUDGEMENTS_DIR) -> list[str]:
        """Return Suva reference case_ids not present in the Vector-DB (e.g. older quarters, #10)."""
        ref_ids = load_reference_case_ids(judgements_dir)
        present = {cid for r in self.query(is_reference=True) for cid in r["case_ids"]}
        return sorted(ref_ids - present)

    def verify_quarter_counts(self, raw_dir: str | Path = DEFAULT_RAW_DIR) -> list[dict[str, Any]]:
        """Compare per-quarter judgement counts in data/raw/ against the Vector-DB (#37)."""
        raw_counts: dict[str, int] = {}
        for path in sorted(Path(raw_dir).rglob("*.json")):
            if path.name.startswith("_"):
                continue
            prefix = path.name[:10]
            if ISO_DATE_PREFIX_RE.match(prefix):
                iso_date = prefix
            else:
                iso_date = json.loads(path.read_text(encoding="utf-8"))["decision_date"]
            q = quarter_of(iso_date)
            raw_counts[q] = raw_counts.get(q, 0) + 1

        db_counts: dict[str, int] = {}
        chunk_counts: dict[str, int] = {}
        ref_counts: dict[str, int] = {}
        for item in self.query():
            q = quarter_of(item["decision_date"])
            db_counts[q] = db_counts.get(q, 0) + 1
            chunk_counts[q] = chunk_counts.get(q, 0) + int(item["n_chunks"])
            ref_counts[q] = ref_counts.get(q, 0) + int(item["is_reference"])

        quarters = sorted(set(raw_counts) | set(db_counts))
        rows: list[dict[str, Any]] = []
        for q in quarters:
            r_cnt = raw_counts.get(q, 0)
            d_cnt = db_counts.get(q, 0)
            rows.append(
                {
                    "quarter": q,
                    "raw_count": r_cnt,
                    "vectordb_count": d_cnt,
                    "chunks": chunk_counts.get(q, 0),
                    "reference_cases": ref_counts.get(q, 0),
                    "match": r_cnt == d_cnt,
                }
            )
        return rows


def write_vectordb_report(rows: Sequence[dict[str, Any]], csv_path: str | Path = DEFAULT_REPORT_PATH) -> None:
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["quarter", "raw_count", "vectordb_count", "chunks", "reference_cases", "match"],
        )
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest and query BGer judgements in the local Vector-DB.")
    parser.add_argument("--db-dir", default=DEFAULT_DB_DIR)
    parser.add_argument("--raw-dir", default=DEFAULT_RAW_DIR)
    parser.add_argument("--judgements-dir", default=DEFAULT_JUDGEMENTS_DIR)
    parser.add_argument("--report-path", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--embedder", choices=["e5", "tfidf"], default="e5")
    parser.add_argument("--ingest", action="store_true", help="ingest JSON files from --raw-dir")
    parser.add_argument("--check", action="store_true", help="verify DB counts match --raw-dir per quarter")
    parser.add_argument("--from", dest="date_from", help="start date filter YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="end date filter YYYY-MM-DD")
    parser.add_argument("--lang", dest="language", help="language filter (de/fr/it)")
    parser.add_argument("--references-only", action="store_true", help="restrict to is_reference=True")
    parser.add_argument("--similar", help="find top-k judgements most similar to case_id / id")
    parser.add_argument("--query", help="semantic free-text search (#30)")
    parser.add_argument("--fts", help="exact full-text search via SQLite FTS5 (#30)")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)

    emb = build_tfidf_embedder(args.judgements_dir) if args.embedder == "tfidf" else None
    db = VectorDB(db_dir=args.db_dir, embedder=emb, judgements_dir=args.judgements_dir)

    if args.ingest:
        stats = db.ingest_directory(
            raw_dir=args.raw_dir,
            judgements_dir=args.judgements_dir,
            date_from=args.date_from,
            date_to=args.date_to,
        )
        print(json.dumps(stats))

    if args.check or args.ingest:
        rows = db.verify_quarter_counts(args.raw_dir)
        write_vectordb_report(rows, args.report_path)
        for row in rows:
            print(json.dumps(row))
        if not all(r["match"] for r in rows):
            raise SystemExit("Quarter count mismatch between data/raw and Vector-DB!")

    ref_filter = True if args.references_only else None
    if args.similar:
        matches = db.similar_to_case(
            args.similar,
            top_k=args.top_k,
            date_from=args.date_from,
            date_to=args.date_to,
            language=args.language,
            is_reference=ref_filter,
        )
        for m in matches:
            print(json.dumps({k: m[k] for k in ("case_id", "id", "decision_date", "language", "division", "subject", "similarity", "is_reference")}, ensure_ascii=False))
    elif args.query:
        matches = db.similar_to_text(
            args.query,
            top_k=args.top_k,
            date_from=args.date_from,
            date_to=args.date_to,
            language=args.language,
            is_reference=ref_filter,
        )
        for m in matches:
            print(json.dumps({k: m[k] for k in ("case_id", "id", "decision_date", "language", "division", "subject", "similarity", "is_reference")}, ensure_ascii=False))
    elif args.fts:
        matches = db.fulltext_search(
            args.fts,
            top_k=args.top_k,
            date_from=args.date_from,
            date_to=args.date_to,
            language=args.language,
            is_reference=ref_filter,
        )
        for m in matches:
            print(json.dumps({k: m[k] for k in ("case_id", "decision_date", "language", "subject", "bm25_score", "snippet")}, ensure_ascii=False))
    elif (args.date_from or args.date_to) and not args.ingest:
        items = db.query(date_from=args.date_from, date_to=args.date_to, language=args.language, is_reference=ref_filter)
        print(json.dumps({"matched": len(items), "first": items[0]["case_id"] if items else None}))


if __name__ == "__main__":
    main()
