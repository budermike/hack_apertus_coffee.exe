# Contributing – Team Coffee.exe @ Hack Apertus Zürich

So arbeiten wir zusammen. Einmal lesen (ca. 10 Minuten), dann gilt es für alle, auch für den PM.

> **Rahmen:** Start Fr 27.11.2026, 9:00 · Abgabe **Sa 28.11.2026, 14:30** (keine Verlängerung)
> Den Ablauf, die Tools und die Abgabe findet ihr im [Leitfaden](docs/LEITFADEN.md).

---

## 1. Grundprinzipien

1. **Ein lauffähiges Ganzes schlägt viele halbe Features.** Zuerst ein dünner End-to-End-Durchstich (Input → Apertus → Output), dann ausbauen.
2. **`main` läuft immer.** Wenn `main` kaputt ist, hat das Beheben Priorität vor allem anderen.
3. **Klein und oft.** Kleine Branches, kleine Pull Requests, mehrmals pro Stunde mergen.
4. **Hängen bleiben ist normal, schweigen nicht.** Mehr als 20 Minuten blockiert → im Team-Chat melden (Label `blocker`).
5. **Was die Jury sehen soll, steht im Repo oder in der Abgabe.** Nichts, was nur auf einem Laptop läuft.
6. **Wir schlafen und essen.** Müde Teams machen kurz vor der Abgabe die dümmsten Fehler.

---

## 2. Hackathon-Regeln, die jede Zeile Code betreffen

Wer die Regeln 1, 3, 5 oder 6 verletzt, ist **nicht preisberechtigt**. Die Regeln gelten für alle von uns.

| Regel (Organisatoren) | Was das für uns konkret heisst |
|---|---|
| **1. Apertus im Kern** | Apertus v1.5 (8B oder 70B, auch feingetunt) erledigt die Kernaufgabe der Challenge (z.B. die Klassifikation oder die Zusammenfassung selbst). Kein anderes Modell darf diese Kernaufgabe übernehmen. |
| **2. Entwicklung frei** | Beim Bauen dürfen wir alle Tools und Modelle nutzen, auch geschlossene Modelle und Coding-Assistenten. |
| **3. Laufzeit nur offen** | Die fertige Lösung nutzt zur Laufzeit nur Apertus und für Hilfsaufgaben andere **Open-Weights-Modelle** (z.B. Embeddings, OCR, Speech-to-Text, Reranking). Open-Weights heisst: Gewichte öffentlich herunterladbar und selbst hostbar. **Keine proprietären KI-APIs zur Laufzeit.** |
| **4. Evaluation** | Als automatische Richter in der Evaluation dürfen Open-Weights-Modelle dienen, damit die Jury die Resultate reproduzieren kann. |
| **5. Deployability** | Die Lösung muss in einer der drei Zielarchitekturen lauffähig sein: on-premise, air-gapped oder souveräne Schweizer Cloud. Also keine Abhängigkeit von ausländischen Closed Services. |
| **6. Offenlegung** | Der Technical Report listet **jedes** verwendete Modell mit Version und Rolle, in der Entwicklung und zur Laufzeit. |

**Wichtig für die Praxis:**

- **Coding-Assistenten zählen bei Regel 6 mit.** Wenn jemand z.B. Claude, Copilot oder ein anderes Modell beim Programmieren verwendet, wird es im Modell-Register (Abschnitt 5) eingetragen.
- **Eigene Arbeit.** Bei der Abgabe bestätigen wir: «This is our own work, built during this event». Den Lösungscode schreiben wir deshalb während des Events. Vorab bereiten wir nur Prozess- und Dokumentvorlagen vor.
- **Das Repo ist öffentlich.** Die Jury muss es sehen können. Siehe Abschnitt 6 zu Secrets und Daten.

---

## 3. Rollen

Das Team hat 2–5 Personen. Rollen lassen sich kombinieren, jede Aufgabe braucht aber genau eine verantwortliche Person.

| Rolle | Verantwortung | Person |
|---|---|---|
| **Project Manager** | Scope und MVP, Zeitplan, Blocker lösen, Kontakt zum Challenge-Partner, Abgabe (Verantwortung und Checkliste), Entscheidungen protokollieren | Mike |
| **LLM / Backend** | Apertus-Anbindung, Prompting, RAG oder Fine-Tuning, Pipeline | Abi |
| **Frontend / Demo** | Oberfläche, Demo-Ablauf, Demo-Video | Linus |
| **Daten / Evaluation** | Testdaten, Evaluationsskript, Metriken, Ergebnistabelle | Ben |
| **Pitch / Doku** | Slides, Technical Report, README (alle liefern zu, eine Person bündelt) | ... |

Der PM schreibt auch Code oder Doku, wenn es das Team weiterbringt. Seine Hauptaufgabe ist aber, dass das Team am richtigen Problem arbeitet und rechtzeitig abgibt.

---

## 4. Workflow

### Board (GitHub Projects)

Spalten: **Doing → Review → Done**. Das Board ist die einzige Wahrheit, wer woran arbeitet.

- Neue Aufgabe = neues Issue, Schätzung: 30 Min / 1 Std / 2 Std. Alles, was grösser ist, wird zerlegt.
- Wer anfängt, weist sich das Issue zu und schiebt es nach *Doing*. Maximal 1–2 Issues gleichzeitig pro Person.
- Labels: `doing`, `review`, `done`, `blocker`, `bug`, `eval`, `docs`, `demo`.

### Branches

- Ein Issue = ein Branch: `feat/<issue-nr>-kurzname`, `fix/<issue-nr>-kurzname`, `docs/...`.
- Kein direkter Push auf `main`. Ausnahme: Tippfehler in Doku.

### Pull Requests

- Klein halten, möglichst innerhalb von 1–2 Stunden nach dem Branch öffnen und mergen.
- **Review: 5–10 Minuten, nicht perfekt.** Prüfen: läuft es, sind Secrets draussen, ist die Doku angepasst?
- Reagiert niemand innerhalb von 15 Minuten, weist der PM jemanden zu. Danach darf der Autor selbst mergen, wenn der Test-Befehl durchläuft.
- Merge-Art: **Squash**. Der Titel ist eine verständliche Zusammenfassung.

### Commits

Kurz, Präsens: `feat: ...`, `fix: ...`, `docs: ...`, `eval: ...`, `chore: ...`.

### Definition of Done

Ein Issue ist erledigt, wenn:

- [ ] es läuft (lokal und, wo relevant, im Demo-Setup),
- [ ] ein Reviewer es gesehen hat und es auf `main` ist,
- [ ] README aktualisiert ist, falls nötig,
- [ ] neue Modelle oder Tools im Modell-Register stehen.

---

## 5. Modell- und Tool-Register (für Regel 6)

Wir führen dieses Register laufend in `docs/MODELS.md` (Vorlage unten). Sobald jemand ein Modell oder einen KI-Assistenten nutzt, trägt er es **sofort** ein. Aus dem Register wird später der Abschnitt im Technical Report.

| Modell / Tool | Version | Rolle | Phase | Open-Weights? | Lizenz |
|---|---|---|---|---|---|
| Apertus v1.5 8B/70B | | Kernaufgabe: <...> | Laufzeit | ja | |
| <Embedding-Modell> | | Retrieval | Laufzeit | ja | |
| <Coding-Assistent> | | Code schreiben | Entwicklung | nein | |

Regel zur Selbstkontrolle: Steht in der Spalte «Phase» *Laufzeit* ein «nein» bei Open-Weights, ist das ein Regelverstoss. Dann sofort beim PM melden.

---

## 6. Secrets, Daten und Lizenzen

Das Repo ist **öffentlich**.

- **Keine Secrets im Repo.** API-Keys (z.B. CSCS/Keera, Hugging Face) kommen in `.env` (steht in `.gitignore`). Neue Variablen kommen in `.env.example`, ohne Werte.
- **Daten der Challenge-Partner:** Nur so verwenden, wie der Partner es erlaubt. Im Zweifel **nicht** ins öffentliche Repo. Dann kommen synthetische oder öffentlich verfügbare Beispieldaten ins Repo, und die Originaldaten bleiben lokal.
- **Personendaten:** Keine echten Personendaten im Repo, auch nicht in Beispielen.
- **Abhängigkeiten:** Nur Bibliotheken und Modelle verwenden, deren Lizenz Open-Source-Nutzung zulässt. Lizenz im Register vermerken.
- **Lizenz des Repos:** Wird nach den Vorgaben im Template-Repo und den Hackathon-Regeln entschieden (offen, z.B. Apache-2.0 oder MIT).

---

## 7. Qualität: was die Jury sehen will

Die Hauptjury bewertet unter anderem «Technical rigour» und «Implementation feasibility». Dafür brauchen wir minimal:

- **Ein Befehl startet die Demo** (Skript oder `make run`), dokumentiert im README.
- **Ein Evaluationsskript** mit einer kleinen, ehrlichen Ergebnistabelle (Datensatz, Metrik, Ergebnis, bekannte Schwächen). Die Tabelle wandert in den Technical Report.
- **Reproduzierbar:** Abhängigkeiten festgehalten (z.B. `requirements.txt` oder `pyproject.toml`), Modellversionen genannt.
- **Deployment-Skizze:** Wie läuft das on-premise, air-gapped oder in der Schweizer Cloud? Ein kurzer Abschnitt in `docs/ARCHITECTURE.md` reicht.
- **Kosten und Skalierung** grob abschätzen (Modellgrösse, Latenz, Hardware).

Perfekten Code erwartet niemand. Ehrlichkeit über Grenzen und eine funktionierende Demo schlagen Polish.

---

## 8. Kommunikation und Entscheidungen

- **Kanal:** [Discord](https://discord.com/channels/1573287621239635988/1573287621675851796).
- **Standups:** 10 Minuten, was ist fertig, was kommt als Nächstes, was blockiert. Zeiten stehen im Leitfaden.
- **Entscheidungen:** Wer die Entscheidung trifft, hört kurz alle an (max. 5 Minuten). Bei Uneinigkeit entscheidet der PM. Die Entscheidung kommt mit Begründung in `docs/DECISIONS.md`.
- **Mentoren und Challenge-Partner** sind vor Ort. Fragen lohnt sich. Der PM bündelt die Fragen des Teams, damit niemand doppelt fragt.
- **Discord** der Veranstalter: https://discord.gg/hack-apertus · E-Mail: hello@hackapertus.ch

---

## 9. Feature-Freeze und Abgabe

- **Feature-Freeze** kündigt der PM an (Vorschlag: Samstag 10:30). Danach nur noch Bugfixes, Evaluation, Report, Slides, Video.
- **Abgabe:** Der PM verantwortet sie, aber alle im Team können einreichen. Die Checkliste steht im Leitfaden. Es gilt das **Vier-Augen-Prinzip**: Eine zweite Person prüft vor dem Klick auf «Submit project» jeden Punkt.
- **Ein Entwurf ist keine Abgabe.** Nur «Submit project» zählt. Wir reichen frühzeitig ein (Ziel: 13:45) und aktualisieren bei Bedarf bis zum Ablauf.
