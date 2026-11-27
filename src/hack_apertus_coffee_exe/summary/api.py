"""HTTP backend for the UI's summary endpoints (issue #26), stdlib only.

Implements the summary part of the contract in ``src/presentation/api.js``:

    GET    /api/judgements/{id}/summary          stored entry, never generates (404 if none)
    POST   /api/judgements/{id}/summary          stored entry; generates DE + FR first if missing
    PUT    /api/judgements/{id}/summary          body {de, fr}: changed texts become 'edited' versions
    POST   /api/judgements/{id}/summary/approve  body {languages?}: approve pending edits
    GET    /api/knowledge-base?q=...             search over all stored summaries, newest first
    DELETE /api/knowledge-base                   405: the knowledge base is append-only

``{id}`` is a case number in either spelling (``8C_7_2025`` or ``8C_7/2025``).
The judgement list (``GET /api/judgements``), ``GET /api/config`` and the feedback
placeholder are only routed here; their logic is in ``present/judgements.py``.

Run:
    python -m hack_apertus_coffee_exe.summary.api --input data/raw --static src/presentation
"""

from __future__ import annotations

import argparse
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

from ..present.judgements import JudgementList, config_response, list_response, record_feedback
from . import store
from .judgement import Judgement, load_judgements, same_case
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
        with closing(store.connect(self.db_path)) as conn:
            if summary_entry(conn, case_number) is None:
                return _error(HTTPStatus.NOT_FOUND, "no summary stored")
            for lang in store.LANGUAGES:
                text = body.get(lang)
                if not isinstance(text, str) or not text.strip():
                    continue
                current = store.get_pending_edit(conn, case_number, lang) or store.get_display(
                    conn, case_number, lang
                )
                if current is None or text != current.text:
                    store.save_edit(conn, case_number, lang, text)
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

    # --- helpers ------------------------------------------------------------------

    def find_judgement(self, case_number: str) -> Judgement | None:
        """Load one judgement from the crawl folder (file names end in the safe id)."""
        safe_id = case_number.replace("/", "_")
        for path in sorted(self.crawl_dir.rglob(f"*{safe_id}.json")):
            for judgement in load_judgements(path):
                if same_case(judgement.case_id, case_number):
                    return judgement
        return None


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

    return {
        "id": case.case_id.replace("/", "_"),
        "caseNumber": case.case_id.replace("_", " ", 1),
        "date": case.decision_date,
        "url": case.url,
        "title": per_lang(lambda s: s.title),
        "de": shown["de"].text if shown["de"] else None,
        "fr": shown["fr"].text if shown["fr"] else None,
        "status": per_lang(lambda s: s.status),
        "model": per_lang(lambda s: s.model),
        "coherenceOk": per_lang(lambda s: s.coherence_ok),
        "issues": per_lang(lambda s: s.issues),
        "pending": {lang: p.text if p else None for lang, p in pending.items()},
        "storedAt": max(s.created_at for s in history),
        "editedAt": max(edits) if edits else None,
    }


# --- HTTP layer --------------------------------------------------------------------


class Handler(SimpleHTTPRequestHandler):
    """Routes ``/api/...`` to the backend; everything else is served from ``--static``."""

    backend: Backend  # set by make_server
    judgements: JudgementList | None = None  # set by make_server (present/judgements.py)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        parts = _api_parts(url.path)
        if parts is None:
            return super().do_GET()
        if parts == ["judgements"]:  # present/judgements.py
            return self._respond(lambda: list_response(self.judgements, parse_qs(url.query)))
        if parts == ["config"]:  # present/judgements.py
            return self._respond(lambda: config_response(self.judgements))
        if parts == ["knowledge-base"]:
            query = parse_qs(url.query).get("q", [""])[0]
            return self._respond(lambda: self.backend.search(query))
        if len(parts) == 3 and parts[0] == "judgements" and parts[2] == "summary":
            return self._respond(lambda: self.backend.get_summary(parts[1]))
        self._respond(lambda: _error(HTTPStatus.NOT_FOUND, "unknown endpoint"))

    def do_POST(self) -> None:
        parts = _api_parts(urlparse(self.path).path) or []
        body = self._body()
        if len(parts) == 3 and parts[0] == "judgements" and parts[2] == "summary":
            return self._respond(lambda: self.backend.create_summary(parts[1]))
        if len(parts) == 4 and parts[0] == "judgements" and parts[2:] == ["summary", "approve"]:
            return self._respond(lambda: self.backend.approve_summary(parts[1], body))
        if len(parts) == 3 and parts[0] == "judgements" and parts[2] == "feedback":  # present/judgements.py
            return self._respond(lambda: (HTTPStatus.OK, record_feedback(parts[1], body)))
        self._respond(lambda: _error(HTTPStatus.NOT_FOUND, "unknown endpoint"))

    def do_PUT(self) -> None:
        parts = _api_parts(urlparse(self.path).path) or []
        body = self._body()
        if len(parts) == 3 and parts[0] == "judgements" and parts[2] == "summary":
            return self._respond(lambda: self.backend.edit_summary(parts[1], body))
        self._respond(lambda: _error(HTTPStatus.NOT_FOUND, "unknown endpoint"))

    def do_DELETE(self) -> None:
        self._respond(lambda: _error(HTTPStatus.METHOD_NOT_ALLOWED, "knowledge base is append-only"))

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

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
    backend: Backend, host: str, port: int, static_dir: str | Path, judgements: JudgementList | None = None
) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"backend": backend, "judgements": judgements})
    return ThreadingHTTPServer((host, port), partial(handler, directory=str(static_dir)))


def _api_parts(path: str) -> list[str] | None:
    if not path.startswith("/api/"):
        return None
    return [unquote(p) for p in path[len("/api/"):].split("/") if p]


def _summarized_case_ids(db_path: str | Path | None) -> list[str]:
    """Case ids with a stored summary (read-only, for the judgement list)."""
    with closing(store.connect(db_path)) as conn:
        return [s.case_id for s in store.list_display(conn)]


def _error(status: int, message: str) -> Response:
    return status, {"error": message}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="summary-api", description="Summary backend for the UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=None, help=f"SQLite file (default: ${store.DB_PATH_ENV} or {store.DEFAULT_DB_PATH})")
    parser.add_argument("--input", default="data/raw", help="Crawl folder with the judgements")
    parser.add_argument("--static", default="src/presentation", help="Folder with the UI files")
    parser.add_argument("--model", default=None, help="Gateway model ID (default: LLM_NAME / APERTUS_MODEL)")
    parser.add_argument("--judgements-dir", default="judgements", help="Suva example summaries")
    parser.add_argument("--examples", type=int, default=2, help="Few-shot examples per language")
    parser.add_argument("--triage", default=None, help="Triage results JSON for GET /api/judgements")
    args = parser.parse_args(argv)
    load_dotenv()  # SUMMARY_DB_PATH and gateway settings from .env
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    backend = Backend(args.db, args.input, summarizer_factory(args.model, args.judgements_dir, args.examples))
    judgements = (
        JudgementList(args.triage, summarized_ids=lambda: _summarized_case_ids(args.db), crawl_dir=args.input)
        if args.triage else None
    )
    server = make_server(backend, args.host, args.port, args.static, judgements)
    logger.info("Serving on http://%s:%s (API under /api)", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
