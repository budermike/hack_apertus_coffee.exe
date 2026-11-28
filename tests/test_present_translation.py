"""Texts in the language of the UI (present/translation.py). Apertus is faked, no network."""

import json
import re
import threading
import urllib.request
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hack_apertus_coffee_exe.present.judgements import JudgementList
from hack_apertus_coffee_exe.present.translation import SYSTEM, Localizer, Translator
from hack_apertus_coffee_exe.summary import store
from hack_apertus_coffee_exe.summary.api import Backend, make_server
from hack_apertus_coffee_exe.triage.config import TriageConfig


class FakeClient:
    """Answers like Apertus would: every numbered line comes back marked with the target language."""

    def __init__(self, drop=(), fail_after=None, error=None):
        self.calls = []
        self.drop = set(drop)
        self.fail_after = fail_after
        self.error = error or RuntimeError("401: key sk-...1234 rejected")

    def call(self, stage, key, system, user, schema, max_tokens):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise self.error
        self.calls.append(SimpleNamespace(stage=stage, key=key, system=system, user=user, schema=schema))
        target = stage.rsplit("_", 1)[1]
        items = []
        for nr, text in re.findall(r"^(\d+)\. (.*)$", user, flags=re.M):
            if text not in self.drop:
                items.append({"a_nr": int(nr), "b_text": f"[{target}] {text}"})
        return {"items": items}


def translator(tmp_path, client, **kwargs):
    return Translator(lambda: client, tmp_path / "cache.json", glossary="| UVG | LAA |", **kwargs)


def test_texts_are_carried_over_once_and_then_come_from_the_file(tmp_path):
    client = FakeClient()
    first = translator(tmp_path, client)
    out = first.translate(["Invalidenrente; Revision", " Art. 6 Abs. 1 UVG ", "", "Invalidenrente; Revision"], "fr")
    assert out == {"Invalidenrente; Revision": "[fr] Invalidenrente; Revision", "Art. 6 Abs. 1 UVG": "[fr] Art. 6 Abs. 1 UVG"}
    assert len(client.calls) == 1 and client.calls[0].stage == "translate_fr"
    assert first.translate(["Art. 6 Abs. 1 UVG"], "fr") == {"Art. 6 Abs. 1 UVG": "[fr] Art. 6 Abs. 1 UVG"}
    assert len(client.calls) == 1                                    # nothing asked again

    # a new process reads the file; the other direction is separate
    again = translator(tmp_path, FakeClient(fail_after=0))
    assert again.lookup("Invalidenrente; Revision", "fr") == "[fr] Invalidenrente; Revision"
    assert again.lookup("Invalidenrente; Revision", "de") is None
    assert again.translate(["Invalidenrente; Revision"], "fr") == {"Invalidenrente; Revision": "[fr] Invalidenrente; Revision"}


def test_the_prompt_asks_for_legal_wording_not_word_for_word(tmp_path):
    client = FakeClient()
    translator(tmp_path, client).translate(["Einspracheentscheid"], "fr")
    translator(tmp_path, client).translate(["décision sur opposition"], "de")
    fr, de = client.calls[0].system, client.calls[1].system
    assert "Pas de traduction mot à mot" in fr and "UVG devient LAA" in fr and "| UVG | LAA |" in fr
    assert "ne rien ajouter, ne rien omettre" in fr
    assert "Keine wörtliche Übersetzung" in de and "LAA wird UVG" in de and "| UVG | LAA |" in de
    assert "{glossary}" not in fr + de
    assert client.calls[0].user == "1. Einspracheentscheid"
    assert set(SYSTEM) == {"de", "fr"}


def test_long_lists_go_in_batches_and_what_apertus_leaves_out_stays_missing(tmp_path):
    client = FakeClient(drop={"Text 3"})
    tr = translator(tmp_path, client, max_items=2)
    out = tr.translate([f"Text {n}" for n in range(1, 6)], "fr")
    assert [c.user.count("\n") + 1 for c in client.calls] == [2, 2, 1]
    assert "Text 3" not in out and len(out) == 4
    assert tr.lookup("Text 3", "fr") is None                        # asked again next time
    tr.translate(["Text 3", "Text 1"], "fr")
    assert client.calls[-1].user == "1. Text 3"


def test_what_succeeded_is_kept_when_apertus_fails_later(tmp_path):
    client = FakeClient(fail_after=1)
    tr = translator(tmp_path, client, max_items=1)
    with pytest.raises(RuntimeError):
        tr.translate(["Erster Text", "Zweiter Text"], "fr")
    assert tr.lookup("Erster Text", "fr") == "[fr] Erster Text" and tr.lookup("Zweiter Text", "fr") is None
    with pytest.raises(ValueError):
        tr.translate(["x"], "it")
    assert tr.lookup("", "fr") is None


# --- Localizer: what the API offers -----------------------------------------------------

def _row(case_id, language, subject, quotes=()):
    criteria = [{"a_id": f"K{n}", "b_stelle": "E3", "c_zitat": q, "beleg_status": "exakt"} for n, q in enumerate(quotes, 1)]
    return {
        "case_id": case_id, "language": language, "final_score": 70.0, "judgement_date": "2026-04-02",
        "relevance_reason": "Begründung.", "key_topics": [c["a_id"] for c in criteria],
        "metadata": {"subject": subject, "language": language, "relevance": {"p": 0.5, "threshold": 0.05, "area": "UV", "criteria": criteria}},
    }


ROWS = [
    _row("8C_10/2026", "de", "Unfallversicherung (Invalidenrente; Revision)", ["«Der Kausalzusammenhang ist zu bejahen.»", "Zweites Zitat."]),
    _row("8C_11/2026", "fr", "Assurance-accidents (rente d'invalidité)", ["«Le lien de causalité doit être admis.»"]),
    _row("8C_12/2026", "it", "Assicurazione contro gli infortuni (rendita)"),
]
ROWS[0]["metadata"]["relevance"]["criteria"].insert(0, {"a_id": "K9", "c_zitat": 5})   # malformed: the list skips it


@pytest.fixture
def localizer(tmp_path):
    path = tmp_path / "triage.json"
    path.write_text(json.dumps(ROWS, ensure_ascii=False), encoding="utf-8")
    client = FakeClient()
    judgements = JudgementList(path, config=TriageConfig())
    loc = Localizer(translator(tmp_path, client), judgements, tmp_path / "s.db")
    loc.client = client
    return loc


def test_subjects_of_the_list_in_the_other_language(localizer):
    status, body = localizer.subjects({"lang": "fr", "ids": ["8C_10_2026", "8C_11_2026", "8C_12_2026", "9C_1_2020"]})
    assert status == 200 and body["complete"] is True
    assert body["subjects"] == {
        "8C_10_2026": "[fr] Unfallversicherung (Invalidenrente; Revision)",
        "8C_12_2026": "[fr] Assicurazione contro gli infortuni (rendita)",
    }                                                                # the French judgement keeps its own subject
    status, body = localizer.subjects({"lang": "de", "ids": ["8C_10_2026", "8C_11_2026"]})
    assert body["subjects"] == {"8C_11_2026": "[de] Assurance-accidents (rente d'invalidité)"}
    with pytest.raises(ValueError):
        localizer.subjects({"lang": "en", "ids": []})
    with pytest.raises(ValueError):
        localizer.subjects({"lang": "fr", "ids": "8C_10_2026"})


def test_the_list_delivers_stored_subjects_and_names_what_is_missing(localizer):
    items = localizer.fill_subjects(localizer.judgements.items())
    by_id = {i["id"]: i for i in items}
    assert by_id["8C_10_2026"]["subjectMissing"] == ["fr"]
    assert by_id["8C_11_2026"]["subjectMissing"] == ["de"]
    assert by_id["8C_12_2026"]["subjectMissing"] == ["de", "fr"]     # Italian: missing in both
    assert localizer.client.calls == []                              # reading the list never asks Apertus

    localizer.subjects({"lang": "fr", "ids": list(by_id)})
    by_id = {i["id"]: i for i in localizer.fill_subjects(localizer.judgements.items())}
    assert by_id["8C_10_2026"]["subject"] == {
        "de": "Unfallversicherung (Invalidenrente; Revision)",
        "fr": "[fr] Unfallversicherung (Invalidenrente; Revision)",
    }
    assert "subjectMissing" not in by_id["8C_10_2026"]
    assert by_id["8C_12_2026"]["subjectMissing"] == ["de"]


def test_quotes_and_summary_basis_of_one_judgement(localizer):
    with store.connect(localizer.db_path) as conn:
        store.save_case(conn, "8C_10/2026", "2026-04-02", "https://example.invalid/x")
        facts = {"gesetzesartikel": ["Art. 6 Abs. 1 UVG"], "kernerwaegungen": [{"aussage": "Der Kausalzusammenhang genügt.", "erwaegung": "3.2"}]}
        store.save_generated(conn, "8C_10/2026", "de", store.GeneratedSummary(text="DE", model="apertus-v1-5-8b", prompt_version="v1", coherence_ok=True, facts=facts))

    status, body = localizer.judgement("8C_10_2026", {"lang": "fr"})
    assert status == 200 and body["complete"] is True
    assert body["subject"] == "[fr] Unfallversicherung (Invalidenrente; Revision)"
    # positions as in the list: the malformed criterion is not counted, guillemets are not part of the text
    listed = next(i for i in localizer.judgements.items() if i["id"] == "8C_10_2026")["criteria"]
    assert [c["quote"] for c in listed] == ["Der Kausalzusammenhang ist zu bejahen.", "Zweites Zitat."]
    assert body["quotes"] == {"0": "[fr] Der Kausalzusammenhang ist zu bejahen.", "1": "[fr] Zweites Zitat."}
    assert body["basis"] == {
        "laws": ["[fr] Art. 6 Abs. 1 UVG"],
        "considerations": [{"erwaegung": "3.2", "aussage": "[fr] Der Kausalzusammenhang genügt."}],
    }
    assert len(localizer.client.calls) == 1                          # one request for the whole judgement

    # German UI, German judgement: nothing to do, Apertus is not asked
    status, body = localizer.judgement("8C_10/2026", {"lang": "de"})
    assert body == {"lang": "de", "complete": True, "quotes": {}} and len(localizer.client.calls) == 1
    # German UI, French judgement: quotes and subject, no basis (the facts are German already)
    status, body = localizer.judgement("8C_11_2026", {"lang": "de"})
    assert body["quotes"] == {"0": "[de] Le lien de causalité doit être admis."} and "basis" not in body
    with pytest.raises(LookupError):
        localizer.judgement("9C_1_2020", {"lang": "fr"})


def test_without_apertus_the_originals_stay_and_the_reason_is_short(localizer):
    localizer.subjects({"lang": "fr", "ids": ["8C_10_2026"]})        # one subject is stored
    localizer.translator._client_factory = lambda: FakeClient(fail_after=0)
    status, body = localizer.subjects({"lang": "fr", "ids": ["8C_10_2026", "8C_12_2026"]})
    assert status == 200 and body["complete"] is False
    assert body["subjects"] == {"8C_10_2026": "[fr] Unfallversicherung (Invalidenrente; Revision)"}
    assert body["error"] == "Apertus call failed"                    # what the gateway said is not passed on

    localizer.translator._client_factory = Mock(side_effect=ValueError("Missing API key: set LLM_API_KEY"))
    status, body = localizer.judgement("8C_10_2026", {"lang": "fr"})
    assert body["complete"] is False and "Missing API key" in body["error"] and body["quotes"] == {}
    assert body["subject"] == "[fr] Unfallversicherung (Invalidenrente; Revision)"


def test_routes(localizer, tmp_path):
    backend = Backend(tmp_path / "s.db", tmp_path, Mock())
    server = make_server(backend, "127.0.0.1", 0, tmp_path, localizer.judgements, localizer=localizer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api"

    def post(path, body):
        req = urllib.request.Request(f"{base}{path}", method="POST", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as res:
            return json.load(res)

    try:
        with urllib.request.urlopen(f"{base}/judgements?lang=fr") as res:
            assert {i["id"]: i.get("subjectMissing") for i in json.load(res)}["8C_10_2026"] == ["fr"]
        assert post("/subjects/localize", {"lang": "fr", "ids": ["8C_10_2026"]})["subjects"]["8C_10_2026"].startswith("[fr] ")
        assert post("/judgements/8C_11_2026/localize", {"lang": "de"})["quotes"] == {"0": "[de] Le lien de causalité doit être admis."}
        with urllib.request.urlopen(f"{base}/judgements?lang=fr") as res:
            assert "subjectMissing" not in {i["id"]: i for i in json.load(res)}["8C_10_2026"]
        with pytest.raises(urllib.error.HTTPError) as bad:
            post("/subjects/localize", {"lang": "en", "ids": []})
        assert bad.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as missing:
            post("/judgements/9C_1_2020/localize", {"lang": "fr"})
        assert missing.value.code == 404
    finally:
        backend.queue.stop()
        server.shutdown()
        server.server_close()
