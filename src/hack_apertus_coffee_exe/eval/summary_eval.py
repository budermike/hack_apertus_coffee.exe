"""Style analysis and evaluation of the DE/FR summaries (notebook ``notebooks/summary/01_stilanalyse``).

Data: the 55 Suva summaries in ``judgements/Export_*.html`` (DE + FR each) and the full
judgement text of each case in ``data/raw/``.

- A/B: form and language of a summary (``style_metrics``)
- C: what Suva takes from the judgement (``content_selection``)
- D: terminology DE <-> FR (``glossary_usage``, ``term_pairs``)
- E: author and original language (``Pair.author``, ``Pair.judgement_language``)
- F: generate summaries with Apertus 8B for the same 55 judgements (``generate``), compare them
  with the Suva version (``content_match``) and let Apertus 70B grade them (``judge``).
"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from hack_apertus_coffee_exe.summary.judgement import Judgement, consideration_numbers, load_judgements, operative_outcome
from hack_apertus_coffee_exe.triage.reference_corpus import ReferenceCase, load_reference_corpus

LANGS = ("de", "fr")


# --- data ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pair:
    """One Suva summary (DE + FR) with the judgement it summarises."""

    case_id: str
    author: str  # short name, e.g. "Frei"
    judgement_language: str  # de / fr / it; the title of the export is in this language
    text_de: str
    text_fr: str
    judgement: Judgement

    def text(self, lang: str) -> str:
        return self.text_de if lang == "de" else self.text_fr


def _author(ref: ReferenceCase) -> str:
    return ref.author.split(",")[0].split()[0] if ref.author else "?"


def _title_language(title: str) -> str:
    low = title.lower()
    return "fr" if low.startswith("arrêt") else "it" if low.startswith("sentenza") else "de"


def find_judgement_file(case_id: str, raw_dir: str | Path = "data/raw") -> Path | None:
    hits = sorted(Path(raw_dir).glob(f"*/*_{case_id.replace('/', '_')}.json"))
    return hits[0] if hits else None


def load_pairs(judgements_dir: str | Path = "judgements", raw_dir: str | Path = "data/raw") -> list[Pair]:
    pairs = []
    for ref in load_reference_corpus(judgements_dir):
        path = find_judgement_file(ref.case_id, raw_dir)
        if path is None or not ref.text_de.strip() or not ref.text_fr.strip():
            continue
        judgement = load_judgements(path)[0]
        pairs.append(Pair(ref.case_id, _author(ref), _title_language(ref.title), ref.text_de.strip(),
                          ref.text_fr.strip(), judgement))
    return pairs


# --- A/B: form and language -----------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?»])\s+(?=[A-ZÄÖÜÀÂÉÈÊÎÔÛÇ«(])")
_CONSID = {"de": re.compile(r"\b(?:E|Erw)\.\s*\d"), "fr": re.compile(r"\bconsid\.\s*\d")}
_ARTICLE = re.compile(r"\b[Aa]rt\.\s*\d+")
_PERSON = re.compile(r"\b(?:Versichert|versichert|Beschwerdeführ|Arbeitnehmer|assuré|recourant|travailleu|employé)", re.I)
_VERDICT_OPENING = re.compile(r"^(?:In diesem (?:Urteil|Fall|Entscheid)|Dans (?:cet arrêt|cette affaire|ce cas))", re.I)
_DATE_OPENING = re.compile(r"^(?:Am|Le|En|Im)\s+\d|^\d")
_OUTCOME_WORDS = re.compile(r"abgewiesen|gutgeheissen|zurückgewiesen|nicht eingetreten|wies\b[^.]*\b(?:ab|zurück)\b|"
                            r"hiess\b[^.]*\bgut\b|rejet|admis|renvo|irrecevable", re.I)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(" ".join(text.split())) if s.strip()]


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def opening_type(text: str) -> str:
    """How the summary starts: 'urteil' (In diesem Urteil …), 'fall' (person / date / event) or 'regel'."""
    first = (sentences(text) or [""])[0]
    if _VERDICT_OPENING.search(first):
        return "urteil"
    if _DATE_OPENING.search(first) or _PERSON.search(" ".join(first.split()[:8])):
        return "fall"
    return "regel"


def style_metrics(text: str, lang: str) -> dict[str, Any]:
    sents = sentences(text)
    words = len(text.split())
    paras = paragraphs(text)
    return {
        "woerter": words,
        "absaetze": len(paras),
        "saetze": len(sents),
        "woerter_pro_satz": words / max(len(sents), 1),
        "erwaegungshinweise": len(_CONSID[lang].findall(text)),
        "artikelzitate": len(_ARTICLE.findall(text)),
        "einstieg": opening_type(text),
        "ergebnis_im_schluss": bool(_OUTCOME_WORDS.search(paras[-1])) if paras else False,
        "ergebnis_irgendwo": bool(_OUTCOME_WORDS.search(text)),
        "nennt_suva": "Suva" in text,
        "cavete": "cavete" in text.lower(),
    }


def style_table(pairs: Sequence[Pair]) -> pd.DataFrame:
    rows = []
    for p in pairs:
        for lang in LANGS:
            rows.append({"case_id": p.case_id, "sprache": lang, "autor": p.author,
                         "urteilssprache": p.judgement_language,
                         "original": lang == p.judgement_language, **style_metrics(p.text(lang), lang)})
    return pd.DataFrame(rows)


def first_words(texts: Sequence[str], n: int = 3) -> pd.Series:
    return pd.Series([" ".join(t.split()[:n]) for t in texts]).value_counts()


# --- C: what Suva takes from the judgement --------------------------------------------------

_NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s", re.MULTILINE)


def judgement_segments(text: str) -> list[tuple[str, str]]:
    """Judgement split into ('sachverhalt', text), ('E <top-level number>', text) and ('dispositiv', text)."""
    from hack_apertus_coffee_exe.summary.judgement import _CONSIDERATIONS_RE, _operative_match

    cons = _CONSIDERATIONS_RE.search(text)
    oper = _operative_match(text)
    start = cons.end() if cons else 0
    end = oper.start() if oper and oper.start() > start else len(text)
    segs: list[tuple[str, str]] = [("sachverhalt", text[:start])] if start else []
    body = text[start:end]
    marks = list(_NUMBERED.finditer(body))
    for i, m in enumerate(marks):
        chunk = body[m.start(): marks[i + 1].start() if i + 1 < len(marks) else len(body)]
        segs.append((f"E {m.group(1).split('.')[0]}", chunk))
    if end < len(text):
        segs.append(("dispositiv", text[end:]))
    return [(label, chunk) for label, chunk in segs if len(chunk.split()) >= 5]


def content_selection(pair: Pair) -> dict[str, Any] | None:
    """Map every sentence of the summary (in the judgement's language) to its most similar judgement part."""
    from hack_apertus_coffee_exe.triage.embeddings import TfidfEmbedder

    lang = pair.judgement_language
    if lang not in LANGS:
        return None
    summary = pair.text(lang)
    segs = judgement_segments(pair.judgement.text)
    if not segs:
        return None
    sents = sentences(summary)
    emb = TfidfEmbedder().fit([c for _, c in segs] + sents)
    seg_vecs, sent_vecs = emb.embed([c for _, c in segs]), emb.embed(sents)
    best = (sent_vecs @ seg_vecs.T).argmax(axis=1)
    labels = [segs[i][0] for i in best]
    tops = sorted({int(l.split()[1]) for l in labels if l.startswith("E ")})
    all_tops = sorted({int(l.split()[1]) for l, _ in segs if l.startswith("E ")})
    return {
        "case_id": pair.case_id,
        "verdichtung": len(summary.split()) / max(len(pair.judgement.text.split()), 1),
        "anteil_sachverhalt": labels.count("sachverhalt") / len(labels),
        "anteil_erwaegungen": sum(l.startswith("E ") for l in labels) / len(labels),
        "anteil_dispositiv": labels.count("dispositiv") / len(labels),
        "erwaegungen_genutzt": len(tops),
        "erwaegungen_total": len(all_tops),
        "position_genutzt": float(np.mean([all_tops.index(t) / max(len(all_tops) - 1, 1) for t in tops])) if tops else np.nan,
    }


# --- D: terminology ---------------------------------------------------------------------------


def glossary_rows(path: str | Path = "prompts/glossary.md") -> list[tuple[str, str]]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] and not set(cells[0]) <= set("-") and cells[0] != "Deutsch":
            rows.append((cells[0], cells[1]))
    return rows


def _variants(cell: str) -> list[str]:
    """'Beschwerde gutheissen / teilweise gutheissen' -> stems to look for in a text."""
    cell = re.sub(r"\(.*?\)|«.*?»|…", " ", cell)
    out = []
    for part in re.split(r"[/,;]", cell):
        words = [w for w in re.findall(r"[\wÀ-ÿ'’-]+", part) if len(w) > 2]
        if words:
            out.append(" ".join(words[:3]).lower())
    return out


def glossary_usage(pairs: Sequence[Pair], path: str | Path = "prompts/glossary.md") -> pd.DataFrame:
    """For every glossary row: in how many Suva texts the DE / FR term occurs (stem match)."""
    de = [p.text_de.lower() for p in pairs]
    fr = [p.text_fr.lower() for p in pairs]

    def pattern(word: str) -> re.Pattern[str]:
        # abbreviations as whole words ("lai" must not match "laisser"); longer words by their stem,
        # so inflected forms count too ("Taggeld" -> "Taggeldern")
        if len(word) <= 4:
            return re.compile(rf"\b{re.escape(word)}\b")
        return re.compile(rf"\b{re.escape(word[: max(4, len(word) - 3)])}")

    def hits(variants: list[str], texts: list[str]) -> int:
        pats = [[pattern(w) for w in v.split()] for v in variants]
        return sum(any(all(p.search(t) for p in ps) for ps in pats) for t in texts)

    rows = [{"deutsch": d, "francais": f, "texte_de": hits(_variants(d), de), "textes_fr": hits(_variants(f), fr)}
            for d, f in glossary_rows(path)]
    return pd.DataFrame(rows)


_STOP = set("""der die das den dem des ein eine einer eines einem und oder zu im in am an auf aus bei mit nach
von vor für über unter durch nicht sich sie er es wird wurde werden hat hatte haben ist sind war waren dass
als auch nur noch so wie wenn ob bzw sowie diese dieser dieses diesem einen keine kein le la les l un une des
du de d et ou à au aux en dans par pour sur avec sans ne pas que qui qu se sa son ses il elle a été est sont
ce cette ces lui leur leurs y être avoir était""".split())


def frequent_terms(texts: Sequence[str], top: int = 40, min_docs: int = 4) -> pd.Series:
    """Content words / bigrams that occur in at least ``min_docs`` texts, by number of texts."""
    counts: dict[str, int] = {}
    for t in texts:
        toks = [w for w in re.findall(r"[A-Za-zÀ-ÿÄÖÜäöüß'’-]{3,}", t.lower()) if w not in _STOP]
        grams = set(toks) | {f"{a} {b}" for a, b in zip(toks, toks[1:])}
        for g in grams:
            counts[g] = counts.get(g, 0) + 1
    s = pd.Series(counts)
    return s[s >= min_docs].sort_values(ascending=False).head(top)


def term_pairs(pairs: Sequence[Pair], de_terms: Sequence[str], fr_terms: Sequence[str]) -> pd.DataFrame:
    """Best FR partner for each DE term by co-occurrence in the same Suva pair (Dice coefficient)."""
    de = [p.text_de.lower() for p in pairs]
    fr = [p.text_fr.lower() for p in pairs]
    rows = []
    for d in de_terms:
        a = np.array([d in t for t in de])
        best = max(((2 * (a & (b := np.array([f in t for t in fr]))).sum() / max(a.sum() + b.sum(), 1), f)
                    for f in fr_terms), default=(0.0, ""))
        rows.append({"deutsch": d, "francais": best[1], "dice": round(float(best[0]), 2), "texte": int(a.sum())})
    return pd.DataFrame(rows).sort_values("dice", ascending=False)


# --- style closeness without a model ------------------------------------------------------------


def style_closeness(generated: str, suva: str, lang: str) -> dict[str, float]:
    """How close a generated text is to its Suva version in form, 0..1 per aspect (1 = same).

    Rule-based on purpose: a second view next to the 70B judge, which might prefer 70B texts.
    """
    from hack_apertus_coffee_exe.triage.embeddings import TfidfEmbedder

    g, s = style_metrics(generated, lang), style_metrics(suva, lang)
    ratio = max(g["woerter"], 1) / max(s["woerter"], 1)
    emb = TfidfEmbedder().fit([generated, suva])
    a, b = emb.embed([generated, suva])
    parts = {
        "laenge": max(0.0, 1 - abs(np.log2(ratio))),           # 1 = same length, 0 = half or double
        "absaetze": 1.0 if abs(g["absaetze"] - s["absaetze"]) <= 1 else 0.0,
        "einstieg": float(g["einstieg"] == s["einstieg"]),
        "ergebnis_schluss": float(g["ergebnis_im_schluss"] == s["ergebnis_im_schluss"]),
        "zitierweise": float((g["erwaegungshinweise"] > 0) == (s["erwaegungshinweise"] > 0)),
        "wortwahl": float(a @ b),                                # TF-IDF word + char n-gram cosine
    }
    parts["stilnaehe"] = float(np.mean(list(parts.values())))
    return parts


# --- F: comparison with the Suva version ------------------------------------------------------


def _articles(text: str) -> set[tuple[str, str]]:
    from hack_apertus_coffee_exe.summary.check import cited_articles
    return cited_articles(text)


def _considerations(text: str) -> set[str]:
    from hack_apertus_coffee_exe.summary.check import cited_considerations
    return cited_considerations(text)


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def content_match(suva: str, generated: str, pair: Pair, lang: str) -> dict[str, Any]:
    """Rule-based comparison: outcome, cited articles and considerations, length."""
    outcome = operative_outcome(pair.judgement.text)
    gen_arts, suva_arts = _articles(generated), _articles(suva)
    known = consideration_numbers(pair.judgement.text)
    gen_cons = _considerations(generated)
    return {
        "laenge_verhaeltnis": len(generated.split()) / max(len(suva.split()), 1),
        "artikel_jaccard": _jaccard(gen_arts, suva_arts),
        "artikel_suva_gefunden": len(gen_arts & suva_arts) / len(suva_arts) if suva_arts else np.nan,
        "erwaegungen_erfunden": len(gen_cons - known),
        "ergebnis_genannt": bool(_OUTCOME_WORDS.search(generated)),
        "ergebnis_dispositiv": outcome.result or "nicht erkannt",
    }


JUDGE_SYSTEM = """Du bist erfahrene Juristin im Rechtsdienst der Suva und prüfst automatisch erstellte Zusammenfassungen \
von Bundesgerichtsurteilen. Du vergleichst einen KANDIDATEN mit der REFERENZ, die eine Suva-Juristin geschrieben hat, \
in derselben Sprache. Bewerte streng und nur anhand der Texte.

Kriterien, je 1 (schlecht) bis 5 (sehr gut):
- kernaussage: Gibt der Kandidat dieselbe Rechtsfrage und Kernregel wieder wie die Referenz?
- ergebnis: Stimmt das Ergebnis (Dispositiv) mit dem genannten Ergebnis überein, ohne Widerspruch zur Referenz? 5 auch dann, wenn beide das Ergebnis nicht nennen.
- treue: Enthält der Kandidat keine Aussagen, die der Referenz oder dem Ergebnis widersprechen oder erfunden wirken?
- stil: Entspricht er der Referenz in Aufbau, Länge, Ton und Verdichtung (Suva-Stil, keine Aufzählungen)?
- sprache: Korrekte juristische Fachsprache und Terminologie der Zielsprache (Schweizer Recht), natürlich formuliert, keine Übersetzungsfehler?
- gesamt: Könnte der Kandidat die Referenz in der Suva-Praxis ersetzen?

"begruendung": höchstens 3 Sätze. Antworte nur mit kompaktem JSON ohne Leerzeilen."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        **{k: {"type": "integer", "minimum": 1, "maximum": 5}
           for k in ("kernaussage", "ergebnis", "treue", "stil", "sprache", "gesamt")},
        "begruendung": {"type": "string"},
    },
    "required": ["kernaussage", "ergebnis", "treue", "stil", "sprache", "gesamt", "begruendung"],
    "additionalProperties": False,
}
JUDGE_CRITERIA = ("kernaussage", "ergebnis", "treue", "stil", "sprache", "gesamt")


def judge(
    items: Sequence[dict[str, Any]],
    out_path: str | Path,
    *,
    model: str = "apertus-v1-5-70b",
    workers: int = 6,
    client: Any = None,
) -> JsonlCache:
    """Apertus 70B grades each candidate against the Suva reference (fixed rubric, JSON schema, temperature 0).

    ``items``: dicts with ``key``, ``lang``, ``reference``, ``candidate``, ``outcome``.
    """
    from hack_apertus_coffee_exe.triage.llm import GatewayConfig, _build_client

    cache = JsonlCache(out_path)
    cfg = GatewayConfig.from_env(model=model)
    client = client or _build_client(cfg, timeout=180, max_retries=2)
    fmt = {"type": "json_schema", "json_schema": {"name": "bewertung", "strict": True, "schema": JUDGE_SCHEMA}}

    def one(item: dict[str, Any]) -> None:
        user = (f"Sprache: {item['lang']}\nParteien und Ergebnis laut Urteil (gesichert):\n{item['outcome']}\n\n"
                f"REFERENZ (Suva):\n{item['reference']}\n\nKANDIDAT:\n{item['candidate']}")
        error = "no answer"
        # Second try with a little temperature: a greedy decoding that loops (e.g. endless blank
        # lines) would otherwise fail the same way again.
        for temperature in (0.0, 0.3):
            try:
                resp = client.chat.completions.create(
                    model=cfg.model, temperature=temperature, seed=7, max_tokens=1200, response_format=fmt,
                    messages=[{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}])
                answer = json.loads(resp.choices[0].message.content)
                cache.add({"key": item["key"], **answer, "judge_model": cfg.model})
                return
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:300]
        cache.add({"key": item["key"], "error": error})

    todo = [i for i in items if i["key"] not in cache.rows or "error" in cache.rows[i["key"]]]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, todo))
    return cache


def judge_items(pairs: Sequence[Pair], generated: dict[str, dict[str, Any]], version: str) -> list[dict[str, Any]]:
    """Judge inputs for one generated version (key = version|case|lang)."""
    items = []
    for p in pairs:
        row = generated.get(p.case_id)
        if not row or "error" in row:
            continue
        from hack_apertus_coffee_exe.summary.judgement import outcome_block

        # who appealed and who won, parsed from the judgement: without it the judge itself
        # misread "Beschwerde abgewiesen" (e.g. 8C_334/2025, where the Suva appealed and won)
        outcome = outcome_block(p.judgement, "de")
        for lang in LANGS:
            items.append({"key": f"{version}|{p.case_id}|{lang}", "lang": lang, "reference": p.text(lang),
                          "candidate": row[lang], "outcome": outcome})
    return items


# --- F: generation with Apertus 8B -----------------------------------------------------------


class JsonlCache:
    """Append-only JSONL file keyed by ``key``; reruns skip what is already there."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.rows: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.rows[row["key"]] = row

    def add(self, row: dict[str, Any]) -> None:
        with self._lock:
            self.rows[row["key"]] = row
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def dev_split(pairs: Sequence[Pair], n: int = 20, seed: int = 0) -> tuple[list[Pair], list[Pair]]:
    """Fixed random split: ``n`` development cases to iterate the prompt on, the rest as held-out test."""
    order = np.random.default_rng(seed).permutation(len(pairs))
    dev = {pairs[i].case_id for i in order[:n]}
    return [p for p in pairs if p.case_id in dev], [p for p in pairs if p.case_id not in dev]


def generate(
    pairs: Sequence[Pair],
    out_path: str | Path,
    *,
    prompts_dir: str | Path = "prompts/v1",
    model: str = "apertus-v1-5-8b",
    workers: int = 4,
    make_summarizer: Callable[[], Any] | None = None,
) -> JsonlCache:
    """Summarise every judgement with the production pipeline (``summary.generate.Summarizer``).

    The judgement's own Suva summary is never a few-shot example (``ExampleSelector``
    excludes the case), so this is a leave-one-out run. Failures are stored with ``error``.
    """
    cache = JsonlCache(out_path)
    if make_summarizer is None:
        def make_summarizer() -> Any:
            from hack_apertus_coffee_exe.summary.examples import ExampleSelector
            from hack_apertus_coffee_exe.summary.generate import Summarizer
            from hack_apertus_coffee_exe.summary.llm import SummaryLLM
            from hack_apertus_coffee_exe.triage.llm import GatewayConfig

            return Summarizer(SummaryLLM(config=GatewayConfig.from_env(model=model)), ExampleSelector(),
                              prompts_dir=Path(prompts_dir))
    summarizer = make_summarizer()

    def one(pair: Pair) -> None:
        try:
            result = summarizer.summarize(pair.judgement)
            cache.add({"key": pair.case_id, "case_id": pair.case_id, "de": result.summary_de, "fr": result.summary_fr,
                       "examples": result.examples, "model": result.model, "prompt_version": result.prompt_version,
                       "attempts": result.attempts, "facts": result.facts})
        except Exception as exc:  # keep going; stored so the next run retries it
            cache.add({"key": pair.case_id, "case_id": pair.case_id, "error": f"{type(exc).__name__}: {exc}"[:300]})

    todo = [p for p in pairs if p.case_id not in cache.rows or "error" in cache.rows[p.case_id]]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, todo))
    return cache


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Generate summaries for the 55 Suva cases (leave-one-out)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--prompts-dir", default="prompts/v1")
    parser.add_argument("--model", default="apertus-v1-5-8b")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--pipeline", choices=["production", "v3", "v4"], default="production",
                        help="production = summary.generate.SummarizerV1 (old, e.g. --prompts-dir prompts/v1); v3 = summary.generate.Summarizer (prompts/); v4 = eval.summary_v3")
    parser.add_argument("--subset", choices=["all", "dev", "test"], default="all")
    parser.add_argument("--analysis-model", default=None, help="v3 only: model for step 1 (core question); default --model")
    parser.add_argument("--judge", metavar="VERSION", default=None,
                        help="grade the summaries in --out with Apertus 70B instead of generating (writes --judge-out)")
    parser.add_argument("--judge-out", default=None)
    args = parser.parse_args(argv)
    load_dotenv()
    if args.judge:
        generated = {k: r for k, r in JsonlCache(args.out).rows.items() if "error" not in r}
        pairs = load_pairs()
        if args.subset != "all":
            dev, test = dev_split(pairs)
            pairs = dev if args.subset == "dev" else test
        judged = judge(judge_items(pairs, generated, args.judge), args.judge_out, workers=args.workers)
        failed = sum("error" in r for r in judged.rows.values())
        print(f"{len(judged.rows) - failed} graded, {failed} failed -> {args.judge_out}")
        return 0
    pairs = load_pairs()
    if args.subset != "all":
        dev, test = dev_split(pairs)
        pairs = dev if args.subset == "dev" else test
    from hack_apertus_coffee_exe.eval.summary_v3 import BudgetLLM
    from hack_apertus_coffee_exe.summary.llm import SummaryLLM
    from hack_apertus_coffee_exe.triage.llm import GatewayConfig

    def llm(model: str) -> Any:  # thinking models automatically get 4x the token budget
        return BudgetLLM(SummaryLLM(config=GatewayConfig.from_env(model=model), timeout=600))

    def make() -> Any:
        if args.pipeline == "production":
            from hack_apertus_coffee_exe.summary.examples import ExampleSelector
            from hack_apertus_coffee_exe.summary.generate import SummarizerV1

            return SummarizerV1(llm(args.model), ExampleSelector(), prompts_dir=Path(args.prompts_dir))
        from hack_apertus_coffee_exe.eval.summary_v3 import SummarizerV3, SummarizerV4

        cls = SummarizerV4 if args.pipeline == "v4" else SummarizerV3
        return cls(llm(args.model), prompts_dir=Path(args.prompts_dir),
                   analysis_llm=llm(args.analysis_model) if args.analysis_model else None)
    cache = generate(pairs, args.out, prompts_dir=args.prompts_dir, model=args.model, workers=args.workers,
                     make_summarizer=make)
    ok = sum("error" not in r for r in cache.rows.values())
    print(f"{ok} of {len(cache.rows)} summarised -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
