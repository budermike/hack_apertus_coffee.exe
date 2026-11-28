# Modell- und Tool-Register

Dieses Register erfüllt **Regel 6 (Offenlegung)** der Veranstalter: Der Technical Report listet **jedes** verwendete Modell mit Version und Rolle, in der Entwicklung und zur Laufzeit. Aus dieser Datei entsteht später der entsprechende Abschnitt im Report.

**Pflege:** Wer ein Modell oder einen KI-Assistenten verwendet, trägt es **sofort** hier ein (im selben PR wie die Änderung). Lieber einen Eintrag zu viel als einen zu wenig.

Regeln im Überblick (Details in `CONTRIBUTING.md`):

- **Apertus v1.5 (8B oder 70B, auch feingetunt) löst die Kernaufgabe.**
- **Laufzeit:** nur Apertus und Open-Weights-Modelle für Hilfsaufgaben (Embeddings, OCR, Speech-to-Text, Reranking). Keine proprietären KI-APIs.
- **Entwicklung:** alle Tools und Modelle erlaubt, sie müssen aber hier stehen.
- **Evaluation:** als automatische Richter nur Open-Weights-Modelle (Jury muss es reproduzieren können).

---

## 1. Laufzeit (in der abgegebenen Lösung)

Alles, was die Lösung beim Ausführen verwendet. **In dieser Tabelle darf in der Spalte «Open-Weights» nirgends «nein» stehen.**

| # | Modell | Version / ID | Quelle | Rolle in der Lösung | Open-Weights | Lizenz | Eingetragen von / Datum |
|---|---|---|---|---|---|---|---|
| R1 | Apertus v1.5 (8B) | `apertus-v1-5-8b` (Gateway) bzw. `swiss-ai/Apertus-v1.5-8B` (HF) | CSCS/Keera Gateway, Hugging Face | **Kernaufgabe:** Relevanz-Triage (Stufe 2 der alten Pipeline, Option `--model`). Zusammenfassung DE/FR nur noch optional (`SUMMARY_MODEL`, älterer Ablauf `pipeline="v1"`) | ja | Apache-2.0 | abirahm / Mike, 27.11.2026 |
| R2 | Apertus v1.5 (70B) | `apertus-v1-5-70b` bzw. `swiss-ai/Apertus-v1.5-70B` | CSCS/Keera Gateway, Hugging Face | **Kernaufgabe:** Relevanz-Bewertung für die Suva-Liste in zwei Durchgängen (`triage/relevance.py`: Kriterien mit Beleg und Score, dann «Vertiefung»; `docs/ISSUE_RECALL.md`). **Zusammenfassung DE/FR (Standard):** Pipeline v3 (`summary/v3.py`, `prompts/v3/`: Kernfrage, Text DE, Text FR; `SUMMARY_MODEL`). **Evaluation:** Richter für die Zusammenfassungen (`eval/summary_eval.py`, feste Bewertungsliste, Open-Weights wie gefordert). Vergleich in `notebooks/summary/02_modelle_prompts.ipynb` | ja | Apache-2.0 | abirahm, 27.11.2026 |
| R1b | Apertus v1.5 (8B, Thinking) | `apertus-v1-5-8b-thinking` | CSCS/Keera Gateway | **Kernaufgabe, optional:** Relevanz-Bewertung wie R2 im Reasoning-Modus mit dem kleinen Modell (`triage.cli relevance --model apertus-v1-5-8b-thinking`). Verglichen in `notebooks/triage/05_pitch_varianten.ipynb`, Ergebnisse in `results/tables/thinking_8b/` | ja | Apache-2.0 | Mike, 28.11.2026 |
| R2b | Apertus v1.5 (70B, Thinking) | `apertus-v1-5-70b-thinking` | CSCS/Keera Gateway | **Kernaufgabe, optional:** dieselbe Relevanz-Bewertung wie R2 im Reasoning-Modus (`triage.cli relevance --model apertus-v1-5-70b-thinking`). Verglichen in `notebooks/triage/04_gesamtvergleich.ipynb`, Ergebnisse in `results/tables/thinking_70b/` | ja | Apache-2.0 | Mike, 28.11.2026 |
| R3 | Multilingual-E5-small | `intfloat/multilingual-e5-small` (`sentence-transformers==6.3.0`) | Hugging Face (lokal auf MPS/CPU) | Mehrsprachige Embeddings (DE/FR/IT) für Vector-DB, Triage-Retrieval & semantische Suche (#5, #8, #30, #37). Gebraucht, wenn neue Urteile in die Vector-DB aufgenommen werden; die Bibliothek steht in `requirements-dev.txt` und fehlt im Container, der mit den gespeicherten Vektoren auskommt | ja | MIT | abirahm, 27.11.2026 |

Modell-IDs auf dem Gateway: `apertus-v1-5-8b`, `apertus-v1-5-70b` (mit Tool Calls) sowie `apertus-v1-5-8b-thinking`, `apertus-v1-5-70b-thinking` (Reasoning-Modus, ohne Tool Calls). Nicht verwendete Zeilen löschen.

---

## 2. Entwicklung (nur beim Bauen verwendet, nicht in der Lösung)

Hier stehen auch **Coding-Assistenten** und alle Modelle, mit denen Texte, Code, Bilder, Audio oder Video für die Abgabe entstanden sind.

| # | Tool / Modell | Version / Modellname | Anbieter | Rolle | Phase | Open-Weights | Eingetragen von / Datum |
|---|---|---|---|---|---|---|---|
| D1 | Claude | Claude Sonnet 5.5 | Anthropic | Planung und Entwurf der Teamdokumente (CONTRIBUTING, Leitfaden, AGENTS.md, dieses Register) | Vorbereitung | nein | PM, 20.11.2026 |
| D2 | Claude Code (Coding-Assistent) | Claude Opus 5.5 (`claude-opus-5-5`) | Anthropic | Code schreiben und prüfen: Setup (pip, `requirements.txt`, `.env`), `keera_example.py`, Anpassungen in AGENTS.md, Crawler (`crawl.py`) | Entwicklung | nein | abirahm, 27.11.2026 |
| D3 | Antigravity (Coding-Assistent) | Gemini | Google DeepMind | Code-Review-Fixes, Schema-Dokumentation (`docs/SCHEMA.md`), Embeddings (`embed.py`) und lokale Vector-DB (`crawl/vectordb.py`, Tests) | Entwicklung | nein | abirahm, 27.11.2026 |
| D4 | Video- oder Sprachtool für Demo-Video und Pitch | noch nicht gemeldet | – | Demo-Video / Pitch (nicht in der Lösung). Wer das Video erstellt, trägt das Tool hier ein oder vermerkt «keines» | Entwicklung | – | offen, 28.11.2026 |
| D5 | Claude Code (Coding-Assistent) | Claude Opus 5.5 (`claude-opus-5-5`) | Anthropic | Summary-Modul (`src/hack_apertus_coffee_exe/summary/`), Stilprofile und Glossar (`prompts/`), Tests; Paket-Layout-Fix; Schnittstellen und zentrale Konfiguration #48 (`config.py`, `configs/paths.json`, `triage/service.py`, `summary/service.py`, `docs/ARCHITECTURE.md`); Datenanalyse Relevanz (`eval/relevance.py`, `eval/learned.py`, `eval/compare.py`, `notebooks/triage/01`–`04`, Prompt v2 als Experiment; rechnet Metriken, die Bewertung der Urteile macht Apertus) | Entwicklung | nein | Mike, 27.11.2026 |
| D6 | Claude Code (Coding-Assistent) | Claude Fable 5.2 (`claude-fable-5-2`) | Anthropic | Evaluation #21: `eval/eval.py`, Tests, `results/eval.md` (rechnet nur Metriken, bewertet keine Antworten). Relevanz-Bewertung für die Suva-Liste: `triage/relevance.py`, `triage/calibration.py`, `triage/relevance_period.py`, `eval/relevance_eval.py`, Tests, Benchmark und `docs/ISSUE_RECALL.md` (schreibt Code und Prompts; die Bewertung der Urteile macht Apertus) | Entwicklung | nein | abirahm, 27.11.2026 |
| D7 | Claude Code (Coding-Assistent) | Claude Fable 5.2 (`claude-fable-5-2`), Claude Opus 5.5 (`claude-opus-5-5`) | Anthropic | Web-Oberfläche (`src/presentation/`), Urteilsliste für die UI (`present/judgements.py`), Audit und Issue-Entwürfe (`docs/FRONTEND_OPEN_ISSUES.md`) | Entwicklung | nein | lim_n_to_infty, 27.11.2026 |
| D8 | Claude Code (Coding-Assistent) | Claude Fable 5.2 (`claude-fable-5-2`) | Anthropic | Audit aller Issues und PRs mit Nachtrag (`docs/audit/`); Korrekturen #77–#83 (u. a. Schutz der Suva-Liste, `LLM_*`, Docker); Korrekturen zur Oberfläche und zum Bearbeiten (#97), Lernen aus Relevanz-Entscheidungen samt Simulation (`triage/feedback_learning.py`, `eval/feedback_sim.py`, #98), gespeicherter Volltext (#99); Tests; `technical_report.md`, README-Projektteil, `docs/ARCHITECTURE.md`. Schreibt Code, Tests und Doku; bewertet, wählt und fasst zur Laufzeit nichts | Entwicklung | nein | abirahm, 28.11.2026 |

Hinweis: Diese Tools dürfen beim Entwickeln benutzt werden (Regel 2), aber **nicht zur Laufzeit** der Lösung (Regel 3).

---

## 3. Evaluation (automatische Richter, Metriken)

Falls ein Modell Antworten automatisch bewertet. Nur Open-Weights, damit die Jury die Ergebnisse nachvollziehen kann.

**Keine Modell-Richter.** Alle Kennzahlen sind gerechnet, kein Modell bewertet Antworten:

- Relevanz: Recall, Precision, F2 und Listenlänge gegen Suvas Auswahl (`eval/eval.py`, `eval/relevance_eval.py`), mit Bootstrap-Intervallen.
- Lernen aus Entscheidungen: Recall, Precision, Listenlänge und Average Precision in der Simulation mit Suvas Entscheiden (`eval/feedback_sim.py`).
- Zusammenfassungen: Prüfung nach Regeln ohne Modell (`summary/check.py`: zitierte Erwägungen und Gesetzesartikel kommen im Urteil vor, Ausgang stimmt mit dem Dispositiv überein).

---

## 4. Kontrollen vor der Abgabe

- [ ] Jede Zeile in Abschnitt 1 hat «Open-Weights: ja» und eine **exakte Version**.
- [ ] **Apertus v1.5** erledigt die Kernaufgabe (kein anderes Modell übernimmt sie).
- [ ] Im Code kommen keine Aufrufe an proprietäre KI-APIs vor (Suche nach `api.openai.com`, `api.anthropic.com` und ähnlichen).
- [ ] Jede Person im Team hat gemeldet, welche KI-Assistenten sie verwendet hat (Abschnitt 2).
- [ ] Video- und Sprachtools sind in Abschnitt 2 eingetragen, falls genutzt.
- [ ] Der Technical Report enthält diese Tabellen (Modell, Version, Rolle, Phase).
- [ ] Die Pitch-Slide «So nutzen wir Apertus» stimmt mit Abschnitt 1 überein.

---
