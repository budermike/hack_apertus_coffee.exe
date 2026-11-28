"""HTTP backend for the UI's summary endpoints (issue #26), stdlib only.

Implements the summary part of the contract in ``src/presentation/api.js``:

    GET    /api/judgements/{id}/summary          stored entry, never generates (404 if none)
    POST   /api/judgements/{id}/summary          stored entry; generates DE + FR first if missing
    PUT    /api/judgements/{id}/summary          body {de, fr}: a changed text becomes the one edited
                                                 version of that language and is shown at once; if only
                                                 one language changed, Apertus aligns the other one
                                                 (unless a lawyer wrote that one by hand)
    DELETE /api/judgements/{id}/summary/edit     drop the edited version (?lang=de|fr, default both):
                                                 back to the generated text
    POST   /api/judgements/{id}/summary/approve  body {languages?}: approve pending edits (no longer
                                                 used by the UI: edits need no approval)
    GET    /api/judgements/{id}/original         the judgement text as the crawler stored it, as a
                                                 plain HTML page (?lang=de|fr for the labels): the
                                                 original can be read without leaving the installation
    POST   /api/subjects/localize                body {lang, ids}: subjects of these judgements in the
                                                 language of the UI (Apertus, stored after the first time)
    POST   /api/judgements/{id}/localize         body {lang}: subject, quotes under the criteria and the
                                                 basis of the summary of one judgement in that language
    GET    /api/knowledge-base?q=...             search over all stored summaries, newest first
    DELETE /api/knowledge-base                   405: the knowledge base is append-only

``{id}`` is a case number in either spelling (``8C_7_2025`` or ``8C_7/2025``).
The judgement list (``GET /api/judgements``), ``GET /api/config`` and the feedback
placeholder are only routed here; their logic is in ``present/judgements.py``.

Run (``--triage`` is needed for the judgement list; without it every search answers 503):
    python -m hack_apertus_coffee_exe.summary.api --input data/raw --static src/presentation         --triage <triage results from "triage.cli score --output">
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import threading
from collections.abc import Callable
from contextlib import closing
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv

from ..config import load_paths
from ..present.evaluation import evaluation_response
from ..present.judgements import JudgementList, config_response, list_response, parse_feedback_body
from ..present.period_jobs import PeriodTriageManager
from ..present.translation import Localizer, Translator
from ..triage import relevance
from ..triage.llm import GatewayConfig
from ..triage.feedback_learning import FeedbackLearner
from . import store
from .judgement import Judgement, load_judgements, same_case
from .queue import SummaryQueue
from .run import summarize_case, summarizer_factory

logger = logging.getLogger(__name__)

Response = tuple[int, Any]


class Backend:
    """Request logic without HTTP, so it can be tested directly.

    Opens one SQLite connection per call (safe with the threading server).
    """

    def __init__(
        self,
        db_path: str | Path | None,
        crawl_dir: str | Path,
        get_summarizer: Callable[[], Any],
    ) -> None:
        self.db_path = db_path
        self.crawl_dir = Path(crawl_dir)
        self.get_summarizer = get_summarizer
        # One generation at a time: avoids two LLM calls for the same case.
        self._generate_lock = threading.Lock()
        self.queue = SummaryQueue(self)

    # --- endpoints ----------------------------------------------------------------

    def get_summary(self, case_number: str) -> Response:
        with closing(store.connect(self.db_path)) as conn:
            entry = summary_entry(conn, case_number)
        return (HTTPStatus.OK, entry) if entry else _error(HTTPStatus.NOT_FOUND, "no summary stored")

    def create_summary(self, case_number: str) -> Response:
        with self._generate_lock, closing(store.connect(self.db_path)) as conn:
            entry = summary_entry(conn, case_number)
            if entry:
                return HTTPStatus.OK, entry
            judgement = self.find_judgement(case_number)
            if judgement is None:
                return _error(HTTPStatus.NOT_FOUND, f"judgement {case_number} not found in {self.crawl_dir}")
            summarize_case(judgement, conn, self.get_summarizer)
            return HTTPStatus.OK, summary_entry(conn, judgement.case_id)

    def edit_summary(self, case_number: str, body: dict[str, Any]) -> Response:
        """Save the lawyer's texts: one edited version per language, shown at once.

        If only one language was changed, Apertus brings the other language in line with
        it and that text is saved too, so both keep saying the same (``"align": false`` in
        the body switches this off). The changed text is authoritative: the other language
        is rewritten to say exactly what it says (``prompts.ALIGN_SYSTEM``). Apertus never overwrites what a lawyer typed: a language
        that was edited by hand stays as it is. When Apertus cannot do it (no key, gateway
        down), the edit is saved anyway. ``alignment`` in the answer says what happened:
        ``null`` (nothing to align), ``{lang, ok: true}`` or
        ``{lang, ok: false, reason: "edited_by_hand" | "failed", error?}``.
        """
        with closing(store.connect(self.db_path)) as conn:
            entry = summary_entry(conn, case_number)
            if entry is None:
                return _error(HTTPStatus.NOT_FOUND, "no summary stored")
            changed = []
            for lang in store.LANGUAGES:
                text = body.get(lang)
                if not isinstance(text, str) or not text.strip():
                    continue
                if text != entry.get(lang):
                    store.replace_edit(conn, case_number, lang, text)
                    changed.append(lang)
                elif entry["pending"].get(lang) is not None:
                    # an edit from before 28.11. still waited for approval, and the lawyer
                    # went back to the shown text: that waiting edit is obsolete
                    store.discard_pending(conn, case_number, lang)
            alignment = None
            if len(changed) == 1 and body.get("align") is not False:
                source = changed[0]
                target = next(lang for lang in store.LANGUAGES if lang != source)
                alignment = self._align_other_language(conn, case_number, entry, source, body[source], target)
            result = summary_entry(conn, case_number)
            result["alignment"] = alignment
            return HTTPStatus.OK, result

    def _align_other_language(
        self, conn: Any, case_number: str, entry: dict[str, Any], source: str, source_text: str, target: str
    ) -> dict[str, Any]:
        failed = {"lang": target, "ok": False, "reason": "failed"}
        target_text = entry.get(target)
        if not target_text:
            return {**failed, "error": "no summary in that language"}
        typed_by_hand = entry["status"].get(target) != "generated" and not entry["alignedBy"].get(target)
        if typed_by_hand or entry["pending"].get(target) is not None:
            return {"lang": target, "ok": False, "reason": "edited_by_hand"}
        try:
            summarizer = self.get_summarizer()
            # The lawyer's text is what counts: no facts of the judgement are passed, so
            # Apertus cannot "correct" the edit back to what the judgement says.
            aligned = summarizer.align_language(
                target_lang=target,
                target_text=target_text,
                source_lang=source,
                source_text=source_text,
                instruction=None,
                facts=None,
            )
        except ValueError as exc:  # our own message, e.g. no gateway key set
            logger.warning("Could not align %s (%s) after an edit: %s", case_number, target, exc)
            return {**failed, "error": str(exc)[:300]}
        except Exception as exc:  # gateway errors may quote request details: log the kind only
            logger.warning(
                "Could not align %s (%s) after an edit: %s", case_number, target, type(exc).__name__
            )
            return {**failed, "error": "Apertus call failed"}
        if not isinstance(aligned, str) or not aligned.strip():
            return {**failed, "error": "Apertus returned no text"}
        if aligned != target_text:
            model = getattr(getattr(summarizer, "llm", None), "model", None) or "apertus"
            store.replace_edit(conn, case_number, target, aligned, aligned_by=model)
        return {"lang": target, "ok": True}

    def reset_summary(self, case_number: str, language: str | None = None) -> Response:
        """Drop the edited version (one language or both): the generated text is shown again."""
        with closing(store.connect(self.db_path)) as conn:
            if summary_entry(conn, case_number) is None:
                return _error(HTTPStatus.NOT_FOUND, "no summary stored")
            store.discard_edit(conn, case_number, language or None)
            return HTTPStatus.OK, summary_entry(conn, case_number)

    def approve_summary(self, case_number: str, body: dict[str, Any]) -> Response:
        with closing(store.connect(self.db_path)) as conn:
            languages = body.get("languages") or [
                lang for lang in store.LANGUAGES if store.get_pending_edit(conn, case_number, lang)
            ]
            if not languages:
                return _error(HTTPStatus.CONFLICT, "no edited version waiting for approval")
            for lang in languages:
                store.approve(conn, case_number, lang)
            return HTTPStatus.OK, summary_entry(conn, case_number)

    def search(self, query: str) -> Response:
        with closing(store.connect(self.db_path)) as conn:
            return HTTPStatus.OK, [summary_entry(conn, cid) for cid in store.search(conn, query)]

    def save_feedback(self, case_number: str, body: dict[str, Any]) -> Response:
        parsed = parse_feedback_body(body)
        with closing(store.connect(self.db_path)) as conn:
            return HTTPStatus.OK, store.save_feedback(conn, case_number, **parsed)

    def original_page(self, case_number: str, lang: str = "de") -> tuple[int, str]:
        """The stored judgement text as a self-contained HTML page (status, HTML).

        The text comes from an external site, so every character of it is escaped; the
        page loads nothing from elsewhere and works without network access.
        """
        labels = _ORIGINAL_LABELS["fr" if lang == "fr" else "de"]
        judgement = self.find_judgement(case_number)
        if judgement is None:
            head, _, year = case_number.rpartition("_")  # 8C_7_2025 -> 8C_7/2025
            spelled = f"{head}/{year}" if "_" in head and year.isdigit() and len(year) == 4 else case_number
            number = html.escape(spelled)
            body = f"<h1>{number}</h1><p class=\"meta\">{labels['missing']}</p>"
            return HTTPStatus.NOT_FOUND, _ORIGINAL_PAGE.format(lang=labels["code"], title=number, body=body)
        number = html.escape(judgement.case_id)
        meta = [labels["stored"]]
        if judgement.decision_date:
            meta.append(f"{labels['decided']} {judgement.decision_date.strftime('%d.%m.%Y')}")
        if judgement.language:
            meta.append(f"{labels['language']} {html.escape(judgement.language.upper())}")
        url = judgement.url or ""
        if url.startswith(("https://", "http://")):
            meta.append(f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{labels["external"]}</a>')
        body = (
            f"<h1>{labels['heading']} {number}</h1><p class=\"meta\">{' · '.join(meta)}</p>"
            f"<pre lang=\"{html.escape(judgement.language or labels['code'], quote=True)}\">{html.escape(judgement.text)}</pre>"
        )
        return HTTPStatus.OK, _ORIGINAL_PAGE.format(lang=labels["code"], title=f"{number} – {labels['title']}", body=body)

    def enqueue_summaries(self, body: dict[str, Any]) -> Response:
        case_ids = body.get("caseIds")
        if not isinstance(case_ids, list) or not case_ids:
            raise ValueError("caseIds must be a non-empty list")
        priority = body.get("priority", False)
        if not isinstance(priority, bool):
            raise ValueError("priority must be a boolean")
        return HTTPStatus.ACCEPTED, self.queue.enqueue(case_ids, priority=priority)

    def queue_status(self, raw_ids: str | None) -> Response:
        ids = [i for i in (raw_ids or "").split(",") if i.strip()] if raw_ids else None
        return HTTPStatus.OK, self.queue.status(ids)

    def cancel_queued_summary(self, case_number: str) -> Response:
        return HTTPStatus.OK, {"cancelled": self.queue.cancel(case_number)}

    # --- helpers ------------------------------------------------------------------

    def find_judgement(self, case_number: str) -> Judgement | None:
        """Load one judgement from the crawl folder (file names end in the safe id)."""
        safe_id = case_number.replace("/", "_")
        for path in sorted(self.crawl_dir.rglob(f"*{safe_id}.json")):
            for judgement in load_judgements(path):
                if same_case(judgement.case_id, case_number):
                    return judgement
        return None


_ORIGINAL_LABELS = {
    "de": {
        "code": "de", "heading": "BGer-Urteil", "title": "gespeicherter Volltext",
        "stored": "Lokal gespeicherter Volltext, wie ihn der Crawler abgerufen hat",
        "decided": "Entscheid vom", "language": "Sprache", "external": "Original auf der Website öffnen",
        "missing": "Der Volltext dieses Urteils ist lokal nicht gespeichert.",
    },
    "fr": {
        "code": "fr", "heading": "Arrêt du TF", "title": "texte intégral enregistré",
        "stored": "Texte intégral enregistré localement, tel que le crawler l'a récupéré",
        "decided": "Arrêt du", "language": "Langue", "external": "Ouvrir l'original sur le site",
        "missing": "Le texte intégral de cet arrêt n'est pas enregistré localement.",
    },
}

_ORIGINAL_PAGE = (
    '<!DOCTYPE html><html lang="{lang}"><head><meta charset="UTF-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>'
    "<style>body{{max-width:46rem;margin:2rem auto;padding:0 1rem;font:16px/1.6 Georgia,serif;color:#1b1f24}}"
    "h1{{font:600 22px/1.3 system-ui,sans-serif}}"
    ".meta{{font:13px/1.5 system-ui,sans-serif;color:#555;border-bottom:1px solid #ccc;padding-bottom:.7rem}}"
    "pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;margin:1rem 0}}</style></head>"
    "<body>{body}</body></html>"
)


def summary_entry(conn, case_number: str) -> dict[str, Any] | None:
    """One knowledge-base entry in the shape the UI expects, or None if nothing is stored."""
    shown = {lang: store.get_display(conn, case_number, lang) for lang in store.LANGUAGES}
    if not any(shown.values()):
        return None
    case = store.get_case(conn, case_number)
    pending = {lang: store.get_pending_edit(conn, case_number, lang) for lang in store.LANGUAGES}
    history = [s for lang in store.LANGUAGES for s in store.get_history(conn, case_number, lang)]
    edits = [s.created_at for s in history if s.status != "generated"]

    def per_lang(fn: Callable[[store.Summary], Any]) -> dict[str, Any]:
        return {lang: fn(s) if s else None for lang, s in shown.items()}

    def per_lang_or_history(fn: Callable[[store.Summary], Any], empty: Any) -> dict[str, Any]:
        fallback = next((fn(item) for item in history if fn(item)), empty)
        return {lang: (fn(s) or fallback) if s else fallback for lang, s in shown.items()}

    return {
        "id": case.case_id.replace("/", "_"),
        "caseNumber": case.case_id,
        "date": case.decision_date,
        "url": case.url,
        "title": per_lang(lambda s: s.title),
        "de": shown["de"].text if shown["de"] else None,
        "fr": shown["fr"].text if shown["fr"] else None,
        "status": per_lang(lambda s: s.status),
        "model": per_lang(lambda s: s.model),
        "alignedBy": per_lang(lambda s: s.aligned_by),
        "coherenceOk": per_lang(lambda s: s.coherence_ok),
        "issues": per_lang(lambda s: s.issues),
        "facts": per_lang_or_history(lambda s: s.facts, {}),
        "examples": per_lang_or_history(lambda s: s.examples, []),
        "pending": {lang: p.text if p else None for lang, p in pending.items()},
        "storedAt": max(s.created_at for s in history),
        "editedAt": max(edits) if edits else None,
    }


# --- HTTP layer --------------------------------------------------------------------


class Handler(SimpleHTTPRequestHandler):
    """Routes ``/api/...`` to the backend; everything else is served from ``--static``."""

    backend: Backend  # set by make_server
    judgements: JudgementList | None = None  # set by make_server (present/judgements.py)
    periods: PeriodTriageManager | None = None  # set by make_server (present/period_jobs.py)
    localizer: Localizer | None = None  # set by make_server (present/translation.py)
    tables_dir: Path = Path("results/tables")
    models_path: Path = Path("docs/MODELS.md")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        parts = _api_parts(url.path)
        if parts is None:
            return super().do_GET()
        if parts == ["judgements"]:  # present/judgements.py
            return self._respond(lambda: self._judgement_list(parse_qs(url.query)))
        if parts == ["config"]:  # present/judgements.py
            return self._respond(lambda: config_response(self.judgements))
        if parts == ["evaluation"]:  # present/evaluation.py
            return self._respond(lambda: evaluation_response(self.tables_dir, self.models_path))
        if parts == ["periods", "status"]:
            q = parse_qs(url.query)
            return self._respond(lambda: self._period_status(q))
        if parts == ["knowledge-base"]:
            query = parse_qs(url.query).get("q", [""])[0]
            return self._respond(lambda: self.backend.search(query))
        if parts == ["summaries", "status"]:
            ids = parse_qs(url.query).get("ids", [""])[0]
            return self._respond(lambda: self.backend.queue_status(ids))
        if len(parts) == 3 and parts[0] == "judgements" and parts[2] == "summary":
            return self._respond(lambda: self.backend.get_summary(parts[1]))
        if len(parts) == 3 and parts[0] == "judgements" and parts[2] == "original":
            lang = parse_qs(url.query).get("lang", ["de"])[0]
            return self._respond_html(lambda: self.backend.original_page(parts[1], lang))
        self._respond(lambda: _error(HTTPStatus.NOT_FOUND, "unknown endpoint"))

    def do_POST(self) -> None:
        parts = _api_parts(urlparse(self.path).path) or []
        body = self._body()
        if parts == ["periods", "ensure"]:
            return self._respond(lambda: self._period_ensure(body))
        if parts in (["judgements", "add"], ["add"]):
            return self._respond(lambda: self._add_judgement(body))
        if parts == ["summaries", "queue"]:
            return self._respond(lambda: self.backend.enqueue_summaries(body))
        if parts == ["subjects", "localize"]:
            return self._respond(lambda: self._localize(lambda localizer: localizer.subjects(body)))
        if len(parts) == 3 and parts[0] == "judgements" and parts[2] == "localize":
            return self._respond(lambda: self._localize(lambda localizer: localizer.judgement(parts[1], body)))
        if len(parts) == 3 and parts[0] == "judgements" and parts[2] == "reason-fr":
            return self._respond(lambda: self._translate_reason_fr(parts[1]))
        if len(parts) == 3 and parts[0] == "judgements" and parts[2] == "summary":
            return self._respond(lambda: self.backend.create_summary(parts[1]))
        if len(parts) == 4 and parts[0] == "judgements" and parts[2:] == ["summary", "approve"]:
            return self._respond(lambda: self.backend.approve_summary(parts[1], body))
        if len(parts) == 3 and parts[0] == "judgements" and parts[2] == "feedback":
            return self._respond(lambda: self.backend.save_feedback(parts[1], body))
        self._respond(lambda: _error(HTTPStatus.NOT_FOUND, "unknown endpoint"))

    def _judgement_list(self, query: dict[str, list[str]]) -> Response:
        status, body = list_response(self.judgements, query)
        if status == HTTPStatus.OK and self.localizer is not None:
            self.localizer.fill_subjects(body)  # subjects already carried over into the other language
        return status, body

    def _localize(self, call: Callable[[Localizer], Response]) -> Response:
        if self.localizer is None:
            return _error(HTTPStatus.SERVICE_UNAVAILABLE, "no triage results loaded: start the server with --triage <file>")
        return call(self.localizer)

    def _period_status(self, query: dict[str, list[str]]) -> Response:
        if self.periods is None:
            return _error(HTTPStatus.SERVICE_UNAVAILABLE, "period triage manager unavailable")
        df = (query.get("from") or [None])[0]
        dt = (query.get("to") or [None])[0]
        return HTTPStatus.OK, self.periods.status(df, dt)

    def _period_ensure(self, body: dict[str, Any]) -> Response:
        if self.periods is None:
            return _error(HTTPStatus.SERVICE_UNAVAILABLE, "period triage manager unavailable")
        snap = self.periods.ensure_async(body.get("from"), body.get("to"))
        code = HTTPStatus.OK if snap["status"] == "done" else HTTPStatus.ACCEPTED
        return code, snap

    def _add_judgement(self, body: dict[str, Any]) -> Response:
        if self.periods is None:
            return _error(HTTPStatus.SERVICE_UNAVAILABLE, "period triage manager unavailable")
        case_number = body.get("caseNumber")
        if not isinstance(case_number, str) or not case_number.strip():
            raise ValueError("caseNumber must be a non-empty string")
        summarize = body.get("summarize", True)
        if not isinstance(summarize, bool):
            raise ValueError("summarize must be a boolean")
        return HTTPStatus.OK, self.periods.add_case(case_number, summarize=summarize)

    def _translate_reason_fr(self, case_number: str) -> Response:
        if self.periods is None:
            return _error(HTTPStatus.SERVICE_UNAVAILABLE, "period triage manager unavailable")
        return HTTPStatus.OK, self.periods.translate_reason_fr(case_number)

    def do_PUT(self) -> None:
        parts = _api_parts(urlparse(self.path).path) or []
        body = self._body()
        if len(parts) == 3 and parts[0] == "judgements" and parts[2] == "summary":
            return self._respond(lambda: self.backend.edit_summary(parts[1], body))
        self._respond(lambda: _error(HTTPStatus.NOT_FOUND, "unknown endpoint"))

    def do_DELETE(self) -> None:
        url = urlparse(self.path)
        parts = _api_parts(url.path) or []
        if len(parts) == 3 and parts[:2] == ["summaries", "queue"]:
            return self._respond(lambda: self.backend.cancel_queued_summary(parts[2]))
        if len(parts) == 4 and parts[0] == "judgements" and parts[2:] == ["summary", "edit"]:
            language = parse_qs(url.query).get("lang", [""])[0]
            return self._respond(lambda: self.backend.reset_summary(parts[1], language))
        self._respond(lambda: _error(HTTPStatus.METHOD_NOT_ALLOWED, "knowledge base is append-only"))

    def end_headers(self) -> None:
        # Browsers must revalidate every file (cheap: unchanged files answer 304). Without
        # this header they cache the UI scripts heuristically from Last-Modified, often for
        # an hour, and run an outdated script next to updated ones after a change.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _respond_html(self, call: Callable[[], tuple[int, str]]) -> None:
        try:
            status, page = call()
        except Exception:
            logger.exception("request failed: %s %s", self.command, self.path)
            status, page = HTTPStatus.INTERNAL_SERVER_ERROR, "<!DOCTYPE html><title>Error</title><p>internal error</p>"
        data = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # the page shows text from an external site: no scripts, nothing loaded from elsewhere
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _respond(self, call: Callable[[], Response]) -> None:
        try:
            status, payload = call()
        except LookupError as exc:
            status, payload = _error(HTTPStatus.NOT_FOUND, str(exc))
        except (store.InvalidTransitionError, store.SummaryExistsError) as exc:
            status, payload = _error(HTTPStatus.CONFLICT, str(exc))
        except ValueError as exc:  # e.g. invalid language, missing gateway key
            status, payload = _error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            logger.exception("request failed: %s %s", self.command, self.path)
            status, payload = _error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_server(
    backend: Backend,
    host: str,
    port: int,
    static_dir: str | Path,
    judgements: JudgementList | None = None,
    periods: PeriodTriageManager | None = None,
    tables_dir: str | Path = Path("results/tables"),
    models_path: str | Path = Path("docs/MODELS.md"),
    localizer: Localizer | None = None,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundHandler",
        (Handler,),
        {
            "backend": backend,
            "judgements": judgements,
            "periods": periods,
            "localizer": localizer,
            "tables_dir": Path(tables_dir),
            "models_path": Path(models_path),
        },
    )
    return ThreadingHTTPServer((host, port), partial(handler, directory=str(static_dir)))


def _api_parts(path: str) -> list[str] | None:
    if not path.startswith("/api/"):
        return None
    return [unquote(p) for p in path[len("/api/"):].split("/") if p]


def _summarized_case_ids(db_path: str | Path | None) -> list[str]:
    """Case ids with a stored summary (read-only, for the judgement list)."""
    with closing(store.connect(db_path)) as conn:
        return [s.case_id for s in store.list_display(conn)]


def _latest_feedback(db_path: str | Path | None) -> dict[str, dict[str, Any]]:
    """Latest lawyer feedback decisions per UI case id (#64)."""
    with closing(store.connect(db_path)) as conn:
        return store.get_latest_feedback(conn)


def _load_selected_ids(judgements_dir: str | Path) -> set[str]:
    """Case numbers previously chosen by Suva, for highlighting similar cases (#72)."""
    path = Path(judgements_dir)
    if not path.exists():
        return set()
    try:
        from ..crawl.vectordb import load_reference_case_ids

        return load_reference_case_ids(path)
    except Exception:
        return set()


def _error(status: int, message: str) -> Response:
    return status, {"error": message}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="summary-api", description="Summary backend for the UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=None, help=f"SQLite file (default: ${store.DB_PATH_ENV} or {store.DEFAULT_DB_PATH})")
    parser.add_argument("--input", default=load_paths().raw_dir, help="Crawl folder with the judgements")
    parser.add_argument("--static", default="src/presentation", help="Folder with the UI files")
    parser.add_argument("--model", default=None, help="Gateway model ID (default: LLM_NAME / APERTUS_MODEL)")
    parser.add_argument("--judgements-dir", default=load_paths().judgements_dir, help="Suva example summaries")
    parser.add_argument("--examples", type=int, default=2, help="Few-shot examples per language")
    parser.add_argument("--triage", default=None, help="Triage results JSON for GET /api/judgements")
    parser.add_argument(
        "--calibration",
        default="results/tables/relevance_calibration_q2_2026.json",
        help="Calibration of the relevance thresholds (written by triage.cli relevance); explains them in GET /api/config",
    )
    parser.add_argument("--auto-summarize", action="store_true", help="Queue relevant triage cases on startup")
    args = parser.parse_args(argv)
    load_dotenv()  # SUMMARY_DB_PATH and gateway settings from .env
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    backend = Backend(args.db, args.input, summarizer_factory(args.model, args.judgements_dir, args.examples))
    judgements = (
        JudgementList(
            args.triage,
            summarized_ids=lambda: _summarized_case_ids(args.db),
            crawl_dir=args.input,
            feedback_map=lambda: _latest_feedback(args.db),
            selected_ids=_load_selected_ids(args.judgements_dir),
            extra_triage_dir="data/processed/triage_periods",
            fr_cache_path="data/processed/triage/fr_cache.json",
            learner=FeedbackLearner(),  # relevance decisions are learned from, not added to a score
            calibration_path=args.calibration,
        )
        if args.triage else None
    )
    periods = PeriodTriageManager(
        raw_dir=args.input,
        store_dir="data/processed/triage_periods",
        judgements=judgements,
        db_path=args.db,
        backend=backend,
        judgements_dir=args.judgements_dir,
        model=args.model,
        calibration_path=args.calibration,
    )
    if args.auto_summarize and judgements is not None:
        green_threshold = judgements.config.green_threshold
        ids = [i["id"] for i in judgements.items() if i["score"] >= green_threshold and not i["summarized"]]
        if ids:
            backend.queue.enqueue(ids)
    localizer = None
    if judgements is not None:
        # Same model as the relevance scoring (70B unless --model says otherwise): it knows the legal terms best.
        translator = Translator(
            lambda: relevance.ApertusRelevanceClient(
                config=GatewayConfig.from_env(model=args.model or relevance.DEFAULT_MODEL),
                cache_dir=judgements.config.cache_dir,
            )
        )
        localizer = Localizer(translator, judgements, args.db)
    server = make_server(backend, args.host, args.port, args.static, judgements, periods=periods, localizer=localizer)
    logger.info("Serving on http://%s:%s (API under /api)", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        periods.stop()
        backend.queue.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
