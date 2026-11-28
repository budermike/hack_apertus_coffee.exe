# Evaluation Relevanz-Bewertung: zwei Apertus-Durchgänge mit Kalibrierung

Erzeugt mit `python -m hack_apertus_coffee_exe.eval.relevance_eval`. Nicht von Hand ändern.

Modell: `apertus-v1-5-70b`. Kandidaten: Urteile der III. und IV. öffentlich-rechtlichen Abteilung (8C, 9C, 8F, 9F); von Apertus bewertet werden Unfall-, Invaliden- und Militärversicherung.

## Hold-out: Kalibrierung und Schwelle stammen nie aus dem gemessenen Quartal

| Kalibriert auf | Gemessen auf | Richtung | Schwelle | Auf der Liste | Gefunden | Recall | Precision | F2 | Suva-Urteile unter den ersten 30 |
|---|---|---|---|---|---|---|---|---|---|
| 2026-Q1 | 2025-Q4 | rückwärts | 0.142 | 35 | 22 von 28 | 78.6 % | 62.9 % | 0.748 | 20.9 |
| 2025-Q4 | 2026-Q1 | vorwärts | 0.150 | 62 | 27 von 31 | 87.1 % | 43.5 % | 0.726 | 16.1 |

## Schwelle senken: mehr Recall gegen längere Liste

Alle Hold-out-Messungen zusammen. Faktor 1.0 ist die F2-Schwelle der Kalibrierung; kleinere Faktoren senken sie. In Klammern das 95-%-Intervall (Bootstrap über Urteile, 2000 Ziehungen). Δ ist die gepaarte Differenz zu Faktor 1.0.

| Faktor | Auf der Liste | Gefunden | Recall | F2 | Δ Recall | Δ F2 |
|---|---|---|---|---|---|---|
| 1.0 | 97 | 49 von 59 | 83.0 % (72.5 % bis 92.1 %) | 0.736 (0.644 bis 0.814) | +0.0 bis +0.0 Punkte | +0.000 bis +0.000 |
| 0.9 | 104 | 50 von 59 | 84.8 % (74.5 % bis 93.5 %) | 0.735 (0.647 bis 0.812) | +0.0 bis +5.6 Punkte | -0.022 bis +0.031 |
| 0.8 | 106 | 50 von 59 | 84.8 % (74.5 % bis 93.5 %) | 0.731 (0.644 bis 0.808) | +0.0 bis +5.6 Punkte | -0.028 bis +0.027 |
| 0.7 | 135 | 52 von 59 | 88.1 % (78.6 % bis 95.8 %) | 0.701 (0.618 bis 0.773) | +0.0 bis +11.3 Punkte | -0.080 bis +0.019 |
| 0.6 | 149 | 53 von 59 | 89.8 % (81.4 % bis 96.8 %) | 0.688 (0.605 bis 0.756) | +1.6 bis +14.0 Punkte | -0.098 bis +0.012 |
| 0.5 | 174 | 56 von 59 | 94.9 % (88.4 % bis 100.0 %) | 0.683 (0.605 bis 0.748) | +4.4 bis +20.8 Punkte | -0.120 bis +0.022 |
| 0.4 | 206 | 58 von 59 | 98.3 % (94.3 % bis 100.0 %) | 0.656 (0.576 bis 0.721) | +6.8 bis +25.4 Punkte | -0.157 bis +0.006 |
| 0.3 **(eingestellt)** | 226 | 59 von 59 | 100.0 % (100.0 % bis 100.0 %) | 0.638 (0.561 bis 0.702) | +7.9 bis +27.5 Punkte | -0.178 bis -0.009 |

## Recall-Ziel (#75): Anteil der bekannten Suva-Urteile über der Schwelle

Die Schwelle wird auf dem einen Quartal so gesetzt, dass dort mindestens dieser Anteil der Suva-Urteile auf der Liste steht, und auf dem anderen Quartal gemessen. Ist ein Recall-Ziel eingestellt, ersetzt es den Faktor oben.

| Ziel | Auf der Liste | Gefunden | Recall | F2 | Je Quartal |
|---|---|---|---|---|---|
| 80 % | 97 | 49 von 59 | 83.0 % | 0.736 | 2025-Q4: 22 von 28 (35 auf der Liste); 2026-Q1: 27 von 31 (62 auf der Liste) |
| 85 % | 104 | 50 von 59 | 84.8 % | 0.735 | 2025-Q4: 23 von 28 (38 auf der Liste); 2026-Q1: 27 von 31 (66 auf der Liste) |
| 90 % | 152 | 53 von 59 | 89.8 % | 0.683 | 2025-Q4: 24 von 28 (52 auf der Liste); 2026-Q1: 29 von 31 (100 auf der Liste) |
| 95 % **(eingestellt)** | 193 | 58 von 59 | 98.3 % | 0.676 | 2025-Q4: 28 von 28 (83 auf der Liste); 2026-Q1: 30 von 31 (110 auf der Liste) |
| 100 % | 206 | 58 von 59 | 98.3 % | 0.656 | 2025-Q4: 28 von 28 (96 auf der Liste); 2026-Q1: 30 von 31 (110 auf der Liste) |

## Was diese Zahlen nicht zeigen

- Die Kriterien im Prompt wurden mit Blick auf beide gelabelten Quartale formuliert. Die Werte sind deshalb eher zu günstig.
- Zwei Quartale mit zusammen rund 60 Suva-Urteilen: Ein bis zwei Urteile Unterschied sind Zufall.
- Gemessen wird die Liste, nicht die Qualität der Begründung oder der Zusammenfassungen.
