import json
from datetime import date

import pytest

from hack_apertus_coffee_exe.eval import summary_eval as se
from hack_apertus_coffee_exe.summary.judgement import Judgement

JUDGEMENT = """Sachverhalt:
A. Der Versicherte stürzte am 3. März 2022 von einer Leiter und verletzte sich an der Schulter.

Erwägungen:
1. Die Beschwerde ist zulässig. Das Bundesgericht prüft Rechtsverletzungen von Amtes wegen.
2. Streitig ist der natürliche Kausalzusammenhang zwischen dem Sturz und den Schulterbeschwerden nach Art. 6 Abs. 1 UVG.
2.1 Die Vorinstanz stützte sich auf das Gutachten und verneinte den Kausalzusammenhang mit überwiegender Wahrscheinlichkeit.
3. Die Beschwerde ist somit abzuweisen.

Demnach erkennt das Bundesgericht:
1. Die Beschwerde wird abgewiesen.
"""


def _pair(text_de: str = "Der natürliche Kausalzusammenhang zwischen Sturz und Schulterbeschwerden fehlt (Art. 6 Abs. 1 UVG).",
          text_fr: str = "Le lien de causalité naturelle fait défaut (art. 6 al. 1 LAA).") -> se.Pair:
    judgement = Judgement(case_id="8C_1/2025", text=JUDGEMENT, language="de", decision_date=date(2025, 5, 1))
    return se.Pair("8C_1/2025", "Frei", "de", text_de, text_fr, judgement)


def test_style_metrics_counts_form_and_citations():
    m = se.style_metrics("In diesem Urteil hielt das Bundesgericht fest, dass X gilt (E. 2.1).\n\n"
                         "Die Beschwerde wurde abgewiesen.", "de")
    assert m["absaetze"] == 2
    assert m["erwaegungshinweise"] == 1
    assert m["einstieg"] == "urteil"
    assert m["ergebnis_im_schluss"] is True


@pytest.mark.parametrize("text, kind", [
    ("Erwartet der Versicherer einen Berufswechsel, muss er dies mitteilen.", "regel"),
    ("Der Versicherte stürzte 2021 von einem Gerüst.", "fall"),
    ("Dans cet arrêt, le Tribunal fédéral a rappelé que X.", "urteil"),
])
def test_opening_type(text, kind):
    assert se.opening_type(text) == kind


def test_judgement_segments_split_facts_considerations_operative_part():
    labels = [label for label, _ in se.judgement_segments(JUDGEMENT)]
    assert labels[0] == "sachverhalt"
    assert "E 2" in labels and labels[-1] == "dispositiv"


def test_content_selection_maps_summary_to_considerations():
    sel = se.content_selection(_pair())
    assert sel["anteil_erwaegungen"] == 1.0
    assert sel["erwaegungen_genutzt"] >= 1


def test_content_match_flags_invented_considerations_and_outcome():
    pair = _pair()
    m = se.content_match(pair.text_de, "Die Beschwerde wurde abgewiesen (E. 9.9). Art. 6 Abs. 1 UVG gilt.", pair, "de")
    assert m["erwaegungen_erfunden"] == 1
    assert m["ergebnis_genannt"] is True
    assert m["artikel_jaccard"] == 1.0


def test_glossary_usage_matches_stems_and_whole_abbreviations(tmp_path):
    path = tmp_path / "g.md"
    path.write_text("| Deutsch | Français |\n|---|---|\n| UVG | LAA |\n| Taggeld | indemnités journalières |\n")
    pairs = [_pair("Die Taggelder nach UVG.", "Il faut laisser les indemnités journalières.")]
    g = se.glossary_usage(pairs, path).set_index("deutsch")
    assert g.loc["UVG", "texte_de"] == 1 and g.loc["UVG", "textes_fr"] == 0   # "laisser" is no LAA hit
    assert g.loc["Taggeld", "texte_de"] == 1 and g.loc["Taggeld", "textes_fr"] == 1


def test_generate_and_judge_use_caches(tmp_path):
    class FakeResult:
        summary_de, summary_fr, examples, model, prompt_version, attempts, facts = "de", "fr", [], "m", "v", {}, {}

    class FakeSummarizer:
        def summarize(self, judgement):
            return FakeResult()

    out = tmp_path / "gen.jsonl"
    cache = se.generate([_pair()], out, make_summarizer=FakeSummarizer, workers=1)
    assert cache.rows["8C_1/2025"]["de"] == "de"

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    content = json.dumps({**{k: 4 for k in se.JUDGE_CRITERIA}, "begruendung": "ok"})
                    msg = type("M", (), {"content": content})
                    return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    items = se.judge_items([_pair()], cache.rows, "v1")
    judged = se.judge(items, tmp_path / "judge.jsonl", client=FakeClient(), workers=1)
    assert len(judged.rows) == 2 and judged.rows["v1|8C_1/2025|de"]["gesamt"] == 4


def test_style_closeness_is_one_for_identical_text_and_lower_for_a_different_one():
    suva = "Erwartet der Versicherer einen Berufswechsel, muss er dies mitteilen.\n\nIm vorliegenden Fall fehlte die Frist."
    same = se.style_closeness(suva, suva, "de")
    other = se.style_closeness("In diesem Urteil wurde die Beschwerde abgewiesen.", suva, "de")
    assert same["stilnaehe"] == pytest.approx(1.0)
    assert other["stilnaehe"] < 0.6 and other["einstieg"] == 0.0
