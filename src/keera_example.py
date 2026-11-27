"""Minimal smoke test: send one prompt to Apertus via the Keera gateway.

Setup:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
Run:    .venv/bin/python keera_example.py
Reads APERTUS_BASE_URL, APERTUS_API_KEY and APERTUS_MODEL from .env.
The openai package is used only as a client for the Apertus gateway.
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    base_url=os.environ.get("APERTUS_BASE_URL", "https://gateway.keera.ch/api/v1"),
    api_key=os.environ["APERTUS_API_KEY"],
    timeout=60,
    max_retries=3,
)

response = client.chat.completions.create(
    model=os.environ.get("APERTUS_MODEL", "apertus-v1-5-8b"),
    messages=[{"role": "user", "content": "Erkläre in einem Satz, was Apertus ist."}],
    temperature=0,
    max_tokens=200,
)

print(response.choices[0].message.content)
