"""Minimal smoke test: send one prompt to Apertus via the Keera gateway.

Setup:  python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
Run:    .venv/bin/python keera_example.py
Reads LLM_NAME, LLM_BASE_URL and LLM_API_KEY from .env (names required by the template).
The openai package is used only as a client for the Apertus gateway.
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    base_url=os.environ.get("LLM_BASE_URL", "https://gateway.keera.ch/api/v1"),
    api_key=os.environ["LLM_API_KEY"],
    timeout=60,
    max_retries=3,
)

response = client.chat.completions.create(
    model=os.environ.get("LLM_NAME", "apertus-v1-5-8b"),
    messages=[{"role": "user", "content": "Erkläre in einem Satz, was Apertus ist."}],
    temperature=0,
    max_tokens=200,
)

print(response.choices[0].message.content)
