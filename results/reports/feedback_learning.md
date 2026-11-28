# Simulation: Lernen aus Relevanz-Entscheiden

Erzeugt mit `python -m hack_apertus_coffee_exe.eval.feedback_sim`. Nicht von Hand ändern.

Frage: Was ändert sich an der Liste, wenn Juristinnen und Juristen `k` Urteile entscheiden und das System daraus lernt (`triage/feedback_learning.py`)?

Aufbau: Quartale 2025-Q4 und 2026-Q1, in denen Suvas Auswahl bekannt ist. Die Relevanz eines Quartals stammt aus der Kalibrierung auf dem jeweils anderen Quartal, wie im Betrieb. Die simulierte Juristin entscheidet `k` Urteile genau so, wie Suva es getan hat: entweder die ersten `k` der Liste («von oben») oder `k` zufällige (Mittel über 50 Ziehungen). Gemessen wird die Liste der **noch nicht entschiedenen** Urteile an der Listenschwelle (`list_factor` 0.3). «Ungeschützt» ist das Lernen ohne weitere Regel, «geschützt» mit der Regel im Betrieb: Ein Urteil, das auf der Liste war, bleibt auf der Liste. Es wird kein Modell aufgerufen.

## Mittlere Änderung gegenüber der Liste ohne Lernen

Liste = Anzahl Urteile, Recall und Precision in Prozentpunkten, AP = Average Precision der Rangfolge. `prior_sd` 0.25 ist die Einstellung im Betrieb.

| Reihenfolge | prior_sd | k | Liste ungeschützt | Recall ungeschützt | Precision ungeschützt | Liste geschützt | Recall geschützt | Precision geschützt | AP geschützt |
|---|---|---|---|---|---|---|---|---|---|
| von oben | 0.1 | 5 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | -0.001 |
| von oben | 0.1 | 10 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | +0.008 |
| von oben | 0.1 | 20 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | +0.001 |
| von oben | 0.1 | 40 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | -0.039 |
| von oben | 0.1 | 80 | -0.5 | +0.0 | +0.1 | +0.0 | +0.0 | +0.0 | -0.002 |
| von oben | 0.25 **(Betrieb)** | 5 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | -0.001 |
| von oben | 0.25 **(Betrieb)** | 10 | -2.5 | -2.0 | +0.1 | +0.0 | +0.0 | +0.0 | +0.010 |
| von oben | 0.25 **(Betrieb)** | 20 | -10.5 | -7.5 | +1.0 | +0.0 | +0.0 | +0.0 | -0.002 |
| von oben | 0.25 **(Betrieb)** | 40 | -6.5 | -10.0 | -0.2 | +0.0 | +0.0 | +0.0 | -0.035 |
| von oben | 0.25 **(Betrieb)** | 80 | -4.5 | -33.3 | -0.4 | +0.0 | +0.0 | +0.0 | +0.006 |
| von oben | 0.5 | 5 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | -0.005 |
| von oben | 0.5 | 10 | -13.5 | -6.0 | +2.3 | +0.0 | +0.0 | +0.0 | -0.008 |
| von oben | 0.5 | 20 | -21.5 | -10.0 | +5.0 | +0.0 | +0.0 | +0.0 | -0.008 |
| von oben | 0.5 | 40 | -19.0 | -20.0 | +1.6 | +0.0 | +0.0 | +0.0 | +0.007 |
| von oben | 0.5 | 80 | -9.0 | -66.7 | -1.3 | +0.0 | +0.0 | +0.0 | +0.052 |
| von oben | 1.0 | 5 | -1.5 | -1.9 | -0.1 | +0.0 | +0.0 | +0.0 | +0.005 |
| von oben | 1.0 | 10 | -23.5 | -8.0 | +6.4 | +0.0 | +0.0 | +0.0 | -0.008 |
| von oben | 1.0 | 20 | -26.0 | -10.0 | +8.4 | +0.5 | +0.0 | -0.1 | +0.007 |
| von oben | 1.0 | 40 | -22.0 | -20.0 | +3.0 | +0.0 | +0.0 | +0.0 | -0.013 |
| von oben | 1.0 | 80 | -8.5 | -33.3 | +0.4 | +1.0 | +0.0 | -0.2 | +0.136 |
| zufällig | 0.1 | 5 | +0.1 | +0.0 | -0.0 | +0.1 | +0.0 | -0.0 | -0.004 |
| zufällig | 0.1 | 10 | +0.3 | +0.0 | -0.1 | +0.3 | +0.0 | -0.1 | -0.007 |
| zufällig | 0.1 | 20 | +0.2 | +0.0 | -0.1 | +0.2 | +0.0 | -0.1 | -0.009 |
| zufällig | 0.1 | 40 | +0.1 | +0.0 | -0.0 | +0.1 | +0.0 | -0.0 | -0.011 |
| zufällig | 0.1 | 80 | -0.1 | -0.1 | +0.0 | +0.0 | +0.0 | -0.0 | -0.011 |
| zufällig | 0.25 **(Betrieb)** | 5 | +0.4 | -0.1 | -0.1 | +0.5 | +0.0 | -0.1 | -0.005 |
| zufällig | 0.25 **(Betrieb)** | 10 | +0.3 | -0.4 | -0.2 | +0.7 | +0.0 | -0.2 | -0.008 |
| zufällig | 0.25 **(Betrieb)** | 20 | -0.2 | -0.7 | -0.1 | +0.5 | +0.0 | -0.1 | -0.009 |
| zufällig | 0.25 **(Betrieb)** | 40 | -2.0 | -1.7 | +0.3 | +0.4 | +0.0 | -0.1 | -0.007 |
| zufällig | 0.25 **(Betrieb)** | 80 | -1.3 | -1.5 | +0.3 | +0.1 | +0.0 | -0.0 | -0.006 |
| zufällig | 0.5 | 5 | -1.1 | -1.0 | +0.0 | +0.7 | +0.0 | -0.2 | -0.003 |
| zufällig | 0.5 | 10 | -2.6 | -1.7 | +0.4 | +1.4 | +0.0 | -0.3 | -0.007 |
| zufällig | 0.5 | 20 | -4.5 | -2.4 | +0.9 | +0.9 | +0.0 | -0.2 | -0.008 |
| zufällig | 0.5 | 40 | -5.7 | -2.9 | +1.9 | +0.7 | +0.0 | -0.2 | -0.007 |
| zufällig | 0.5 | 80 | -2.8 | -2.3 | +1.2 | +0.3 | +0.0 | -0.1 | -0.003 |
| zufällig | 1.0 | 5 | -8.7 | -3.5 | +2.0 | +2.0 | +0.0 | -0.5 | -0.010 |
| zufällig | 1.0 | 10 | -11.2 | -4.5 | +3.1 | +2.4 | +0.0 | -0.5 | -0.016 |
| zufällig | 1.0 | 20 | -12.5 | -4.7 | +3.6 | +1.4 | +0.0 | -0.3 | -0.018 |
| zufällig | 1.0 | 40 | -9.0 | -3.8 | +3.6 | +1.3 | +0.0 | -0.4 | -0.010 |
| zufällig | 1.0 | 80 | -5.3 | -2.2 | +2.8 | +0.9 | +0.0 | -0.4 | +0.001 |

## Was die Zahlen sagen

Über alle 80 Läufe (einzelne Quartale, nicht gemittelt):

- **Ungeschützt** ändert sich die Liste um -52 bis +1 Urteile und der Recall um -66.7 bis +0.0 Prozentpunkte. Von den Entscheiden «von oben» lauten 51 % «nicht relevant»; das Lernen senkt dann ganze Rechtsgebiete und Kriterien, und Urteile nahe der Schwelle fallen von der Liste, auch solche, die Suva gewählt hat.
- **Geschützt** ändert sich die Liste um +0 bis +5 Urteile, der Recall bleibt in jedem Lauf gleich: Es geht kein von Suva gewähltes Urteil verloren.
- Die Rangfolge (AP, geschützt) ändert sich um -0.097 bis +0.136. Die Werte streuen in beide Richtungen um null: Ein Gewinn in der Rangfolge ist mit diesen Daten **nicht nachgewiesen**.

Folgerung für den Betrieb: Gelernt wird mit Listenschutz. Entscheide verändern die Bewertung ähnlicher Urteile (gleiches Rechtsgebiet, gleiche Kriterien) und damit die Reihenfolge, können Urteile auf die Liste bringen, aber keines von der Liste nehmen. Grenzen: zwei Quartale mit je rund 30 von Suva gewählten Urteilen sind wenig; Rechtsgebiet und Kriterien sind grobe Merkmale. Ein feineres Merkmal (Ähnlichkeit der Urteilstexte) ist der nächste Schritt.
