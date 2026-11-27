"""Offline tests for the triage issue fixes (#11, #12, #13, #28, #33) and the cache."""

from __future__ import annotations

import csv
from datetime import date
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from hack_apertus_coffee_exe.triage.cache import TriageCache
from hack_apertus_coffee_exe.triage.cli import main as cli_main
from hack_apertus_coffee_exe.triage.config import TriageConfig, load_config
from hack_apertus_coffee_exe.triage.date_filter import DateRange, parse_date_string
from hack_apertus_coffee_exe.triage.embeddings import TfidfEmbedder
from hack_apertus_coffee_exe.triage.export import RANKING_COLUMNS, export_period, format_recall_list
from hack_apertus_coffee_exe.triage.llm import ApertusTriageClient, GatewayConfig
from hack_apertus_coffee_exe.triage.pipeline import CrawlItem, TriagePipeline, _normalize_crawl_dict
from hack_apertus_coffee_exe.triage.reference_corpus import ReferenceCase, load_reference_corpus

ACCIDENT_TEXT = (
    "Beschwerde der Suva betreffend unfallähnliche Körperschädigung nach Art. 6 Abs. 2 UVG. "
    "Die Unfallversicherung verweigerte Leistungen für den Sehnenriss am Knie. "
)
ACCIDENT_TEXT_2 = (
    "Die Suva lehnte Leistungen der Unfallversicherung ab; strittig ist die "
    "unfallähnliche Körperschädigung (Sehnenriss) nach Art. 6 Abs. 2 UVG. "
)
DIVORCE_TEXT = "Scheidungsrecht, Güterrecht und Unterhaltsbeiträge nach Art. 125 ZGB ohne Versicherungsbezug. "


def _ref(case_id: str, text_de: str, decided: date | None) -> ReferenceCase:
    return ReferenceCase(
        case_id=case_id,
        chamber=case_id.split("_")[0],
        title=f"Urteil {case_id}",
        text_de=text_de,
        text_fr="",
        bger_url=f"https://example.invalid/{case_id}",
        author="T",
        source_file="dummy.html",
        decision_date=decided,
    )


REFS = [
    _ref("8C_1/2025", "Unfallähnliche Körperschädigung Art. 6 Abs. 2 UVG, Beschwerde der Suva, Sehnenriss.", date(2025, 11, 3)),
    _ref("8C_2/2026", "Berufskrankheit nach UVG, Lärmschwerhörigkeit, Suva.", date(2026, 5, 10)),
    _ref("8C_3/2025", "Integritätsentschädigung UVG, Unfall, Suva-Leistungen.", date(2025, 12, 1)),
]


def _item(case_id: str, decided: str, text: str) -> CrawlItem:
    return _normalize_crawl_dict(
        {"case_id": case_id, "decision_date": decided, "language": "de", "text": text * 5},
        fallback_id=case_id,
    )


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        user = kwargs["messages"][1]["content"]
        score = 85 if "Suva" in user.split("Urteilstext")[-1] else 10
        content = json.dumps(
            {"relevant": score > 50, "apertus_score": score, "reason": "Begründung.", "key_topics": []}
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _llm(model: str = "apertus-v1-5-8b") -> tuple[ApertusTriageClient, _FakeCompletions]:
    completions = _FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    cfg = GatewayConfig(api_key="dummy", base_url="https://example.invalid", model=model)
    return ApertusTriageClient(config=cfg, client=client), completions


class PeriodAndDateTests(unittest.TestCase):
    def test_from_period_quarter_month_year(self) -> None:
        self.assertEqual(DateRange.from_period("2026-Q2"), DateRange(date(2026, 4, 1), date(2026, 6, 30)))
        self.assertEqual(DateRange.from_period("2026q1").until, date(2026, 3, 31))
        self.assertEqual(DateRange.from_period("2024-02").until, date(2024, 2, 29))
        self.assertEqual(DateRange.from_period("2026").since, date(2026, 1, 1))
        with self.assertRaises(ValueError):
            DateRange.from_period("last 3 months")

    def test_label(self) -> None:
        self.assertEqual(DateRange.from_period("2026-Q2").label(), "q2_2026")
        self.assertEqual(DateRange(date(2026, 4, 1), date(2026, 4, 30)).label(), "2026-04-01_2026-04-30")

    def test_french_november_and_reference_dates(self) -> None:
        self.assertEqual(parse_date_string("Arrêt du TF 8C_129/2025 du 11 novembre 2025"), date(2025, 11, 11))
        cases = load_reference_corpus("judgements")
        self.assertTrue(all(c.decision_date is not None for c in cases))

    def test_decision_date_preferred_over_date(self) -> None:
        item = _normalize_crawl_dict(
            {"case_id": "8C_9/2026", "decision_date": "2026-05-02", "date": "2026-07-01", "text": "x"},
            fallback_id="x",
        )
        pipe = TriagePipeline(reference_cases=REFS, date_range=DateRange.from_period("2026-Q2"))
        self.assertEqual(len(pipe.score_items([item])), 1)

    def test_cli_requires_explicit_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            path.write_text(json.dumps({"case_id": "8C_9/2026", "text": "x"}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                cli_main(["score", "--input", str(path), "--no-llm", "--no-cache", "--no-export"])


class CrawlLayoutTests(unittest.TestCase):
    def test_loads_crawler_month_folders_and_skips_index(self) -> None:
        from hack_apertus_coffee_exe.triage.pipeline import load_crawl_items

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "_index.json").write_text(json.dumps({"days": {"2026-05-20": {}}}))
            (root / "2026-05").mkdir()
            (root / "2026-05" / "2026-05-20_8C_10-2026.json").write_text(
                json.dumps({"case_id": "8C_10/2026", "decision_date": "2026-05-20", "text": "Suva"})
            )
            items = load_crawl_items(root)
        self.assertEqual([i.case_id for i in items], ["8C_10/2026"])


class LeakageTests(unittest.TestCase):
    def test_references_from_period_or_scored_set_are_excluded(self) -> None:
        pipe = TriagePipeline(reference_cases=REFS, date_range=DateRange.from_period("2026-Q2"))
        active = pipe.active_reference_indices({"8C_3/2025"})
        self.assertEqual([REFS[i].case_id for i in active], ["8C_1/2025"])
        self.assertEqual(sorted(pipe.last_excluded_references), ["8C_2/2026", "8C_3/2025"])

    def test_excluded_reference_never_appears_in_similar_cases(self) -> None:
        pipe = TriagePipeline(reference_cases=REFS, date_range=DateRange.from_period("2026-Q2"))
        results = pipe.score_items([_item("8C_10/2026", "2026-05-20", "Berufskrankheit Lärmschwerhörigkeit Suva. ")])
        ids = {m["case_id"] for m in results[0].similar_cases}
        self.assertNotIn("8C_2/2026", ids)


class ScoreRankAmpelTests(unittest.TestCase):
    def test_both_scores_rank_ampel_and_aliases(self) -> None:
        llm, _ = _llm()
        pipe = TriagePipeline(reference_cases=REFS, llm_client=llm, date_range=DateRange.from_period("2026-Q2"))
        results = pipe.score_items(
            [_item("5A_1/2026", "2026-04-02", DIVORCE_TEXT), _item("8C_10/2026", "2026-05-20", ACCIDENT_TEXT)]
        )
        top, bottom = results
        self.assertEqual((top.case_id, top.rank, bottom.rank), ("8C_10/2026", 1, 2))
        self.assertAlmostEqual(top.final_score, round(0.75 * 85 + 0.25 * top.cosine_score, 2), places=2)
        self.assertAlmostEqual(top.cosine_score, round(top.cosine_topk_similarity * 100, 2), places=2)
        self.assertEqual((top.ampel, top.relevant), ("gruen", True))
        self.assertEqual((bottom.ampel, bottom.relevant), ("rot", False))
        data = top.to_dict()
        self.assertEqual(data["relevance_reason"], data["reason"])
        self.assertEqual(data["similar_cases"], data["top_reference_matches"])
        self.assertEqual(data["relevance_score"], data["final_score"])

    def test_rank_by_cosine_and_thresholds_from_config(self) -> None:
        cfg = TriageConfig(rank_by="cosine_score", green_threshold=99.0, yellow_threshold=1.0)
        pipe = TriagePipeline(reference_cases=REFS, config=cfg)
        res = pipe.score_items([_item("8C_10/2026", "2026-05-20", ACCIDENT_TEXT)])[0]
        self.assertEqual(res.rank_score, res.cosine_score)
        self.assertEqual(res.ampel, "gelb")

    def test_load_config_file(self) -> None:
        self.assertEqual(load_config("configs/triage.json"), TriageConfig())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps({"green_threshold": 70}))
            cfg = load_config(path)
            self.assertEqual(cfg.green_threshold, 70)
            path.write_text(json.dumps({"green_treshold": 70}))
            with self.assertRaises(ValueError):
                load_config(path)


class PromptTests(unittest.TestCase):
    def test_raw_html_never_reaches_metadata_or_prompt(self) -> None:
        item = _normalize_crawl_dict(
            {
                "case_id": "8C_10/2026",
                "decision_date": "2026-05-20",
                "subject": "Unfallversicherung",
                "text": ACCIDENT_TEXT,
                "content_html": "<div>" + "x" * 5000 + "</div>",
                "fetch": {"text_sha256": "abc"},
            },
            fallback_id="x",
        )
        self.assertNotIn("content_html", item.metadata)
        llm, completions = _llm()
        TriagePipeline(reference_cases=REFS, llm_client=llm).score_items([item])
        user_prompt = completions.calls[0]["messages"][1]["content"]
        self.assertIn("Unfallversicherung", user_prompt)
        self.assertNotIn("xxxxx", user_prompt)
        self.assertNotIn("text_sha256", user_prompt)


class CacheTests(unittest.TestCase):
    def test_embeddings_and_decisions_are_reused(self) -> None:
        items = [_item("8C_10/2026", "2026-05-20", ACCIDENT_TEXT), _item("5A_1/2026", "2026-04-02", DIVORCE_TEXT)]
        with tempfile.TemporaryDirectory() as tmp:
            llm, completions = _llm()
            cache = TriageCache(tmp)
            first = TriagePipeline(reference_cases=REFS, llm_client=llm, cache=cache).score_items(items)
            self.assertEqual((cache.stats.embedding_misses, cache.stats.decision_misses), (2, 2))
            self.assertTrue((Path(tmp) / "_index.json").exists())
            self.assertTrue((Path(tmp) / "2026-05" / "2026-05-20_8C_10-2026.npy").exists())
            self.assertTrue((Path(tmp) / "2026-05" / "2026-05-20_8C_10-2026.json").exists())

            cache2 = TriageCache(tmp)  # fresh process: reads the index from disk
            second = TriagePipeline(reference_cases=REFS, llm_client=llm, cache=cache2).score_items(items)
            self.assertEqual((cache2.stats.embedding_hits, cache2.stats.decision_hits), (2, 2))
            self.assertEqual(len(completions.calls), 2)
            self.assertEqual([r.to_dict() for r in first], [r.to_dict() for r in second])

    def test_changed_text_or_model_recomputes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            llm, completions = _llm()
            TriagePipeline(reference_cases=REFS, llm_client=llm, cache=TriageCache(tmp)).score_items(
                [_item("8C_10/2026", "2026-05-20", ACCIDENT_TEXT)]
            )
            cache = TriageCache(tmp)
            TriagePipeline(reference_cases=REFS, llm_client=llm, cache=cache).score_items(
                [_item("8C_10/2026", "2026-05-20", ACCIDENT_TEXT + "Nachtrag. ")]
            )
            self.assertEqual((cache.stats.embedding_misses, cache.stats.decision_misses), (1, 1))

            llm70, completions70 = _llm("apertus-v1-5-70b")
            cache = TriageCache(tmp)
            TriagePipeline(reference_cases=REFS, llm_client=llm70, cache=cache).score_items(
                [_item("8C_10/2026", "2026-05-20", ACCIDENT_TEXT + "Nachtrag. ")]
            )
            self.assertEqual((cache.stats.embedding_hits, cache.stats.decision_misses), (1, 1))
            self.assertEqual(len(completions70.calls), 1)

    def test_tfidf_vocab_is_stable_across_periods(self) -> None:
        # Leakage exclusion must not refit the vocabulary, or every cached vector would be invalid.
        q1 = TriagePipeline(reference_cases=REFS, embedder=TfidfEmbedder(), date_range=DateRange.from_period("2026-Q1"))
        q2 = TriagePipeline(reference_cases=REFS, embedder=TfidfEmbedder(), date_range=DateRange.from_period("2026-Q2"))
        self.assertEqual(q1.fingerprint, q2.fingerprint)


class ExportTests(unittest.TestCase):
    def test_ranking_csv_and_recall_list(self) -> None:
        pipe = TriagePipeline(reference_cases=REFS, date_range=DateRange.from_period("2026-Q2"))
        results = pipe.score_items(
            [_item(f"8C_{i}/2026", "2026-05-20", ACCIDENT_TEXT if i % 2 else DIVORCE_TEXT) for i in range(10, 15)]
        )
        with tempfile.TemporaryDirectory() as tmp:
            ranking, recall = export_period(results, tmp, "q2_2026", top_n=3)
            self.assertEqual(ranking.name, "ranking_q2_2026.csv")
            with ranking.open(encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(tuple(rows[0]), RANKING_COLUMNS)
            self.assertEqual([r[0] for r in rows[1:]], ["1", "2", "3", "4", "5"])
            listed = recall.read_text(encoding="utf-8").strip().split(", ")
            self.assertEqual(listed, [r.case_id for r in results[:3]])
        self.assertEqual(format_recall_list(results, 0), "")


if __name__ == "__main__":
    unittest.main()
