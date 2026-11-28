"""Tests for the summary HTTP backend (no gateway; the summarizer is faked)."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hack_apertus_coffee_exe.summary import store
from hack_apertus_coffee_exe.summary.align import Alignment
from hack_apertus_coffee_exe.summary.api import Backend, make_server

CRAWL_DIR = Path("src/hack_apertus_coffee_exe/triage/mock_crawl")
CASE = "8C_901/2026"
SAFE = "8C_901_2026"


def fake_result(judgement, similar_case_ids=()):
    return SimpleNamespace(
        case_id=judgement.case_id,
        title_de=f"BGer-Urteil {judgement.case_id}",
        title_fr=f"Arrêt du TF {judgement.case_id}",
        summary_de="Zusammenfassung DE",
        summary_fr="Résumé FR",
        facts={"form": "fall"},
        examples=["8C_177/2025"],
        model="apertus-v1-5-8b",
        prompt_version="v1",
        coherence_ok=True,
        attempts={},
        check=SimpleNamespace(to_dicts=lambda: []),
        text=lambda lang: "Zusammenfassung DE" if lang == "de" else "Résumé FR",
    )


@pytest.fixture
def summarizer():
    return SimpleNamespace(
        summarize=Mock(side_effect=fake_result),
        align_edit=Mock(return_value=Alignment("Angepasste Fassung", ["Bitte E. 4.2 prüfen."], True)),
        llm=SimpleNamespace(model="apertus-v1-5-8b"),
    )


@pytest.fixture
def backend(tmp_path, summarizer):
    return Backend(tmp_path / "s.db", CRAWL_DIR, lambda: summarizer)


def test_get_without_summary_is_404_and_never_generates(backend, summarizer):
    status, _ = backend.get_summary(SAFE)
    assert status == 404
    summarizer.summarize.assert_not_called()


def test_create_generates_once_and_returns_entry(backend, summarizer):
    status, entry = backend.create_summary(SAFE)
    assert status == 200
    assert entry["id"] == SAFE and entry["caseNumber"] == CASE
    assert entry["de"] == "Zusammenfassung DE" and entry["fr"] == "Résumé FR"
    assert entry["facts"] == {"de": {"form": "fall"}, "fr": {"form": "fall"}}
    assert entry["examples"] == {"de": ["8C_177/2025"], "fr": ["8C_177/2025"]}
    assert entry["url"].startswith("https://search.bger.ch/")
    assert entry["status"] == {"de": "generated", "fr": "generated"}

    status, again = backend.create_summary(CASE)
    assert status == 200 and again == entry
    assert summarizer.summarize.call_count == 1


def test_restart_reads_from_db_without_llm(tmp_path, backend):
    backend.create_summary(SAFE)
    restarted = Backend(tmp_path / "s.db", CRAWL_DIR, lambda: pytest.fail("LLM must not be called"))
    status, entry = restarted.create_summary(SAFE)
    assert status == 200 and entry["de"] == "Zusammenfassung DE"


def test_unknown_judgement_is_404(backend):
    status, payload = backend.create_summary("8C_999_2026")
    assert status == 404 and "not found" in payload["error"]


def _versions(backend, lang):
    with store.connect(backend.db_path) as conn:
        return [(s.status, s.model == "human", s.text) for s in store.get_history(conn, CASE, lang)]


def test_edit_is_shown_at_once_and_needs_no_approval(backend):
    backend.create_summary(SAFE)
    status, entry = backend.edit_summary(SAFE, {"de": "Vom Juristen", "fr": "Du juriste"})
    assert status == 200
    assert entry["de"] == "Vom Juristen" and entry["fr"] == "Du juriste"  # both changed: nothing to align
    assert entry["alignment"] is None
    assert entry["pending"] == {"de": None, "fr": None}
    assert entry["model"] == {"de": "human", "fr": "human"} and entry["editedAt"] is not None
    # facts and examples of the generated version stay available for the UI
    assert entry["facts"] == {"de": {"form": "fall"}, "fr": {"form": "fall"}}
    assert entry["examples"] == {"de": ["8C_177/2025"], "fr": ["8C_177/2025"]}


def test_a_second_edit_replaces_the_first_one(backend):
    backend.create_summary(SAFE)
    backend.edit_summary(SAFE, {"de": "Erste Fassung", "fr": "Première version"})
    _, entry = backend.edit_summary(SAFE, {"de": "Zweite Fassung", "fr": "Première version"})
    assert entry["de"] == "Zweite Fassung"
    # the generated text and exactly one human version: no growing pile of versions
    assert _versions(backend, "de") == [("generated", False, "Zusammenfassung DE"), ("approved", True, "Zweite Fassung")]
    # saving the same texts again changes nothing
    backend.edit_summary(SAFE, {"de": "Zweite Fassung", "fr": "Première version"})
    assert len(_versions(backend, "de")) == 2 and len(_versions(backend, "fr")) == 2


def test_editing_one_language_brings_the_other_in_line(backend, summarizer):
    backend.create_summary(SAFE)
    status, entry = backend.edit_summary(SAFE, {"de": "Vom Juristen", "fr": "Résumé FR"})
    assert status == 200
    assert entry["de"] == "Vom Juristen" and entry["fr"] == "Angepasste Fassung"
    assert entry["alignment"] == {"lang": "fr", "ok": True, "changed": True, "notes": ["Bitte E. 4.2 prüfen."], "reference": True}
    assert entry["model"] == {"de": "human", "fr": "human"}
    # the database says which of the two the lawyer typed and which one Apertus wrote
    assert entry["alignedBy"] == {"de": None, "fr": "apertus-v1-5-8b"}
    call = summarizer.align_edit.call_args.kwargs
    assert (call["source_lang"], call["source_text"]) == ("de", "Vom Juristen")
    assert (call["target_lang"], call["target_text"]) == ("fr", "Résumé FR")
    # Apertus sees what the text said before the edit and gets the judgement as the reference
    assert call["source_before"] == "Zusammenfassung DE"
    assert call["judgement"].case_id == CASE and call["considerations"] == []
    # a further German edit: the French text Apertus wrote follows again, still one version
    summarizer.align_edit.return_value = Alignment("Zweite Angleichung")
    _, entry = backend.edit_summary(SAFE, {"de": "Vom Juristen, zweite Fassung"})
    assert entry["alignment"] == {"lang": "fr", "ok": True, "changed": True, "notes": [], "reference": False}
    assert entry["fr"] == "Zweite Angleichung"
    assert summarizer.align_edit.call_args.kwargs["target_text"] == "Angepasste Fassung"
    assert summarizer.align_edit.call_args.kwargs["source_before"] == "Vom Juristen"
    assert len(_versions(backend, "fr")) == 2


def test_an_edit_of_the_wording_alone_leaves_the_other_language_as_generated(backend, summarizer):
    backend.create_summary(SAFE)
    summarizer.align_edit.return_value = Alignment("Résumé FR", ["Seule la version allemande cite l'art. 6 LAA."])
    _, entry = backend.edit_summary(SAFE, {"de": "Zusammenfassung DE, umformuliert"})
    assert entry["alignment"] == {"lang": "fr", "ok": True, "changed": False,
                                  "notes": ["Seule la version allemande cite l'art. 6 LAA."], "reference": False}
    assert entry["fr"] == "Résumé FR" and entry["status"]["fr"] == "generated" and entry["alignedBy"]["fr"] is None


def test_alignment_works_from_french_to_german(backend):
    backend.create_summary(SAFE)
    _, entry = backend.edit_summary(SAFE, {"fr": "Du juriste"})  # only the changed language is sent
    assert entry["alignment"]["lang"] == "de" and entry["alignment"]["ok"] is True
    assert entry["fr"] == "Du juriste" and entry["de"] == "Angepasste Fassung"
    assert entry["alignedBy"] == {"de": "apertus-v1-5-8b", "fr": None}


def test_apertus_never_overwrites_a_text_typed_by_hand(backend, summarizer):
    backend.create_summary(SAFE)
    backend.edit_summary(SAFE, {"de": "Vom Juristen"})  # French follows (Apertus)
    # the lawyer corrects the French text by hand: the German one is theirs and stays
    _, entry = backend.edit_summary(SAFE, {"fr": "Corrigé à la main"})
    assert entry["alignment"] == {"lang": "de", "ok": False, "reason": "edited_by_hand"}
    assert entry["de"] == "Vom Juristen" and entry["fr"] == "Corrigé à la main"
    assert entry["alignedBy"] == {"de": None, "fr": None}
    # now both are hand-written: a German edit no longer touches the French text either
    _, entry = backend.edit_summary(SAFE, {"de": "Vom Juristen, ergänzt"})
    assert entry["alignment"] == {"lang": "fr", "ok": False, "reason": "edited_by_hand"}
    assert entry["fr"] == "Corrigé à la main"
    assert summarizer.align_edit.call_count == 1
    # after a reset Apertus may write again
    backend.reset_summary(SAFE)
    _, entry = backend.edit_summary(SAFE, {"de": "Neu angefangen"})
    assert entry["alignment"]["lang"] == "fr" and entry["alignment"]["ok"] is True


def test_edit_is_kept_when_apertus_cannot_align(backend, summarizer):
    backend.create_summary(SAFE)
    summarizer.align_edit.side_effect = ValueError("Missing API key: set LLM_API_KEY")
    status, entry = backend.edit_summary(SAFE, {"de": "Vom Juristen"})
    assert status == 200 and entry["de"] == "Vom Juristen" and entry["fr"] == "Résumé FR"
    assert entry["alignment"]["lang"] == "fr" and entry["alignment"]["ok"] is False
    assert entry["alignment"]["reason"] == "failed"
    assert "Missing API key" in entry["alignment"]["error"]
    assert entry["model"]["fr"] != "human"
    # what the gateway answers is not passed on (it may quote parts of the request)
    summarizer.align_edit.side_effect = RuntimeError("401: invalid key sk-...1234")
    _, entry = backend.edit_summary(SAFE, {"de": "Vom Juristen, zweiter Versuch"})
    assert entry["de"] == "Vom Juristen, zweiter Versuch" and entry["fr"] == "Résumé FR"
    assert entry["alignment"] == {"lang": "fr", "ok": False, "reason": "failed", "error": "Apertus call failed"}


def test_alignment_can_be_switched_off(backend, summarizer):
    backend.create_summary(SAFE)
    _, entry = backend.edit_summary(SAFE, {"de": "Vom Juristen", "align": False})
    assert entry["alignment"] is None and entry["fr"] == "Résumé FR"
    summarizer.align_edit.assert_not_called()


def test_reset_goes_back_to_the_generated_text(backend):
    backend.create_summary(SAFE)
    backend.edit_summary(SAFE, {"de": "Vom Juristen", "fr": "Du juriste"})
    status, entry = backend.reset_summary(SAFE, "de")
    assert status == 200 and entry["de"] == "Zusammenfassung DE" and entry["fr"] == "Du juriste"
    status, entry = backend.reset_summary(SAFE)
    assert entry["fr"] == "Résumé FR" and entry["status"] == {"de": "generated", "fr": "generated"}
    assert entry["editedAt"] is None
    assert _versions(backend, "de") == [("generated", False, "Zusammenfassung DE")]
    assert backend.reset_summary(SAFE)[0] == 200  # nothing to drop is not an error
    assert backend.reset_summary("8C_999_2026")[0] == 404


def test_approve_route_still_works_for_versions_saved_as_pending(backend):
    backend.create_summary(SAFE)
    with store.connect(backend.db_path) as conn:
        store.save_edit(conn, CASE, "de", "Wartet auf Freigabe")
    status, entry = backend.approve_summary(SAFE, {})
    assert status == 200 and entry["de"] == "Wartet auf Freigabe" and entry["status"]["de"] == "approved"


def test_approve_without_edit_is_conflict(backend):
    backend.create_summary(SAFE)
    status, _ = backend.approve_summary(SAFE, {})
    assert status == 409


def test_edit_without_summary_is_404(backend):
    status, _ = backend.edit_summary(SAFE, {"de": "x"})
    assert status == 404


def test_search_knowledge_base(backend):
    backend.create_summary(SAFE)
    status, results = backend.search("résumé")
    assert status == 200 and [e["id"] for e in results] == [SAFE]
    assert backend.search("gibtsnicht")[1] == []


def test_http_roundtrip(backend):
    server = make_server(backend, "127.0.0.1", 0, CRAWL_DIR)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api"
    try:
        req = urllib.request.Request(f"{base}/judgements/{SAFE}/summary", method="POST")
        with urllib.request.urlopen(req) as res:
            assert json.loads(res.read())["de"] == "Zusammenfassung DE"

        body = json.dumps({"de": "Neu", "fr": "Résumé FR"}).encode()
        req = urllib.request.Request(
            f"{base}/judgements/{SAFE}/summary", data=body, method="PUT",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as res:
            saved = json.loads(res.read())
            assert saved["de"] == "Neu" and saved["pending"]["de"] is None
            assert saved["alignment"]["ok"] is True and saved["alignment"]["notes"] == ["Bitte E. 4.2 prüfen."]
            assert saved["fr"] == "Angepasste Fassung"

        req = urllib.request.Request(f"{base}/judgements/{SAFE}/summary/edit?lang=fr", method="DELETE")
        with urllib.request.urlopen(req) as res:
            reset = json.loads(res.read())
            assert reset["fr"] == "Résumé FR" and reset["de"] == "Neu"

        with urllib.request.urlopen(f"{base}/knowledge-base?q=resume") as res:
            assert json.loads(res.read()) == []  # accents matter in the simple search

        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(urllib.request.Request(f"{base}/knowledge-base", method="DELETE"))
        assert err.value.code == 405

        # static files must be revalidated, or browsers run outdated UI scripts after a change
        static = f"http://127.0.0.1:{server.server_address[1]}/{next(CRAWL_DIR.glob('*.json')).name}"
        with urllib.request.urlopen(static) as res:
            assert res.headers["Cache-Control"] == "no-cache"
    finally:
        server.shutdown()
        server.server_close()
    with store.connect(backend.db_path) as conn:
        assert [s.status for s in store.get_history(conn, CASE, "de")] == ["generated", "approved"]
        assert [s.status for s in store.get_history(conn, CASE, "fr")] == ["generated"]


def test_cli_parser_builds_with_config_defaults(capsys):
    """`python -m ...summary.api --help` must not crash: the Docker image starts this way."""
    from hack_apertus_coffee_exe.summary.api import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "--triage" in capsys.readouterr().out


def test_queue_http_routes_and_removed_adjust_route(backend, summarizer):
    import time

    server = make_server(backend, "127.0.0.1", 0, CRAWL_DIR)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api"
    try:
        req = urllib.request.Request(
            f"{base}/summaries/queue",
            method="POST",
            data=json.dumps({"caseIds": [SAFE], "priority": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as res:
            assert res.status == 202

        deadline = time.monotonic() + 3.0
        status_payload = {}
        while time.monotonic() < deadline:
            with urllib.request.urlopen(f"{base}/summaries/status?ids={SAFE}") as res:
                status_payload = json.load(res)
            if SAFE in status_payload["done"]:
                break
            time.sleep(0.02)
        assert SAFE in status_payload["done"]

        align_req = urllib.request.Request(
            f"{base}/judgements/{SAFE}/summary/align",
            method="POST",
            data=json.dumps({
                "sourceLang": "de",
                "sourceText": "Vom Juristen geändert",
                "targetLang": "fr",
                "targetText": "Résumé FR",
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        # "Anpassen" is gone (correction of 28.11.): the route no longer exists, nothing is sent to Apertus
        with pytest.raises(urllib.error.HTTPError) as gone:
            urllib.request.urlopen(align_req)
        assert gone.value.code == 404
        summarizer.align_edit.assert_not_called()

        # Validation: bad payload must yield 400 (never 500)
        for url, payload in (
            (f"{base}/summaries/queue", {"caseIds": [], "priority": "yes"}),
        ):
            bad_req = urllib.request.Request(
                url,
                method="POST",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(bad_req)
            assert exc.value.code == 400
    finally:
        backend.queue.stop()
        server.shutdown()
        server.server_close()


# --- the stored judgement text as a page (original can be read without the external site) ---


def test_original_page_shows_the_stored_text(backend):
    status, page = backend.original_page(SAFE)
    assert status == 200
    assert "<h1>BGer-Urteil 8C_901/2026</h1>" in page and "Lokal gespeicherter Volltext" in page
    judgement = backend.find_judgement(SAFE)
    assert judgement.text[:40].replace("&", "&amp;") in page  # the text itself, not a summary of it
    status, page_fr = backend.original_page(SAFE, "fr")
    assert "Arrêt du TF 8C_901/2026" in page_fr and "Texte intégral enregistré localement" in page_fr


def test_original_page_for_an_unknown_judgement_says_so(backend):
    status, page = backend.original_page("8C_999_2026")
    assert status == 404 and "lokal nicht gespeichert" in page and "8C_999/2026" in page


def test_original_page_escapes_the_text_and_only_links_to_web_addresses(tmp_path, summarizer):
    record = json.loads((CRAWL_DIR / "8C_901_2026.json").read_text(encoding="utf-8"))
    record["judgement_text"] = 'Erwägung 1 <script>alert("x")</script> & <img src=x onerror=alert(1)>'
    record["url"] = "javascript:alert(1)"
    crawl = tmp_path / "crawl"
    crawl.mkdir()
    (crawl / "8C_901_2026.json").write_text(json.dumps(record), encoding="utf-8")
    status, page = Backend(tmp_path / "s.db", crawl, lambda: summarizer).original_page(SAFE)
    assert status == 200
    assert "<script>" not in page and "<img" not in page and "javascript:" not in page
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &lt;img" in page
    assert "<a " not in page  # no link for an address that is not http(s)


def test_original_page_over_http_is_html_without_scripts(backend):
    server = make_server(backend, "127.0.0.1", 0, CRAWL_DIR)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api"
    try:
        with urllib.request.urlopen(f"{base}/judgements/{SAFE}/original?lang=fr") as res:
            assert res.status == 200 and res.headers["Content-Type"] == "text/html; charset=utf-8"
            assert "default-src 'none'" in res.headers["Content-Security-Policy"]
            assert "Arrêt du TF 8C_901/2026" in res.read().decode("utf-8")
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(f"{base}/judgements/8C_999_2026/original")
        assert missing.value.code == 404 and "text/html" in missing.value.headers["Content-Type"]
    finally:
        backend.queue.stop()
        server.shutdown()
        server.server_close()
