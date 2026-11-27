"""Tests for crawl/crawl.py. No network: `crawl.request` is replaced by fake websites.

The HTML and JSON below are synthetic, shaped like the real sites (not real judgements).
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from hack_apertus_coffee_exe.crawl import crawl

BGER = "https://bger.example/index.php"
ES = "https://es.example/_search"
DAY = date(2026, 5, 15)

JUDGEMENT = """<html><body>
<div class="content">site navigation, not the judgement</div>
<div id="highlight_content" class="box"><div class="content">
  <div class="para"> </div>
  <div class="para"><b>8C_123/2026</b></div>
  <div class="para">Urteil   vom 15. Mai 2026 – Unfallversicherung</div>
  <div class="para">Gegenstand</div>
  <div class="para">Unfallversicherung,</div>
</div></div></body></html>"""
TEXT = "8C_123/2026\nUrteil vom 15. Mai 2026 – Unfallversicherung\nGegenstand\nUnfallversicherung,"


def bger_list_page(total, titles):
    items = "".join(
        f"""<li><span class="rank_title"><a href="#">{t}</a></span>
        <div class="rank_data">
          <div class="court small normal">IV. öffentlich-rechtliche Abteilung</div>
          <div class="subject small normal">Unfallversicherung</div>
          <div class="object small normal">Unfallversicherung
             (Kausalzusammenhang)</div>
        </div></li>"""
        for t in titles
    )
    return f"""<html><body>
    <div class="ranklist_header">{total} Urteile gefunden zwischen 15.05.2026 und 15.05.2026</div>
    <div class="ranklist_content"><ol>{items}</ol></div></body></html>"""


def es_hit(case, day):
    file_id = f"CH_BGer_008_{case.replace('_', '-').replace('/', '-')}_{day}"
    return {"_source": {
        "id": file_id, "date": day, "reference": [case], "language": "de",
        "meta": {"de": "Eidgenossenschaft Bundesgericht IV. Offentlich-rechtliche Abteilung"},
        "attachment": {"content_url": f"https://es.example/docs/{file_id}.html"}}}


class FakeResponse:
    def __init__(self, text="", data=None, url=BGER):
        self.text, self._data, self.url = text, data, url

    def json(self):
        return self._data


@pytest.fixture
def site(monkeypatch):
    """Fake bger.ch and entscheidsuche.ch (one judgement on 15.05.2026). Records every request."""
    state = {
        "requests": [],
        "bger_lists": {"15.05.2026": [bger_list_page(1, ["15.05.2026 8C 123/2026"])]},
        "es_hits": {"2026-05-15": [es_hit("8C_123/2026", "2026-05-15")]},
        "judgement": JUDGEMENT,
    }

    def fake_request(method, url, delay_seconds, retries=3, **kwargs):
        state["requests"].append((method, url))
        if method == "POST":  # entscheidsuche.ch search for one day
            body = kwargs["json"]
            day = body["query"]["bool"]["filter"][1]["range"]["date"]["gte"]
            hits = state["es_hits"].get(day, [])
            return FakeResponse(data={"hits": {"total": {"value": len(hits)},
                                               "hits": hits[body["from"]:body["from"] + body["size"]]}})
        params = kwargs.get("params") or {}
        if params.get("type") == "simple_query":  # bger.ch result page
            pages = state["bger_lists"].get(params["from_date"], [])
            index = int(params["page"]) - 1
            return FakeResponse(pages[index] if index < len(pages) else bger_list_page(len(pages), []))
        return FakeResponse(state["judgement"], url=url)  # a judgement page

    monkeypatch.setattr(crawl, "request", fake_request)
    return state


def make_config(tmp_path, source, from_date=DAY, to_date=DAY):
    sources = {"bger": {"base_url": BGER, "delay_seconds": 0},
               "entscheidsuche": {"base_url": ES, "delay_seconds": 0}}
    return {"source": source, "from_date": from_date, "to_date": to_date,
            "out_dir": str(tmp_path / "raw"), "sources": sources, **sources[source]}


@pytest.fixture(params=["bger", "entscheidsuche"])
def config(request, tmp_path):
    """Every functional test runs once per source."""
    return make_config(tmp_path, request.param)


def saved_file(config):
    return Path(config["out_dir"]) / "2026-05" / "2026-05-15_8C_123_2026.json"


def index(config):
    return json.loads((Path(config["out_dir"]) / "_index.json").read_text())["days"]


# ---------------------------------------------------------------- unit tests


def write_config(tmp_path, **values):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({**make_config(tmp_path, "bger"), "from_date": "2026-04-01",
                                "to_date": "2026-06-30", **values}))
    return path


def test_load_config_parses_dates_and_copies_source_settings(tmp_path):
    config = crawl.load_config(write_config(tmp_path, source="entscheidsuche"))
    assert (config["from_date"], config["to_date"]) == (date(2026, 4, 1), date(2026, 6, 30))
    assert config["base_url"] == ES


def test_load_config_command_line_wins(tmp_path):
    config = crawl.load_config(write_config(tmp_path), from_date="2025-10-01", to_date=None)
    assert (config["from_date"], config["to_date"]) == (date(2025, 10, 1), date(2026, 6, 30))


def test_load_config_rejects_bad_values(tmp_path):
    with pytest.raises(ValueError):
        crawl.load_config(write_config(tmp_path, from_date="2026-07-01"))
    with pytest.raises(ValueError):
        crawl.load_config(write_config(tmp_path, source="google"))


def test_shipped_config_is_valid():
    config = crawl.load_config("configs/crawl.json")
    assert config["from_date"] <= config["to_date"] and config["base_url"]


def test_bger_parse_list_reads_count_and_tags():
    total, entries = crawl.bger_parse_list(
        bger_list_page(12, ["15.05.2026 8C 123/2026 *", "01.04.2026 1D 1/2025 1D 2/2025"])
    )
    assert total == 12
    assert entries[0]["case_id"] == "8C_123/2026"
    assert entries[0]["decision_date"] == "2026-05-15"
    assert entries[0]["department"] == "IV. öffentlich-rechtliche Abteilung"
    assert entries[0]["subject"] == "Unfallversicherung"
    assert entries[1]["case_id"] == "1D_1/2025"


def test_bger_parse_list_empty_and_unknown_layout():
    assert crawl.bger_parse_list(bger_list_page(0, [])) == (0, [])
    with pytest.raises(ValueError):
        crawl.bger_parse_list("<html>Wartungsarbeiten</html>")


def test_bger_url_points_to_the_original(tmp_path):
    url = crawl.bger_url(make_config(tmp_path, "bger"), "8C_123/2026", "2026-05-15")
    assert url == BGER + "?lang=de&type=show_document&highlight_docid=aza://15-05-2026-8C_123-2026"


def test_parse_judgement_takes_only_the_judgement_block():
    text = crawl.parse_judgement(JUDGEMENT)
    assert text == TEXT
    assert "navigation" not in text
    with pytest.raises(ValueError):
        crawl.parse_judgement("<html>Fehler</html>")


def test_parse_case_ids_collects_multiple_from_header_only():
    text = "Bundesgericht\n1D_1/2025, 1D_2/2025\nUrteil 1. April 2026\nErwägungen: 1D_99/2020"
    assert crawl.parse_case_ids(text, "1D_1/2025") == ["1D_1/2025", "1D_2/2025"]
    assert crawl.parse_case_ids("Verwaltungskommission\n12T_2/2025\nerwägt:", "12T_2/2025") == ["12T_2/2025"]


@pytest.mark.parametrize("text, expected", [
    ("1D_1/2025\nGegenstand\nErmächtigung zur Strafverfolgung,\nVerfassungsbeschwerden gegen den Entscheid.\nSachverhalt:",
     "Ermächtigung zur Strafverfolgung"),
    ("2C_187/2025\nGegenstand\nWiderruf bzw. Nichtverlängerung der\nAufenthaltsbewilligung,\nBeschwerde gegen das Urteil.",
     "Widerruf bzw. Nichtverlängerung der Aufenthaltsbewilligung"),
    ("6B_115/2025\nGegenstand\n6B_115/2025\nEntführung; Willkür,\n6B_144/2025\nEntführung; Willkür,\nSachverhalt:",
     "Entführung; Willkür"),
    ("6B_475/2025\nObjet\n6B_475/2025, 6B_485/2025\nEscroquerie; arbitraire,\nFaits :",
     "Escroquerie; arbitraire"),
    ("7B_1297/2024\nintimé.\nAbus d'autorité, violation du secret de fonction,\nrecours contre le jugement du 5 octobre 2023.\nFaits :",
     "Abus d'autorité, violation du secret de fonction"),
    ("1C_74/2025\nObjet\nRésiliation des rapports de service,\nrecours contre l'arrêt du 17 décembre 2024.\nFaits :",
     "Résiliation des rapports de service"),
    ("6B_9/2026\nOggetto\nFurto,\nricorso contro la sentenza.\nFatti:", "Furto"),
    ("8C_1/2026\nUrteil vom 2. Mai 2026", None),
])
def test_parse_subject(text, expected):
    assert crawl.parse_subject(text) == expected


@pytest.mark.parametrize("text, language", [
    ("8C_1/2026\nUrteil vom 2. Mai 2026", "de"),
    ("6B_569/2025\nUrteil 9. Oktober 2025", "de"),
    ("12T_2/2025\nbetreffend\nRechtsverzögerung\nerwägt:", "de"),
    ("1C_3/2026\nArrêt du 18 juin 2026\nvu l'Urteil vom 1. Mai", "fr"),  # first marker wins
    ("7B_958/2025\nArrêt 3 octobre 2025", "fr"),
    ("6B_9/2026\nSentenza del 3 aprile 2026", "it"),
    ("7B_425/2024\nSentenza 7 novembre 2025", "it"),
    ("no heading", None),
])
def test_guess_language(text, language):
    assert crawl.guess_language(text) == language


def test_record_path_sorts_by_month():
    entry = {"case_id": "8C_123/2026", "decision_date": "2026-05-15"}
    assert crawl.record_path("data/raw", entry) == Path("data/raw/2026-05/2026-05-15_8C_123_2026.json")


def test_is_valid(tmp_path):
    path = tmp_path / "r.json"
    assert not crawl.is_valid(path)  # missing
    path.write_text("{not json")
    assert not crawl.is_valid(path)  # broken JSON
    record = {"text": "Urteil", "fetch": {"text_sha256": crawl.sha256("Urteil")}}
    path.write_text(json.dumps(record))
    assert crawl.is_valid(path)
    record["text"] = "changed"
    path.write_text(json.dumps(record))
    assert not crawl.is_valid(path)  # hash no longer matches


def test_sha256_known_value():
    assert crawl.sha256("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_bot_protection_raises_blocked(monkeypatch):
    blocked = FakeResponse("Request unsuccessful. Incapsula incident ID: 1")
    blocked.raise_for_status = lambda: None
    monkeypatch.setattr(crawl.httpx, "request", lambda *args, **kwargs: blocked)
    with pytest.raises(crawl.Blocked):
        crawl.request("GET", BGER, 0)


# ---------------------------------------------------------------- functional tests (both sources)


def test_record_follows_the_shared_schema(site, config):
    crawl.crawl(config)
    record = json.loads(saved_file(config).read_text(encoding="utf-8"))
    for field in ("id", "case_id", "case_ids", "decision_date", "publication_date",
                  "department", "division", "subject", "language", "url", "text"):  # issue #3 / #37
        assert field in record
    assert "object" not in record
    assert "content_html" not in record
    assert record["id"] == "8C_123_2026"
    assert record["case_id"] == "8C_123/2026"
    assert record["case_ids"] == ["8C_123/2026"]
    assert record["department"].startswith("IV. ")
    assert record["division"] == record["department"]
    assert record["subject"] == "Unfallversicherung"
    assert record["language"] == "de"
    assert record["url"].startswith(BGER) and "8C_123-2026" in record["url"]
    assert record["text"] == TEXT
    assert record["operator_metadata"]
    assert record["fetch"]["source"] == config["source"]
    assert record["fetch"]["text_sha256"] == crawl.sha256(record["text"])
    assert "content_sha256" not in record["fetch"]


def test_second_run_makes_no_requests(site, config):
    first = crawl.crawl(config)
    assert (first["downloaded"], first["days_crawled"]) == (1, 1)
    site["requests"].clear()
    second = crawl.crawl(config)
    assert site["requests"] == []
    assert second["days_done_before"] == 1 and second["downloaded"] == 0


def test_only_the_gap_of_a_partly_covered_span_is_crawled(site, config):
    crawl.crawl(config)  # 15.05. is covered
    config["from_date"], config["to_date"] = DAY - timedelta(days=1), DAY + timedelta(days=1)
    stats = crawl.crawl(config)
    assert stats["days_done_before"] == 1 and stats["days_crawled"] == 2
    assert set(index(config)) == {"2026-05-14", "2026-05-15", "2026-05-16"}


def test_existing_file_is_not_downloaded_again_with_refresh(site, config):
    crawl.crawl(config)
    stats = crawl.crawl(config, refresh=True)  # re-lists the day ...
    assert (stats["skipped"], stats["downloaded"]) == (1, 0)  # ... but keeps the file


def test_damaged_file_is_kept_and_downloaded_again(site, config):
    crawl.crawl(config)
    saved_file(config).write_text("{broken")
    assert crawl.crawl(config, refresh=True)["downloaded"] == 1
    assert crawl.is_valid(saved_file(config))
    backups = list(saved_file(config).parent.glob("*.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == "{broken"


def test_failed_day_is_not_marked_covered_and_retried(site, config):
    site["judgement"] = "<html>Fehler</html>"
    stats = crawl.crawl(config)
    assert (stats["failed"], stats["days_failed"]) == (1, 1)
    assert not saved_file(config).exists()
    assert "2026-05-15" not in index(config) if (Path(config["out_dir"]) / "_index.json").exists() else True
    site["judgement"] = JUDGEMENT
    stats = crawl.crawl(config)
    assert stats["downloaded"] == 1 and "2026-05-15" in index(config)


def test_future_dates_are_refused(site, config):
    config["to_date"] = date.today()
    with pytest.raises(ValueError):
        crawl.crawl(config)


def test_bger_takes_all_pages_of_a_day(site, tmp_path):
    """A day with 120 hits on 12 pages is read completely."""
    site["bger_lists"]["15.05.2026"] = [
        bger_list_page(120, [f"15.05.2026 8C {p * 10 + n}/2026" for n in range(10)]) for p in range(12)]
    assert len(crawl.bger_list_day(make_config(tmp_path, "bger"), DAY)) == 120


def test_bger_incomplete_list_is_an_error(site, tmp_path):
    site["bger_lists"]["15.05.2026"] = [bger_list_page(5, ["15.05.2026 8C 123/2026"])]
    with pytest.raises(ValueError):
        crawl.bger_list_day(make_config(tmp_path, "bger"), DAY)


def test_entscheidsuche_takes_all_hits_over_several_pages(site, tmp_path):
    site["es_hits"]["2026-05-15"] = [es_hit(f"8C_{n}/2026", "2026-05-15") for n in range(250)]
    entries = crawl.entscheidsuche_list_day(make_config(tmp_path, "entscheidsuche"), DAY)
    assert len({e["case_id"] for e in entries}) == 250


def test_report_counts_per_month_and_language(site, tmp_path):
    config = make_config(tmp_path, "entscheidsuche", date(2026, 4, 30), date(2026, 5, 15))
    site["es_hits"]["2026-04-30"] = [es_hit("9C_1/2026", "2026-04-30")]
    site["es_hits"]["2026-04-30"][0]["_source"]["language"] = "fr"
    crawl.crawl(config)
    rows = crawl.report(config["out_dir"], tmp_path / "report.csv")
    assert rows == [{"month": "2026-04", "source": "entscheidsuche", "de": 0, "fr": 1, "total": 1},
                    {"month": "2026-05", "source": "entscheidsuche", "de": 1, "fr": 0, "total": 1}]
    assert (tmp_path / "report.csv").read_text().splitlines()[0] == "month,source,de,fr,total"


def test_command_line(site, tmp_path, capsys):
    path = write_config(tmp_path, source="entscheidsuche", report_path=str(tmp_path / "r.csv"))
    crawl.main(["--config", str(path), "--from", "2026-05-15", "--to", "2026-05-15"])
    assert '"downloaded": 1' in capsys.readouterr().out
    assert saved_file(json.loads(path.read_text())).exists()


# ---------------------------------------------------------------- integration (real websites)


@pytest.mark.integration
@pytest.mark.parametrize("source", ["entscheidsuche", "bger"])
def test_real_site_one_day(source, tmp_path):
    """Crawls one real day. Run: pytest -m integration"""
    config = crawl.load_config("configs/crawl.json", source=source,
                               from_date="2026-05-15", to_date="2026-05-15")
    config["out_dir"] = str(tmp_path)
    stats = crawl.crawl(config)
    assert stats["downloaded"] > 0 and stats["failed"] == 0
