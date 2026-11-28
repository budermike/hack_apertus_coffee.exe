"""Short legal texts in the language of the user interface (corrections of 28.11.2026).

List and detail view show texts that exist in one language only: the subject of a
judgement and the quotes under the criteria (language of the judgement), and the facts a
summary rests on (always German). With the interface in the other language Apertus v1.5
carries them over.

Not word for word. Legal German and legal French have their own fixed terms and
abbreviations (UVG / LAA, Erwägung / considérant, Einspracheentscheid / décision sur
opposition). The prompt asks for the wording a Swiss lawyer uses in the target language
and passes the project's glossary (``prompts/glossary.md``). The meaning must stay exactly
the same.

A carried-over quote is no longer the wording of the court. The UI therefore marks it and
keeps the original one click away; this module never replaces an original.

Every text is carried over once and kept in a JSON file (``Translator.cache_path``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..summary import store
from ..summary.prompts import glossary as load_glossary
from .judgements import relevance_details

logger = logging.getLogger(__name__)

LANGUAGES = ("de", "fr")
DEFAULT_CACHE_PATH = Path("data/processed/triage/translation_cache.json")

SYSTEM = {
    "fr": """\
Tu es juriste au service juridique de la Suva. Tu reçois de courts textes tirés d'arrêts du \
Tribunal fédéral suisse ou de notes sur ces arrêts, en allemand ou en italien : objets de \
litige, passages cités, règles, références légales.

Rends chaque texte dans la langue juridique française de Suisse :
- Pas de traduction mot à mot. Écris comme le ferait une juriste romande, avec les termes \
consacrés du glossaire ci-dessous.
- Abréviations et renvois dans leur forme française : UVG devient LAA, ATSG devient LPGA, \
IVG devient LAI, «Art. 6 Abs. 2 UVG» devient «art. 6 al. 2 LAA», «E. 4.2» devient «consid. 4.2».
- Le sens reste exactement le même : ne rien ajouter, ne rien omettre, ne rien atténuer.
- Chiffres, dates, numéros d'arrêts et noms propres restent inchangés.
- Un texte déjà en français est repris tel quel.

Glossaire :
{glossary}

Réponds uniquement avec l'objet JSON : pour chaque entrée, son numéro et le texte rendu.
""",
    "de": """\
Du bist Juristin / Jurist im Rechtsdienst der Suva. Du erhältst kurze Texte aus Urteilen des \
Schweizerischen Bundesgerichts oder aus Notizen dazu, auf Französisch oder Italienisch: \
Streitgegenstände, zitierte Passagen, Regeln, Gesetzesverweise.

Gib jeden Text in der deutschen Rechtssprache der Schweiz wieder:
- Keine wörtliche Übersetzung. Schreibe so, wie es eine Juristin in der Deutschschweiz \
formuliert, mit den festen Begriffen aus dem Glossar unten.
- Abkürzungen und Verweise in der deutschen Form: LAA wird UVG, LPGA wird ATSG, LAI wird IVG, \
«art. 6 al. 2 LAA» wird «Art. 6 Abs. 2 UVG», «consid. 4.2» wird «E. 4.2».
- Der Sinn bleibt genau gleich: nichts hinzufügen, nichts weglassen, nichts abschwächen.
- Zahlen, Daten, Geschäftsnummern und Eigennamen bleiben unverändert.
- Ein Text, der schon deutsch ist, wird unverändert übernommen.

Glossar:
{glossary}

Antworte ausschliesslich mit dem JSON-Objekt: zu jedem Eintrag seine Nummer und der wiedergegebene Text.
""",
}

# Keys carry a prefix because the gateway's strict mode returns them in alphabetical order.
SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"a_nr": {"type": "integer"}, "b_text": {"type": "string"}},
                "required": ["a_nr", "b_text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _key(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


class Translator:
    """Carries short texts over into German or French with Apertus; remembers every result."""

    def __init__(
        self,
        client_factory: Callable[[], Any],
        cache_path: str | Path = DEFAULT_CACHE_PATH,
        glossary: str | None = None,
        max_items: int = 15,
        max_chars: int = 2400,
    ) -> None:
        self._client_factory = client_factory
        self.cache_path = Path(cache_path)
        self._glossary = glossary
        self.max_items = max_items
        self.max_chars = max_chars
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, str]] | None = None

    # --- cache ----------------------------------------------------------------------

    def _load(self) -> dict[str, dict[str, str]]:
        if self._cache is None:
            data: dict[str, dict[str, str]] = {lang: {} for lang in LANGUAGES}
            if self.cache_path.is_file():
                try:
                    loaded = json.loads(self.cache_path.read_text(encoding="utf-8"))
                    for lang in LANGUAGES:
                        if isinstance(loaded.get(lang), dict):
                            data[lang] = {str(k): str(v) for k, v in loaded[lang].items() if isinstance(v, str)}
                except (OSError, ValueError, AttributeError):
                    logger.warning("Translation cache %s is unreadable, starting empty", self.cache_path)
            self._cache = data
        return self._cache

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_name(f".{self.cache_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(self._cache, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.cache_path)

    def lookup(self, text: str, target: str) -> str | None:
        """The stored text in ``target``, or ``None`` if it was not carried over yet. Never calls Apertus."""
        _check(target)
        if not isinstance(text, str) or not text.strip():
            return None
        with self._lock:
            return self._load()[target].get(_key(text))

    # --- Apertus ----------------------------------------------------------------------

    def translate(self, texts: Iterable[str], target: str) -> dict[str, str]:
        """``{text: text in target}`` for every text that could be carried over.

        Stored texts are not asked again. The others go to Apertus in batches; a text
        Apertus returns nothing for is left out, so the caller keeps showing the original.
        Raises what the client raises (no key, gateway not reachable) after storing the
        batches that did succeed.
        """
        _check(target)
        wanted = list(dict.fromkeys(t.strip() for t in texts if isinstance(t, str) and t.strip()))
        result: dict[str, str] = {}
        with self._lock:
            stored = self._load()[target]
            missing = []
            for text in wanted:
                hit = stored.get(_key(text))
                if hit is not None:
                    result[text] = hit
                else:
                    missing.append(text)
        if not missing:
            return result
        client = self._client_factory()
        glossary = self._glossary if self._glossary is not None else load_glossary()
        system = SYSTEM[target].format(glossary=glossary)
        for batch in self._batches(missing):
            user = "\n".join(f"{nr}. {text}" for nr, text in enumerate(batch, start=1))
            digest = hashlib.sha256((target + "\n" + user).encode("utf-8")).hexdigest()[:16]
            answer = client.call(f"translate_{target}", digest, system, user, SCHEMA, 400 + sum(len(t) for t in batch))
            done = {}
            for entry in answer.get("items") or []:
                nr, text = entry.get("a_nr"), entry.get("b_text")
                if isinstance(nr, int) and 1 <= nr <= len(batch) and isinstance(text, str) and text.strip():
                    done[batch[nr - 1]] = text.strip()
            with self._lock:
                stored = self._load()[target]
                for source, text in done.items():
                    stored[_key(source)] = text
                self._save()
            result.update(done)
        return result

    def _batches(self, texts: Sequence[str]) -> list[list[str]]:
        batches: list[list[str]] = []
        current: list[str] = []
        size = 0
        for text in texts:
            if current and (len(current) >= self.max_items or size + len(text) > self.max_chars):
                batches.append(current)
                current, size = [], 0
            current.append(text)
            size += len(text)
        if current:
            batches.append(current)
        return batches


def _check(target: str) -> None:
    if target not in LANGUAGES:
        raise ValueError(f"lang must be one of {LANGUAGES}, got {target!r}")


def _row_language(row: dict[str, Any]) -> str:
    meta = row.get("metadata") or {}
    return str(row.get("language") or meta.get("language") or "de")


def _row_subject(row: dict[str, Any]) -> str:
    meta = row.get("metadata") or {}
    subject = meta.get("subject")
    return subject.strip() if isinstance(subject, str) else ""


class Localizer:
    """What the API needs: subjects for the list, quotes and summary basis for one judgement."""

    def __init__(self, translator: Translator, judgements: Any, db_path: str | Path | None = None) -> None:
        self.translator = translator
        self.judgements = judgements
        self.db_path = db_path

    def _rows_by_id(self) -> dict[str, dict[str, Any]]:
        return {row["case_id"].replace("/", "_"): row for row in self.judgements.rows()}

    # --- GET /api/judgements: what is stored already ------------------------------------

    def fill_subjects(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Put stored subjects into ``subject[lang]`` and name the languages still missing.

        ``subject`` of an item holds the original in both languages until then.
        ``subjectMissing`` lists the UI languages the subject is not available in yet.
        """
        for item in items:
            subject = item.get("subject")
            source = item.get("lang") or "de"
            if not isinstance(subject, dict):
                continue
            original = subject.get(source if source in LANGUAGES else "de") or subject.get("de") or ""
            missing = []
            for target in LANGUAGES:
                if target == source or not original:
                    continue
                stored = self.translator.lookup(original, target)
                if stored is not None:
                    subject[target] = stored
                else:
                    missing.append(target)
            if missing:
                item["subjectMissing"] = missing
        return items

    # --- POST /api/subjects/localize -----------------------------------------------------

    def subjects(self, body: dict[str, Any]) -> tuple[int, Any]:
        """Subjects of the given judgements in ``lang``: ``{subjects: {id: text}, complete}``."""
        target = body.get("lang")
        _check(target)
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise ValueError("ids must be a list of judgement ids")
        if len(ids) > 500:
            raise ValueError("at most 500 ids per request")
        rows = self._rows_by_id()
        wanted = {
            ui: _row_subject(rows[ui])
            for ui in ids
            if ui in rows and _row_language(rows[ui]) != target and _row_subject(rows[ui])
        }
        translated, error = self._translate(wanted.values(), target)
        out = {"lang": target, "subjects": {ui: translated[s] for ui, s in wanted.items() if s in translated}}
        return 200, {**out, "complete": error is None, **({"error": error} if error else {})}

    # --- POST /api/judgements/{id}/localize ----------------------------------------------

    def judgement(self, case_number: str, body: dict[str, Any]) -> tuple[int, Any]:
        """Subject, quotes under the criteria and the basis of the summary of one judgement in ``lang``.

        ``quotes`` maps the position of a criterion to its quote in ``lang``; ``basis`` holds
        the articles of law and the key considerations of the summary. A field is left out
        when the original is in ``lang`` already or nothing could be carried over.
        """
        target = body.get("lang")
        _check(target)
        row = self._rows_by_id().get(case_number.replace("/", "_"))
        if row is None:
            raise LookupError(f"judgement {case_number} not found")
        source = _row_language(row)
        texts: list[str] = []
        subject = _row_subject(row) if source != target else ""
        quotes = {}
        if source != target:
            # the criteria exactly as the list delivers them, so the positions agree with the UI
            for index, criterion in enumerate(relevance_details(row.get("metadata") or {}).get("criteria") or []):
                quote = criterion.get("quote")
                if isinstance(quote, str) and quote.strip():
                    quotes[index] = quote.strip()
        laws, points = self._summary_basis(case_number) if target != "de" else ([], [])
        texts += [subject, *quotes.values(), *laws, *(p["aussage"] for p in points)]
        translated, error = self._translate(texts, target)

        out: dict[str, Any] = {"lang": target, "complete": error is None}
        if error:
            out["error"] = error
        if subject in translated:
            out["subject"] = translated[subject]
        out["quotes"] = {str(i): translated[q] for i, q in quotes.items() if q in translated}
        if laws or points:
            basis = {
                "laws": [translated.get(law, law) for law in laws],
                "considerations": [{**p, "aussage": translated.get(p["aussage"], p["aussage"])} for p in points],
            }
            if any(law in translated for law in laws) or any(p["aussage"] in translated for p in points):
                out["basis"] = basis
        return 200, out

    def _summary_basis(self, case_number: str) -> tuple[list[str], list[dict[str, str]]]:
        """Articles of law and key considerations of the stored summary (the facts are German)."""
        with closing(store.connect(self.db_path)) as conn:
            facts: dict[str, Any] = {}
            for lang in store.LANGUAGES:
                for version in store.get_history(conn, case_number, lang):
                    if version.facts:
                        facts = version.facts
                        break
                if facts:
                    break
        laws = [str(a).strip() for a in facts.get("gesetzesartikel") or [] if isinstance(a, str) and a.strip()]
        points = []
        for point in facts.get("kernerwaegungen") or []:
            if isinstance(point, dict) and isinstance(point.get("aussage"), str) and point["aussage"].strip():
                points.append({"erwaegung": str(point.get("erwaegung") or ""), "aussage": point["aussage"].strip()})
        return laws, points

    def _translate(self, texts: Iterable[str], target: str) -> tuple[dict[str, str], str | None]:
        """Translations, and a short reason if Apertus could not be asked (the rest stays original)."""
        wanted = [t for t in texts if t]
        try:
            return self.translator.translate(wanted, target), None
        except ValueError as exc:  # our own message, e.g. no gateway key set
            logger.warning("Could not carry texts over into %s: %s", target, exc)
            reason = str(exc)[:300]
        except Exception as exc:  # gateway errors may quote request details: log the kind only
            logger.warning("Could not carry texts over into %s: %s", target, type(exc).__name__)
            reason = "Apertus call failed"
        stored = {t: hit for t in wanted if (hit := self.translator.lookup(t, target)) is not None}
        return stored, reason

