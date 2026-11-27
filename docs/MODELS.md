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
| R1 | Apertus v1.5 | `apertus-v1-5-8b` (Gateway) bzw. `swiss-ai/Apertus-v1.5-8B` (HF) | CSCS/Keera Gateway, Hugging Face | **Kernaufgabe:** <z.B. Klassifikation / Zusammenfassung / Dialog> | ja | <prüfen> | |
| R2 | Apertus v1.5 (grosses Modell, falls genutzt) | `apertus-v1-5-70b` bzw. `swiss-ai/Apertus-v1.5-70B` | CSCS/Keera Gateway, Hugging Face | <Rolle> | ja | <prüfen> | |
| R3 | <Embedding-Modell, falls genutzt> | <exakte ID und Version> | <Hugging Face> | Retrieval | ja | <Lizenz> | |
| R4 | <OCR / Reranker / STT, falls genutzt> | | | | ja | | |

Modell-IDs auf dem Gateway: `apertus-v1-5-8b`, `apertus-v1-5-70b` (mit Tool Calls) sowie `apertus-v1-5-8b-thinking`, `apertus-v1-5-70b-thinking` (Reasoning-Modus, ohne Tool Calls). Nicht verwendete Zeilen löschen.

---

## 2. Entwicklung (nur beim Bauen verwendet, nicht in der Lösung)

Hier stehen auch **Coding-Assistenten** und alle Modelle, mit denen Texte, Code, Bilder, Audio oder Video für die Abgabe entstanden sind.

| # | Tool / Modell | Version / Modellname | Anbieter | Rolle | Phase | Open-Weights | Eingetragen von / Datum |
|---|---|---|---|---|---|---|---|
| D1 | Claude | Claude Sonnet 5.5 | Anthropic | Planung und Entwurf der Teamdokumente (CONTRIBUTING, Leitfaden, AGENTS.md, dieses Register) | Vorbereitung | nein | PM, 20.11.2026 |
| D2 | Claude Code (Coding-Assistent) | Claude Opus 5.5 (`claude-opus-5-5`) | Anthropic | Code schreiben und prüfen: Setup (pip, `requirements.txt`, `.env`), `keera_example.py`, Anpassungen in AGENTS.md | Entwicklung | nein | abirahm, 27.11.2026 |
| D3 | Antigravity (Coding-Assistent) | Gemini 4 Hydrogen | Google | Entwicklung und Tests der Triage-Pipeline (`triage/`, `tests/test_triage.py`, Datumsfilter, Dokumentation) | Entwicklung | nein | bgriff, 27.11.2026 |
| D4 | <Video- oder Sprachtool, z.B. ElevenLabs, Edit with Ava, falls genutzt> | | | Demo-Video / Pitch (nicht in der Lösung) | Entwicklung | nein | |

Hinweis: Diese Tools dürfen beim Entwickeln benutzt werden (Regel 2), aber **nicht zur Laufzeit** der Lösung (Regel 3).

---

## 3. Evaluation (automatische Richter, Metriken)

Falls ein Modell Antworten automatisch bewertet. Nur Open-Weights, damit die Jury die Ergebnisse nachvollziehen kann.

| # | Modell | Version / ID | Rolle | Open-Weights | Lizenz |
|---|---|---|---|---|---|
| E1 | <z.B. LLM-as-Judge> | | | ja | |

Falls wir keinen Modell-Richter verwenden: Tabelle löschen und hier vermerken: «Keine Modell-Richter, Metriken: <...>».

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
