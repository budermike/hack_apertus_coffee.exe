"""Prompt templates for facts extraction and DE/FR writing (issues #14, #15).

Style profiles and glossary live in ``prompts/*.md`` so lawyers can edit them
without touching code. ``prompt_version()`` changes whenever a template or one
of those files changes; it is stored with every summary.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from ..triage.reference_corpus import ReferenceCase

TEMPLATE_VERSION = "v1"
PROMPTS_DIR = Path("prompts")  # current prompts (generate.Summarizer)
PROMPTS_DIR_V1 = Path("prompts/v1")  # prompts of the older SummarizerV1

FACTS_SYSTEM = """\
Du bist Juristin / Jurist im Rechtsdienst der Suva (Unfallversicherung). Du liest ein Urteil des \
Schweizerischen Bundesgerichts und hältst die Fakten für eine spätere Zusammenfassung fest.

Regeln:
- Nur was im Urteil steht. Nichts ergänzen, nichts vermuten.
- Parteien anonym («die Versicherte», «der Beschwerdeführer», «die Suva»), keine Namen.
- Erwägungsnummern genau so, wie sie im Urteil stehen (z. B. "4.4").
- Schreibe die Werte auf Deutsch.

Antworte AUSSCHLIESSLICH mit gültigem JSON in diesem Format:
{
  "rechtsgebiet": "z. B. Unfallversicherung – adäquater Kausalzusammenhang",
  "form": "grundsatz" oder "fall",
  "sachverhalt": "3-6 Sätze: versicherte Person, Ereignis, Gesundheitsschaden, Leistungen",
  "verfahren": "1-3 Sätze: Entscheid der Suva / des Versicherers, Entscheid der Vorinstanz, wer Beschwerde führt",
  "rechtsfragen": ["Frage, die das Bundesgericht beantwortet"],
  "kernerwaegungen": [{"aussage": "Rechtsregel oder Würdigung in einem Satz", "erwaegung": "4.4"}],
  "gesetzesartikel": ["Art. 6 Abs. 2 UVG"],
  "ergebnis": "Dispositiv in einem Satz",
  "bedeutung_suva": "Warum das Urteil für die Praxis der Suva wichtig ist, 1-2 Sätze"
}
"form" ist "grundsatz", wenn eine Rechtsfrage im Vordergrund steht und der Sachverhalt nebensächlich ist, sonst "fall".
"""

WRITE_SYSTEM = {
    "de": """\
Du bist Juristin / Jurist im Rechtsdienst der Suva und schreibst die interne Zusammenfassung \
eines Bundesgerichtsurteils auf Deutsch, genau im Stil der Suva-Beispiele.

{style}

{glossary}

Ausgabe: nur der Text der Zusammenfassung (ohne Titel), Absätze durch eine Leerzeile getrennt.
""",
    "fr": """\
Tu es juriste au service juridique de la Suva et tu rédiges le résumé interne d'un arrêt du \
Tribunal fédéral en français, exactement dans le style des exemples Suva. Rédige directement en \
français juridique romand ; ce n'est pas une traduction.

{style}

{glossary}

Sortie : uniquement le texte du résumé (sans titre), paragraphes séparés par une ligne vide.
""",
}

_LABELS = {
    "de": {
        "examples": "Beispiele von Suva-Zusammenfassungen (Stil und Länge übernehmen, Inhalt nicht):",
        "case": "Zu zusammenfassendes Urteil",
        "facts": "Festgehaltene Fakten (JSON)",
        "outcome": "Dispositiv laut Urteil (verbindlich)",
        "considerations": "Im Urteil vorhandene Erwägungen (nur diese zitieren)",
        "passages": "Wortlaut der wichtigsten Erwägungen",
        "fix": "Die letzte Fassung hatte diese Fehler, korrigiere sie:",
        "task": "Schreibe jetzt die Zusammenfassung auf Deutsch.",
    },
    "fr": {
        "examples": "Exemples de résumés Suva (reprendre le style et la longueur, pas le contenu) :",
        "case": "Arrêt à résumer",
        "facts": "Faits relevés (JSON, en allemand)",
        "outcome": "Dispositif selon l'arrêt (contraignant)",
        "considerations": "Considérants existant dans l'arrêt (ne citer que ceux-ci)",
        "passages": "Texte des considérants principaux",
        "fix": "La dernière version contenait ces erreurs, corrige-les :",
        "task": "Rédige maintenant le résumé en français.",
    },
}


@lru_cache(maxsize=None)
def _read(name: str, prompts_dir: str) -> str:
    return (Path(prompts_dir) / name).read_text(encoding="utf-8").strip()


def style(language: str, prompts_dir: Path = PROMPTS_DIR_V1) -> str:
    return _read(f"style_{language}.md", str(prompts_dir))


def glossary(prompts_dir: Path = PROMPTS_DIR_V1) -> str:
    return _read("glossary.md", str(prompts_dir))


def prompt_version(prompts_dir: Path = PROMPTS_DIR_V1) -> str:
    parts = [TEMPLATE_VERSION, FACTS_SYSTEM, *WRITE_SYSTEM.values(),
             style("de", prompts_dir), style("fr", prompts_dir), glossary(prompts_dir)]
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:10]
    return f"{TEMPLATE_VERSION}-{digest}"


def facts_user(case_id: str, judgement_excerpt: str) -> str:
    return f"Urteil {case_id}:\n\n{judgement_excerpt}"


def write_system(language: str, prompts_dir: Path = PROMPTS_DIR_V1) -> str:
    return WRITE_SYSTEM[language].format(style=style(language, prompts_dir), glossary=glossary(prompts_dir))


def write_user(
    language: str,
    title: str,
    facts: dict[str, Any],
    outcome_text: str,
    considerations: Sequence[str],
    passages: str,
    examples: Sequence[ReferenceCase],
    fix_notes: Sequence[str] = (),
) -> str:
    lab = _LABELS[language]
    blocks: list[str] = []
    if examples:
        shots = []
        for ex in examples:
            text = ex.text_de if language == "de" else ex.text_fr
            shots.append(f"### {ex.title}\n{text}")
        blocks.append(lab["examples"] + "\n\n" + "\n\n".join(shots))
    blocks.append(f"## {lab['case']}: {title}")
    blocks.append(f"{lab['facts']}:\n{json.dumps(facts, ensure_ascii=False, indent=2)}")
    blocks.append(f"{lab['outcome']}: {outcome_text}")
    if considerations:
        blocks.append(f"{lab['considerations']}: {', '.join(considerations)}")
    if passages:
        blocks.append(f"{lab['passages']}:\n{passages}")
    if fix_notes:
        blocks.append(lab["fix"] + "\n" + "\n".join(f"- {n}" for n in fix_notes))
    blocks.append(lab["task"])
    return "\n\n".join(blocks)


def prompt_files_version(prompts_dir: Path = PROMPTS_DIR) -> str:
    """'v3-<hash>' over every prompt file of ``generate.Summarizer``, stored with each summary."""
    digest = hashlib.sha256()
    for path in sorted(Path(prompts_dir).glob("*.md")):
        digest.update(path.read_bytes())
    return f"v3-{digest.hexdigest()[:10]}"
