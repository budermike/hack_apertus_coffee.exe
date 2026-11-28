"""Thin Apertus chat wrapper for the summary stage.

Reuses the gateway configuration of the triage stage (``hack_apertus_coffee_exe.triage.llm.GatewayConfig``),
so ``LLM_*`` / ``APERTUS_*`` environment variables are resolved in one place.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..triage.llm import GatewayConfig, _build_client

DEFAULT_MAX_TOKENS = 1500


class SummaryLLM:
    """Send one system + user prompt to Apertus and return the text answer."""

    def __init__(
        self,
        config: GatewayConfig | None = None,
        client: Any | None = None,
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = 180,
        max_retries: int = 3,
    ) -> None:
        self.config = config or GatewayConfig.from_env()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = client if client is not None else _build_client(
            self.config, timeout=timeout, max_retries=max_retries
        )

    @property
    def model(self) -> str:
        return self.config.model

    def chat(self, system: str, user: str, max_tokens: int | None = None,
             response_format: dict[str, Any] | None = None, temperature: float | None = None) -> str:
        """``response_format`` (e.g. a JSON schema) is passed to the gateway as it is;
        ``temperature`` replaces the configured one for this call."""
        response = self._client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=max_tokens or self.max_tokens,
            **({"response_format": response_format} if response_format else {}),
        )
        return (response.choices[0].message.content or "").strip()


def parse_json_object(raw: str) -> dict[str, Any]:
    """Parse the first JSON object in a model answer, tolerating ``` fences."""
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.DOTALL)
    candidate = fence.group(1) if fence else raw
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object in model answer")
    data = json.loads(candidate[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Model answer is not a JSON object")
    return data
