"""Unit tests for embed.py (issue #5) — fast & offline via mocked backend."""

from __future__ import annotations

import numpy as np
import pytest

from hack_apertus_coffee_exe import embed as embed_mod


class _FakeSentenceTransformer:
    def __init__(self, model_name: str, device: str = "cpu", **kwargs) -> None:
        self.model_name = model_name
        self.device = device
        self.seen_inputs: list[list[str]] = []

    def get_embedding_dimension(self) -> int:
        return 4

    def encode(self, texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False):
        self.seen_inputs.append(list(texts))
        rows = []
        for idx, t in enumerate(texts):
            rows.append([float(len(t)), float(idx + 1), 1.0, 0.5])
        return np.asarray(rows, dtype=np.float32)


def test_chunk_text_splits_with_overlap():
    text = " ".join(f"w{i}" for i in range(100))
    chunks = embed_mod.chunk_text(text, max_words=40, overlap_words=10)
    assert len(chunks) == 3
    assert chunks[0].split()[0] == "w0"
    assert chunks[1].split()[0] == "w30"
    assert embed_mod.chunk_text("") == []


def test_e5_prefixes_and_l2_normalization(monkeypatch):
    import sentence_transformers

    fake_holder: dict[str, _FakeSentenceTransformer] = {}

    def _factory(model_name, device="cpu", **kwargs):
        inst = _FakeSentenceTransformer(model_name, device=device, **kwargs)
        fake_holder["inst"] = inst
        return inst

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", _factory)
    emb = embed_mod.MultilingualEmbedder("intfloat/multilingual-e5-small", device="cpu")

    doc_vecs = emb.embed(["Unfallversicherung nach Art. 6 UVG"])
    assert doc_vecs.shape == (1, 4)
    assert np.isclose(np.linalg.norm(doc_vecs[0]), 1.0)
    assert fake_holder["inst"].seen_inputs[-1] == ["passage: Unfallversicherung nach Art. 6 UVG"]

    q_vecs = emb.embed_queries(["Rückenverletzung nach Sturz"])
    assert q_vecs.shape == (1, 4)
    assert fake_holder["inst"].seen_inputs[-1] == ["query: Rückenverletzung nach Sturz"]

    empty = emb.embed([])
    assert empty.shape == (0, 4)


@pytest.mark.integration
def test_real_multilingual_e5_cross_lingual_similarity():
    """Verify DE <-> FR <-> IT semantic alignment with the real model."""
    emb = embed_mod.MultilingualEmbedder("intfloat/multilingual-e5-small")
    q = emb.embed_queries(["Rückenverletzung nach Sturz am Arbeitsplatz"])
    docs = emb.embed(
        [
            "Lésion dorsale suite à une chute sur le lieu de travail (assurance-accidents LAA).",
            "Infortunio sul lavoro con trauma alla schiena dopo una caduta.",
            "Scheidungskonvention und Gütertrennung nach Art. 125 ZGB.",
        ]
    )
    sims = (docs @ q.T).ravel()
    assert sims[0] > sims[2]
    assert sims[1] > sims[2]
