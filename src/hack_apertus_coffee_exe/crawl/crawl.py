"""Download all Federal Supreme Court (BGer) judgements of a time span into data/raw/.

Run:
    python -m hack_apertus_coffee_exe.crawl.crawl --from 2026-04-01 --to 2026-06-30
    python -m hack_apertus_coffee_exe.crawl.crawl --report        # counts per month and language

Defaults (source, URL, pause, folder, time span) come from configs/crawl.json:
    "source": "bger"            official site search.bger.ch (final target)
    "source": "entscheidsuche"  open mirror entscheidsuche.ch (for testing)

Storage, sorted by month (schema: docs/SCHEMA.md, issue #3):
    data/raw/2026-05/2026-05-15_8C_123_2026.json   one file per judgement
    data/raw/_index.json                            days that are completely crawled

Nothing is fetched twice:
    - a day listed in _index.json is not asked for again (use --refresh to re-check it)
    - a judgement whose file exists and whose SHA-256 still matches is not downloaded again
    - a damaged file is renamed to *.corrupt-<time> (never deleted) and downloaded again
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = "hack-apertus-crawler/0.1 (research prototype)"


class Blocked(Exception):
    """The site's bot protection refused the request: stop and retry later."""


def load_config(path, **overrides):
    """Read the JSON config; command line values (if not None) win.

    The settings of the chosen source (base_url, delay_seconds) are copied to the top level.
    """
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    config.update({key: value for key, value in overrides.items() if value is not None})
    config["from_date"] = date.fromisoformat(str(config["from_date"]))
    config["to_date"] = date.fromisoformat(str(config["to_date"]))
    if config["from_date"] > config["to_date"]:
        raise ValueError("from_date must not be after to_date")
    if config["source"] not in SOURCES:
        raise ValueError(f"source must be one of {list(SOURCES)}")
    config.update(config["sources"][config["source"]])
    return config


def request(method, url, delay_seconds, retries=3, **kwargs):
    """HTTP request with a pause before each try and retries on errors."""
    for attempt in range(1, retries + 1):
        time.sleep(delay_seconds)  # be polite; bger.ch asks for Crawl-delay: 2
        try:
            response = httpx.request(method, url, headers={"User-Agent": USER_AGENT},
                                     timeout=60, follow_redirects=True, **kwargs)
            response.raise_for_status()
            if "Incapsula incident" in response.text:
                raise Blocked("bot protection blocked the request; try again later")
            return response
        except httpx.HTTPError as error:
            if attempt == retries:
                raise
            log.warning("%s failed (%s), retry %d", url, error, attempt)
            time.sleep(delay_seconds * 2**attempt)
    raise ValueError("retries must be at least 1")


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean(node):
    return " ".join(node.get_text().split()) if node else None


def bger_url(config, case_id, decision_date):
    """Link to the original judgement on bger.ch (also used for the UI)."""
    day = date.fromisoformat(decision_date)
    docid = f"aza://{day:%d-%m-%Y}-{case_id.replace('/', '-')}"
    return f"{config['sources']['bger']['base_url']}?lang=de&type=show_document&highlight_docid={docid}"


# ---------------------------------------------------------------- source: bger.ch


def bger_list_params(day, page):
    d = day.strftime("%d.%m.%Y")
    return {"lang": "de", "type": "simple_query", "query_words": "", "top_subcollection_aza": "all",
            "from_date": d, "to_date": d, "sort": "date_desc", "page": str(page)}


def bger_parse_list(html):
    """(total hits, entries on this page) of one bger.ch result page."""
    soup = BeautifulSoup(html, "html.parser")
    match = re.search(r"(\d+)\s+Urteile gefunden", soup.get_text())
    if match is None:
        raise ValueError("result page has no hit count; did the page layout change?")
    entries = []
    for item in soup.select("div.ranklist_content li"):
        link = item.select_one("span.rank_title a")
        if link is None:
            raise ValueError("result item has no rank_title link; did the page layout change?")
        title = link.get_text(strip=True)  # e.g. "15.05.2026 8C 123/2026 *"
        title_match = BGER_TITLE_RE.search(title)
        if title_match is None:
            raise ValueError(f"cannot parse case title: {title!r}")
        day_str, register, number = title_match.groups()
        entries.append({
            "case_id": f"{register}_{number}",  # official form 8C_123/2026
            "decision_date": datetime.strptime(day_str, "%d.%m.%Y").date().isoformat(),
            "department": clean(item.select_one("div.court")),
            "subject": clean(item.select_one("div.subject")),
            "language": None,  # not in the list; guessed from the text later
            "metadata": {"court": clean(item.select_one("div.court")),
                         "subject": clean(item.select_one("div.subject")),
                         "object": clean(item.select_one("div.object"))},
        })
    return int(match.group(1)), entries


def bger_list_day(config, day):
    """All judgements of one day, page by page (10 hits per page)."""
    entries, page, total = [], 1, None
    while total is None or len(entries) < total:
        html = request("GET", config["base_url"], config["delay_seconds"],
                       params=bger_list_params(day, page)).text
        page_total, hits = bger_parse_list(html)
        total = page_total if total is None else total  # the first page's count is the reference
        if not hits:
            break
        entries += hits
        page += 1
    if len(entries) < total:  # bger.ch shows at most 9 pages; a day has far fewer hits
        raise ValueError(f"{day}: bger.ch reports {total} hits but lists only {len(entries)}")
    return entries


def bger_fetch(config, entry):
    url = bger_url(config, entry["case_id"], entry["decision_date"])
    response = request("GET", url, config["delay_seconds"])
    return response.text, str(response.url)


# ---------------------------------------------------------------- source: entscheidsuche.ch


def entscheidsuche_query(day, offset, size=100):
    return {
        "size": size, "from": offset, "sort": [{"id": "asc"}],
        "_source": {"excludes": ["attachment.content"]},  # the text comes from the HTML file
        "query": {"bool": {"filter": [
            {"term": {"hierarchy": "CH_BGer"}},
            {"range": {"date": {"gte": day.isoformat(), "lte": day.isoformat()}}},
        ]}},
    }


def entscheidsuche_list_day(config, day):
    """All judgements of one day, asked for in pages of 100."""
    entries, total = [], None
    while total is None or len(entries) < total:
        body = entscheidsuche_query(day, len(entries))
        hits = request("POST", config["base_url"], config["delay_seconds"], json=body).json()["hits"]
        total = hits["total"]["value"]
        if not hits["hits"]:
            break
        for hit in hits["hits"]:
            source = hit["_source"]
            department = (source.get("meta") or {}).get("de", "")
            entries.append({
                "case_id": (source.get("reference") or [source["id"]])[0],
                "decision_date": source["date"],
                "department": department.removeprefix("Eidgenossenschaft Bundesgericht ").strip() or None,
                "subject": None,  # entscheidsuche.ch has no Rechtsgebiet; parsed from text later
                "language": source.get("language"),
                "metadata": source,  # everything entscheidsuche.ch offers, unchanged
            })
    if len(entries) < total:
        raise ValueError(f"{day}: entscheidsuche.ch reports {total} hits but returned {len(entries)}")
    return entries


def entscheidsuche_fetch(config, entry):
    url = entry["metadata"]["attachment"]["content_url"]
    response = request("GET", url, config["delay_seconds"])
    return response.text, str(response.url)


SOURCES = {"bger": (bger_list_day, bger_fetch),
           "entscheidsuche": (entscheidsuche_list_day, entscheidsuche_fetch)}


# ---------------------------------------------------------------- judgement


BGER_TITLE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{1,2}[A-Z])[\s_]+(\d+/\d{4})")
DATE_LINE_RE = {
    "de": re.compile(r"^(?:Urteil|Verfügung|Beschluss)\b", re.MULTILINE),
    "fr": re.compile(r"^(?:Arrêt|Ordonnance)\b", re.MULTILINE),
    "it": re.compile(r"^(?:Sentenza|Decreto|Ordinanza)\b", re.MULTILINE),
}
FALLBACK_LANG_RE = {
    "de": re.compile(r"\b(?:Besetzung|Verfahrensbeteiligte|Gegenstand|betreffend|Sachverhalt|Erwägungen|erwägt)\b"),
    "fr": re.compile(r"\b(?:Composition|Participants à la procédure|Objet|Faits|Considérant)\b"),
    "it": re.compile(r"\b(?:Composizione|Partecipanti al procedimento|Oggetto|Fatti|Considerando)\b"),
}
CASE_ID_RE = re.compile(r"\b\d{1,2}[A-Z]_\d+/\d{4}\b")
SUBJECT_HEADING_RE = re.compile(r"^(?:Gegenstand|Objet|Oggetto|betreffend):?$")
BODY_START_RE = re.compile(
    r"^(?:Sachverhalt|Faits|Fatti|Erwägungen|Considérant|Considerando|hat sich ergeben|erwägt)\b",
    re.IGNORECASE,
)
APPEAL_ORIGIN_RE = re.compile(
    r"^(?:(?:Subsidiäre\s+)?(?:Verfassungs|Verwaltungsgerichts|Staatsrechtliche\s+)?[Bb]eschwerde[n]?\s+gegen\b"
    r"|[Rr]evisionsgesuch(?:e)?\s+gegen\b"
    r"|[Rr]ecours\b.*?\bcontre\b"
    r"|[Dd]emande[s]?\s+de\s+révision\b"
    r"|[Rr]icorso\b.*?\bcontro\b"
    r"|[Dd]omanda\s+di\s+revisione\b"
    r"|\(Aufsichtsanzeige\b)"
)


def parse_judgement(html):
    """Judgement body as plain text. Both sources use <div class="content">
    with one <div class="para"> per paragraph; the text keeps one line per paragraph."""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("div.content:has(div.para)")
    if content is None:
        raise ValueError("page has no judgement text; did the page layout change?")
    lines = [clean(p) for p in content.select("div.para")]
    return "\n".join(line for line in lines if line)


def guess_language(text):
    """de / fr / it from the heading of the judgement ("Urteil ...", "Arrêt ...", ...)."""
    head = text[:2000]
    found = {lang: m.start() for lang, pattern in DATE_LINE_RE.items() if (m := pattern.search(head))}
    if not found:
        found = {lang: m.start() for lang, pattern in FALLBACK_LANG_RE.items() if (m := pattern.search(head))}
    return min(found, key=found.get) if found else None


def parse_case_ids(text, primary_case_id):
    """All case numbers from the judgement header (before the date/body line), primary first."""
    head = text[:1000]
    positions = [m.start() for pattern in (*DATE_LINE_RE.values(), *FALLBACK_LANG_RE.values())
                 if (m := pattern.search(head))]
    if positions:
        head = head[:min(positions)]
    found = CASE_ID_RE.findall(head)
    ids = [primary_case_id] if primary_case_id else []
    for case_id in found:
        if case_id not in ids:
            ids.append(case_id)
    return ids


def _is_case_id_list_line(line):
    tokens = [tok for tok in re.split(r"[,;\s]+", line.strip()) if tok and tok not in ("und", "et", "e")]
    return bool(tokens) and all(CASE_ID_RE.fullmatch(tok) for tok in tokens)


def parse_subject(text):
    """Extract the subject block after 'Gegenstand' / 'Objet' / 'Oggetto' in the header."""
    lines = text.splitlines()[:300]
    for i, line in enumerate(lines):
        s = line.strip()
        if BODY_START_RE.match(s):
            break
        if SUBJECT_HEADING_RE.match(s):
            parts = []
            multi_case_block = False
            for idx, raw in enumerate(lines[i + 1 : i + 15]):
                item = raw.strip()
                if not item:
                    continue
                if BODY_START_RE.match(item) or (parts and APPEAL_ORIGIN_RE.match(item)):
                    break
                if _is_case_id_list_line(item) or item in ("und", "et", "e"):
                    multi_case_block = True
                    continue
                cleaned = item.rstrip(", ")
                if cleaned and cleaned not in parts:
                    parts.append(cleaned)
                next_line = lines[i + 2 + idx].strip() if i + 2 + idx < len(lines) else ""
                if item.endswith((",", ".")) and not _is_case_id_list_line(next_line):
                    break
            if not parts:
                return None
            return "; ".join(parts) if (multi_case_block and len(parts) > 1) else " ".join(parts).rstrip(", ")

    # Fallback when the court omitted the 'Gegenstand'/'Objet' label right before 'Beschwerde gegen...':
    for i, line in enumerate(lines[1:], start=1):
        s = line.strip()
        if BODY_START_RE.match(s):
            break
        if APPEAL_ORIGIN_RE.match(s):
            prev = lines[i - 1].strip().rstrip(", ")
            if prev and not _is_case_id_list_line(prev) and not prev.endswith("."):
                return prev
    return None


def build_record(config, entry, html, url):
    """One judgement in the shared schema (docs/SCHEMA.md) plus fetch metadata."""
    text = parse_judgement(html)
    return {
        "id": safe_case_id(entry["case_id"]),
        "case_id": entry["case_id"],
        "case_ids": parse_case_ids(text, entry["case_id"]),
        "decision_date": entry["decision_date"],
        "publication_date": None,  # neither source gives it in the result list
        "department": entry["department"],
        "division": entry["department"],  # alias used in vector-DB metadata (#37)
        "subject": entry["subject"] or parse_subject(text),
        "language": entry["language"] or guess_language(text),
        "url": bger_url(config, entry["case_id"], entry["decision_date"]),
        "text": text,
        "operator_metadata": entry["metadata"],  # tags exactly as given by the source
        "fetch": {
            "fetched_at": now_utc(),
            "fetched_by": USER_AGENT,
            "source": config["source"],
            "url": url,
            "hash_algorithm": "sha256",
            "text_sha256": sha256(text),
        },
    }


# ---------------------------------------------------------------- storage


def safe_case_id(case_id):
    """Replace '/' with '_' for filenames, vector DB IDs, and UI URLs."""
    return case_id.replace("/", "_")


def record_path(out_dir, entry):
    """data/raw/2026-05/2026-05-15_8C_123_2026.json"""
    name = f"{entry['decision_date']}_{safe_case_id(entry['case_id'])}.json"
    return Path(out_dir) / entry["decision_date"][:7] / name


def is_valid(path):
    """True if the saved file can be read and its SHA-256 matches the stored text."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return record["fetch"]["text_sha256"] == sha256(record["text"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def write_json(path, data):
    """Write to a process-unique temporary file first, so concurrent or interrupted runs are safe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_index(out_dir):
    path = Path(out_dir) / "_index.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"days": {}}


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- main loop


def crawl(config, refresh=False):
    """Crawl every day of the span that is not yet in the index. Returns counts."""
    if config["to_date"] >= date.today():
        raise ValueError("to_date must be in the past; today's judgements are not complete yet")
    list_day, fetch = SOURCES[config["source"]]
    out_dir = Path(config["out_dir"])
    index = load_index(out_dir)

    stats = {"days_done_before": 0, "days_crawled": 0, "days_failed": 0,
             "downloaded": 0, "skipped": 0, "failed": 0}
    day = config["from_date"]
    while day <= config["to_date"]:
        if day.isoformat() in index["days"] and not refresh:
            stats["days_done_before"] += 1
            day += timedelta(days=1)
            continue

        day_ok = True
        try:
            entries = list_day(config, day)
        except (httpx.HTTPError, ValueError, KeyError) as error:
            log.error("%s: listing failed: %s", day, error)
            entries, day_ok = [], False

        for entry in entries:
            path = record_path(out_dir, entry)
            if is_valid(path):
                stats["skipped"] += 1
                continue
            if path.exists():  # damaged file: keep it for inspection, then download again
                path.rename(path.with_suffix(f".corrupt-{int(time.time())}"))
            try:
                html, url = fetch(config, entry)
                write_json(path, build_record(config, entry, html, url))
                stats["downloaded"] += 1
            except (httpx.HTTPError, ValueError, KeyError) as error:
                log.error("%s: %s", entry["case_id"], error)
                stats["failed"] += 1
                day_ok = False

        if day_ok:  # only a completely saved day counts as covered
            index["days"][day.isoformat()] = {"source": config["source"], "judgements": len(entries),
                                              "crawled_at": now_utc()}
            write_json(out_dir / "_index.json", index)
            stats["days_crawled"] += 1
        else:
            stats["days_failed"] += 1
        log.info("%s %s", day, stats)
        day += timedelta(days=1)
    return stats


def fetch_reference_cases(config, targets):
    """Fetch specific (case_id, iso_date) judgements without marking their days covered in _index.json."""
    list_day, fetch = SOURCES[config["source"]]
    out_dir = Path(config["out_dir"])
    by_day = {}
    for case_id, iso_date in targets:
        by_day.setdefault(iso_date, set()).add(case_id)

    stats = {"downloaded": 0, "skipped": 0, "missing": 0, "failed": 0}
    for iso_date in sorted(by_day):
        wanted = set(by_day[iso_date])
        pending = set()
        for case_id in wanted:
            path = record_path(out_dir, {"case_id": case_id, "decision_date": iso_date})
            if is_valid(path):
                stats["skipped"] += 1
            else:
                pending.add(case_id)
        if not pending:
            continue

        day = date.fromisoformat(iso_date)
        try:
            entries = list_day(config, day)
        except (httpx.HTTPError, ValueError, KeyError) as error:
            log.error("%s: listing failed: %s", day, error)
            stats["failed"] += len(pending)
            continue

        matched = set()
        for entry in entries:
            if entry["case_id"] not in pending:
                continue
            matched.add(entry["case_id"])
            path = record_path(out_dir, entry)
            if path.exists():
                path.rename(path.with_suffix(f".corrupt-{int(time.time())}"))
            try:
                html, url = fetch(config, entry)
                write_json(path, build_record(config, entry, html, url))
                stats["downloaded"] += 1
            except (httpx.HTTPError, ValueError, KeyError) as error:
                log.error("%s: %s", entry["case_id"], error)
                stats["failed"] += 1
        stats["missing"] += len(pending - matched)
    return stats


# ---------------------------------------------------------------- report (issue #9)


def report(out_dir, csv_path):
    """Judgements per month, source, and language, from the saved files; written as CSV."""
    counts = Counter()
    sources_by_month = {}
    for path in sorted(Path(out_dir).glob("*/*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        month = record["decision_date"][:7]
        counts[(month, record.get("language") or "unknown")] += 1
        source = (record.get("fetch") or {}).get("source")
        if source:
            sources_by_month.setdefault(month, set()).add(source)

    languages = sorted({lang for _, lang in counts})
    rows = [{"month": month,
             "source": "+".join(sorted(sources_by_month.get(month, ()))) or "unknown",
             **{lang: counts[(month, lang)] for lang in languages},
             "total": sum(counts[(month, lang)] for lang in languages)}
            for month in sorted({month for month, _ in counts})]
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["month", "source", *languages, "total"])
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Crawl BGer judgements into data/raw/.")
    parser.add_argument("--from", dest="from_date", help="first day, e.g. 2026-04-01")
    parser.add_argument("--to", dest="to_date", help="last day, e.g. 2026-06-30")
    parser.add_argument("--source", choices=list(SOURCES), help="overrides the config")
    parser.add_argument("--config", default="configs/crawl.json")
    parser.add_argument("--refresh", action="store_true", help="re-check days already in the index")
    parser.add_argument("--report", action="store_true", help="only write the report, no crawl")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = load_config(args.config, from_date=args.from_date, to_date=args.to_date,
                         source=args.source)
    if not args.report:
        print(json.dumps(crawl(config, refresh=args.refresh)))
    for row in report(config["out_dir"], config["report_path"]):
        print(row)


if __name__ == "__main__":
    main()
