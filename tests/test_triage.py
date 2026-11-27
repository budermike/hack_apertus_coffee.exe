"""Offline unit tests for the triage package (never calls the real gateway)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

from triage.embeddings import (
    TfidfEmbedder,
    chunk_text,
    cosine_similarity_matrix,
)
from triage.llm import ApertusTriageClient, GatewayConfig, _parse_json_decision
from triage.pipeline import TriagePipeline, load_crawl_items
from triage.reference_corpus import ReferenceCase, load_reference_corpus


class _FakeCompletions:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = json.dumps(self._payload, ensure_ascii=False)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _FakeOpenAIClient:
    def __init__(self, payload: dict) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(payload))


class TriageUnitTests(unittest.TestCase):
    def test_load_real_reference_corpus(self) -> None:
        cases = load_reference_corpus("judgements")
        self.assertEqual(len(cases), 55)
        by_id = {c.case_id: c for c in cases}
        self.assertIn("8C_177/2025", by_id)
        case = by_id["8C_177/2025"]
        self.assertEqual(case.chamber, "8C")
        self.assertIn("Art. 6 Abs. 2 UVG", case.text_de)
        self.assertIn("art. 6 al. 2 LAA", case.text_fr)
        self.assertIn("bger.ch", case.bger_url)
        # Ensure plain-text URL fallback also works for Export_8C_275_2025.html
        self.assertIn("8C_275/2025", by_id)

    def test_chunk_text_and_cosine_matrix(self) -> None:
        words = [f"w{i}" for i in range(800)]
        chunks = chunk_text(" ".join(words), max_words=350, overlap_words=50)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(len(chunks[0].split()), 350)

        a = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        b = np.array([[1.0, 0.0], [1.0, 1.0]], dtype=np.float32)
        sims = cosine_similarity_matrix(a, b)
        self.assertAlmostEqual(float(sims[0, 0]), 1.0, places=5)
        self.assertAlmostEqual(float(sims[1, 0]), 0.0, places=5)
        self.assertAlmostEqual(float(sims[0, 1]), 1.0 / np.sqrt(2.0), places=5)

    def test_tfidf_similarity_ranks_accident_case_higher_than_unrelated(self) -> None:
        refs = [
            ReferenceCase(
                case_id="8C_177/2025",
                chamber="8C",
                title="Arrêt 8C_177/2025",
                text_de="Die Liste der unfallähnlichen Körperschädigungen in Art. 6 Abs. 2 UVG. Beschwerde der Suva.",
                text_fr="Lésions corporelles assimilées à un accident art. 6 al. 2 LAA. Recours de la Suva.",
                bger_url="https://search.bger.ch/example1",
                author="FRT",
                source_file="dummy1.html",
            ),
            ReferenceCase(
                case_id="8C_21/2025",
                chamber="8C",
                title="Arrêt 8C_21/2025",
                text_de="Eine akute Vergiftung mit Toluol war keine Berufskrankheit nach UVG.",
                text_fr="Intoxication aiguë au toluène maladie professionnelle LAA.",
                bger_url="https://search.bger.ch/example2",
                author="FRT",
                source_file="dummy2.html",
            ),
        ]
        pipeline = TriagePipeline(reference_cases=refs, embedder=TfidfEmbedder(), llm_client=None)

        relevant_item = load_crawl_items_from_dicts(
            [
                {
                    "case_id": "8C_999/2026",
                    "language": "de",
                    "judgement_text": (
                        "Beschwerde der Suva gegen das Urteil des Kantonsgerichts betreffend "
                        "unfallähnliche Körperschädigung nach Art. 6 Abs. 2 UVG und Art. 44 ATSG."
                    ),
                },
                {
                    "case_id": "5A_100/2026",
                    "language": "de",
                    "judgement_text": (
                        "Scheidungsrecht, Güterrecht und Unterhaltsbeiträge für minderjährige Kinder "
                        "nach Art. 125 ZGB ohne Versicherungsbezug."
                    ),
                },
            ]
        )
        results = pipeline.score_items(relevant_item)
        self.assertEqual(results[0].case_id, "8C_999/2026")
        self.assertGreater(results[0].cosine_max_similarity, results[1].cosine_max_similarity)
        self.assertGreater(results[0].relevance_score, results[1].relevance_score)
        self.assertTrue(results[0].relevant)
        self.assertFalse(results[1].relevant)

    def test_parse_json_decision_with_markdown_fence(self) -> None:
        raw = """```json
        {"relevant": true, "apertus_score": 88, "reason": "Betrifft Art. 6 UVG.", "key_topics": ["UVG", "Suva"]}
        ```"""
        decision = _parse_json_decision(raw, model="apertus-v1-5-8b")
        self.assertTrue(decision.relevant)
        self.assertEqual(decision.apertus_score, 88.0)
        self.assertEqual(decision.key_topics, ["UVG", "Suva"])

    def test_pipeline_with_mocked_apertus_client_and_json_dir(self) -> None:
        import tempfile

        fake_llm = _FakeOpenAIClient(
            {
                "relevant": True,
                "apertus_score": 92,
                "reason": "Direkter Bezug zu Suva und Art. 6 Abs. 2 UVG.",
                "key_topics": ["UVG", "Körperschädigung"],
            }
        )
        cfg = GatewayConfig(
            api_key="dummy-key",
            base_url="https://gateway.keera.ch/api/v1",
            model="apertus-v1-5-8b",
        )
        llm_client = ApertusTriageClient(config=cfg, client=fake_llm, temperature=0.0)
        pipeline = TriagePipeline(judgements_dir="judgements", embedder=TfidfEmbedder(), llm_client=llm_client)

        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = Path(tmpdir) / "case1.json"
            p1.write_text(
                json.dumps(
                    {
                        "metadata": {"case_id": "8C_500/2026", "date": "2026-10-01"},
                        "judgement_text": "Suva Unfallversicherung Art. 6 Abs. 2 UVG Bänderriss.",
                    }
                ),
                encoding="utf-8",
            )
            items = load_crawl_items(tmpdir)
            results = pipeline.score_items(items)

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.case_id, "8C_500/2026")
        self.assertTrue(res.relevant)
        self.assertEqual(res.apertus_score, 92.0)
        self.assertEqual(res.llm_model, "apertus-v1-5-8b")
        self.assertEqual(len(res.top_reference_matches), 3)
        self.assertEqual(len(fake_llm.chat.completions.calls), 1)
        self.assertEqual(fake_llm.chat.completions.calls[0]["temperature"], 0.0)


def load_crawl_items_from_dicts(dicts: list[dict]):
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "batch.json"
        path.write_text(json.dumps(dicts), encoding="utf-8")
        return load_crawl_items(path)


if __name__ == "__main__":
    unittest.main()
