"""Submission export for Suva (present/submission.py, #23). No network: generation is faked."""

from __future__ import annotations

from types import SimpleNamespace

from hack_apertus_coffee_exe.present import submission as sub
from hack_apertus_coffee_exe.summary import store
from hack_apertus_coffee_exe.summary.check import CheckReport

CURRENT = ("apertus-v1-5-70b", "v3-abc")


def _row(case_id, rank, day, ampel="gruen", score=80.0):
    return {"case_id": case_id, "rank": rank, "ampel": ampel, "final_score": score, "judgement_date": day,
            "metadata": {"url": f"https://bger/{case_id}", "relevance": {"area": "UV"}}, "relevance_reason": "UVG-Fall."}


ROWS = [
    _row("8C_3/2026", 3, "2026-06-03"), _row("8C_1/2026", 1, "2026-06-10"), _row("8C_2/2026", 2, "2026-05-02"),
    _row("8C_4/2026", 4, "2026-06-20"), _row("8C_9/2026", 9, "2026-06-21", ampel="gelb"),
]


def test_recall_lists_follow_the_rank_and_contain_only_the_list():
    green = sub.ranked_green(ROWS)
    assert [r["case_id"] for r in green] == ["8C_1/2026", "8C_2/2026", "8C_3/2026", "8C_4/2026"]
    assert sub.suva_format(green[:2]) == "8C_1/2026, 8C_2/2026"


def test_top_of_month_takes_the_best_ranked_of_that_month():
    assert [r["case_id"] for r in sub.top_of_month(ROWS, "2026-06", 2)] == ["8C_1/2026", "8C_3/2026"]


def _db(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    for cid in ("8C_1/2026", "8C_3/2026"):
        store.save_case(conn, case_id=cid, decision_date="2026-06-10", url=None)
    return conn


def _gen(text, model, pv):
    return store.GeneratedSummary(text=text, model=model, prompt_version=pv, coherence_ok=True)


def test_current_and_lawyer_approved_summaries_are_used_and_old_ones_are_flagged(tmp_path):
    conn = _db(tmp_path)
    for lang in ("de", "fr"):
        store.save_generated(conn, "8C_1/2026", lang, _gen(f"{lang} aktuell", *CURRENT))
        store.save_generated(conn, "8C_3/2026", lang, _gen(f"{lang} alt", "apertus-v1-5-8b", "v1-x"))
    store.save_edit(conn, "8C_3/2026", "de", "de von Juristin")
    store.approve(conn, "8C_3/2026", "de")
    entries = {e["case_id"]: e for e in sub.build(ROWS, "2026-06", 3, conn, CURRENT)}
    assert entries["8C_1/2026"]["de"]["text"] == "de aktuell" and not entries["8C_1/2026"]["de"]["outdated"]
    assert entries["8C_3/2026"]["de"]["source"] == "lawyer" and not entries["8C_3/2026"]["de"]["outdated"]
    assert entries["8C_3/2026"]["fr"]["outdated"] is True
    assert entries["8C_4/2026"]["de"] is None                      # nothing stored
    assert [e["rank"] for e in sub.build(ROWS, "2026-06", 3, conn, CURRENT)] == [1, 3, 4]


def test_generate_replaces_only_missing_or_outdated_texts_and_never_writes_the_db(tmp_path):
    conn = _db(tmp_path)
    for lang in ("de", "fr"):
        store.save_generated(conn, "8C_1/2026", lang, _gen(f"{lang} aktuell", *CURRENT))
        store.save_generated(conn, "8C_3/2026", lang, _gen(f"{lang} alt", "apertus-v1-5-8b", "v1-x"))
    asked = []

    def generate(case_id):
        asked.append(case_id)
        return SimpleNamespace(text=lambda lang: f"{lang} neu {case_id}", model=CURRENT[0], prompt_version=CURRENT[1],
                               coherence_ok=True, title_de="T", title_fr="T", facts={}, examples=[], check=CheckReport())

    entries = {e["case_id"]: e for e in sub.build(ROWS, "2026-06", 3, conn, CURRENT, generate=generate)}
    assert asked == ["8C_3/2026", "8C_4/2026"]                       # 8C_1 is current: no call
    assert entries["8C_3/2026"]["fr"]["text"] == "fr neu 8C_3/2026" and entries["8C_4/2026"]["de"]["source"] == "generated for the export"
    assert store.get_display(conn, "8C_3/2026", "fr").text == "fr alt"   # the DB keeps its own history

    # the next run reuses the export instead of asking the gateway again
    again = sub.build(ROWS, "2026-06", 3, conn, CURRENT, previous=entries, generate=generate)
    assert asked == ["8C_3/2026", "8C_4/2026"] and all(not sub.needs_generation(e) for e in again)


def test_markdown_marks_outdated_and_lawyer_texts():
    entries = [{"rank": 1, "case_id": "8C_1/2026", "date": "2026-06-10", "url": "u", "score": 81.0,
                "de": {"text": "Text", "source": "lawyer", "outdated": False, "issues": [], "model": "m", "prompt_version": "p"},
                "fr": {"text": "Texte", "source": "stored", "outdated": True, "issues": [], "model": "8b", "prompt_version": "v1"}}]
    md = sub.render_markdown(entries, "2026-06", "q2_2026", CURRENT)
    assert "von Jurist:in bearbeitet" in md and "VERALTET: 8b, Prompt v1" in md and "## 1. 8C_1/2026 (2026-06-10)" in md


def test_recall_csv_has_one_row_per_judgement_in_rank_order(tmp_path):
    import csv

    path = sub.write_recall_csv(sub.ranked_green(ROWS), tmp_path / "recall.csv")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert tuple(rows[0]) == sub.RECALL_COLUMNS
    assert [r[1] for r in rows[1:]] == ["8C_1/2026", "8C_2/2026", "8C_3/2026", "8C_4/2026"]
    assert rows[1][0] == "1" and rows[1][3] == "80.0" and rows[1][4] == "UV" and rows[1][6] == "https://bger/8C_1/2026"
