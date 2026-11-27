"""Summary stage: Suva-style DE/FR summaries of Federal Court judgements with Apertus.

Issues #14 (style + glossary in ``prompts/``), #15 (generation), #16 (check),
#17 (batch + on demand), #26 (SQLite store in ``store.py``). See ``run.py`` for
the CLI and ``api.py`` for the HTTP backend of the UI.
"""

from .generate import Summarizer, SummaryResult
from .judgement import Judgement, load_judgements

__all__ = ["Judgement", "Summarizer", "SummaryResult", "load_judgements"]
