# Leitfaden – Team Coffee.exe @ Hack Apertus Zürich

Alles, was jede Person im Team wissen muss, auf einen Blick. Die Teamregeln stehen in [CONTRIBUTING.md](../CONTRIBUTING.md).

> Stand: 20.11.2026, basierend auf den offiziellen Infos der Veranstalter (Notion/sprintd). Im Zweifel gilt immer die offizielle Seite: https://app.sprintd.org/events/hack-apertus-zurich

---

## 0. Die wichtigsten Punkte in 60 Sekunden

1. **Fr 27.11.:** Check-in 8:00–9:00, Start 9:00. Nur Teams, die einchecken und vor Ort arbeiten, können gewinnen.
2. **Die Challenges kennen wir erst vor Ort.** Wir geben Präferenzen ab, ein Algorithmus teilt zu. Es kann die 2. oder 3. Wahl werden.
3. **Apertus v1.5 muss die Kernaufgabe lösen.** Zur Laufzeit nur Apertus und Open-Weights-Modelle, keine proprietären KI-APIs.
4. **Abgabe: Sa 28.11., 14:30**, auf sprintd, keine Verlängerung. Nur «Submit project» zählt, ein Entwurf nicht.
5. **Abgabe besteht aus:** öffentliches GitHub-Repo, Demo-Video (max. 2 Min.), ZIP mit Pitch-Deck (PDF, max. 10 Slides) und Technical Report (PDF, max. 6 Seiten).
6. **Pitch: Sa 28.11., 15:00**, 7 Min. + 2 Min. Fragen, vor dem Challenge-Partner.
7. **Finalisten** werden um 17:30 bekannt gegeben (Top 6), Finale auf der Main Stage mit 5 Min. und Slides.

---

## 1. Ablauf des Hackathons (offiziell)

### Freitag, 27.11.2026

| Zeit | Was |
|---|---|
| 8:00–9:00 | Check-in mit Frühstück |
| 9:00 | Welcome und Start |
| Vormittag | Die drei Challenges werden auf sprintd enthüllt. Jeder Challenge-Partner pitcht 5 Minuten. |
| danach | Wir geben unsere Präferenzen auf sprintd ab. Ein Algorithmus teilt die Teams zu, abgewogen zwischen Präferenzen und gleichmässiger Verteilung auf die drei Challenges. |
| nach der Zuteilung | Deep-Dive-Workshop mit dem Challenge-Partner (ca. 60 Min.) |
| danach | Bauen auf Apertus, mit Mentoren und Challenge-Ownern vor Ort |

### Samstag, 28.11.2026

| Zeit | Was |
|---|---|
| bis 14:30 | **Abgabe** auf sprintd (kein Aufschub) |
| 15:00 | **Pitch** vor dem Challenge-Partner: 7 Min. Pitch + 2 Min. Q&A. Raum (1, 2 oder 3) und Reihenfolge stehen auf sprintd. In der Nähe des Raums bleiben, man kann früher drankommen, wenn ein Team ausfällt. |
| 17:30 | **Top 6** werden bekannt gegeben |
| danach | **Finale** auf der Main Stage (Main Hall): 5 Min. pro Team mit Slides, 1 Frage der Hauptjury |

**Zur Hauptjury:** Zuerst bewertet jeder Challenge-Partner nach seinen eigenen Kriterien (stehen in der Challenge-Beschreibung) und wählt seine Top 2. Die Top 6 treten im Finale gegen die Hauptjury an. Diese bestimmt die Top 3. Entscheide der Jury sind endgültig, es gibt keine Einzelrückmeldungen.

---

## 2. Regeln (Kurzfassung)

Details und Konsequenzen für den Code stehen in [CONTRIBUTING.md](../CONTRIBUTING.md), Abschnitt 2.

- **Apertus v1.5 (8B/70B, auch feingetunt) löst die Kernaufgabe.**
- **Laufzeit:** nur Apertus und Open-Weights-Hilfsmodelle (Embeddings, OCR, Speech-to-Text, Reranking). Keine Closed-Modelle oder proprietären APIs.
- **Entwicklung:** alle Tools erlaubt, auch Coding-Assistenten.
- **Deployment:** on-premise, air-gapped oder souveräne Schweizer Cloud muss möglich sein.
- **Offenlegung:** Technical Report listet jedes Modell mit Version und Rolle (Entwicklung und Laufzeit).
- **Nicht preisberechtigt**, wer die Regeln 1, 3, 5 oder 6 verletzt.

---

## 3. Bewertung: worauf wir hinarbeiten

**Challenge-Partner:** eigene Kriterien, in der Challenge-Beschreibung veröffentlicht. Die lesen wir direkt nach dem Pitch genau, notieren sie in `docs/DECISIONS.md` und richten den MVP daran aus.

**Hauptjury (Finale):**

| Kriterium | Frage der Jury | Was wir dafür liefern |
|---|---|---|
| 🎯 Sinnvoller KI-Einsatz | Löst Apertus v1.5 die Kernaufgabe, und ist es das richtige Werkzeug? | 1 Slide «So nutzen wir Apertus» (Pflicht), klare Begründung |
| 🔬 Technische Sorgfalt | Gut gebaut, getestet, evaluiert? Funktioniert die Demo? | Evaluationsskript, Ergebnistabelle, stabile Demo |
| 💸 Wert, Kosten, Skalierbarkeit | Echter Mehrwert? Bezahlbar im Betrieb? Skalierbar? | Nutzenargument, grobe Kosten- und Hardware-Schätzung |
| 🇨🇭 Souveräne Deployability | Läuft air-gapped, on-premise oder Swiss Cloud, ohne ausländische Closed Services? | Deployment-Skizze, keine Closed-Abhängigkeit |
| 🛠 Umsetzbarkeit | Kann der Partner es nach dem Hackathon übernehmen und betreiben? | Saubere README, einfaches Setup, Grenzen benennen |
| 🎤 Pitch | Klar, überzeugende Demo, Zeit eingehalten, gute Antworten? | Probe-Pitches, Demo-Backup, Q&A-Vorbereitung |

---

## 4. Tools und Ressourcen

### Von den Veranstaltern zur Verfügung gestellt

| Was | Details |
|---|---|
| **Apertus-Zugang (CSCS)** | Inferenz für Apertus v1.5 über das **Keera Gateway**, Endpoint `https://gateway.keera.ch/api/v1`. **Jedes Team bekommt einen eigenen Key**, den man bei der Ansprechperson vor Ort oder auf Discord anfordert (laut sprintd: Sabine Wildemann). Freischaltung erst **am 27.11. ab 8:00 nach dem Check-in**, vorher gibt es keinen Key. Modelle: `apertus-v1-5-8b` und `apertus-v1-5-70b` (mit Tool Calls), `apertus-v1-5-8b-thinking` und `apertus-v1-5-70b-thinking` (Reasoning-Modus, **ohne** Tool Calls). |
| **Hugging Face** | 10 $ Credits (Inference Providers, Endpoints, Jobs, Spaces, Compute, Storage; ohne Abo-Gebühren). **Pro Person einmal einlösbar**, ein **persönliches HF-Konto** ist nötig, die Credits verfallen 1 Jahr nach dem Einlösen. Einlösbar ab 27.11. um 8:00 nach dem Check-in (Reiter «Perks»). |
| **ElevenLabs** | 1 Monat Creator-Tier gratis, **verfügbar ab 27.11.** (nicht früher). Wer sich vor dem 26.11. angemeldet hat, kann über den Discord-Bot einen Gutscheincode holen (Discord beitreten, Kanal #coupon-codes, «Start Redemption», mit der sprintd-E-Mail anmelden). **Achtung:** ElevenLabs ist eine proprietäre KI-API und nach den Regeln **nicht zur Laufzeit** der Lösung erlaubt. Höchstens für Demo-Video oder Pitch nutzen und dann im Technical Report offenlegen. Bei Zweifeln die Organisatoren fragen. |
| **Edit with Ava** | Gratis Credits für Videos, z.B. für das Demo-Video. Infos auf der Info-Seite der Veranstalter. |

### Modelle

- Apertus v1.5 8B: `huggingface.co/swiss-ai/Apertus-v1.5-8B`
- Apertus v1.5 70B: `huggingface.co/swiss-ai/Apertus-v1.5-70B`
- Zugriff z.B. über vLLM oder Transformers, oder über das Gateway.

### Nützliche Links

- Offizielle Apertus-Website mit Doku: https://www.apertus-ai.org/
- Technischer Report zur 1.5-Familie: https://huggingface.co/papers/2610.15542
- Überblick: https://github.com/rnckp/awesome-apertus
- Fragen zur Wissenschaft und zum Training: https://ask.apertus-ai.org/

### Vorlagen und Beispiele (laut Veranstalter-Notion)

- **Swiss Legal RAG Demo:** minimale RAG-App (Gradio, Apertus-8B über die HF Inference API) über Schweizer Gesetze, viersprachig, ohne GPU und ohne Vektor-DB. Gutes Muster für «Chat mit Dokumenten».
- **Apertus Translate:** Experimente zu Übersetzung, ein Beispiel für sauberes Testen von Modellfähigkeiten.
- **Apertus Probe:** Linear Probes auf den Aktivierungen, um Halluzinationen vorherzusagen (Interpretierbarkeit).
- **Fallstudie Kanton Tessin:** feingetuntes Apertus-8B übersetzt produktiv für eine Kantonsverwaltung. Hinweis auf öffentliche Verwaltung als Framing.
- **Fine-Tuning:** Apertus-Rezepte (LoRA und Full-Parameter), PEFT und TRL, Alignment Handbook.
- **Prompt-Optimierung:** Survey zu automatischer Prompt-Optimierung.

### Mentoren vor Ort (Auszug aus der Notion-Liste, Themenschwerpunkte)

| Name | Schwerpunkt |
|---|---|
| Prof. Dr. Aygul Zagidullina (HSLU) | LLM-/SLM-Fine-Tuning, RAG, agentische Systeme, On-prem/Cloud |
| Irune Lansorena | Production AI, AI Governance, sichere Architekturen |
| Josef Kruckenberg (Liip) | RAG, CMS-Integration, Open Source |
| Justinas Mišeikis (Sony AI) | Computer Vision, Product-Market-Fit, AI-Ethik |
| Mike Romano | MVP-Scoping, Architektur, Apertus-Deployment |
| Olivier Grognuz (Artifact) | RAG, Deployment, System Design |

Die Liste kann weitere Personen enthalten, vor Ort nachfragen.

---

## 5. Abgabe

Alles läuft über sprintd (Event-Seite → Tab «Submit», öffnet mit dem Start der Bauphase). **Eine Abgabe pro Team.** Alle im Team sehen und bearbeiten dasselbe Formular. Bis zum Ablauf kann man aktualisieren.

### Checkliste (PM führt, zweite Person kontrolliert)

- [ ] **GitHub-Repo-URL**, öffentlich, basierend auf dem **Template-Repo der Veranstalter** (Link wird am 27.11. um 8:00 veröffentlicht)
  - [ ] Link öffnet sich in einem Inkognito-Fenster (wirklich öffentlich)
  - [ ] README mit Quickstart, Demo-Befehl und Modellangaben
  - [ ] Keine Secrets, keine echten Personendaten, keine vertraulichen Partnerdaten
  - [ ] `docs/MODELS.md` vollständig
- [ ] **Demo-Video**, URL, max. 2 Minuten (öffentlich oder per Link abrufbar, Link getestet)
- [ ] **Projekt-ZIP** mit genau zwei Dateien:
  - [ ] **Pitch-Deck** (PDF, max. 10 Slides, enthält **eine Slide, die genau zeigt, wie Apertus verwendet wird**)
  - [ ] **Technical Report** (PDF, max. 6 Seiten, Vorlage im Template-Repo, listet jedes Modell mit Version und Rolle)
- [ ] Häkchen bei «This is our own work, built during this event»
- [ ] Auf **«Submit project»** geklickt (nicht nur «Save draft») und Bestätigung gesehen

Das Pitch-Deck (PDF) wird zusätzlich fürs Finale gebraucht.

---

## 6. Pitch und Demo-Video

### Pitch (7 Min. + 2 Min. Q&A, Finale: 5 Min.)

Aufbau (anpassen, höchstens 10 Slides):

1. Problem (konkretes Beispiel)
2. Lösung in einem Satz
3. **Live-Demo** (ein sicherer Happy Path, Backup-Aufnahme bereit)
4. **Wie wir Apertus nutzen** (Pflicht-Slide: Modell, Aufgabe, Pipeline)
5. Ergebnisse der Evaluation (ehrlich, mit Schwächen)
6. Souverän deploybar: on-premise / air-gapped / Swiss Cloud
7. Wert, Kosten, Skalierung, nächste Schritte für den Partner
8. Team

Vor dem Slot: eigener Laptop dabei, angeschlossen und **früh getestet**. Wer spricht, wer klickt, ist vorab geklärt. Mindestens zwei Probedurchläufe mit Stoppuhr.

### Demo-Video (max. 2 Min.)

Skript in 5 Sätzen: Problem → Lösung → kurzer Durchlauf → Apertus-Rolle → Nutzen. Sprache und Untertitel festlegen, nach der Aufnahme einmal komplett ansehen.

---

## 7. Unser Zeitplan (Vorschlag, an die offizielle Agenda anpassen)

### Vorab (bis 26.11.)

- [ ] Jede Person: sprintd-Login mit der Anmelde-E-Mail, Discord beigetreten
- [ ] Jede Person: **persönliches Hugging-Face-Konto** angelegt (die Credits werden pro Person eingelöst)
- [ ] GitHub-Accounts bereit, Team-Repo beigetreten
- [ ] Rollen verteilt (siehe CONTRIBUTING); alle sind auf dem Discord-Server von Hack Apertus und in unserer Team-Gruppe
- [ ] Logistik: Anreise, Laptops, Ladegeräte, Verlängerungskabel, Kopfhörer
- [ ] Nur Prozess- und Dokumentvorlagen vorbereiten, **keinen Lösungscode**

### Freitag 27.11.

| Zeit (Ziel) | Was | Wer |
|---|---|---|
| 7:45 | Treffpunkt, Check-in | alle |
| direkt nach dem Check-in (ab 8:00) | **Team-Key** fürs Gateway anfordern (Ansprechperson vor Ort oder Discord), Key sicher ans Team verteilen (nie ins Repo oder in einen öffentlichen Chat), Smoke-Test, HF- und ElevenLabs-Perks einlösen | PM + Backend |
| bis 9:00 | Template-Repo prüfen (falls schon da), Team-Repo aufsetzen, Board anlegen | PM + Backend |
| 9:00 | Welcome, Challenge-Pitches: **Notizen** zu Ziel, Daten, Erfolgskriterien und Kriterien des Partners | alle |
| nach den Pitches (max. 10 Min.) | Präferenzen bewerten (Abschnitt 8) und auf sprintd eintragen | PM |
| nach der Zuteilung | Deep-Dive-Workshop, PM stellt die gesammelten Fragen | PM + alle |
| bis 60 Min. nach dem Workshop | **Scoping-Session:** Nutzer, Kernfall, MVP, Erfolgsmetrik, Evaluationsset, Aufgaben als Issues | alle |
| bis ca. 14:00 | **Durchstich:** Input → Apertus → Output läuft mit echtem Modell | Backend |
| Nachmittag | Kernfunktionen, Evaluationsset anlegen, erste Oberfläche | alle |
| ca. 17:00 | Zwischenstand (15 Min.): Läuft der Durchstich? MVP-Ziel anpassen, Streichliste ziehen | PM |
| Abend | Weiterbauen. Öffnungszeiten des Venues und Übernachtung klären. **Schlaf einplanen.** | alle |
| Ab 24:00 | Schichtarbeit gemäss Arbeitsplan | alle |

### Samstag 28.11.

| Zeit (Ziel) | Was | Wer |
|---|---|---|
| 7:00 | Standup (10 Min.): Stand gegen den MVP, Plan-Anpassungen | alle |
| bis 10:30 | Kernfunktion fertig, Evaluation läuft | Backend + Daten |
| **10:30** | **Feature-Freeze** | PM |
| 10:30–12:30 | Evaluation und Technical Report, Pitch-Deck, Demo-Video **parallel**; Demo absichern | verteilt |
| 12:30–13:15 | Essen und Puffer für Fehler | alle |
| 13:15 | Abgabe als Entwurf auf sprintd, Checkliste durchgehen | PM + 2. Person |
| **13:45** | **«Submit project»** (45 Min. Puffer zur Deadline) | PM |
| 14:00–14:45 | 2 Probe-Pitches (7 + 2 Min.), Laptop testen | alle |
| 14:30 | Offizielle Deadline (haben wir schon erledigt) | |
| 15:00 | Pitch im zugewiesenen Raum | alle |
| 17:30 | Finalisten-Bekanntgabe. Falls Finale: 5-Min.-Version vorbereiten | alle |

---

## 8. Projektauswahl

Die Challenges werden erst vor Ort enthüllt. Wir können also nicht frei wählen, sondern nur eine **Rangliste** abgeben. Der Algorithmus teilt dann zu. Deshalb: schnell bewerten, keinen Frust bei der 2. oder 3. Wahl, **jede Challenge wird mit demselben Prozess gewonnen**.

### Schritt 1: Während der Pitches

Pro Challenge notieren: Aufgabe in einem Satz, vorhandene Daten, Erfolgskriterien des Partners, offene Fragen.

### Schritt 2: Scoring (max. 10 Minuten)

Jede Person vergibt pro Challenge 1–5 Punkte pro Kriterium. Der PM summiert, bei Gleichstand entscheidet er.

| Kriterium | Gewicht | Leitfrage |
|---|---|---|
| Apertus-Fit | ×3 | Kann **Apertus** die Kernaufgabe sinnvoll lösen (Regel 1)? |
| Machbarkeit in ca. 24 Std. | ×3 | Gibt es einen MVP, der bis Samstagmittag läuft? |
| Team-Skills | ×2 | Haben wir die Fähigkeiten dafür im Team? |
| Kriterien des Partners | ×2 | Wissen wir, woran wir gemessen werden, und können wir das liefern? |
| Demo-Wirkung und Daten | ×2 | Haben wir Daten für Test und Demo, und wirkt es? |
| Souveräne Deployability | ×1 | Läuft es leicht on-premise oder air-gapped? |

### Schritt 3: Nach der Zuteilung (Scoping-Session)

1. **Nutzer und Problem** in einem Satz.
2. **Ein Kernfall** (Happy Path), den wir perfekt machen.
3. **Erfolgsmetrik** und Evaluationsset (wie wissen wir, dass es funktioniert?).
4. **Streichliste:** Was bauen wir bewusst nicht?
5. **Deployment-Story** in 3 Sätzen (air-gapped, on-prem, Swiss Cloud).
6. **Aufgaben** als Issues, Verantwortliche, Zeit.
7. Entscheid und Annahmen in `docs/DECISIONS.md` festhalten.

### Erste Annahmen zu den drei Challenges (vor der Enthüllung, **nur Hypothesen**)

Diese Notizen beruhen nur auf den Kurzbeschrieben. Sie werden nach den Pitches und dem Workshop überprüft.

| Challenge | Erste Idee | Auf was achten |
|---|---|---|
| **ti&m «Trust Room»**: eigener KI-Raum des Kunden im E-Banking | Assistent über die eigenen Daten des Kunden, mit Fokus auf Datenschutz, Nachvollziehbarkeit und Schweizer Betrieb | Keine echten Bankdaten, also synthetische Daten. Vertrauen, Erklärbarkeit und UX sind entscheidend. |
| **Suva**: relevante Bundesgerichtsurteile finden und juristisch zusammenfassen | Retrieval über Urteile plus strukturierte Zusammenfassung mit Quellenbelegen, mehrsprachig (DE/FR/IT) | Halluzinationen, Quellenangaben, lange Texte, Qualität der Evaluation (Faktentreue, korrekte Zitate) |
| **Digitec Galaxus**: Produktbilder klassifizieren | Klassifikation durch **Apertus v1.5 (multimodal)** mit definierter Taxonomie | Regel 1: Apertus muss klassifizieren, kein separates Vision-Modell als Kern. **Vorab testen**, ob Bild-Input über das Gateway möglich ist. Kosten und Durchsatz bei grossen Mengen. |

---

## 9. Kontakt

- Discord: https://discord.gg/hack-apertus
- E-Mail: hello@hackapertus.ch
- Plattform: https://app.sprintd.org/events/hack-apertus-zurich