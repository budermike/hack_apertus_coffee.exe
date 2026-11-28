"""Submission export for Suva (issue #23): recall lists and the top summaries in DE and FR.

    python -m hack_apertus_coffee_exe.present.submission                 # from what is stored
    python -m hack_apertus_coffee_exe.present.submission --generate      # also (re)generate, needs the gateway

What Suva grades (``challenge_description.md``, evaluation criteria):

1. "your 30 high scored relevant court judgements": ``recall_top30_<label>.txt``, the 30
   best-ranked judgements in Suva's format (``8C_691/2024, 9C_612/2025, ...``);
   ``recall_all_<label>.txt`` has every judgement on the list ("you didn't miss anything").
2. "your top 10 highly relevant summaries" (against a hold-out set): the ``n`` best-ranked
   judgements of ``month`` with their summary in German and French,
   ``summaries_top<n>_<month>.md`` (to read) and ``.json`` (complete).

Which summary goes in, per judgement and language:

- a version a lawyer edited and approved (``status = approved``): reviewed by a person;
- else the generated one, if it comes from the current pipeline (model and prompt version);
- else, with ``--generate``, a new one from the current pipeline. It is written only into the
  export (the summaries DB is append-only and keeps the lawyers' work untouched) and reused
  by the next run;
- else the outdated one, marked as such in the export and on the command line.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

LANGUAGES = ("de", "fr")
GREEN = "gruen"


# ---------------------------------------------------------------- recall lists


def ranked_green(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Judgements on the Suva list, best rank first."""
    green = [r for r in rows if r.get("ampel") == GREEN and r.get("case_id")]
    return sorted(green, key=lambda r: (r.get("rank") if isinstance(r.get("rank"), int) else 10**9, -(r.get("final_score") or 0)))


def suva_format(rows: Sequence[dict[str, Any]]) -> str:
    """``8C_691/2024, 9C_612/2025, ...``"""
    return ", ".join(r["case_id"] for r in rows)


def top_of_month(rows: Sequence[dict[str, Any]], month: str, n: int) -> list[dict[str, Any]]:
    """The ``n`` best-ranked judgements on the list decided in ``month`` (``YYYY-MM``)."""
    return [r for r in ranked_green(rows) if str(r.get("judgement_date") or "").startswith(month)][:n]


# ---------------------------------------------------------------- summaries


def current_pipeline(model: str | None = None) -> tuple[str, str]:
    """``(model, prompt version)`` a summary written now would carry."""
    import os

    from ..summary import prompts
    from ..summary.run import DEFAULT_SUMMARY_MODEL, SUMMARY_MODEL_ENV

    return model or os.environ.get(SUMMARY_MODEL_ENV) or DEFAULT_SUMMARY_MODEL, prompts.prompt_files_version()


def stored_entry(conn: Any, case_id: str, current: tuple[str, str]) -> dict[str, Any]:
    """What the summaries DB holds for one judgement, per language, and whether it is usable as it is."""
    from ..summary import store

    out: dict[str, Any] = {}
    for lang in LANGUAGES:
        s = store.get_display(conn, case_id, lang) if conn is not None else None
        if s is None:
            out[lang] = None
            continue
        reviewed = s.status == "approved"
        out[lang] = {
            "text": s.text, "title": s.title, "model": s.model, "prompt_version": s.prompt_version,
            "status": s.status, "coherence_ok": s.coherence_ok,
            "issues": [i for i in (s.issues or []) if i.get("severity") == "error"],
            "source": "lawyer" if reviewed else "stored",
            "outdated": not reviewed and (s.model, s.prompt_version) != current,
        }
    return out


def needs_generation(entry: dict[str, Any]) -> bool:
    return any(entry.get(lang) is None or entry[lang]["outdated"] for lang in LANGUAGES)


def generated_entry(result: Any) -> dict[str, Any]:
    """A ``SummaryResult`` of the current pipeline as export entry (DE and FR)."""
    from ..summary.run import to_generated

    out = {}
    for lang, g in to_generated(result).items():
        out[lang] = {
            "text": g.text, "title": g.title, "model": g.model, "prompt_version": g.prompt_version,
            "status": "generated", "coherence_ok": g.coherence_ok,
            "issues": [i for i in g.issues if i.get("severity") == "error"],
            "source": "generated for the export", "outdated": False,
        }
    return out


def build(
    rows: Sequence[dict[str, Any]],
    month: str,
    n: int,
    conn: Any,
    current: tuple[str, str],
    previous: dict[str, dict[str, Any]] | None = None,
    generate: Callable[[str], Any] | None = None,
) -> list[dict[str, Any]]:
    """Export entries for the top ``n`` of ``month``.

    ``previous`` are the entries of an earlier export (reused if from the current pipeline);
    ``generate(case_id)`` returns a ``SummaryResult`` or ``None`` (judgement not found).
    """
    entries = []
    for row in top_of_month(rows, month, n):
        cid = row["case_id"]
        rel = (row.get("metadata") or {}).get("relevance") or {}
        entry = {
            "rank": row.get("rank"), "case_id": cid, "date": row.get("judgement_date"),
            "url": (row.get("metadata") or {}).get("url"), "score": row.get("final_score"),
            "area": rel.get("area"), "reason": row.get("relevance_reason") or row.get("reason") or "",
        }
        texts = stored_entry(conn, cid, current)
        earlier = (previous or {}).get(cid) or {}
        for lang in LANGUAGES:  # an earlier export of the current pipeline beats an outdated DB version
            old = earlier.get(lang)
            if old and (old.get("model"), old.get("prompt_version")) == current and (texts[lang] is None or texts[lang]["outdated"]):
                texts[lang] = old
        if generate is not None and needs_generation(texts):
            try:
                result = generate(cid)
            except Exception as exc:  # keep the export going; the entry stays as it is
                logger.error("%s: generation failed: %s", cid, exc)
                result = None
            if result is not None:
                fresh = generated_entry(result)
                for lang in LANGUAGES:
                    if texts[lang] is None or texts[lang]["outdated"]:
                        texts[lang] = fresh[lang]
        entries.append({**entry, **texts})
    return entries


def render_markdown(entries: Sequence[dict[str, Any]], month: str, label: str, current: tuple[str, str]) -> str:
    lines = [
        f"# Urteilsradar: die {len(entries)} relevantesten Urteile {month} mit Zusammenfassung DE/FR",
        "",
        f"Rangfolge aus der Relevanz-Bewertung `{label}` (Apertus, kalibriert). Zusammenfassungen: aktuelle Pipeline "
        f"`{current[0]}`, Prompt `{current[1]}`, sofern nicht anders vermerkt. Erzeugt mit "
        "`python -m hack_apertus_coffee_exe.present.submission`.",
        "",
    ]
    for e in entries:
        lines += [f"## {e['rank']}. {e['case_id']} ({e['date']}) – Relevanz {e['score']:.1f}" if e.get("score") is not None
                  else f"## {e['rank']}. {e['case_id']} ({e['date']})", ""]
        if e.get("url"):
            lines += [f"Urteil: <{e['url']}>", ""]
        for lang, heading in (("de", "Deutsch"), ("fr", "Français")):
            s = e.get(lang)
            if not s:
                lines += [f"**{heading}:** – (keine Zusammenfassung vorhanden)", ""]
                continue
            notes = []
            if s["source"] == "lawyer":
                notes.append("von Jurist:in bearbeitet und freigegeben")
            if s["outdated"]:
                notes.append(f"VERALTET: {s['model']}, Prompt {s['prompt_version']}")
            if s["issues"]:
                notes.append("Prüfung: " + "; ".join(i.get("detail", "") for i in s["issues"]))
            lines += [f"**{heading}**" + (f" ({'; '.join(notes)})" if notes else ""), "", s["text"].strip(), ""]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- command line


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recall lists and top summaries for the Suva submission (#23)")
    parser.add_argument("--triage", default="results/triage_q2_2026.json")
    parser.add_argument("--month", default="2026-06", help="Month of the top summaries (YYYY-MM)")
    parser.add_argument("--n", type=int, default=10, help="Number of summaries (Suva grades the top 10)")
    parser.add_argument("--top", type=int, default=None, help="Length of the top list (default: recall_top_n in configs/triage.json, 30)")
    parser.add_argument("--db", default=None, help="Summaries DB (default: $SUMMARY_DB_PATH)")
    parser.add_argument("--input", default="data/raw", help="Crawl folder, for --generate")
    parser.add_argument("--model", default=None, help="Summary model (default: $SUMMARY_MODEL or Apertus 70B)")
    parser.add_argument("--out-dir", default="results/submission")
    parser.add_argument("--generate", action="store_true", help="Generate missing or outdated summaries (Apertus gateway)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from dotenv import load_dotenv

    from ..summary import store
    from ..triage.config import load_config

    load_dotenv(Path(".env"))
    rows = json.loads(Path(args.triage).read_text(encoding="utf-8"))
    label = Path(args.triage).stem.removeprefix("triage_")
    top_n = args.top if args.top is not None else load_config().recall_top_n
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    green = ranked_green(rows)
    (out / f"recall_top{top_n}_{label}.txt").write_text(suva_format(green[:top_n]) + "\n", encoding="utf-8")
    (out / f"recall_all_{label}.txt").write_text(suva_format(green) + "\n", encoding="utf-8")
    print(f"recall lists: top {min(top_n, len(green))} and all {len(green)} -> {out}", file=sys.stderr)

    current = current_pipeline(args.model)
    db_path = Path(args.db) if args.db else store.db_path_from_env()
    conn = store.connect(db_path) if db_path.exists() else None
    json_path = out / f"summaries_top{args.n}_{args.month}.json"
    previous = {}
    if json_path.exists():
        previous = {e["case_id"]: e for e in json.loads(json_path.read_text(encoding="utf-8"))}

    generate = None
    if args.generate:
        from ..summary.judgement import load_judgements, same_case
        from ..summary.run import summarizer_factory

        month_dir = Path(args.input) / args.month
        judgements = load_judgements(month_dir if month_dir.is_dir() else args.input)
        get_summarizer = summarizer_factory(current[0])

        def generate(case_id: str) -> Any:
            judgement = next((j for j in judgements if same_case(j.case_id, case_id)), None)
            if judgement is None:
                logger.error("%s: no crawl record in %s", case_id, month_dir)
                return None
            logger.info("%s: generating with %s", case_id, current[0])
            return get_summarizer().summarize(judgement)

    entries = build(rows, args.month, args.n, conn, current, previous, generate)
    json_path.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    md_path = json_path.with_suffix(".md")
    md_path.write_text(render_markdown(entries, args.month, label, current), encoding="utf-8")

    missing = sum(e.get(lang) is None for e in entries for lang in LANGUAGES)
    outdated = sum(bool(e.get(lang) and e[lang]["outdated"]) for e in entries for lang in LANGUAGES)
    lawyer = sum(bool(e.get(lang) and e[lang]["source"] == "lawyer") for e in entries for lang in LANGUAGES)
    print(f"summaries: {len(entries)} judgements -> {md_path} | missing {missing}, outdated {outdated}, "
          f"lawyer-approved {lawyer} (of {2 * len(entries)} texts)", file=sys.stderr)
    if missing or outdated:
        print("not ready for submission: run again with --generate (needs the gateway key)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
