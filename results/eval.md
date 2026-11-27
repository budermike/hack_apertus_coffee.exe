# Evaluation Triage: Recall, Precision, F2 (2026-01-01 bis 2026-03-31)

Erzeugt mit `python -m hack_apertus_coffee_exe.eval.eval` (Issue #21). Nicht von Hand ändern.

## Ergebnis

- **Schwelle (bestes F2): Score ≥ 91.53**
- **Recall: 74.2 %** (23 von 31 Suva-Urteilen gefunden)
- Precision: 46.0 % (23 von 50 ausgewählten Urteilen)
- **F2: 0.661**
- Recall@30: 48.4 %, Recall@100: 83.9 % (Anteil der Suva-Urteile unter den ersten n der Rangliste)

## Aufbau

- Kandidaten: alle 2004 Urteile mit Entscheiddatum 2026-01-01 bis 2026-03-31.
- Ground Truth: 31 davon stehen auf der Suva-Liste (`is_reference` im Vector-DB), das sind 1.5 %.
- Referenzbasis: nur die 28 Suva-Urteile mit Entscheiddatum 2025-10-01 bis 2025-12-31.
- Score: Vector-DB Stufe 1, hybrid (Cosine + Fachgebiets-Bonus), Referenzen 2025-Q4, ohne Apertus. Embedder: `sentence-transformers:intfloat/multilingual-e5-small`.
- Ein Urteil gilt als ausgewählt, wenn Score ≥ Schwelle. Der Sweep prüft jeden vorkommenden Score als Schwelle.

## Arbeitspunkte

| Arbeitspunkt | Schwelle | Ausgewählt | Gefunden | Recall | Precision | F2 |
|---|---|---|---|---|---|---|
| Bestes F2 | 91.53 | 50 | 23 | 74.2 % | 46.0 % | 0.661 |
| Recall ≥ 90 % | 84.84 | 114 | 28 | 90.3 % | 24.6 % | 0.588 |
| Recall = 100 % | 76.82 | 1847 | 31 | 100.0 % | 1.7 % | 0.079 |

## Vergleich der Scores

Jede Zeile mit ihrer eigenen besten F2-Schwelle. Die Schwellen sind zwischen den Zeilen nicht vergleichbar.

| Score | Schwelle | Ausgewählt | Recall | Precision | F2 | Recall@30 | Recall@100 |
|---|---|---|---|---|---|---|---|
| Vector-DB Stufe 1, hybrid (Cosine + Fachgebiets-Bonus), Referenzen 2025-Q4, ohne Apertus | 91.53 | 50 | 74.2 % | 46.0 % | 0.661 | 48.4 % | 83.9 % |
| Vector-DB Stufe 1, nur Cosine, Referenzen 2025-Q4 | 95.31 | 138 | 93.5 % | 21.0 % | 0.553 | 29.0 % | 71.0 % |
| Vector-DB Stufe 1, hybrid, alle Referenzen vor 2026-01-01 | 91.75 | 51 | 74.2 % | 45.1 % | 0.657 | 51.6 % | 90.3 % |
| Triage-Pipeline ohne Apertus (TF-IDF gegen Suva-Summaries + Keywords), Referenzen vor 2026-01-01 | 62.75 | 108 | 64.5 % | 18.5 % | 0.431 | 29.0 % | 61.3 % |

## Verpasste Suva-Urteile bei Schwelle 91.53 (8)

| Rang | Geschäftsnummer | Entscheiddatum | Sprache | Score | Sachgebiet |
|---|---|---|---|---|---|
| 79 | 8C_377/2025 | 2026-02-20 | de | 85.73 | Invalidenversicherung (Invalidenrente; Arbeitsunfähigkeit) |
| 81 | 8C_149/2025 | 2026-03-26 | de | 85.64 | Invalidenversicherung (Invalidenrente) |
| 85 | 8C_272/2025 | 2026-02-11 | de | 85.53 | Invalidenversicherung (Invalidenrente) |
| 104 | 8C_450/2025 | 2026-01-07 | de | 85.19 | Invalidenversicherung (Invalidenrente; berufliche Massnahmen) |
| 114 | 8C_314/2025 | 2026-02-09 | fr | 84.84 | Assurance-invalidité (allocation pour impotent) |
| 227 | 9C_311/2025 | 2026-03-18 | de | 81.10 | Invalidenversicherung |
| 230 | 9C_145/2025 | 2026-03-16 | de | 80.99 | Invalidenversicherung |
| 1847 | 9C_65/2025 | 2026-01-29 | it | 76.82 | Assicurazione per l'invalidità (diniego di giustizia e ritardata giustizia; spese giudiziarie in procedura cantonale) |

## Was diese Zahlen nicht zeigen

- Die Schwelle ist auf denselben Urteilen gewählt, auf denen sie gemessen wird. Auf einem neuen Quartal sind die Werte eher tiefer.
- Bewertet wird nur die Rangliste des genannten Scores. Ein Score ohne Apertus-Aufruf sagt nichts über die Apertus-Bewertung aus; diese lässt sich mit `--scores NAME=triage.json` messen.
- Einträge der Suva-Liste ohne Bundesgerichts-Geschäftsnummer (z. B. kantonale Entscheide) sind nicht in der Ground Truth, weil sie nicht von bger.ch stammen.
