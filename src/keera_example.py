"""Minimal smoke test: send one prompt to Apertus via the Keera gateway.

Setup:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
Run:    .venv/bin/python src/keera_example.py
Reads LLM_BASE_URL, LLM_API_KEY and LLM_NAME from .env. These are the names the
organisers' template requires (README, "Environment"). The older names
APERTUS_BASE_URL, APERTUS_API_KEY and APERTUS_MODEL still work as aliases, in the
same order as `triage.llm.GatewayConfig.from_env`.
The openai package is used only as a client for the Apertus gateway.
"""

import os

DEFAULT_BASE_URL = "https://gateway.keera.ch/api/v1"
DEFAULT_MODEL = "apertus-v1-5-8b"


def _first_env(*names: str) -> str:
    """Value of the first of these environment variables that is set and not empty."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def gateway_settings() -> dict[str, str]:
    """Key, endpoint and model for the gateway. LLM_* wins over the older aliases."""
    api_key = _first_env("LLM_API_KEY", "APERTUS_API_KEY", "KEERA_API_KEY")
    if not api_key:
        raise SystemExit("Missing API key: set LLM_API_KEY in .env (see .env.example).")
    return {
        "api_key": api_key,
        "base_url": _first_env("LLM_BASE_URL", "APERTUS_BASE_URL") or DEFAULT_BASE_URL,
        "model": _first_env("LLM_NAME", "APERTUS_MODEL") or DEFAULT_MODEL,
    }


def main() -> None:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    settings = gateway_settings()
    client = OpenAI(
        base_url=settings["base_url"],
        api_key=settings["api_key"],
        timeout=60,
        max_retries=3,
    )
    response = client.chat.completions.create(
        model=settings["model"],
        messages=[{"role": "user", "content": "Erkläre in einem Satz, was Apertus ist."}],
        temperature=0,
        max_tokens=200,
    )
    print(response.choices[0].message.content)


if __name__ == "__main__":
    main()
