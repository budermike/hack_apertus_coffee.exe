"""Offline unit & functional tests for crawl/vectordb.py (#5, #8, #9, #28, #30, #37)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import numpy as np

from hack_apertus_coffee_exe.crawl import vectordb
from hack_apertus_coffee_exe.embed import chunk_judgement, l2_normalize
from hack_apertus_coffee_exe.triage.embeddings import TfidfEmbedder


def _sample_tfidf() -> TfidfEmbedder:
    emb = TfidfEmbedder()
    emb.fit(
        [
            "Unfallversicherung UVG Art. 6 Suva Kausalzusammenhang Schulterläsion Körperschädigung",
            "Berufskrankheit Art. 9 UVG Asbest Suva Invalidenrente Integritätsentschädigung",
            "Assurance-accidents LAA Suva lien de causalité rechute indemnité journalière",
        ]
    )
    return emb


class _DummyDenseEmbedder:
    """Fast deterministic 4-dim dense embedder testing the neural E5 code path offline."""

    name = "dummy-dense-e5"
    dim = 4

    def _vec(self, text: str) -> list[float]:
        low = text.lower()
        suva = float(any(w in low for w in ("suva", "unfall", "uvg", "sturz", "chute", "laa")))
        asbest = float("asbest" in low or "berufskrankheit" in low)
        civil = float(any(w in low for w in ("scheidung", "miet", "aktien", "zgb")))
        return [suva + 0.05, asbest + 0.05, civil + 0.05, 0.1]

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return l2_normalize(np.asarray([self._vec(t) for t in texts], dtype=np.float32))

    def embed_queries(self, queries: Sequence[str]) -> np.ndarray:
        return self.embed(queries)


def _make_record(
    case_id: str,
    decision_date: str,
    text: str,
    *,
    case_ids: list[str] | None = None,
    language: str = "de",
    division: str = "IV. öffentlich-rechtliche Abteilung",
    subject: str = "Unfallversicherung",
) -> dict:
    safe_id = case_id.replace("/", "_")
    return {
        "id": safe_id,
        "case_id": case_id,
        "case_ids": case_ids or [case_id],
        "decision_date": decision_date,
        "publication_date": None,
        "department": division,
        "division": division,
        "subject": subject,
        "language": language,
        "url": f"https://search.bger.ch/example/{safe_id}",
        "text": text,
        "operator_metadata": {},
        "fetch": {
            "fetched_at": "2026-11-27T12:00:00+00:00",
            "fetched_by": "test",
            "source": "entscheidsuche",
            "url": "https://entscheidsuche.example/doc.html",
            "hash_algorithm": "sha256",
            "text_sha256": vectordb.sha256(text),
        },
    }


def _write_raw(raw_dir: Path, record: dict, *, filename: str | None = None) -> Path:
    month_dir = raw_dir / record["decision_date"][:7]
    month_dir.mkdir(parents=True, exist_ok=True)
    path = month_dir / (filename or f"{record['decision_date']}_{record['id']}.json")
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def test_sparse_tfidf_matches_dense_embedder_exactly():
    emb = _sample_tfidf()
    chunks = [
        "Beschwerde der Suva betreffend Unfallversicherung und Kausalzusammenhang nach Art. 6 UVG.",
        "Mietrecht Kündigung Eigenbedarf ohne jeden Bezug zu Sozialversicherungen.",
    ]
    dense = emb.embed(chunks)
    indptr, indices, data, shape = vectordb._tfidf_sparse_chunks(chunks, emb)
    reconstructed = vectordb.csr_to_dense(indptr, indices, data, shape)
    np.testing.assert_allclose(reconstructed, dense, atol=1e-6)


def test_dense_ingest_preserves_references_when_reference_ids_omitted(tmp_path):
    """Regression test for Audit Bug 1.1 & 1.2: reference_ids=None must NOT wipe is_reference."""
    emb = _DummyDenseEmbedder()
    db_dir = tmp_path / "vectordb"
    records = [
        _make_record(
            "8C_100/2026",
            "2026-06-10",
            "Suva Unfallversicherung UVG Kausalzusammenhang Schulterläsion.",
            case_ids=["8C_100/2026", "8C_101/2026"],
        ),
        _make_record(
            "5A_200/2026",
            "2026-05-12",
            "Ehescheidung und Unterhalt nach ZGB.",
            division="II. zivilrechtliche Abteilung",
            subject="Ehescheidung",
        ),
    ]

    db = vectordb.VectorDB(db_dir=db_dir, embedder=emb)
    stats1 = db.ingest_records(records, reference_ids={"8C_101/2026"})
    assert stats1 == {"embedded": 2, "cached": 0, "total": 2}
    assert db.get("8C_100/2026")["is_reference"] is True

    # Calling ingest_records WITHOUT reference_ids (like ensure_period #35) must keep is_reference=True!
    stats2 = db.ingest_records(records)
    assert stats2 == {"embedded": 0, "cached": 2, "total": 2}
    assert db.get("8C_100/2026")["is_reference"] is True

    # Lawyer Ampel feedback (#28 / #40): mark 5A_200/2026 as negative reference
    updated = db.set_reference_flags("5A_200/2026", is_negative_reference=True)
    assert updated["is_negative_reference"] is True
    assert updated["is_reference"] is False

    # Re-ingest even with explicit reference_ids preserves manual feedback:
    db.ingest_records(records, reference_ids=set())
    assert db.get("5A_200/2026")["is_negative_reference"] is True


def test_tfidf_auto_persists_without_private_helper_and_handles_oov_and_empty_text(tmp_path):
    """Regression test for Audit Bug 1.2, 1.4, 1.5."""
    emb = _sample_tfidf()
    db_dir = tmp_path / "tfidf_db"
    db1 = vectordb.VectorDB(db_dir=db_dir, embedder=emb)
    db1.ingest_records(
        [
            _make_record("8C_1/2026", "2026-06-01", "Unfallversicherung Suva Art. 6 UVG."),
            _make_record("8C_2/2026", "2026-06-02", "", subject=""),  # empty text edge case
        ]
    )

    # Reopen from disk WITHOUT passing embedder: must reload persisted TF-IDF cleanly!
    db2 = vectordb.VectorDB(db_dir=db_dir)
    assert isinstance(db2.embedder, TfidfEmbedder)
    assert db2.count() == 2

    # Empty-chunk case does not crash similar_to_case:
    assert db2.similar_to_case("8C_2/2026") == []

    # All-OOV query returns [] instead of arbitrary 0.0 documents:
    assert db2.similar_to_text("zzzzqqqqxxxxnonexistent") == []


def test_duplicate_case_id_across_dates_and_batch_scoring(tmp_path):
    """Regression test for Audit 2 (#37/#11/#21): rectified judgement on later date + leakage-free scoring."""
    emb = _DummyDenseEmbedder()
    db = vectordb.VectorDB(db_dir=tmp_path / "db", embedder=emb)
    db.ingest_records(
        [
            _make_record("8C_50/2025", "2025-11-15", "Suva Unfallversicherung UVG Sturz."),
            _make_record("7B_1356/2025", "2026-01-26", "Altes Urteil Strafrecht.", division="II. strafrechtliche Abteilung", subject="Strafrecht"),
            _make_record("7B_1356/2025", "2026-03-18", "Berichtigtes Urteil Strafrecht.", division="II. strafrechtliche Abteilung", subject="Strafrecht"),
            _make_record("8C_70/2026", "2026-02-10", "Unfallversicherung Suva Sturz Baustelle."),
            _make_record("5A_80/2026", "2026-02-12", "Ehescheidung Güterrecht ZGB.", division="II. zivilrechtliche Abteilung", subject="Ehescheidung"),
        ],
        reference_ids={"8C_50/2025", "8C_70/2026"},
    )
    # Both decisions preserved; get() resolves newest rectified decision:
    assert db.count() == 5
    assert db.get("7B_1356/2025")["decision_date"] == "2026-03-18"

    # Leakage-safe batch scoring of Q1/2026 uses ONLY pre-2026-01-01 references (excludes 8C_70/2026!):
    ranked_pure = db.score_candidates(date_from="2026-01-01", date_to="2026-03-31", top_k=3, hybrid=False)
    ranked_hybrid = db.score_candidates(date_from="2026-01-01", date_to="2026-03-31", top_k=3, hybrid=True)
    assert ranked_pure[0]["case_id"] == "8C_70/2026"
    assert ranked_hybrid[0]["case_id"] == "8C_70/2026"
    assert (ranked_hybrid[0]["score"] - ranked_hybrid[1]["score"]) > (ranked_pure[0]["score"] - ranked_pure[1]["score"])
    assert ranked_hybrid[0]["similar_cases"][0]["case_id"] == "8C_50/2025"

    # Negative reference feedback (#28/#40) suppresses matching candidates:
    db.set_reference_flags("8C_50/2025", is_reference=False, is_negative_reference=True)
    db.ingest_records([_make_record("8C_51/2025", "2025-11-20", "Unfallversicherung UVG.")], reference_ids={"8C_51/2025"})
    ranked_neg = db.score_candidates(date_from="2026-01-01", date_to="2026-03-31", top_k=3, negative_weight=0.3, hybrid=True)
    score_after = next(r["score"] for r in ranked_neg if r["case_id"] == "8C_70/2026")
    assert score_after < ranked_hybrid[0]["score"]


def test_fulltext_search_fts5_and_filtered_semantic_search(tmp_path):
    emb = _DummyDenseEmbedder()
    db = vectordb.VectorDB(db_dir=tmp_path / "db", embedder=emb)
    db.ingest_records(
        [
            _make_record("8C_1/2025", "2025-11-10", "Unfallversicherung nach Art. 6 Abs. 2 UVG und Suva."),
            _make_record("8C_2/2025", "2025-12-05", "Assurance-accidents selon art. 6 LAA; chute sur le chantier.", language="fr"),
            _make_record("8C_3/2026", "2026-06-15", "Berufskrankheit Asbest nach Art. 9 UVG."),
            _make_record("4A_9/2026", "2026-06-18", "Mietvertrag Kündigung nach OR.", subject="Mietrecht"),
        ],
        reference_ids={"8C_1/2025", "8C_2/2025"},
    )

    fts_hits = db.fulltext_search("Art. 6 UVG")
    assert [h["case_id"] for h in fts_hits] == ["8C_1/2025"]
    assert "[" in fts_hits[0]["snippet"]

    fr_refs = db.similar_to_text(
        "Rückenverletzung nach Sturz am Arbeitsplatz",
        is_reference=True,
        language="fr",
        date_to="2025-12-31",
        top_k=3,
    )
    assert [r["case_id"] for r in fr_refs] == ["8C_2/2025"]


def test_fts_is_separate_from_metadata_and_rebuildable_from_raw(tmp_path):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "db"
    emb = _DummyDenseEmbedder()
    r1 = _make_record("8C_1/2025", "2025-11-10", "Unfallversicherung nach Art. 6 Abs. 2 UVG und Suva.")
    r2 = _make_record("4A_9/2026", "2026-06-18", "Mietvertrag Kündigung nach OR.", subject="Mietrecht")
    _write_raw(raw_dir, r1)
    _write_raw(raw_dir, r2)

    db = vectordb.VectorDB(db_dir=db_dir, embedder=emb)
    db.ingest_records([r1, r2])
    assert (db_dir / "fts.sqlite3").exists()
    assert [h["case_id"] for h in db.fulltext_search("Art. 6 UVG")] == ["8C_1/2025"]

    # Delete fts.sqlite3 + sidecars and verify rebuild_fts_index restores FTS5 cleanly:
    for p in db_dir.glob("fts.sqlite3*"):
        p.unlink()
    rebuilt = db.rebuild_fts_index(raw_dir)
    assert rebuilt == 2
    assert [h["case_id"] for h in db.fulltext_search("Art. 6 UVG")] == ["8C_1/2025"]


def test_long_judgement_chunks_point_back_to_parent_case_in_similarity_search(tmp_path):
    emb = _sample_tfidf()
    db = vectordb.VectorDB(db_dir=tmp_path / "db", embedder=emb, max_words=50, overlap_words=10)

    filler = " ".join(["Verfahrensgeschichte Vorinstanz Parteien Sachverhalt"] * 30)
    suva_passage = "Beschwerde der Suva betreffend Unfallversicherung Kausalzusammenhang Schulterläsion nach Art. 6 UVG."
    long_suva_case = f"{filler} {suva_passage} {filler}"
    assert len(chunk_judgement(long_suva_case, max_words=50, overlap_words=10)) >= 4

    db.ingest_records(
        [
            _make_record("8C_10/2026", "2026-06-01", suva_passage, subject=""),
            _make_record("8C_11/2026", "2026-06-02", long_suva_case, subject=""),
            _make_record("4A_99/2026", "2026-06-03", "Kaufvertrag Aktienrecht Obligationenrecht Mietzins.", division="I. zivilrechtliche Abteilung", subject="Kaufvertrag"),
        ]
    )

    matches = db.similar_to_case("8C_10/2026", top_k=2)
    assert matches[0]["case_id"] == "8C_11/2026"
    assert matches[0]["n_chunks"] >= 4
    assert matches[0]["similarity"] > matches[1]["similarity"]


def test_verify_quarter_counts_handles_non_prefixed_filenames_and_cli(tmp_path, capsys):
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "db"
    report_csv = tmp_path / "report.csv"

    _write_raw(raw_dir, _make_record("8C_1/2025", "2025-11-10", "Suva Unfallversicherung UVG."))
    _write_raw(raw_dir, _make_record("8C_2/2026", "2026-02-14", "Berufskrankheit Asbest Suva."), filename="8C_2_2026.json")
    _write_raw(raw_dir, _make_record("8C_3/2026", "2026-06-19", "Suva Kausalzusammenhang UVG."))
    (raw_dir / "_index.json").write_text('{"days": {}}', encoding="utf-8")

    vectordb.main(
        [
            "--db-dir", str(db_dir),
            "--raw-dir", str(raw_dir),
            "--judgements-dir", str(tmp_path / "no_refs"),
            "--report-path", str(report_csv),
            "--embedder", "tfidf",
            "--ingest",
            "--similar", "8C_1/2025",
        ]
    )
    out = capsys.readouterr().out
    assert '"embedded": 3' in out
    assert '"match": true' in out
    assert report_csv.exists()


def test_hybrid_score_is_the_same_without_a_local_fts_index(tmp_path, monkeypatch):
    """fts.sqlite3 is not in Git: a fresh clone must score like a machine that has it (#21)."""
    raw_dir = tmp_path / "raw"
    db_dir = tmp_path / "db"
    records = [
        _make_record("8C_50/2025", "2025-11-15", "Suva Unfallversicherung UVG Sturz."),
        _make_record("9C_70/2026", "2026-02-10", "Rente nach UVG, Suva als Versicherer.", subject="Invalidenversicherung"),
        _make_record("5A_80/2026", "2026-02-12", "Ehescheidung Güterrecht ZGB.", subject="Ehescheidung"),
    ]
    for record in records:
        _write_raw(raw_dir, record)
    monkeypatch.setattr(vectordb, "DEFAULT_RAW_DIR", str(raw_dir))

    db = vectordb.VectorDB(db_dir=db_dir, embedder=_DummyDenseEmbedder())
    db.ingest_records(records, reference_ids={"8C_50/2025"})
    with_index = db.score_candidates(date_from="2026-01-01", date_to="2026-03-31")

    for path in db_dir.glob("fts.sqlite3*"):
        path.unlink()
    fresh_clone = vectordb.VectorDB(db_dir=db_dir, embedder=_DummyDenseEmbedder())
    without_index = fresh_clone.score_candidates(date_from="2026-01-01", date_to="2026-03-31")

    assert [(r["case_id"], r["score"]) for r in without_index] == [(r["case_id"], r["score"]) for r in with_index]
    assert (db_dir / "fts.sqlite3").exists()  # rebuilt from the raw files

    # The comparison is meaningful: without full-text hits 9C_70/2026 scores lower.
    monkeypatch.setattr(vectordb.VectorDB, "fulltext_search", lambda self, *args, **kwargs: [])
    no_bonus = {r["case_id"]: r["score"] for r in db.score_candidates(date_from="2026-01-01", date_to="2026-03-31")}
    assert no_bonus["9C_70/2026"] < {r["case_id"]: r["score"] for r in with_index}["9C_70/2026"]


def test_ingest_without_rebuild_drops_stale_search_index(tmp_path):
    emb = _DummyDenseEmbedder()
    db = vectordb.VectorDB(db_dir=tmp_path / "db", embedder=emb)
    db.ingest_records([_make_record("8C_1/2025", "2025-11-10", "Suva Unfallversicherung UVG.")])
    assert db.search_index_path.exists()

    db.ingest_records([_make_record("8C_2/2025", "2025-11-11", "Suva Sturz UVG.")], rebuild_index=False)
    assert not db.search_index_path.exists()  # a stale index would hide 8C_2/2025

    hits = db.similar_to_text("Suva Unfall", top_k=5)  # rebuilt on demand
    assert {h["case_id"] for h in hits} == {"8C_1/2025", "8C_2/2025"}
