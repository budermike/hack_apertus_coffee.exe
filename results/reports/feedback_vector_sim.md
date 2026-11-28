# Prototyp: Vektor-Verschiebung aus den Relevanz-Entscheiden

Erzeugt mit `python -m hack_apertus_coffee_exe.eval.feedback_vector_sim`. Nicht von Hand ändern. Kein Modellaufruf.

Gleiches Protokoll wie `results/reports/feedback_learning.md`: Suvas echte Entscheide werden als Klicks eingespielt (die ersten `k` der Liste oder `k` zufällige), die Basis-Relevanz kommt aus der Kalibrierung auf dem anderen Quartal, gemessen wird an den noch nicht entschiedenen Urteilen, mit Listenschutz wie im Betrieb (Listenschwelle: Recall-Ziel 95 %).

- **current**: Lernen wie im Betrieb (eine Korrektur je Rechtsgebiet und Kriterium).
- **+proto**: dazu ein Inhaltsmerkmal: Ähnlichkeit zum Mittel der «relevant»-Entscheide minus zum Mittel der «nicht relevant»-Entscheide (e5-Vektoren aus der Vector-DB, Rocchio).
- **+knn**: dazu ein Inhaltsmerkmal: mittlere Ähnlichkeit zu den 5 ähnlichsten «relevant»-Entscheiden minus dasselbe für «nicht relevant».

AP = Average Precision der Rangfolge der noch offenen Urteile (höher = relevante Urteile weiter oben). «vs current» ist der gepaarte Unterschied zu **current** im selben Lauf; «besser» der Anteil der Läufe, in denen die Variante eine höhere AP hat. Recall und Liste in Prozentpunkten bzw. Urteilen gegenüber der Liste ohne Lernen.

## Prior-SD 0.25 (wie im Betrieb)

| Protokoll | k | Läufe | ΔAP current | ΔAP +proto | +proto vs current (SD) | besser | ΔAP +knn | +knn vs current (SD) | besser | Δ Recall current / +proto / +knn | Gewicht proto / knn |
|---|---|---|---|---|---|---|---|---|---|---|---|
| top | 5 | 2 | -0.001 | -0.015 | -0.014 (0.019) | 50% | -0.014 | -0.012 (0.020) | 50% | +0.0 / +0.0 / +0.0 | -0.00 / +0.01 |
| top | 10 | 2 | +0.010 | +0.005 | -0.005 (0.013) | 50% | +0.011 | +0.001 (0.006) | 50% | +0.0 / +0.0 / +0.0 | +0.12 / +0.08 |
| top | 20 | 2 | -0.004 | +0.023 | +0.027 (0.040) | 50% | +0.039 | +0.042 (0.054) | 50% | +0.0 / +0.0 / +0.0 | -0.06 / -0.05 |
| top | 40 | 2 | -0.035 | -0.034 | +0.001 (0.003) | 50% | -0.060 | -0.024 (0.008) | 0% | +0.0 / +0.0 / +0.0 | -0.07 / -0.06 |
| top | 80 | 2 | -0.001 | -0.002 | -0.001 (0.000) | 0% | -0.006 | -0.005 (0.000) | 0% | +0.0 / +0.0 / +0.0 | -0.01 / +0.03 |
| random | 5 | 60 | -0.005 | -0.015 | -0.010 (0.017) | 33% | -0.014 | -0.010 (0.017) | 40% | +0.0 / +0.1 / +0.0 | +0.00 / +0.01 |
| random | 10 | 60 | -0.007 | -0.015 | -0.008 (0.019) | 33% | -0.015 | -0.008 (0.018) | 35% | +0.2 / +0.2 / +0.1 | -0.04 / -0.03 |
| random | 20 | 60 | -0.010 | -0.016 | -0.007 (0.019) | 45% | -0.018 | -0.008 (0.018) | 43% | +0.0 / +0.1 / +0.0 | -0.01 / -0.01 |
| random | 40 | 60 | -0.010 | -0.019 | -0.009 (0.019) | 40% | -0.020 | -0.010 (0.024) | 35% | +0.1 / +0.1 / +0.1 | -0.03 / -0.03 |
| random | 80 | 60 | -0.007 | -0.014 | -0.007 (0.030) | 40% | -0.021 | -0.014 (0.030) | 32% | +0.0 / +0.1 / +0.5 | -0.04 / +0.02 |

## Prior-SD 1.0

| Protokoll | k | Läufe | ΔAP current | ΔAP +proto | +proto vs current (SD) | besser | ΔAP +knn | +knn vs current (SD) | besser | Δ Recall current / +proto / +knn | Gewicht proto / knn |
|---|---|---|---|---|---|---|---|---|---|---|---|
| top | 5 | 2 | +0.005 | +0.008 | +0.002 (0.029) | 50% | +0.011 | +0.005 (0.030) | 50% | +0.0 / +0.0 / +0.0 | -0.25 / -0.15 |
| top | 10 | 2 | -0.010 | -0.041 | -0.031 (0.019) | 0% | -0.021 | -0.011 (0.003) | 0% | +0.0 / +0.0 / +0.0 | +0.46 / -0.00 |
| top | 20 | 2 | +0.001 | -0.066 | -0.067 (0.054) | 0% | -0.050 | -0.051 (0.064) | 50% | +0.0 / +0.0 / +0.0 | +0.02 / -0.15 |
| top | 40 | 2 | -0.015 | -0.077 | -0.062 (0.072) | 50% | -0.025 | -0.010 (0.008) | 0% | +0.0 / +0.0 / +0.0 | -0.41 / -0.22 |
| top | 80 | 2 | +0.065 | +0.079 | +0.014 (0.000) | 50% | +0.044 | -0.021 (0.000) | 0% | +0.0 / +0.0 / +0.0 | -0.11 / +0.01 |
| random | 5 | 60 | -0.011 | -0.036 | -0.025 (0.040) | 23% | -0.033 | -0.023 (0.037) | 27% | +0.3 / +0.3 / +0.3 | +0.08 / +0.13 |
| random | 10 | 60 | -0.015 | -0.060 | -0.045 (0.077) | 28% | -0.059 | -0.044 (0.074) | 17% | +0.5 / +0.2 / +0.2 | -0.15 / -0.11 |
| random | 20 | 60 | -0.021 | -0.050 | -0.030 (0.052) | 28% | -0.048 | -0.027 (0.037) | 25% | +0.4 / +0.5 / +0.4 | -0.11 / -0.08 |
| random | 40 | 60 | -0.010 | -0.039 | -0.028 (0.044) | 30% | -0.044 | -0.034 (0.053) | 32% | +0.8 / +0.8 / +0.5 | -0.23 / -0.20 |
| random | 80 | 60 | -0.001 | -0.020 | -0.019 (0.054) | 28% | -0.028 | -0.027 (0.055) | 28% | +0.4 / +0.6 / +0.6 | -0.22 / -0.02 |

## Obergrenze: alle Suva-Labels statt weniger Klicks

Wie gut trennt das Inhaltsmerkmal Suvas Auswahl, wenn *alle* Labels bekannt sind? AUC 0.5 = Zufall, 1.0 = perfekt. «proto (anderes Quartal)» nutzt Suvas ganze Auswahl des anderen Quartals; «LOO» die Labels des eigenen Quartals ohne das jeweilige Urteil. AP «Apertus + proto»: Prototyp des anderen Quartals mit dort gelerntem Gewicht zur Apertus-Relevanz addiert.

| Quartal | Urteile (Suva) | AUC Apertus | AUC proto (anderes Quartal) | AUC proto LOO | AUC knn LOO | AP Apertus | AP Apertus + proto | Gewicht |
|---|---|---|---|---|---|---|---|---|
| 2025-Q4 | 135 (28) | 0.915 | 0.730 | 0.791 | 0.884 | 0.807 | 0.731 | +0.39 |
| 2026-Q1 | 165 (31) | 0.852 | 0.645 | 0.669 | 0.737 | 0.540 | 0.508 | +0.51 |

## Lernkurve: mehr Entscheide

Entschieden sind das ganze andere Quartal (bisherige Entscheide) plus `k` zufällige Urteile des gemessenen Quartals (Mittel über 30 Ziehungen); gemessen am Rest. Links: wie gut das Inhaltsmerkmal *allein* Suvas Auswahl trennt (AUC). Rechts: Rangfolge (AP) von Apertus allein und mit dem Inhaltsmerkmal, Gewicht auf den Entscheiden gelernt. Die letzte Zeile je Quartal misst nur noch 20 Urteile und streut entsprechend.

| Quartal | Entscheide (bisher + k) | gemessen | AUC proto | AUC knn | AUC Apertus | AP Apertus | AP + proto | AP + knn |
|---|---|---|---|---|---|---|---|---|
| 2025-Q4 | 165 + 0 | 135 | 0.730 | 0.728 | 0.915 | 0.807 | 0.807 | 0.804 |
| 2025-Q4 | 165 + 10 | 125 | 0.781 | 0.754 | 0.916 | 0.812 | 0.810 | 0.807 |
| 2025-Q4 | 165 + 20 | 115 | 0.821 | 0.777 | 0.914 | 0.808 | 0.803 | 0.801 |
| 2025-Q4 | 165 + 40 | 95 | 0.809 | 0.776 | 0.910 | 0.800 | 0.793 | 0.791 |
| 2025-Q4 | 165 + 80 | 55 | 0.855 | 0.833 | 0.917 | 0.828 | 0.825 | 0.822 |
| 2025-Q4 | 165 + 115 | 20 | 0.866 | 0.868 | 0.932 | 0.846 | 0.825 | 0.853 |
| 2026-Q1 | 135 + 0 | 165 | 0.645 | 0.761 | 0.852 | 0.540 | 0.515 | 0.541 |
| 2026-Q1 | 135 + 10 | 155 | 0.654 | 0.764 | 0.852 | 0.545 | 0.519 | 0.552 |
| 2026-Q1 | 135 + 20 | 145 | 0.680 | 0.762 | 0.855 | 0.554 | 0.531 | 0.560 |
| 2026-Q1 | 135 + 40 | 125 | 0.680 | 0.771 | 0.852 | 0.537 | 0.521 | 0.527 |
| 2026-Q1 | 135 + 80 | 85 | 0.713 | 0.771 | 0.851 | 0.557 | 0.542 | 0.527 |
| 2026-Q1 | 135 + 145 | 20 | 0.732 | 0.818 | 0.869 | 0.652 | 0.648 | 0.602 |

Lesart: Das Inhaltsmerkmal allein wird mit mehr Entscheiden besser (es lernt tatsächlich dazu), nähert sich aber der Trennschärfe, die Apertus schon hat. Zusammen mit Apertus bringt es in diesen zwei Quartalen keinen Gewinn über das Rauschen hinaus. Ob es bei deutlich mehr Entscheiden (über viele Quartale) etwas Eigenes beiträgt, zeigen zwei Quartale mit 59 Suva-Urteilen nicht; diese Tabelle lässt sich mit echten Entscheiden wiederholen.

## Was diese Zahlen nicht zeigen

- Zwei Quartale mit zusammen 59 Suva-Urteilen; die zufälligen Ziehungen teilen dieselben Urteile, die Läufe sind nicht unabhängig.
- Suvas Auswahl ist kein Juristen-Feedback im Betrieb: echte Klicks folgen dem, was auf dem Bildschirm steht.
- Gemessen wird die Rangfolge der noch offenen Urteile desselben Quartals, nicht die Wirkung auf spätere Quartale.
