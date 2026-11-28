# [triage] Relevanz-Bewertung in zwei Apertus-Durchgängen mit Kalibrierung: höchster Recall für die Suva-Liste

**Issue:** #62 · **Owner:** Abi · **Hängt zusammen mit:** #12, #13, #17, #21, #22, #33, #50, #53

Dieses Dokument ist der Issue-Text und zugleich die Begründung: was gebaut wurde, warum, und der ganze Weg dorthin mit allen Messwerten. Alle Zahlen stammen aus Läufen vom 27.11.2026.

## Was

Die Suva-Liste eines Quartals entsteht nicht mehr aus einem einzelnen Apertus-Score, der mit der Ähnlichkeit gemischt wird, sondern aus zwei Apertus-Durchgängen und einer Kalibrierung. Im Rückblick auf die beiden gelabelten Quartale findet die Liste damit alle 59 Suva-Urteile statt 44.

## Kurzfassung in Zahlen

Gemessen auf Q4/2025 und Q1/2026, jeweils auf dem einen Quartal eingestellt und auf dem anderen geprüft (Hold-out). Zusammen 59 Urteile, die die Suva gewählt hat.

| Variante | Auf der Liste | Gefunden | Recall | F2 | Suva-Urteile unter den ersten 30 je Quartal (von 60) |
|---|---|---|---|---|---|
| Stufe 1 ohne Apertus (Vector-DB, hybrid) | 87 | 42 von 59 | 71,2 % | 0,650 | 33 |
| Triage wie gebaut, `apertus-v1-5-8b`, Endwert 75/25 | 88 | 44 von 59 | 74,6 % | 0,679 | 33 |
| Triage wie gebaut, `apertus-v1-5-70b` | 71 | 42 von 59 | 71,2 % | 0,684 | 34 |
| Nur der Prompt um IV-Kriterien ergänzt, 8B | 134 | 52 von 59 | 88,1 % | 0,703 | 21 |
| Dieses Issue, Schwelle mit bestem F2 (Faktor 1,0) | 97 | 49 von 59 | 83,1 % | 0,736 | 37 |
| Dieses Issue, Faktor 0,5 | 174 | 56 von 59 | 94,9 % | 0,683 | 37 |
| **Dieses Issue, eingestellt (Faktor 0,3)** | 226 | **59 von 59** | **100 %** | 0,638 | 37 |

Die Zeilen «wie gebaut» sind zu ihren Gunsten gemessen: Sie bekamen nur die Urteile zum Sozialversicherungsrecht als Kandidaten (507 statt rund 4000 in zwei Quartalen).

## Kontext: Wie wir hierher gekommen sind

Das Team hat zwei Codebasen für dieselbe Challenge: dieses Repo (Coffee, das Hauptprojekt) und das Schwesterprojekt **Urteilsradar**, eine eigenständige Pipeline mit eigener Oberfläche. Am Abend des 27.11. stand die Frage: Welches hat den besseren Recall, lohnt sich eine Mischung, und wie kommt der höchste Recall ins Hauptprojekt? Die Schritte in der Reihenfolge, in der sie gemacht wurden:

### 1. Erster Vergleich der gemeldeten Zahlen

| | Urteilsradar | Coffee (`results/eval.md`) |
|---|---|---|
| Recall auf Q1/2026 | 90,3 % (28 von 31) | 74,2 % (23 von 31) |
| Auf der Liste | 65 | 50 |
| F2 | 0,741 | 0,661 |
| Verfahren | Apertus liest und bewertet jedes Urteil | Ähnlichkeitssuche, ohne Apertus |
| Schwelle gewählt auf | dem Vorquartal | demselben Quartal, auf dem gemessen wird |

Die Zahlen waren nicht vergleichbar: verschiedene Kandidatenmengen, verschiedene Art, die Schwelle zu wählen, und bei Coffee war die Apertus-Stufe noch nie gemessen.

### 2. Gleiches Messverfahren für beide

Coffee mit der Schwelle aus dem Vorquartal statt aus dem gemessenen Quartal, auf Q1/2026:

| Stufe 1 (Vector-DB, hybrid) | Recall | Auf der Liste | F2 |
|---|---|---|---|
| Schwelle auf Q1/2026 selbst gewählt (wie `eval.md`) | 74,2 % | 50 | 0,661 |
| Hold-out, Referenzen nur Q4/2025 | 61,3 % | 45 | 0,562 |
| Hold-out, alle Referenzen vor 2026 | 74,2 % | 52 | 0,653 |

### 3. Die Apertus-Stufe von Coffee zum ersten Mal gemessen

`TriagePipeline` mit dem Prompt aus `triage/llm.py`, getrennt vom Repo ausgeführt, auf denselben Urteilen (Q1/2026, Hold-out):

| Modell | Recall | Auf der Liste | F2 |
|---|---|---|---|
| `apertus-v1-5-8b` (Standard), Endwert 75/25 | 74,2 % (23 von 31) | 55 | 0,642 |
| `apertus-v1-5-8b`, Apertus-Score allein | 77,4 % (24 von 31) | 57 | 0,663 |
| `apertus-v1-5-70b` | 74,2 % (23 von 31) | 44 | 0,685 |

Apertus half also kaum. Der Grund stand in den Scores: Die 8 Urteile zur Invalidenversicherung, die die Suva in Q1/2026 gewählt hat, bekamen von 8B die Scores 0, 0, 0, 0, 0, 5, 5 und 85, von 70B 10 bis 20. Die 23 gewählten Urteile zur Unfallversicherung bekamen fast alle 85. Alle verpassten Urteile stammten aus der Invalidenversicherung.

### 4. Reicht es, den Prompt zu ergänzen?

Ein Absatz im Prompt («relevant ist auch ein IV-Urteil mit übertragbarem Grundsatz»), sonst alles gleich:

| | Recall | Auf der Liste | F2 | Unter den ersten 30 (von 60) |
|---|---|---|---|---|
| 8B, beide Quartale | 88,1 % (52 von 59) | 134 | 0,703 | 21 |
| 70B, beide Quartale | 74,6 % (44 von 59) | 99 | 0,657 | 30 |

Mit 8B steigt der Recall, aber die Rangfolge bricht zusammen: Jetzt bekommen auch die IV-Urteile 85, und unter den ersten 30 stehen nur noch 21 statt 33 Suva-Urteile. Mit 70B bringt der Absatz fast nichts. Ein einzelner Score reicht nicht.

### 5. Lohnt sich eine Mischung beider Projekte?

Geprüft wurden 40 Varianten: jedes Projekt allein, Vereinigung der Listen, Rangmittel, Regression über beide Bewertungen, je mit 8B und 70B. Gemessen mit der Bewertung von Urteilsradar, die dieses Issue hier neu baut:

| Variante | Auf der Liste | Gefunden | Recall | F2 |
|---|---|---|---|---|
| Urteilsradar allein | 100 | 50 von 59 | 84,7 % | 0,744 |
| Urteilsradar-Liste plus Grenzfälle, die Coffee hoch bewertet | 128 | 53 | 89,8 % | 0,728 |
| Kontrolle: Urteilsradar allein, gleich lange Liste | 128 | 53 | 89,8 % | 0,728 |
| Vereinigung beider Listen | 139 | 53 | 89,8 % | 0,707 |
| Kontrolle: Urteilsradar allein, gleich lange Liste | 139 | 55 | 93,2 % | 0,733 |
| Regression über beide Bewertungen (bestes F2 der Mischungen) | 111 | 52 | 88,1 % | 0,749 |
| Kontrolle: Urteilsradar allein, gleich lange Liste | 111 | 52 | 88,1 % | 0,749 |

Jede Mischung ist ein Effekt der Listenlänge. Keine ist statistisch besser als die Bewertung allein. Der Hebel für mehr Recall ist die Schwelle, nicht die Mischung.

### 6. Entscheid

Die Bewertung wird im Hauptprojekt gebaut, auf dessen Crawl-Daten und Schnittstellen, statt die Projekte zu mischen oder Ergebnisse zu kopieren. Vorgabe von Abi: der höchste Recall.

### 7. Nachbau geprüft

| | Urteilsradar | Dieses Issue |
|---|---|---|
| Q1/2026, kalibriert auf Q4/2025 | 28 von 31, Liste 65, F2 0,741 | 27 von 31, Liste 62, F2 0,726 |
| Q4/2025, kalibriert auf Q1/2026 | 22 von 28, Liste 35, F2 0,748 | 22 von 28, Liste 35, F2 0,748 |
| Zusammen | 50 von 59, F2 0,744 | 49 von 59, F2 0,736 |

Die Triage-Scores korrelieren mit 0,97 bis 0,99, die Entscheide stimmen in Q4/2025 zu 100 % und in Q1/2026 zu 99 % überein. Der Unterschied von einem Urteil ist nicht gesichert (Recall −1,7 Punkte, Intervall −5,7 bis 0).

Gegen «wie gebaut, 8B» ist der Gewinn bei gleicher Art von Schwelle (bestes F2) gesichert: Recall +8,5 Punkte (95-%-Intervall +1,8 bis +15,9), F2 +0,059 (+0,004 bis +0,121).

## Ursachen, jede mit Beleg

1. **Der Prompt kennt nur die Unfallversicherung.** Siehe Schritt 3: Die IV-Urteile der Suva bekommen 0 bis 20. Das grössere Modell ändert daran nichts.
2. **Ein einzelner Score kann nicht reihen.** Siehe Schritt 4: fast überall 85.
3. **Die Mischung mit der Ähnlichkeit schadet.** Mit 8B im Hold-out auf Q1/2026: Endwert 75/25 ergibt F2 0,642, der Apertus-Score allein 0,663. Der Fachgebiets-Bonus der Stufe 1 belohnt nur Unfall-Stichwörter.
4. **Die Schwelle wurde auf dem gemessenen Quartal gewählt.** Siehe Schritt 2: 74,2 % werden im Hold-out zu 61,3 %.
5. **Ein kaputtes JSON bricht den Lauf ab.** `9C_449/2025` liefert mit 8B und Temperatur 0 reproduzierbar ungültiges JSON; `_score_one` fängt das nicht ab.
6. **Zu viele Kandidaten, zu wenig Text.** Ein Quartal hat rund 2000 Urteile, aber alle 59 Suva-Urteile stammen aus der III. und IV. öffentlich-rechtlichen Abteilung (394 Urteile in Q1/2026). Der Prompt sieht höchstens 24 000 Zeichen und schneidet die Mitte heraus, wo die Erwägungen stehen. Diesen Punkt haben wir nicht einzeln gemessen.

## Was sich ändert

| Schritt | Vorher | Neu | Beleg |
|---|---|---|---|
| Kandidaten | alle rund 2000 Urteile des Quartals | Geschäftsnummern 8C, 9C, 8F, 9F; von Apertus bewertet werden Unfall-, Invaliden- und Militärversicherung | alle 59 Suva-Urteile liegen darin; 165 statt 2004 Apertus-Urteile im Quartal |
| Text | Auszug, 24 000 Zeichen | ganzes Urteil in zitierbaren Absätzen (`[E4.2]`) | Zitate lassen sich prüfen und in der Oberfläche zeigen |
| Durchgang 1 | Score, Begründung | Kriterien K1–K5 und N1–N3 mit wörtlichem Zitat, Begründung, Score zuletzt; übertragbare IV-Grundsätze zählen ausdrücklich (K3) | die IV-Urteile der Suva bekommen 65 bis 85 statt 0 bis 20 |
| Durchgang 2 «Vertiefung» | fehlt | ab Score 50: Grundsatzgehalt 0–3, Nutzen für die Unfallversicherung 0–3, Merksatz, zweiter Score | trennt die wenigen übertragbaren IV-Urteile von den vielen Einzelfällen; in Urteilsradar mit denselben Prompts auf Q1/2026: 23 von 31 ohne, 28 von 31 mit Vertiefung |
| Relevanzwert | 75 % Apertus + 25 % Cosine | Kalibrierung mit vier Parametern auf den beiden Scores und dem Sachgebiet | F2 0,736 statt 0,679 |
| Schwelle | Platzhalter 60/40, gewählt auf dem gemessenen Quartal | aus F2 hergeleitet (`p > F2/5`), nur aus früheren gelabelten Quartalen; `list_factor` senkt sie bewusst | Tabelle unten |
| Fehler | Lauf bricht ab | strenges JSON-Schema, ein zweiter Versuch, Antwort je Urteil im Cache; ein gescheitertes Urteil erscheint gelb mit Fehlertext | 0 Fehler bei 487 Urteilen in drei Quartalen |
| Modell | `apertus-v1-5-8b` | `apertus-v1-5-70b` | gleiches Modell und gleiche Prompts wie im geprüften Schwesterprojekt |

Die Ähnlichkeitssuche bleibt für «ähnliche Fälle» und die Wissensbasis (#30). In die Liste fliesst sie nicht mehr ein. Der bisherige Befehl `triage.cli score` bleibt unverändert bestehen.

Apertus erledigt weiterhin die Kernaufgabe: Innerhalb eines Sachgebiets ist der Relevanzwert streng monoton in dem, was Apertus gesagt hat. Es kommt kein weiteres Modell dazu und keine neue Abhängigkeit.

## Schwelle: Recall gegen Listenlänge

Die Kalibrierung liefert die Schwelle mit dem besten erwarteten F2. `list_factor` in `configs/triage.json` multipliziert sie. Alle Hold-out-Messungen zusammen (59 Suva-Urteile); in Klammern das 95-%-Intervall, Δ ist die gepaarte Differenz zu Faktor 1,0:

| Faktor | Auf der Liste | Gefunden | Recall | F2 | Δ Recall | Δ F2 |
|---|---|---|---|---|---|---|
| 1,0 | 97 | 49 | 83,1 % (72,5 bis 92,1) | 0,736 | | |
| 0,7 | 135 | 52 | 88,1 % (78,6 bis 95,8) | 0,701 | 0 bis +11,3 Punkte | −0,080 bis +0,019 |
| 0,6 | 149 | 53 | 89,8 % (81,4 bis 96,8) | 0,688 | +1,6 bis +14,0 | −0,098 bis +0,012 |
| 0,5 | 174 | 56 | 94,9 % (88,4 bis 100) | 0,683 | +4,4 bis +20,8 | −0,120 bis +0,022 |
| 0,4 | 206 | 58 | 98,3 % (94,3 bis 100) | 0,656 | +6,8 bis +25,4 | −0,157 bis +0,006 |
| **0,3 (eingestellt)** | 226 | 59 | **100 %** | 0,638 | **+7,9 bis +27,5** | −0,178 bis −0,009 |

**Eingestellt ist 0,3, weil die Vorgabe der höchste Recall ist.** Der Preis ist ehrlich benannt: Die Liste wird mehr als doppelt so lang wie beim besten F2, und der Verlust an F2 (0,736 auf 0,638) ist statistisch gesichert. Die Suva vergleicht die Teams an Recall und F2. Wer das beste F2 will, setzt 1,0; der Punkt, an dem der Recall-Gewinn gesichert ist und der F2-Verlust noch nicht, ist 0,5. Es ist eine Zeile in `configs/triage.json`.

Die 100 % gelten für die beiden Quartale, auf denen gemessen wurde. Für Q2/2026 ist das eine Erwartung, keine Garantie.

Auf dem Rechner, auf dem der Lauf lief, kostet ein neuer Lauf mit anderem Faktor keinen Apertus-Aufruf, weil alle Antworten im Cache liegen (`data/processed/triage/relevance/`, nicht im Repo). Auf einem frischen Klon sind es rund 830 Aufrufe für die drei Quartale, etwa 25 Minuten.

## Ergebnis für Q2/2026

> **Seit #75 (28.11.2026):** Die Liste entsteht nicht mehr mit `list_factor` 0,3, sondern mit dem Recall-Ziel `target_recall` 0,9 in `configs/triage.json`: Die Schwelle steht dort, wo 90 % der bekannten Suva-Urteile aus Q4/2025 und Q1/2026 darüber liegen (0,076); gelb beginnt, wo alle darüber liegen (0,053). Für Q2/2026: **100 Urteile auf der Liste** (39 Unfall-, 60 Invaliden-, 1 Militärversicherung), 25 davon im Juni, 11 gelb. Im Hold-out: 53 von 59 gefunden (89,8 %), Liste 152 statt 226 in zwei Quartalen (`results/relevance_eval.md`). Die Zahlen unten beschreiben den Stand mit Faktor 0,3.

Lauf vom 27.11.2026 mit `apertus-v1-5-70b`, kalibriert auf Q4/2025 und Q1/2026 (300 bewertete Urteile, 59 von der Suva gewählt, F2-Schwelle 0,148).

- 410 Urteile der beiden Abteilungen, 187 davon von Apertus bewertet (313 Aufrufe, rund sieben Minuten), kein Urteil gescheitert.
- **Auf der Liste: 116 Urteile** (39 Unfallversicherung, 76 Invalidenversicherung, 1 Militärversicherung), 29 davon mit Entscheiddatum im Juni 2026. Weitere 30 sind gelb.

| Faktor | Schwelle | Urteile auf der Liste Q2/2026 |
|---|---|---|
| 1,0 | 0,148 | 66 |
| 0,7 | 0,104 | 85 |
| 0,5 | 0,074 | 100 |
| 0,4 | 0,059 | 107 |
| **0,3 (eingestellt)** | 0,045 | **116** |

Gegenprobe mit Urteilsradar, das dieselben Urteile unabhängig bewertet hat: Bei Faktor 1,0 stehen 64 Urteile auf beiden Listen (66 hier, 67 dort); die Triage-Scores korrelieren mit 0,97. Die eingestellte Liste enthält alle 67 Urteile von Urteilsradar.

Dateien: `results/recall_q2_2026.txt` (Suva-Format), `results/ranking_q2_2026.csv` (Rang, Score, Ampel, Begründung), `results/triage_q2_2026.json` (Triage-Ausgabe für Oberfläche und Zusammenfassungen).

## Was gebaut ist

| Datei | Inhalt |
|---|---|
| `src/hack_apertus_coffee_exe/triage/relevance.py` | Kandidaten, Sachgebiet, Zerlegung in Absätze, ähnliche frühere Urteile mit Suva-Entscheid, beide Prompts, Apertus-Aufruf mit Schema, Wiederholung und Cache |
| `src/hack_apertus_coffee_exe/triage/calibration.py` | Relevanzwert aus beiden Scores und Sachgebiet, Schwelle aus F2, Ampel, Abbildung auf die Skala 0–100 |
| `src/hack_apertus_coffee_exe/triage/relevance_period.py` | ein Quartal bewerten, auf früheren gelabelten Quartalen kalibrieren, als `TriageResult` ausgeben |
| `src/hack_apertus_coffee_exe/triage/cli.py` | neuer Befehl `relevance` |
| `src/hack_apertus_coffee_exe/eval/relevance_eval.py` | Hold-out-Auswertung in beide Richtungen, Tabelle der Schwellen mit Intervallen |
| `tests/test_relevance.py` | 18 Tests ohne Netz, Apertus ist gemockt |
| `configs/triage.json`, `triage/config.py` | `label_periods`, `list_factor` |
| `results/relevance_eval.md`, `results/tables/relevance_*` | Messwerte, Scores je Urteil, Kalibrierung |
| `results/ranking_q2_2026.csv`, `results/recall_q2_2026.txt`, `results/triage_q2_2026.json` | Rangliste, Suva-Liste und Triage-Ausgabe für Q2/2026 |

Angepasst, damit der Rest des Projekts dazu passt:

| Datei | Änderung |
|---|---|
| `src/hack_apertus_coffee_exe/triage/__init__.py` | exportiert `ApertusRelevanceClient` und `run_relevance` |
| `src/hack_apertus_coffee_exe/triage/README.md` | beschreibt die neue Bewertung und die Befehle |
| `src/hack_apertus_coffee_exe/summary/run.py` | Beispiel für den Batch aus `results/triage_q2_2026.json` |
| `src/presentation/script.js` | Schwelle «relevant» 60 statt 70, gleich wie `green_threshold` in `configs/triage.json` |
| `src/presentation/api.js` | Abbildung von `metadata.relevance` auf die Felder der Oberfläche dokumentiert |
| `docs/DECISIONS.md`, `docs/MODELS.md` | Entscheide und Modellregister |

### Ausgabe

Die Ausgabe ist die gewohnte Liste von `TriageResult`, damit Rangliste, Suva-Liste, Zusammenfassungs-Batch und Oberfläche ohne Umbau weiterlaufen:

- `ampel`: grün (auf der Liste), gelb (knapp darunter oder Bewertung gescheitert), rot.
- `final_score`: der Relevanzwert auf der Skala 0–100 von `configs/triage.json`. Die Listenschwelle liegt auf `green_threshold` (60), die halbe Schwelle auf `yellow_threshold` (40). Die Abbildung ist monoton, die Rangfolge bleibt also die von Apertus und Kalibrierung. Der rohe Wert steht in `metadata["relevance"]["p"]`.
- `relevance_reason`: Begründung von Apertus und Merksatz.
- `metadata["relevance"]`: Sachgebiet, Ausgang, Rechtsfrage, Kriterien mit Fundstelle, Zitat und Prüfstatus (`exakt`, `ungefähr`, `fehlt`), Grundsatzgehalt, Nutzen für die Unfallversicherung, Merksatz. Das sind die Felder, die `src/presentation/api.js` für die Detailansicht vorsieht.
- Die Suva-Liste (`recall_<zeitraum>.txt`) enthält alle grünen Urteile, nicht nur die ersten 30.

## Aufgaben

- [x] Kandidaten, Zerlegung, beide Durchgänge, Kalibrierung, Schwelle
- [x] Befehl `triage.cli relevance`, Ausgabe als `TriageResult`, Exporte
- [x] Hold-out-Auswertung mit Intervallen, `results/relevance_eval.md`
- [x] Tests ohne Netz; alle bisherigen Tests bestehen weiter
- [x] Lauf für Q4/2025, Q1/2026 und Q2/2026 mit `apertus-v1-5-70b`
- [x] `list_factor` für den höchsten Recall gesetzt (0,3)
- [x] Übrige Dateien angepasst (siehe Tabelle), `docs/MODELS.md`, `docs/DECISIONS.md`
- [ ] `score_period` (#50) ruft diese Bewertung auf und schreibt in die Tabelle `triage`; `GET /api/judgements` (#53) liefert sie an die Oberfläche. Bis dahin liegt alles in `results/triage_q2_2026.json`.
- [ ] Zusammenfassungen (#17) für die grünen Urteile: `summary.run batch --triage results/triage_q2_2026.json`. Mit Faktor 0,3 sind es 116 statt rund 65 Urteile.
- [ ] Ampel-Rückmeldungen (#28, #40) als weitere gelabelte Urteile in die Kalibrierung aufnehmen

## Fertig wenn

- [x] Recall im Hold-out auf Q1/2026 mindestens 85 % bei der F2-Schwelle (erreicht: 27 von 31, 87,1 %) und über beide Quartale besser als «wie gebaut» mit gesichertem Abstand
- [x] Mit der eingestellten Schwelle sind im Rückblick alle 59 Suva-Urteile auf der Liste
- [x] `python -m hack_apertus_coffee_exe.eval.relevance_eval` läuft ohne Gateway und ohne Cache
- [x] Für Q2/2026 liegt die Suva-Liste im Format `8C_691/2024, 9C_612/2025, …` vor
- [ ] Die Oberfläche zeigt Score, Ampel und Begründung aus dieser Bewertung (#53)

## Ausführen und prüfen

```bash
# Liste für ein Quartal (Apertus 70B; ein zweiter Lauf kommt aus dem Cache)
python -m hack_apertus_coffee_exe.triage.cli relevance --period 2026-Q2 --output results/triage_q2_2026.json

# Hold-out auf den gelabelten Quartalen (ohne Gateway)
python -m hack_apertus_coffee_exe.eval.relevance_eval

# Tests
python -m pytest tests/test_relevance.py
```

## Wie gemessen wurde

- **Daten:** Urteile mit Entscheiddatum in Q4/2025 und Q1/2026 aus `data/raw/`, Auswahl der Suva aus `judgements/` (28 und 31 Urteile).
- **Hold-out:** Kalibrierung und Schwelle stammen immer aus dem anderen Quartal. Die Beispiele im Prompt («ähnliche frühere Urteile») stammen nur aus früheren Quartalen.
- **Intervalle:** Bootstrap über Urteile, 2000 Ziehungen (Vergleiche zwischen Varianten: 4000), je Quartal gezogen. Differenzen sind gepaart, also auf denselben Ziehungen gerechnet.
- **Vergleich «wie gebaut»:** `TriagePipeline` mit dem Prompt aus `triage/llm.py` und TF-IDF, Schwelle mit bestem F2 auf dem anderen Quartal, Kandidaten wie bei Urteilsradar.
- **Aufwand:** 516 Apertus-Aufrufe für die beiden gelabelten Quartale und 313 für Q2/2026, je rund sieben bis acht Minuten mit zwölf parallelen Aufrufen.

## Grenzen

- Zwei Quartale, 59 Suva-Urteile. Ein bis zwei Urteile Unterschied sind Zufall.
- Die Kriterien im Prompt wurden mit Blick auf beide gelabelten Quartale formuliert. Die Werte sind eher zu günstig; Q2/2026 kann anders ausfallen.
- Die Variante «nur Prompt ergänzt» wurde in Kenntnis der verpassten Urteile formuliert, ist also ebenfalls eher zu günstig.
- Mit Faktor 0,3 wählt die Suva im Rückblick rund ein Viertel der Liste tatsächlich aus (59 von 226). Die übrigen Urteile brauchen eine Begründung, die trägt; sie steht in `relevance_reason`.
- Unter den ersten 30 je Quartal stehen 37 von 60 Suva-Urteilen. Das verbessert dieses Issue gegenüber «wie gebaut» (33), aber nicht darüber hinaus; die Schwelle ändert daran nichts.
- Die Oberfläche läuft noch mit Beispieldaten (`USE_MOCK`). Angeschlossen ist die neue Bewertung erst über die Dateien in `results/`.
- Die Score-Dateien in `results/tables/` enthalten keine Auswahl der Suva. Die Auswertung liest sie aus `judgements/`.
