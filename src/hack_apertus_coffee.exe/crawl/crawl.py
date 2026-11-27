"""Download all Federal Supreme Court (BGer) judgements of a time span into data/raw/.

Run:
    python -m hack_apertus_coffee_exe.crawl.crawl --from 2026-04-01 --to 2026-06-30
    python -m hack_apertus_coffee_exe.crawl.crawl --report        # counts per month and language

Defaults (source, URL, pause, folder, time span) come from configs/crawl.json:
    "source": "bger"            official site search.bger.ch (final target)
    "source": "entscheidsuche"  open mirror entscheidsuche.ch (for testing)

Storage, sorted by month (schema: issue #3):
    data/raw/2026-05/2026-05-15_8C_123-2026.json   one file per judgement
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
        title = item.select_one("span.rank_title a").get_text(strip=True)  # "15.05.2026 8C 123/2026"
        day_str, register, number = title.split()
        entries.append({
            "case_id": f"{register}_{number}",  # official form 8C_123/2026
            "decision_date": datetime.strptime(day_str, "%d.%m.%Y").date().isoformat(),
            "department": clean(item.select_one("div.court")),
            "subject": clean(item.select_one("div.subject")),
            "object": clean(item.select_one("div.object")),
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
                "subject": None,  # entscheidsuche.ch has no Rechtsgebiet
                "object": None,
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


def parse_judgement(html):
    """Judgement body as HTML and plain text. Both sources use <div class="content">
    with one <div class="para"> per paragraph; the text keeps one line per paragraph."""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("div.content:has(div.para)")
    if content is None:
        raise ValueError("page has no judgement text; did the page layout change?")
    lines = [clean(p) for p in content.select("div.para")]
    return str(content), "\n".join(line for line in lines if line)


def guess_language(text):
    """de / fr / it from the heading of the judgement ("Urteil vom", "Arrêt du", ...)."""
    head = text[:1500]
    markers = {"de": ("Urteil vom", "Verfügung vom", "Beschluss vom"),
               "fr": ("Arrêt du", "Ordonnance du"),
               "it": ("Sentenza del", "Decreto del", "Ordinanza del")}
    found = {lang: min((head.find(m) for m in words if m in head), default=None)
             for lang, words in markers.items()}
    found = {lang: pos for lang, pos in found.items() if pos is not None}
    return min(found, key=found.get) if found else None


def build_record(config, entry, html, url):
    """One judgement in the shared schema (issue #3) plus fetch info and raw HTML."""
    content_html, text = parse_judgement(html)
    return {
        "case_id": entry["case_id"],
        "decision_date": entry["decision_date"],
        "publication_date": None,  # neither source gives it in the result list
        "department": entry["department"],
        "subject": entry["subject"],
        "object": entry["object"],
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
            "content_sha256": sha256(content_html),
            "text_sha256": sha256(text),
        },
        "content_html": content_html,
    }


# ---------------------------------------------------------------- storage


def record_path(out_dir, entry):
    """data/raw/2026-05/2026-05-15_8C_123-2026.json"""
    name = f"{entry['decision_date']}_{entry['case_id'].replace('/', '-')}.json"
    return Path(out_dir) / entry["decision_date"][:7] / name


def is_valid(path):
    """True if the saved file can be read and its SHA-256 matches the stored content."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return record["fetch"]["content_sha256"] == sha256(record["content_html"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def write_json(path, data):
    """Write to a temporary file first, so an interrupted run leaves no half file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
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
        except (httpx.HTTPError, ValueError) as error:
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
            except (httpx.HTTPError, ValueError) as error:
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


# ---------------------------------------------------------------- report (issue #9)


def report(out_dir, csv_path):
    """Judgements per month and language, from the saved files; written as CSV."""
    counts = Counter()
    for path in sorted(Path(out_dir).glob("*/*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        counts[(record["decision_date"][:7], record.get("language") or "unknown")] += 1

    languages = sorted({lang for _, lang in counts})
    rows = [{"month": month, **{lang: counts[(month, lang)] for lang in languages},
             "total": sum(counts[(month, lang)] for lang in languages)}
            for month in sorted({month for month, _ in counts})]
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["month", *languages, "total"])
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
