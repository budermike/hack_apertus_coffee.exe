"""Apertus v1.5 gateway wrapper for Suva legal triage and gateway discovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
import re
from typing import Any, Sequence

from triage.embeddings import ReferenceMatch, SimilarityScore

DEFAULT_BASE_URL = "https://gateway.keera.ch/api/v1"
DEFAULT_MODEL = "apertus-v1-5-8b"

_SYSTEM_PROMPT = """\
Du bist eine erfahrene Juristin / ein erfahrener Jurist bei der Suva (Schweizerische Unfallversicherungsanstalt).
Deine Aufgabe ist das Triage-Screening von Bundesgerichtsurteilen auf Relevanz für die Suva (insbesondere Unfallversicherung nach UVG/LAA, Berufskrankheiten, unfallähnliche Körperschädigungen, ATSG/LPGA-Verfahrensrecht, Suva als Partei oder direkt relevante Sozialversicherungs-/Haftpflichtfragen).

Bewerte das vorliegende Urteil anhand:
1. der Metadaten und Urteilsauszüge,
2. der ähnlichsten bereits von Suva-Juristen zusammengefassten Präzedenzfälle (inkl. Kosinus-Ähnlichkeit).

Antworte AUSSCHLIESSLICH mit gültigem JSON im folgenden Format:
{
  "relevant": true oder false,
  "apertus_score": Ganzzahl von 0 bis 100 (0 = völlig irrelevant für die Suva, 100 = hochrelevant für Suva-Rechtspraxis),
  "reason": "Prägnante juristische Begründung in 2-3 Sätzen: Rechtsgebiet, Bezug zur Suva bzw. zum UVG (oder warum dieser fehlt) und Verweis auf den ähnlichsten Referenzfall mit Geschäftsnummer.",
  "key_topics": ["Stichwort 1", "Stichwort 2"]
}

Begründe auch bei nicht relevanten Urteilen kurz, warum sie für die Suva nicht relevant sind.
"""

# Only these metadata fields go into the prompt; crawl records also carry the raw
# HTML, hashes and fetch info, which would bloat the prompt and the cache key.
PROMPT_METADATA_KEYS = (
    "case_id",
    "case_ids",
    "decision_date",
    "date",
    "department",
    "chamber",
    "subject",
    "object",
    "language",
    "title",
    "parties",
)


@dataclass(frozen=True)
class GatewayConfig:
    """Resolved Apertus/Keera gateway configuration from environment variables."""

    api_key: str
    base_url: str
    model: str
    embedding_model: str | None = None

    @classmethod
    def from_env(
        cls,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        embedding_model: str | None = None,
    ) -> "GatewayConfig":
        """Load config supporting both LLM_* and APERTUS_* env var conventions."""
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass

        resolved_key = (
            api_key
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("APERTUS_API_KEY")
            or os.environ.get("KEERA_API_KEY")
            or ""
        ).strip()
        resolved_url = (
            base_url
            or os.environ.get("LLM_BASE_URL")
            or os.environ.get("APERTUS_BASE_URL")
            or DEFAULT_BASE_URL
        ).strip()
        resolved_model = (
            model
            or os.environ.get("LLM_NAME")
            or os.environ.get("APERTUS_MODEL")
            or DEFAULT_MODEL
        ).strip()
        resolved_emb = (
            embedding_model
            or os.environ.get("EMBEDDING_MODEL")
            or os.environ.get("APERTUS_EMBEDDING_MODEL")
            or None
        )
        return cls(
            api_key=resolved_key,
            base_url=resolved_url,
            model=resolved_model,
            embedding_model=resolved_emb.strip() if resolved_emb else None,
        )


@dataclass(frozen=True)
class TriageDecision:
    """Structured triage assessment produced by Apertus v1.5."""

    relevant: bool
    apertus_score: float
    reason: str
    key_topics: list[str]
    model: str
    raw_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TriageDecision":
        return cls(
            relevant=bool(data["relevant"]),
            apertus_score=float(data["apertus_score"]),
            reason=str(data["reason"]),
            key_topics=list(data.get("key_topics", [])),
            model=str(data["model"]),
            raw_response=str(data.get("raw_response", "")),
        )


def _build_client(config: GatewayConfig, timeout: int = 60, max_retries: int = 3) -> Any:
    if not config.api_key:
        raise ValueError(
            "Missing API key: set LLM_API_KEY or APERTUS_API_KEY in .env or environment."
        )
    from openai import OpenAI

    return OpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout=timeout,
        max_retries=max_retries,
    )


def discover_gateway_models(config: GatewayConfig | None = None) -> list[str]:
    """List model IDs exposed by the configured gateway (/models endpoint)."""
    cfg = config or GatewayConfig.from_env()
    client = _build_client(cfg)
    response = client.models.list()
    return sorted(model.id for model in response.data)


def probe_gateway_embeddings(
    model_id: str,
    config: GatewayConfig | None = None,
    sample_text: str = "Suva Unfallversicherung UVG Art. 6 Abs. 2",
) -> dict[str, Any]:
    """Test whether the gateway supports /embeddings for ``model_id``."""
    cfg = config or GatewayConfig.from_env()
    client = _build_client(cfg)
    resp = client.embeddings.create(model=model_id, input=[sample_text])
    dim = len(resp.data[0].embedding) if resp.data else 0
    return {"model": model_id, "supported": dim > 0, "dimension": dim}


def _select_judgement_excerpt(text: str, max_chars: int = 24000) -> str:
    """Keep header/facts and tail/conclusions when a judgement exceeds prompt budget."""
    clean = text.strip()
    if len(clean) <= max_chars:
        return clean
    head_len = int(max_chars * 0.6)
    tail_len = max_chars - head_len - 40
    return f"{clean[:head_len]}\n\n[... gekürzt ...]\n\n{clean[-tail_len:]}"


def _format_matches(matches: Sequence[ReferenceMatch]) -> str:
    if not matches:
        return "Keine Referenzfälle verfügbar."
    lines: list[str] = []
    for idx, match in enumerate(matches, start=1):
        lines.append(
            f"{idx}. {match.case_id} (Cosine-Similarity: {match.similarity:.3f}) — {match.title}\n"
            f"   Suva-Zusammenfassung: {match.summary_snippet}"
        )
    return "\n".join(lines)


def _parse_json_decision(raw: str, model: str) -> TriageDecision:
    """Parse JSON from Apertus output, tolerating markdown code fences."""
    cleaned = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if brace_match:
            cleaned = brace_match.group(0)

    data = json.loads(cleaned)
    score = float(data.get("apertus_score", 0.0))
    score = max(0.0, min(100.0, score))
    relevant = bool(data.get("relevant", score >= 50.0))
    reason = str(data.get("reason", "")).strip() or "Keine Begründung geliefert."
    topics_raw = data.get("key_topics", [])
    key_topics = [str(t).strip() for t in topics_raw if str(t).strip()] if isinstance(topics_raw, list) else []

    return TriageDecision(
        relevant=relevant,
        apertus_score=score,
        reason=reason,
        key_topics=key_topics,
        model=model,
        raw_response=raw,
    )


class ApertusTriageClient:
    """Configurable Apertus v1.5 client for relevance scoring and explanation."""

    def __init__(
        self,
        config: GatewayConfig | None = None,
        client: Any | None = None,
        temperature: float = 0.0,
        max_tokens: int = 500,
        timeout: int = 60,
        max_retries: int = 3,
    ) -> None:
        self.config = config or GatewayConfig.from_env()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = client if client is not None else _build_client(
            self.config, timeout=timeout, max_retries=max_retries
        )

    def build_messages(
        self,
        case_id: str,
        judgement_text: str,
        metadata: dict[str, Any],
        similarity: SimilarityScore,
        keyword_hits: Sequence[str],
    ) -> list[dict[str, str]]:
        """Build the chat messages for one judgement (also the basis of the cache key)."""
        excerpt = _select_judgement_excerpt(judgement_text)
        prompt_meta = {k: metadata[k] for k in PROMPT_METADATA_KEYS if metadata.get(k)}
        meta_preview = json.dumps(prompt_meta, ensure_ascii=False, sort_keys=True)
        keywords_str = ", ".join(keyword_hits) if keyword_hits else "keine"
        user_prompt = (
            f"Urteil: {case_id}\n"
            f"Metadaten: {meta_preview}\n"
            f"Erkannte Schlüsselwörter: {keywords_str}\n"
            f"Embedding-Kosinus-Ähnlichkeit zu Suva-Präzedenzfällen: "
            f"max={similarity.max_similarity:.3f}, top_k_mean={similarity.top_k_mean_similarity:.3f}\n\n"
            f"Ähnlichste bisher von der Suva zusammengefasste Urteile:\n"
            f"{_format_matches(similarity.top_matches)}\n\n"
            f"Urteilstext (Auszug):\n{excerpt}"
        )
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def request_key(self, messages: Sequence[dict[str, str]]) -> str:
        """Hash of everything that determines the answer: model, parameters, prompt."""
        payload = json.dumps(
            {
                "model": self.config.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "messages": list(messages),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def complete(self, messages: Sequence[dict[str, str]]) -> TriageDecision:
        """Send prepared messages to Apertus and parse the JSON decision."""
        response = self._client.chat.completions.create(
            model=self.config.model,
            messages=list(messages),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        content = response.choices[0].message.content or ""
        return _parse_json_decision(content, model=self.config.model)

    def evaluate(
        self,
        case_id: str,
        judgement_text: str,
        metadata: dict[str, Any],
        similarity: SimilarityScore,
        keyword_hits: Sequence[str],
    ) -> TriageDecision:
        """Ask Apertus v1.5 to score Suva relevance and explain why."""
        return self.complete(
            self.build_messages(case_id, judgement_text, metadata, similarity, keyword_hits)
        )
