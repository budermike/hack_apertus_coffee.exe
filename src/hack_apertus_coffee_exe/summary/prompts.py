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


ALIGN_SYSTEM = {
    "de": """\
Du bist Juristin / Jurist im Rechtsdienst der Suva und überarbeitest eine deutsche Urteilszusammenfassung.

Wichtige Regeln:
1. Wenn eine geänderte Vorlage in der anderen Sprache mitgegeben wird, ist diese geänderte Vorlage MASSGEBEND: Übernimm alle inhaltlichen Änderungen, Ergänzungen, Kürzungen, Bewertungen und Streichungen 1:1 ins Deutsche (gleicher Umfang, gleiche Aussagen). Behalte nichts aus der alten deutschen Fassung bei, was in der geänderten Vorlage gestrichen oder ersetzt wurde.
   Inhaltlich 1:1 heisst nicht Wort für Wort: Schreibe in der deutschen Rechtssprache der Schweiz, mit den festen Begriffen und Abkürzungen des Glossars (LAA wird UVG, «consid.» wird «E.»), so wie es eine Juristin der Suva auf Deutsch formuliert.
2. Wenn eine ausdrückliche Anweisung mitgegeben wird, setze diese Anweisung direkt und vollständig um.
3. Verwende korrekte Schweizer Rechtsterminologie im Suva-Stil:
{glossary}

Ausgabe: ausschliesslich der überarbeitete deutsche Text (ohne Titel, ohne Vorbemerkung, ohne Erklärungen).
""",
    "fr": """\
Tu es juriste au service juridique de la Suva et tu adaptes un résumé d'arrêt en français juridique suisse.

Règles impératives :
1. Lorsqu'une version modifiée dans l'autre langue est fournie, cette version modifiée FAIT FOI : reprends fidèlement tous les ajouts, raccourcissements, reformulations, appréciations et suppressions (même longueur et même contenu). Ne conserve aucun passage de l'ancienne version française qui a été supprimé ou remplacé dans la version modifiée.
   Fidèle au contenu ne veut pas dire mot à mot : écris dans la langue juridique française de Suisse, avec les termes et abréviations consacrés du glossaire (UVG devient LAA, «E.» devient «consid.»), comme le ferait une juriste romande de la Suva.
2. Lorsqu'une instruction explicite est donnée, applique-la directement et intégralement.
3. Respecte la terminologie juridique romande de la Suva :
{glossary}

Sortie : uniquement le texte français révisé (sans titre, sans préambule ni commentaire).
""",
}


def align_system(language: str, prompts_dir: Path = PROMPTS_DIR) -> str:
    """System prompt for adapting/refining one summary language in Suva style (#65)."""
    if language not in ALIGN_SYSTEM:
        raise ValueError(f"language must be 'de' or 'fr', got {language!r}")
    return ALIGN_SYSTEM[language].format(glossary=glossary(prompts_dir))


def align_user(
    *,
    target_language: str,
    target_text: str,
    source_language: str | None = None,
    source_text: str | None = None,
    instruction: str | None = None,
    facts: dict[str, Any] | None = None,
) -> str:
    """User prompt asking Apertus v1.5 to align or adjust one language (#65)."""
    if target_language not in _LABELS:
        raise ValueError(f"language must be 'de' or 'fr', got {target_language!r}")
    blocks: list[str] = []
    has_source = bool(source_text and source_text.strip() and source_language)
    if target_language == "de":
        if has_source:
            blocks.append(
                f"Massgebende geänderte Fassung ({source_language.upper()}), an die der deutsche Text 1:1 angeglichen werden muss:\n{source_text.strip()}"
            )
            if instruction:
                blocks.append(f"Zusätzliche Anweisung:\n{instruction.strip()}")
            blocks.append(f"Bisherige deutsche Fassung (nur als sprachliche Referenz für unveränderte Stellen):\n{target_text.strip()}")
            blocks.append("Schreibe jetzt ausschliesslich die angeglichene deutsche Fassung (ohne Titel).")
        else:
            blocks.append("Überarbeite die bestehende deutsche Zusammenfassung strikt gemäss der folgenden Anweisung.")
            if instruction:
                blocks.append(f"Anweisung:\n{instruction.strip()}")
            if facts:
                blocks.append(f"Fakten aus dem Urteil (nichts hinzuerfinden):\n{json.dumps(facts, ensure_ascii=False, indent=2)}")
            blocks.append(f"Bisherige deutsche Fassung:\n{target_text.strip()}")
            blocks.append("Gib ausschliesslich die überarbeitete deutsche Zusammenfassung ohne Titel zurück.")
    else:
        if has_source:
            blocks.append(
                f"Version modifiée faisant foi ({source_language.upper()}), sur laquelle aligner intégralement le texte français :\n{source_text.strip()}"
            )
            if instruction:
                blocks.append(f"Instruction complémentaire :\n{instruction.strip()}")
            blocks.append(f"Ancienne version française (uniquement comme repère terminologique pour les passages inchangés) :\n{target_text.strip()}")
            blocks.append("Rédige maintenant uniquement la version française alignée (sans titre).")
        else:
            blocks.append("Révise le résumé français existant strictement selon l'instruction ci-dessous.")
            if instruction:
                blocks.append(f"Instruction :\n{instruction.strip()}")
            if facts:
                blocks.append(f"Faits établis dans l'arrêt (ne rien inventer) :\n{json.dumps(facts, ensure_ascii=False, indent=2)}")
            blocks.append(f"Version française actuelle :\n{target_text.strip()}")
            blocks.append("Retourne uniquement le résumé français révisé, sans titre.")
    return "\n\n".join(blocks)


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
