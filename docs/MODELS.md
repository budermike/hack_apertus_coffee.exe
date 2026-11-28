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

| # | Modell | Version / ID | Quelle | Rolle in der Lösung | Open-Weights | Lizenz |
|---|---|---|---|---|---|---|
| R1 | Apertus v1.5 70B | `apertus-v1-5-70b`, Reasoning-Modus `apertus-v1-5-70b-thinking` (bzw. `swiss-ai/Apertus-v1.5-70B` auf Hugging Face) | CSCS/Keera Gateway | **Kernaufgabe (Standard):** Relevanz-Bewertung jedes Kandidaten in zwei Durchgängen mit Begründung und Belegzitaten; Zusammenfassungen DE und FR im Suva-Stil; Übertragung von Begründungen und Zitaten in die Sprache der Oberfläche. DE/FR-Abgleich jeder Zusammenfassung: Prüfer in zwei Durchgängen und Schiedsrichter gegen das Urteil (`summary/crosscheck.py`, Standard; aus mit `SUMMARY_CROSSCHECK=0`). Der Reasoning-Modus ist wählbar (`--model`) | ja | Apache-2.0 |
| R2 | Apertus v1.5 8B | `apertus-v1-5-8b`, Reasoning-Modus `apertus-v1-5-8b-thinking` (bzw. `swiss-ai/Apertus-v1.5-8B`) | CSCS/Keera Gateway | **Kernaufgabe (Option):** kleineres, günstigeres Modell für Relevanz-Bewertung und Zusammenfassungen (`--model`, `SUMMARY_MODEL`); Testaufruf des Gateways (`keera_example.py`) | ja | Apache-2.0 |
| R3 | Multilingual-E5-small | `intfloat/multilingual-e5-small` (`sentence-transformers==6.3.0`) | Hugging Face, lokal | Mehrsprachige Embeddings (DE/FR/IT) für die Vector-DB; nur nötig, wenn neue Urteile aufgenommen werden | ja | MIT |

Alle vier Apertus-Varianten wurden auf denselben Daten verglichen (`notebooks/triage/`, `notebooks/summary/`); Standard ist 70B ohne Reasoning-Modus.

---

## 2. Entwicklung (nur beim Bauen verwendet, nicht in der Lösung)

Coding-Assistenten und alle Modelle, mit denen Texte, Code, Bilder, Audio oder Video für die Abgabe entstanden sind.

| # | Tool | Modell | Anbieter | Rolle | Genutzt von | Open-Weights |
|---|---|---|---|---|---|---|
| D1 | Claude (Chat) | Claude Sonnet 5.5 | Anthropic | Planung und Entwurf der Teamdokumente (Leitfaden, CONTRIBUTING, AGENTS.md, dieses Register) | PM | nein |
| D2 | Claude Code (Coding-Assistent) | Claude Opus 5.5 (`claude-opus-5-5`), Claude Fable 5.2 (`claude-fable-5-2`) | Anthropic | Code, Tests und Dokumentation schreiben und prüfen (Crawler, Relevanz-Bewertung, Zusammenfassung, Wissensbasis, Web-Oberfläche, Evaluation, Report); Audits von Issues und Pull Requests; **agentisches Rubberducking** für Entwurfs- und Analyseentscheide (Datenbank, Schwellen, Modell- und Prompt-Wahl, Datenanalysen in `notebooks/`) | abirahm, Mike, lim_n_to_infty, Ben (Claude Opus 5.5, auch Patches erstellen und anwenden) | nein |
| D3 | Antigravity (Coding-Assistent) | Gemini; Gemini 4 Hydrogen | Google DeepMind | Code-Review-Korrekturen, Embeddings und Vector-DB, Schema-Dokumentation (abirahm); erste Triage-Pipeline mit Tests und Datumsfilter (Ben, Gemini 4 Hydrogen) | abirahm, Ben | nein |
| D4 | Video- oder Sprachtool | noch nicht gemeldet | – | Demo-Video und Pitch. Wer das Video erstellt, trägt das Tool hier ein oder vermerkt «keines» | offen | – |

Diese Tools haben Code, Prompts, Tests und Texte geschrieben. Zur Laufzeit bewerten, wählen oder fassen sie nichts zusammen; das macht Apertus. Sie dürfen beim Entwickeln benutzt werden (Regel 2), aber **nicht zur Laufzeit** der Lösung (Regel 3).

---

## 3. Evaluation (automatische Richter, Metriken)

Falls ein Modell Antworten automatisch bewertet. Nur Open-Weights, damit die Jury die Ergebnisse nachvollziehen kann.

**Ein Modell-Richter, nur für die Zusammenfassungen:** Apertus v1.5 70B (`apertus-v1-5-70b`, Open-Weights, R1) bewertet in der Entwicklung jede erzeugte Zusammenfassung gegen die Suva-Fassung desselben Urteils nach einer festen Liste (Kernaussage, Ergebnis, Treue, Stil, Sprache, Gesamt; je 1–5; JSON-Schema, `temperature = 0`, Seed 7; `eval/summary_eval.py`, Ergebnisse `results/tables/summary/`). Grenze: Der Richter ist selbst 70B und könnte 70B-Texte bevorzugen; daneben steht eine Stilnähe nach Regeln ohne Modell. Alle anderen Kennzahlen sind gerechnet:

- Relevanz: Recall, Precision, F2 und Listenlänge gegen Suvas Auswahl (`eval/eval.py`, `eval/relevance_eval.py`), mit Bootstrap-Intervallen.
- Lernen aus Entscheidungen: Recall, Precision, Listenlänge und Average Precision in der Simulation mit Suvas Entscheiden (`eval/feedback_sim.py`).
- Zusammenfassungen: Prüfung nach Regeln ohne Modell (`summary/check.py`: zitierte Erwägungen und Gesetzesartikel kommen im Urteil vor, Ausgang stimmt mit dem Dispositiv überein) und Stilnähe zur Suva-Fassung (`eval/summary_eval.style_closeness`: Länge, Absätze, Einstieg, Ergebnis-Satz, Zitierweise, Wortwahl).

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
