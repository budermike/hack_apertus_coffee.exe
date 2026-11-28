"""Carry a lawyer's edit of one summary language over to the other language (issue #102).

Not a translation. The steps:

1. The edit is found here, without a model: the sentences of the edited language before and
   after the edit are compared (``source_changes``).
2. For every changed passage Apertus v1.5 gets one small task: the passage before and after,
   the wording of the judgement as the reference for the legal meaning and the other language
   text. It answers with JSON: does the change touch the substance, which sentences of the
   other language say the same (quoted word for word), and how do these sentences read now.
3. The answer is applied here: the quoted sentences are looked up in the text and replaced
   (``planned_replacement``, ``replace_sentences``). Every other sentence stays word for
   word. An answer that fails one of the checks listed in ``planned_replacement`` is not
   applied; the lawyer is told to carry that change over by hand. The checks limit what a
   wrong answer can do; they cannot tell whether the new sentence is legally right, so the
   aligned text stays marked for review.
4. Apertus marks a change that is unclear or contradicts the judgement, and a last call
   compares both languages for statements one of them lacks (``CHECK_SYSTEM``). These notes
   go to the lawyer; nothing is changed because of them.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import prompts
from .check import cited_considerations
from .judgement import Judgement, consideration_numbers, consideration_passages, excerpt, outcome_block

LANGUAGES = ("de", "fr")
MAX_CHECK_NOTES = 5  # findings of the closing comparison that are shown
CALL_TIMEOUT = 60    # seconds per Apertus call: a lawyer is waiting for the save

# A full stop after one of these does not end a sentence («Art. 6», «consid. 4.2», «al. 2»).
_ABBREVIATIONS = frozenset(
    "art abs al lit let ziff ch nr no e erw consid cons vgl cf bzw resp ca env s ss ff p pp inkl sog evtl ggf "
    "dr prof st rz n bge atf sr rs chf z b u a d h i v m c par".split()
)

_SENTENCE_END_RE = re.compile(r"[.!?][»“”\")\]]*(\s+)")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_LAST_WORD_RE = re.compile(r"(\w+)$")
_CONSIDERATION_END_RE = re.compile(r"\d\.\d+$")


@dataclass(frozen=True)
class Alignment:
    """One language after the edit of the other one was carried over."""

    text: str
    notes: list[str] = field(default_factory=list)  # what the lawyer should look at
    has_reference: bool = False  # the wording of the judgement was in the prompt


def split_sentences(text: str) -> list[tuple[str, str]]:
    """Sentences of ``text`` with the whitespace that follows each one.

    Joining all pairs gives ``text.strip()`` back, so a text that is split, changed in one
    sentence and joined again is otherwise unchanged.
    """
    clean = text.strip()
    parts: list[tuple[str, str]] = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(clean):
        gap = match.group(1)
        if not _PARAGRAPH_RE.fullmatch(gap) and not _ends_sentence(clean[start:match.start() + 1], clean[match.end():]):
            continue
        parts.append((clean[start:match.start(1)], gap))
        start = match.end()
    if start < len(clean):
        parts.append((clean[start:], ""))
    return parts


def _ends_sentence(before: str, after: str) -> bool:
    if not after or not (after[0].isupper() or after[0] in "«“„\"("):
        return False
    stem = before[:-1]
    if not before.endswith(".") or not stem or not stem[-1].isalnum():
        return True  # «!», «?», or a full stop after a bracket or quotation mark
    if _CONSIDERATION_END_RE.search(stem):
        return True  # «… E. 4.2. Die Suva …»: a consideration number, not an ordinal
    last = _LAST_WORD_RE.search(stem).group(1)
    if last.isdigit():  # «am 12. März», «2. Aufl.» go on; a year or an amount («… le 3 mai 2021. La Suva …») ends a sentence
        return len(last) >= 3
    return not (len(last) == 1 or last.lower() in _ABBREVIATIONS)


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def source_changes(before: str, after: str) -> list[dict[str, str]]:
    """What an edit changed, sentence by sentence.

    Per change ``before`` and ``after`` (one sentence each; ``before`` is empty for a new
    sentence, ``after`` for a removed one) and, word by word, what was ``removed`` and ``added``.
    ``number`` is the place of ``before`` among the sentences of the text before the edit (from 1; 0 for a new sentence).
    """
    old = [" ".join(s.split()) for s, _ in split_sentences(before)]
    new = [" ".join(s.split()) for s, _ in split_sentences(after)]
    pairs: list[tuple[str, str, int]] = []  # sentence before, after, number of the sentence before (0: new)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        # Within a changed passage every sentence is a change of its own: reworded (it has a
        # counterpart that is at least half the same), removed or new. The other language
        # often says the same in another place or order, so each one is placed separately.
        j = j1
        for i in range(i1, i2):
            match = next((k for k in range(j, j2) if _similar(old[i], new[k]) >= 0.5), None)
            if match is None:
                pairs.append((old[i], "", i + 1))
                continue
            pairs += [("", now, 0) for now in new[j:match]] + [(old[i], new[match], i + 1)]
            j = match + 1
        pairs += [("", now, 0) for now in new[j:j2]]
    changes = []
    for was, now, number in pairs:
        words_was, words_now = was.split(), now.split()
        removed, added = [], []
        for word_tag, a1, a2, b1, b2 in difflib.SequenceMatcher(a=words_was, b=words_now, autojunk=False).get_opcodes():
            if word_tag in ("replace", "delete"):
                removed.append(" ".join(words_was[a1:a2]))
            if word_tag in ("replace", "insert"):
                added.append(" ".join(words_now[b1:b2]))
        changes.append({"before": was, "after": now, "removed": " […] ".join(removed), "added": " […] ".join(added),
                        "number": number})
    return changes


def reference_text(judgement: Judgement, language: str, texts: Sequence[str],
                   considerations: Sequence[str] = (), max_chars: int = 9000) -> str:
    """Wording of the judgement that the edit is read against.

    The considerations the summaries cite (plus ``considerations``, e.g. the ones the summary
    was written from) and the block on parties and outcome; without any cited consideration
    an excerpt of the whole judgement.
    """
    known = consideration_numbers(judgement.text)
    wanted = {str(n).strip().rstrip(".") for n in considerations}
    for text in texts:
        wanted |= cited_considerations(text or "")
    numbers = sorted(wanted & known, key=lambda n: [int(p) for p in n.split(".") if p.isdigit()])
    passages = consideration_passages(judgement.text, numbers, max_chars=max_chars) if numbers else ""
    if not passages:
        return excerpt(judgement.text, max_chars)
    return f"{passages}\n\n{outcome_block(judgement, language)}"


# --- prompts ------------------------------------------------------------------------------

MAX_SENTENCES_PER_CHANGE = 3  # a change that would rewrite more sentences is not applied but noted

CHANGE_SCHEMA = {
    "type": "object",
    "properties": {
        "inhaltlich": {"type": "boolean"},
        "alt": {"type": "string"},
        "neu": {"type": "string"},
        "widerspricht_urteil": {"type": "boolean"},
        "unklar": {"type": "boolean"},
        "hinweis": {"type": "string"},
    },
    "required": ["inhaltlich", "alt", "neu", "widerspricht_urteil", "unklar", "hinweis"],
    "additionalProperties": False,
}

CHECK_SCHEMA = {
    "type": "object",
    "properties": {"abweichungen": {"type": "array", "items": {"type": "string"}}},
    "required": ["abweichungen"],
    "additionalProperties": False,
}

CHANGE_SYSTEM = {
    "de": """\
Du bist Juristin / Jurist im Rechtsdienst der Suva. Zu einem Bundesgerichtsurteil bestehen eine deutsche und eine französische Zusammenfassung. In der französischen Fassung wurde von Hand eine Stelle geändert. Du überträgst diese eine Änderung in die deutsche Fassung, sofern sie den Inhalt betrifft.

Antworte mit JSON:
1. «inhaltlich»: true, wenn die Änderung eine rechtliche Aussage, Voraussetzung, Einschränkung, Rechtsfolge oder Tatsache ändert, ergänzt oder streicht. false, wenn sie nur Wortwahl, Zeitform, Satzbau oder Rechtschreibung betrifft (z. B. «hat festgehalten» wird «hält fest», ein Synonym, ein umgestellter Satz); dann «alt»: "" und «neu»: "". Im Zweifel false.
2. «alt»: die Sätze der deutschen Fassung, die die geänderte Aussage enthalten, wörtlich und vollständig aus der deutschen Fassung kopiert (in der Regel ein Satz, höchstens drei aufeinanderfolgende). Ist die Aussage neu, kopiere den einen Satz, nach dem sie stehen soll. Leer (""), wenn die deutsche Fassung die gestrichene Aussage gar nicht enthält oder bereits sagt, was die Änderung sagt.
3. «neu»: der Text, der «alt» ersetzt. KEINE ÜBERSETZUNG der bearbeiteten Fassung: Gehe vom Wortlaut in «alt» aus und ändere daran nur, was die Änderung verlangt; alles Übrige bleibt wörtlich.
   - geänderte Aussage: derselbe Satz mit der geänderten Aussage;
   - neue Aussage: der Satz aus «alt» unverändert, danach ein neuer deutscher Satz mit der neuen Aussage;
   - gestrichene Aussage: der Satz ohne diese Aussage; enthält er sonst nichts, ist «neu» leer ("").
4. Was von Hand geändert wurde, gilt. Übernimm die Änderung auch dann, wenn sie dem Urteilstext widerspricht, und setze in diesem Fall «widerspricht_urteil»: true.
5. Der Urteilstext ist die verbindliche Referenz für die juristische Bedeutung und den Zusammenhang: Entnimm ihm, was mit der geänderten Stelle gemeint ist und wie die Begriffe lauten. Füge daraus nichts hinzu, was die bearbeitete Fassung nicht sagt (keine Beträge, Daten oder Artikel aus dem Urteil), und erfinde nichts.
6. Schreibe in der deutschen Rechtssprache der Schweiz, mit den festen Begriffen und Abkürzungen des Glossars (LAA wird UVG, «consid.» wird «E.»), so wie es eine Juristin der Suva auf Deutsch schreibt:
{glossary}
7. «unklar»: true, wenn nicht eindeutig ist, was die Änderung rechtlich aussagen will. Dann nichts hineininterpretieren.
8. «hinweis»: nur wenn «unklar» oder «widerspricht_urteil» true ist: ein Satz auf Französisch (die Sprache der bearbeiteten Fassung), was unklar ist oder was das Urteil stattdessen sagt. Sonst "".
""",
    "fr": """\
Tu es juriste au service juridique de la Suva. Un arrêt du Tribunal fédéral fait l'objet d'un résumé en allemand et d'un résumé en français. Un passage de la version allemande a été modifié à la main. Tu reportes cette seule modification dans la version française, pour autant qu'elle touche le fond.

Réponds en JSON :
1. «inhaltlich» : true si la modification change, ajoute ou supprime une affirmation juridique, une condition, une restriction, une conséquence juridique ou un fait. false si elle ne touche que le choix des mots, le temps du verbe, la syntaxe ou l'orthographe (p. ex. «hat festgehalten» devient «hält fest», un synonyme, une phrase réordonnée) ; alors «alt» : "" et «neu» : "". En cas de doute, false.
2. «alt» : les phrases de la version française qui contiennent l'affirmation modifiée, copiées mot pour mot et en entier depuis la version française (en règle générale une phrase, trois phrases consécutives au plus). Si l'affirmation est nouvelle, copie la seule phrase après laquelle elle doit figurer. Vide ("") si la version française ne contient pas l'affirmation supprimée ou dit déjà ce que dit la modification.
3. «neu» : le texte qui remplace «alt». PAS DE TRADUCTION de la version modifiée : pars du libellé de «alt» et n'y change que ce que la modification exige ; tout le reste demeure mot pour mot.
   - affirmation modifiée : la même phrase avec l'affirmation modifiée ;
   - affirmation nouvelle : la phrase de «alt» telle quelle, suivie d'une nouvelle phrase française contenant la nouvelle affirmation ;
   - affirmation supprimée : la phrase sans cette affirmation ; si elle ne contient rien d'autre, «neu» est vide ("").
4. Ce qui a été modifié à la main fait foi. Reprends la modification même si elle contredit le texte de l'arrêt, et mets dans ce cas «widerspricht_urteil» : true.
5. Le texte de l'arrêt est la référence contraignante pour le sens juridique et le contexte : tires-en ce que signifie le passage modifié et les termes exacts. N'y ajoute rien que la version modifiée ne dise pas (ni montants, ni dates, ni articles tirés de l'arrêt), et n'invente rien.
6. Écris dans la langue juridique française de Suisse, avec les termes et abréviations consacrés du glossaire (UVG devient LAA, «E.» devient «consid.»), comme le ferait une juriste romande de la Suva :
{glossary}
7. «unklar» : true s'il n'est pas clair ce que la modification veut dire juridiquement. Dans ce cas, n'interprète rien.
8. «hinweis» : seulement si «unklar» ou «widerspricht_urteil» vaut true : une phrase en allemand (la langue de la version modifiée) disant ce qui est peu clair ou ce que dit l'arrêt à la place. Sinon "".
""",
}

# A sentence that was removed as a whole: Apertus only quotes the sentence of the other language
# that says the same. One field, no judgement text in the prompt: with the full answer of
# CHANGE_SCHEMA, Apertus 70B took 38 s for 500 tokens and ran on after the empty «neu» (28.11.2026).
DROP_SCHEMA = {
    "type": "object",
    "properties": {"alt": {"type": "string"}},
    "required": ["alt"],
    "additionalProperties": False,
}
DROP_SYSTEM = {
    "de": """\
Du vergleichst die französische und die deutsche Zusammenfassung desselben Bundesgerichtsurteils. Ein Satz der französischen Zusammenfassung wurde gestrichen. Kopiere in «alt» wörtlich den einen Satz der deutschen Zusammenfassung, der dieselbe Aussage enthält: genau ein Satz, unverändert aus der deutschen Zusammenfassung übernommen. Enthält kein deutscher Satz diese Aussage, ist «alt» leer (""). Antworte ausschliesslich mit JSON.
""",
    "fr": """\
Tu compares le résumé allemand et le résumé français du même arrêt du Tribunal fédéral. Une phrase du résumé allemand a été supprimée. Copie dans «alt», mot pour mot, la seule phrase du résumé français qui contient la même affirmation : exactement une phrase, reprise telle quelle du résumé français. Si aucune phrase française ne contient cette affirmation, «alt» est vide (""). Réponds uniquement en JSON.
""",
}
DROP_LABELS = {"de": ("Deutsche Zusammenfassung", "Gestrichener französischer Satz"),
               "fr": ("Résumé français", "Phrase allemande supprimée")}

BACK_SCHEMA = {
    "type": "object",
    "properties": {"satz": {"type": "integer"}},
    "required": ["satz"],
    "additionalProperties": False,
}

# Cross-check before a sentence is changed or removed: asked the other way round, does Apertus
# name the edited sentence for the sentence it wants to touch? (Prompt in the language of that sentence.)
BACK_SYSTEM = {
    "de": """\
Du vergleichst zwei Zusammenfassungen desselben Bundesgerichtsurteils. Du erhältst einen deutschen Satz und eine nummerierte Liste französischer Sätze. Gib in «satz» die Nummer des französischen Satzes an, der dieselbe Aussage enthält wie der deutsche Satz. Entspricht ihm keiner, gib 0 an. Antworte ausschliesslich mit JSON.
""",
    "fr": """\
Tu compares deux résumés du même arrêt du Tribunal fédéral. Tu reçois une phrase en français et une liste numérotée de phrases en allemand. Indique dans «satz» le numéro de la phrase allemande qui contient la même affirmation que la phrase française. Si aucune ne lui correspond, indique 0. Réponds uniquement en JSON.
""",
}
BACK_LABELS = {"de": ("Deutscher Satz", "Französische Sätze"), "fr": ("Phrase française", "Phrases allemandes")}
BACK_CHECK = True  # default of ``align_edit(back_check=...)``

CHECK_SYSTEM = {
    "de": """\
Du bist Juristin / Jurist im Rechtsdienst der Suva und vergleichst die deutsche und die französische Zusammenfassung desselben Bundesgerichtsurteils. Beide sollen dieselben rechtlichen Aussagen, Voraussetzungen, Einschränkungen und Rechtsfolgen enthalten; im Wortlaut dürfen sie sich unterscheiden.

Nenne in «abweichungen» jede Stelle, an der eine Fassung eine rechtliche Aussage, Voraussetzung, Einschränkung oder Rechtsfolge enthält, die in der anderen fehlt oder ihr widerspricht: je ein kurzer Satz auf Deutsch, der sagt, welche Fassung was sagt. Höchstens fünf, die wichtigsten zuerst. Unterschiede in Wortwahl, Satzbau, Reihenfolge oder Ausführlichkeit zählen nicht. Erfinde nichts. Sagen beide dasselbe, ist die Liste leer. Antworte ausschliesslich mit JSON.
""",
    "fr": """\
Tu es juriste au service juridique de la Suva et tu compares le résumé allemand et le résumé français du même arrêt du Tribunal fédéral. Les deux doivent contenir les mêmes affirmations juridiques, conditions, restrictions et conséquences juridiques ; leur formulation peut différer.

Indique dans «abweichungen» chaque endroit où une version contient une affirmation juridique, une condition, une restriction ou une conséquence juridique qui manque dans l'autre ou la contredit : une phrase courte en français chacun, disant quelle version dit quoi. Cinq au plus, les plus importants d'abord. Les différences de vocabulaire, de syntaxe, d'ordre ou de niveau de détail ne comptent pas. N'invente rien. Si les deux disent la même chose, la liste est vide. Réponds uniquement en JSON.
""",
}

_LABELS = {
    "de": {
        "names": {"de": "deutsche", "fr": "französische"},
        "reference": "Urteilstext (verbindliche Referenz für Bedeutung und Begriffe, Auszug)",
        "missing": "nicht verfügbar",
        "change": "Von Hand geänderte Stelle der {source}n Fassung",
        "kinds": {"changed": "Aussage geändert", "removed": "Stelle gestrichen", "added": "Stelle neu eingefügt"},
        "before": "vorher", "after": "nachher", "removed": "gestrichene Wörter", "added": "neue Wörter",
        "target": "Die {target} Fassung",
        "tasks": {
            "replace": "Aufgabe: «{removed}» wurde durch «{added}» ersetzt. Kopiere in «alt» die Sätze der {target}n Fassung, die die Aussage «{removed}» enthalten, und gib sie in «neu» mit der neuen Aussage wieder.",
            "delete": "Aufgabe: «{removed}» wurde gestrichen. Kopiere in «alt» die Sätze der {target}n Fassung, die genau diese gestrichene Aussage enthalten (nicht den Rest des Satzes unter «vorher»), und gib sie in «neu» ohne diese Aussage wieder; enthält ein Satz nur diese Aussage, ist «neu» leer.",
            "extend": "Aufgabe: «{added}» wurde ergänzt. Kopiere in «alt» den Satz der {target}n Fassung, der dem Satz unter «vorher» entspricht, und gib ihn in «neu» mit dieser zusätzlichen Aussage wieder.",
            "insert": "Aufgabe: Die Stelle unter «nachher» ist neu. Kopiere in «alt» den Satz der {target}n Fassung, nach dem sie stehen soll, und gib ihn in «neu» unverändert wieder, gefolgt von einem neuen Satz, der nur diese Aussage enthält.",
        },
        "task": "«alt» und «neu» sind ausschliesslich auf Deutsch, «hinweis» auf Französisch. Gib jetzt das JSON.",
        "check_failed": "Die Schlussprüfung der beiden Fassungen konnte nicht durchgeführt werden.",
        "version": "Die {name} Fassung",
        "not_applied": "Diese Änderung konnte Apertus nicht sicher in die andere Sprache übertragen, bitte dort von Hand nachführen: «{change}»",
        "unclear": "Apertus hält diese Änderung für unklar und hat sie nicht übertragen: «{change}»",
        "contradicts": "Übertragen, widerspricht aber nach Apertus dem Urteilstext: «{change}»",
        "too_many": "Es wurden mehr als {limit} Stellen geändert. Die übrigen hat Apertus nicht übertragen, bitte von Hand nachführen.",
    },
    "fr": {
        "names": {"de": "allemande", "fr": "française"},
        "reference": "Texte de l'arrêt (référence contraignante pour le sens et les termes, extrait)",
        "missing": "non disponible",
        "change": "Passage de la version {source} modifié à la main",
        "kinds": {"changed": "affirmation modifiée", "removed": "passage supprimé", "added": "passage ajouté"},
        "before": "avant", "after": "après", "removed": "mots supprimés", "added": "mots ajoutés",
        "target": "La version {target}",
        "tasks": {
            "replace": "Tâche : «{removed}» a été remplacé par «{added}». Copie dans «alt» les phrases de la version {target} qui contiennent l'affirmation «{removed}» et rends-les dans «neu» avec la nouvelle affirmation.",
            "delete": "Tâche : «{removed}» a été supprimé. Copie dans «alt» les phrases de la version {target} qui contiennent précisément cette affirmation supprimée (et non le reste de la phrase sous «avant») et rends-les dans «neu» sans cette affirmation ; si une phrase ne contient que cette affirmation, «neu» est vide.",
            "extend": "Tâche : «{added}» a été ajouté. Copie dans «alt» la phrase de la version {target} qui correspond à la phrase sous «avant» et rends-la dans «neu» avec cette affirmation supplémentaire.",
            "insert": "Tâche : le passage sous «après» est nouveau. Copie dans «alt» la phrase de la version {target} après laquelle il doit figurer et rends-la telle quelle dans «neu», suivie d'une nouvelle phrase qui ne contient que cette affirmation.",
        },
        "task": "«alt» et «neu» sont entièrement en français, «hinweis» en allemand. Donne maintenant le JSON.",
        "check_failed": "Le contrôle final des deux versions n'a pas pu être effectué.",
        "version": "La version {name}",
        "not_applied": "Apertus n'a pas pu reporter cette modification de manière sûre dans l'autre langue, à y reprendre à la main : «{change}»",
        "unclear": "Apertus juge cette modification peu claire et ne l'a pas reportée : «{change}»",
        "contradicts": "Reportée, mais selon Apertus elle contredit le texte de l'arrêt : «{change}»",
        "too_many": "Plus de {limit} passages ont été modifiés. Apertus n'a pas reporté les autres, à reprendre à la main.",
    },
}


def change_system(target_lang: str, prompts_dir: Path = prompts.PROMPTS_DIR) -> str:
    if target_lang not in CHANGE_SYSTEM:
        raise ValueError(f"language must be one of {LANGUAGES}, got {target_lang!r}")
    return CHANGE_SYSTEM[target_lang].format(glossary=prompts.glossary(prompts_dir))


def change_user(target_lang: str, source_lang: str, change: dict[str, str], target_text: str, reference: str = "") -> str:
    lab = _LABELS[target_lang]
    kind = "changed" if change["before"] and change["after"] else "removed" if change["before"] else "added"
    lines = [f"{lab['change'].format(source=lab['names'][source_lang])} ({lab['kinds'][kind]}):"]
    lines += [f"  {lab[key]}: {change[key]}" for key in ("before", "after", "removed", "added") if change.get(key)]
    return "\n\n".join([
        f"{lab['reference']}:\n{reference.strip() or lab['missing']}",
        f"{lab['target'].format(target=lab['names'][target_lang])}:\n{target_text.strip()}",
        "\n".join(lines),
        lab["tasks"][_task(change)].format(target=lab["names"][target_lang], **change) + " " + lab["task"],
    ])


def _task(change: dict[str, str]) -> str:
    """Kind of a change within a sentence (words replaced, removed: ``delete``, added: ``extend``)
    or a new sentence (``insert``). A sentence removed as a whole has its own question (``DROP_SYSTEM``)."""
    return ("replace" if change["removed"] and change["added"] else "delete" if change["removed"]
            else "extend" if change["before"] else "insert")


def check_user(target_lang: str, texts: dict[str, str]) -> str:
    lab = _LABELS[target_lang]
    return "\n\n".join(f"{lab['version'].format(name=lab['names'][lang])}:\n{texts[lang].strip()}" for lang in LANGUAGES)


# --- applying an answer -------------------------------------------------------------------

MIN_QUOTE_MATCH = 0.75  # how closely a sentence Apertus quotes must match a sentence of the text
MIN_KEPT = 0.6          # how much of the old wording a replacement must keep
MAX_CHANGES = 12        # changed sentences carried over per save (one Apertus call each); the rest is noted

_CONTENT_WORD_RE = re.compile(r"[^\W\d_]{4,}")


def _locate(sentences: Sequence[tuple[str, str]], quoted: str) -> int:
    """Number (from 1) of the sentence that ``quoted`` copies, 0 when none is close enough."""
    if not sentences:
        return 0
    score, number = max((_similar(quoted, sentence), n) for n, (sentence, _) in enumerate(sentences, start=1))
    return number if score >= MIN_QUOTE_MATCH else 0


def _content_words(text: str) -> set[str]:
    return {word.lower() for word in _CONTENT_WORD_RE.findall(text)}


_FIGURE_RE = re.compile(r"\d+")


def _transfer(actual: str, quoted: str, new: str) -> str | None:
    """Apply the change ``quoted`` -> ``new`` to ``actual``, the sentence that ``quoted`` copies loosely.

    Apertus sometimes quotes a sentence with a word missing («Le recours est rejeté.» for
    «Le recours de l'assuré est rejeté.»). Replacing the sentence by Apertus' new one would
    drop what the quote left out, so only the words that differ between quote and new
    sentence are exchanged in the real sentence. None when those words cannot be placed.
    """
    a, q, n = actual.split(), quoted.split(), new.split()
    place: dict[int, int] = {}  # word of the quote -> the same word in the real sentence
    for block in difflib.SequenceMatcher(a=q, b=a, autojunk=False).get_matching_blocks():
        place.update({block.a + k: block.b + k for k in range(block.size)})
    edits = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=q, b=n, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            start = place[i1 - 1] + 1 if i1 - 1 in place else place.get(i1)
            if start is None:
                return None
            edits.append((start, start, n[j1:j2]))
            continue
        spots = [place.get(k) for k in range(i1, i2)]
        if None in spots or spots != list(range(spots[0], spots[0] + len(spots))):
            return None
        edits.append((spots[0], spots[-1] + 1, n[j1:j2]))
    for start, end, words in sorted(edits, reverse=True):
        a[start:end] = words
    return " ".join(a)


def planned_replacement(sentences: Sequence[tuple[str, str]], quote: str, new: str,
                        change: dict[str, str]) -> tuple[list[int], str] | None:
    """Which sentences Apertus' answer replaces by which text; None when it cannot be applied safely.

    ``quote`` (the answer's «alt») and ``new`` («neu») are compared sentence by sentence; only
    where they differ something changes. Not safe, hence None:

    - a quoted sentence that is not in the text, or sentences that do not follow each other
      or lie in different paragraphs;
    - a quote that differs from the sentence it stands for, when the words Apertus changed
      cannot be placed in the real sentence (``_transfer``), or when part of the sentence
      would be lost;
    - a replacement that keeps less than 60 % of the old wording (a new text, not the old
      one with the change);
    - more new sentences than the edit added;
    - a figure that is neither in the old sentence nor in the edit (taken from the judgement
      or invented);
    - removing whole sentences when the edit did not remove a whole passage.
    """
    old_s = [sentence for sentence, _ in split_sentences(quote)]
    new_s = [sentence for sentence, _ in split_sentences(new)]
    head = 0
    while head < min(len(old_s), len(new_s)) and old_s[head] == new_s[head]:
        head += 1
    tail = 0
    while tail < min(len(old_s), len(new_s)) - head and old_s[-1 - tail] == new_s[-1 - tail]:
        tail += 1
    old_rest, new_rest = old_s[head:len(old_s) - tail], new_s[head:len(new_s) - tail]
    added = " ".join(new_rest)
    if not old_rest:  # only new sentences: after the quoted sentence before them, else before the one after
        neighbour = old_s[head - 1] if head else old_s[head] if tail else ""
        numbers = [_locate(sentences, neighbour)] if neighbour and added else [0]
    else:
        numbers = [_locate(sentences, quoted) for quoted in old_rest]
    if 0 in numbers or len(numbers) > MAX_SENTENCES_PER_CHANGE or numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        return None
    if any(_PARAGRAPH_RE.search(sentences[n - 1][1]) for n in numbers[:-1]):
        return None
    kept = " ".join(sentences[n - 1][0] for n in numbers)
    if set(_FIGURE_RE.findall(added)) - set(_FIGURE_RE.findall(kept + " " + change["after"])):
        return None
    extra_in_edit = max(0, len(split_sentences(change["after"])) - len(split_sentences(change["before"])))
    if not old_rest:
        if len(new_rest) > len(split_sentences(change["after"])):
            return None
        return numbers, f"{kept} {added}" if head else f"{added} {kept}"
    if not added:  # whole sentences go only when the edit removed a whole passage
        return (numbers, "") if not change["after"] else None
    if len(new_rest) - len(old_rest) > extra_in_edit:
        return None
    if len(new_rest) == len(old_rest):  # sentence for sentence: a loose quote must not cost words
        carried = [
            fresh if quoted == sentences[n - 1][0] else _transfer(sentences[n - 1][0], quoted, fresh)
            for n, quoted, fresh in zip(numbers, old_rest, new_rest)
        ]
        if None in carried:
            return None
        added = " ".join(carried)
    if _similar(kept, added) < MIN_KEPT:
        return None
    if len(_content_words(kept) - _content_words(" ".join(old_rest)) - _content_words(added)) >= 2:
        return None  # the quote skipped part of the sentence and the replacement drops it
    return numbers, added


def replace_sentences(sentences: Sequence[tuple[str, str]], numbers: Sequence[int], new_text: str) -> str:
    """The text with the sentences ``numbers`` (counted from 1) replaced by ``new_text``.

    All other sentences stay word for word. An empty ``new_text`` removes the sentences.
    Raises ``ValueError`` for a number that does not exist.
    """
    if not numbers or any(not isinstance(n, int) or not 1 <= n <= len(sentences) for n in numbers):
        raise ValueError(f"no such sentences: {list(numbers)!r}, the text has {len(sentences)}")
    first, last = min(numbers), max(numbers)
    out = [list(pair) for pair in sentences[: first - 1]]
    gap = sentences[last - 1][1]
    if new_text.strip():
        out.append([new_text.strip(), gap])
    elif out and _PARAGRAPH_RE.search(gap):
        out[-1][1] = gap  # the paragraph break after a removed sentence moves to the one before
    out += [list(pair) for pair in sentences[last:]]
    return "".join(text + gap for text, gap in out).strip()


def _json(llm: Any, system: str, user: str, name: str, schema: dict[str, Any], max_tokens: int = 600) -> dict[str, Any]:
    """Apertus' JSON answer; an answer that is no JSON gets one retry with a little temperature.

    ``max_tokens`` is small on purpose: an answer is a few sentences, and a decoding that runs
    away (seen with an empty «neu») must end before the time limit.
    """
    fmt = {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}
    for temperature in (None, 0.3):
        try:
            data = json.loads(llm.chat(system, user, max_tokens=max_tokens, response_format=fmt,
                                       temperature=temperature, timeout=CALL_TIMEOUT))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("Apertus did not answer with JSON")


def confirmed(llm: Any, target_lang: str, target_sentence: str, source_sentences: Sequence[str], number: int) -> bool:
    """True when Apertus, asked the other way round, names sentence ``number`` of the edited
    language (before the edit) as the one that says the same as ``target_sentence``."""
    one, many = BACK_LABELS[target_lang]
    listed = "\n".join(f"[{n}] {sentence}" for n, sentence in enumerate(source_sentences, start=1))
    try:
        answer = _json(llm, BACK_SYSTEM[target_lang], f"{one}:\n{target_sentence}\n\n{many}:\n{listed}",
                       "gegenprobe", BACK_SCHEMA, max_tokens=20)
    except ValueError:
        return False
    return answer.get("satz") == number


def align_edit(
    llm: Any,
    *,
    target_lang: str,
    target_text: str,
    source_lang: str,
    source_text: str,
    source_before: str,
    judgement: Judgement | None = None,
    considerations: Sequence[str] = (),
    prompts_dir: Path = prompts.PROMPTS_DIR,
    final_check: bool = False,
    back_check: bool | None = None,
) -> Alignment:
    """Carry the edit ``source_before`` -> ``source_text`` over to ``target_text`` (see module text).

    ``llm`` is a ``summary.llm.SummaryLLM``. One Apertus call per changed sentence, and one more
    for the closing comparison when ``final_check`` is set (off by default: the lawyer asked
    for the change to be carried over, not for a list of differences). The notes are written
    in ``source_lang``, the language the lawyer worked in. With ``back_check`` (default
    ``BACK_CHECK``) every sentence that would be changed or removed is confirmed by a second,
    reverse question first (``confirmed``); without that confirmation it is left alone and noted. A change Apertus gives no usable answer for is
    not applied and noted. A gateway error is raised when nothing was carried over yet;
    after that, the changes that are left are noted, as is an error in the closing comparison.
    """
    if target_lang not in LANGUAGES or source_lang not in LANGUAGES or target_lang == source_lang:
        raise ValueError(f"languages must be two of {LANGUAGES}, got {source_lang!r} -> {target_lang!r}")
    changes = source_changes(source_before, source_text)
    text = target_text.strip()
    reference = ""
    if judgement is not None:
        reference = reference_text(judgement, target_lang, [target_text, source_text, source_before], considerations)
    if not changes:
        return Alignment(text=text, has_reference=bool(reference))
    system = change_system(target_lang, prompts_dir)
    lab = _LABELS[source_lang]  # notes are for the lawyer, in the language of the text they edited
    check_back = BACK_CHECK if back_check is None else back_check
    before_sentences = [" ".join(sentence.split()) for sentence, _ in split_sentences(source_before)]

    def safe(numbers: Sequence[int], change: dict[str, Any]) -> bool:
        """The sentences Apertus wants to touch really belong to the edited sentence."""
        if not check_back or not change["number"]:
            return True
        return all(confirmed(llm, target_lang, sentences[n - 1][0], before_sentences, change["number"]) for n in numbers)

    notes: list[str] = []  # one per change that needs the lawyer: never cut short
    for position, change in enumerate(changes[:MAX_CHANGES]):
        sentences = split_sentences(text)
        shown = change["after"] or change["before"]
        try:
            if not change["after"]:  # a whole sentence was removed: only the sentence that says the same is asked for
                whole, removed_label = DROP_LABELS[target_lang]
                answer = _json(llm, DROP_SYSTEM[target_lang], f"{whole}:\n{text}\n\n{removed_label}:\n{change['before']}",
                               "streichung", DROP_SCHEMA, max_tokens=300)
            else:
                answer = _json(llm, system, change_user(target_lang, source_lang, change, text, reference),
                               "angleichung", CHANGE_SCHEMA)
        except ValueError:  # no JSON, twice
            notes.append(lab["not_applied"].format(change=shown))
            continue
        except Exception:
            # The gateway fell away in the middle. What was carried over so far is kept and the
            # rest is left to the lawyer; when nothing was carried over yet, the caller reports
            # that Apertus could not be reached.
            if text == target_text.strip():
                raise
            notes += [lab["not_applied"].format(change=c["after"] or c["before"]) for c in changes[position:MAX_CHANGES]]
            break
        quote, fresh = str(answer.get("alt") or ""), str(answer.get("neu") or "")
        if not change["after"]:
            if not quote.strip():
                continue  # the other language does not have it: nothing to do
            numbers = sorted({_locate(sentences, quoted) for quoted, _ in split_sentences(quote)})
            if len(numbers) == 1 and numbers[0] and safe(numbers, change):
                text = replace_sentences(sentences, numbers, "")  # removing is done here, not by Apertus
            else:
                notes.append(lab["not_applied"].format(change=shown))
            continue
        reason = str(answer.get("hinweis") or "").strip()
        if answer.get("unklar"):  # nothing is read into an unclear edit
            notes.append(" ".join(filter(None, [lab["unclear"].format(change=shown), reason])))
            continue
        if not answer.get("inhaltlich"):
            continue  # wording only
        plan = planned_replacement(sentences, quote, fresh, change)
        if plan and not safe(plan[0], change):
            plan = None
        if not plan:  # nothing to apply safely: this change is left to the lawyer
            notes.append(lab["not_applied"].format(change=shown))
            continue
        text = replace_sentences(sentences, *plan)
        if answer.get("widerspricht_urteil"):
            notes.append(" ".join(filter(None, [lab["contradicts"].format(change=shown), reason])))
    if len(changes) > MAX_CHANGES:
        notes.append(lab["too_many"].format(limit=MAX_CHANGES))
    notes = list(dict.fromkeys(n for n in notes if n))
    if final_check:
        try:
            check = _json(llm, CHECK_SYSTEM[source_lang],
                          check_user(source_lang, {source_lang: source_text, target_lang: text}),
                          "schlusspruefung", CHECK_SCHEMA)
            found = [str(n).strip() for n in check.get("abweichungen") or []]
            notes += [n for n in dict.fromkeys(found) if n and n not in notes][:MAX_CHECK_NOTES]
        except Exception:  # the comparison is an extra: what was carried over stays
            notes.append(lab["check_failed"])
    return Alignment(text=text, notes=notes, has_reference=bool(reference))
