"""How well does the DE <-> FR cross-check find disagreements? Needs the Apertus gateway.

    python -m hack_apertus_coffee_exe.eval.crosscheck_eval [--n 10] [--model apertus-v1-5-70b]

Material: Suva's own DE/FR summary pairs (``judgements/``), written by people and taken as
consistent. Each pair is checked as it is (a finding is a false alarm) and with the French text
falsified in one point (a finding is a hit):

- ``outcome``: the outcome word flipped (rejeté <-> admis),
- ``number``: the first percentage or amount changed,
- ``year``: the first year changed by one,
- ``party``: «la Suva» and «l'assuré(e)» swapped once.

Only the detection is measured (rule-based numbers + the two Apertus reviewer passes,
``summary.crosscheck``); no referee, no rewrite. Writes ``results/tables/crosscheck_eval.csv``
(case number, variant, findings; no text of the summaries, which are partner data) and
``results/reports/crosscheck_eval.md``.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from hack_apertus_coffee_exe.summary import crosscheck as cc

_OUTCOME_SWAPS = [
    (r"\brejeté(e?s?)\b", r"admis\1"), (r"\badmis(e?s?)\b", r"rejeté\1"),
    (r"\brejette\b", "admet"), (r"\badmet\b", "rejette"),
]


def _flip_outcome(text: str) -> str | None:
    for pattern, repl in _OUTCOME_SWAPS:
        new, n = re.subn(pattern, repl, text, count=1)
        if n:
            return new
    return None


def _change_number(text: str) -> str | None:
    m = re.search(r"\b(\d+)\s?%", text) or re.search(r"\b(\d[\d'’ ]*\d|\d)\s?(?:francs?|fr\.|CHF)", text, re.I)
    if not m:
        return None
    raw = m.group(1)
    value = int(re.sub(r"\D", "", raw))
    return text[: m.start(1)] + str(value + 5 if value < 100 else value * 2) + text[m.end(1):]


def _change_year(text: str) -> str | None:
    m = re.search(r"\b(19|20)(\d{2})\b", text)
    return None if not m else text[: m.start()] + str(int(m.group()) + 1) + text[m.end():]


def _swap_party(text: str) -> str | None:
    m_suva, m_ins = re.search(r"\bla Suva\b", text), re.search(r"\bl[’']assuré(e?)\b", text)
    if not (m_suva and m_ins):
        return None
    ins = m_ins.group()
    out = text.replace(m_suva.group(), "\x00", 1).replace(ins, "la Suva", 1)
    return out.replace("\x00", ins, 1)


PERTURBATIONS: dict[str, Callable[[str], str | None]] = {
    "outcome": _flip_outcome, "number": _change_number, "year": _change_year, "party": _swap_party,
}


def detected(findings: list[cc.Finding]) -> bool:
    """What the pipeline would act on: a serious finding confirmed by both passes or by the numbers."""
    return any(f.serious and f.confirmed for f in findings)


def evaluate(pairs: list[tuple[str, str, str]], call: cc.JsonCall, workers: int = 4) -> list[dict[str, Any]]:
    jobs = []
    for case_id, de, fr in pairs:
        jobs.append((case_id, "original", de, fr))
        for name, perturb in PERTURBATIONS.items():
            changed = perturb(fr)
            if changed and changed != fr:
                jobs.append((case_id, name, de, changed))

    def one(job):
        case_id, variant, de, fr = job
        try:
            findings = cc.number_conflicts(de, fr) + cc.review(call, de, fr)
            return {"case_id": case_id, "variant": variant, "detected": detected(findings), "error": "",
                    "findings": "; ".join(f"{f.kind}/{f.topic}{'!' if f.serious else ''}{'+' if f.confirmed else ''}" for f in findings)}
        except Exception as exc:
            return {"case_id": case_id, "variant": variant, "detected": None, "error": type(exc).__name__, "findings": ""}

    with ThreadPoolExecutor(max(1, workers)) as pool:
        return list(pool.map(one, jobs))


def render(rows: list[dict[str, Any]], model: str) -> str:
    ok = [r for r in rows if r["detected"] is not None]
    lines = [
        "# Evaluation DE/FR-Abgleich",
        "",
        "Erzeugt mit `python -m hack_apertus_coffee_exe.eval.crosscheck_eval`. Nicht von Hand ändern.",
        "",
        f"Modell: `{model}`. Material: Suvas eigene DE/FR-Zusammenfassungen (als übereinstimmend angenommen), "
        "unverändert und mit einer verfälschten französischen Fassung. «Erkannt» = ein schwerer Befund, den beide "
        "Prüfdurchgänge oder der Zahlenvergleich bestätigen (darauf reagiert die Pipeline).",
        "",
        "| Fassung | Paare | erkannt | Anteil |",
        "|---|---|---|---|",
    ]
    for variant in ("original", *PERTURBATIONS):
        group = [r for r in ok if r["variant"] == variant]
        if not group:
            continue
        hits = sum(bool(r["detected"]) for r in group)
        label = "unverändert (Fehlalarm)" if variant == "original" else f"verfälscht: {variant} (Treffer)"
        lines.append(f"| {label} | {len(group)} | {hits} | {hits / len(group):.0%} |")
    failed = [r for r in rows if r["detected"] is None]
    lines += [
        "",
        f"Fehlgeschlagene Prüfungen (Gateway): {len(failed)}.",
        "",
        "## Was diese Zahlen nicht zeigen",
        "",
        "- Die Verfälschungen sind einfach und regelbasiert; echte Abweichungen zwischen generierten Texten sind oft "
        "subtiler (eine Begründung, die fehlt; eine Regel, die anders gewichtet wird).",
        "- Suvas Paare sind nicht garantiert deckungsgleich: ein «Fehlalarm» kann eine echte kleine Abweichung sein.",
        "- Wenige Paare; ein bis zwei Fälle mehr oder weniger sind Zufall.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=10, help="Suva pairs (random sample, fixed seed)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--model", default=None, help="Default: $SUMMARY_MODEL or apertus-v1-5-70b")
    parser.add_argument("--judgements-dir", default="judgements")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", default="results/tables/crosscheck_eval.csv")
    parser.add_argument("--report", default="results/reports/crosscheck_eval.md")
    args = parser.parse_args(argv)

    import os

    from dotenv import load_dotenv

    from hack_apertus_coffee_exe.summary.llm import SummaryLLM
    from hack_apertus_coffee_exe.summary.run import DEFAULT_SUMMARY_MODEL, SUMMARY_MODEL_ENV
    from hack_apertus_coffee_exe.triage.llm import GatewayConfig
    from hack_apertus_coffee_exe.triage.reference_corpus import load_reference_corpus

    load_dotenv(Path(".env"))
    model = args.model or os.environ.get(SUMMARY_MODEL_ENV) or DEFAULT_SUMMARY_MODEL
    refs = [r for r in load_reference_corpus(args.judgements_dir) if r.text_de.strip() and r.text_fr.strip()]
    random.Random(args.seed).shuffle(refs)
    pairs = [(r.case_id, r.text_de, r.text_fr) for r in refs[: args.n]]
    llm = SummaryLLM(config=GatewayConfig.from_env(model=model))
    rows = evaluate(pairs, cc.gateway_json_call(llm), args.workers)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "variant", "detected", "error", "findings"])
        writer.writeheader()
        writer.writerows(rows)
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render(rows, model), encoding="utf-8")
    print(render(rows, model))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
