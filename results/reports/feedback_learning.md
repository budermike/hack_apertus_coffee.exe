# Simulation: Lernen aus Relevanz-Entscheiden

Erzeugt mit `python -m hack_apertus_coffee_exe.eval.feedback_sim`. Nicht von Hand ändern.

Frage: Was ändert sich an der Liste, wenn Juristinnen und Juristen `k` Urteile entscheiden und das System daraus lernt (`triage/feedback_learning.py`)?

Aufbau: Quartale 2025-Q4 und 2026-Q1, in denen Suvas Auswahl bekannt ist. Die Relevanz eines Quartals stammt aus der Kalibrierung auf dem jeweils anderen Quartal, wie im Betrieb. Die simulierte Juristin entscheidet `k` Urteile genau so, wie Suva es getan hat: entweder die ersten `k` der Liste («von oben») oder `k` zufällige (Mittel über 50 Ziehungen). Gemessen wird die Liste der **noch nicht entschiedenen** Urteile an der Listenschwelle (Recall-Ziel 95 % der bekannten Suva-Urteile des Kalibrierquartals). «Ungeschützt» ist das Lernen ohne weitere Regel, «geschützt» mit der Regel im Betrieb: Ein Urteil, das auf der Liste war, bleibt auf der Liste. Es wird kein Modell aufgerufen.

## Mittlere Änderung gegenüber der Liste ohne Lernen

Liste = Anzahl Urteile, Recall und Precision in Prozentpunkten, AP = Average Precision der Rangfolge. `prior_sd` 0.25 ist die Einstellung im Betrieb.

| Reihenfolge | prior_sd | k | Liste ungeschützt | Recall ungeschützt | Precision ungeschützt | Liste geschützt | Recall geschützt | Precision geschützt | AP geschützt |
|---|---|---|---|---|---|---|---|---|---|
| von oben | 0.1 | 5 | -3.0 | -1.9 | +0.2 | +0.0 | +0.0 | +0.0 | -0.001 |
| von oben | 0.1 | 10 | -3.0 | -2.0 | +0.2 | +0.5 | +0.0 | -0.2 | +0.008 |
| von oben | 0.1 | 20 | +0.0 | -2.5 | -0.6 | +3.5 | +0.0 | -0.9 | +0.001 |
| von oben | 0.1 | 40 | +0.0 | -5.0 | -0.9 | +3.5 | +0.0 | -0.8 | -0.039 |
| von oben | 0.1 | 80 | -1.5 | -33.3 | -0.8 | +3.5 | +0.0 | +0.0 | -0.004 |
| von oben | 0.25 **(Betrieb)** | 5 | +2.0 | -1.9 | -1.4 | +5.5 | +0.0 | -1.8 | -0.001 |
| von oben | 0.25 **(Betrieb)** | 10 | -4.0 | -4.0 | -0.1 | +6.0 | +0.0 | -1.8 | +0.010 |
| von oben | 0.25 **(Betrieb)** | 20 | -10.5 | -7.5 | +2.6 | +7.5 | +0.0 | -1.7 | -0.004 |
| von oben | 0.25 **(Betrieb)** | 40 | -5.5 | -10.0 | +0.1 | +8.0 | +0.0 | -1.6 | -0.035 |
| von oben | 0.25 **(Betrieb)** | 80 | -5.0 | -66.7 | -3.3 | +7.5 | +0.0 | +0.0 | -0.001 |
| von oben | 0.5 | 5 | +4.5 | -1.9 | -2.1 | +8.5 | +0.0 | -2.6 | -0.005 |
| von oben | 0.5 | 10 | -12.0 | -6.0 | +3.5 | +9.0 | +0.0 | -2.6 | -0.010 |
| von oben | 0.5 | 20 | -14.0 | -7.5 | +6.0 | +9.5 | +0.0 | -2.0 | -0.010 |
| von oben | 0.5 | 40 | -13.5 | -15.0 | +3.1 | +8.0 | +0.0 | -1.6 | +0.007 |
| von oben | 0.5 | 80 | -7.5 | -66.7 | -3.3 | +6.5 | +0.0 | +0.0 | +0.004 |
| von oben | 1.0 | 5 | +5.0 | -3.7 | -2.5 | +14.0 | +0.0 | -3.9 | +0.005 |
| von oben | 1.0 | 10 | -13.0 | -6.0 | +4.6 | +10.0 | +0.0 | -2.8 | -0.010 |
| von oben | 1.0 | 20 | -14.5 | -10.0 | +9.5 | +14.0 | +0.0 | -2.7 | +0.001 |
| von oben | 1.0 | 40 | -15.5 | -15.0 | +5.0 | +11.0 | +0.0 | -2.0 | -0.015 |
| von oben | 1.0 | 80 | -2.5 | -33.3 | +6.7 | +10.5 | +0.0 | +0.0 | +0.065 |
| zufällig | 0.1 | 5 | -4.2 | -2.3 | +0.8 | +0.0 | +0.0 | -0.0 | -0.004 |
| zufällig | 0.1 | 10 | -4.5 | -2.6 | +0.9 | +0.0 | +0.0 | +0.0 | -0.007 |
| zufällig | 0.1 | 20 | -3.3 | -2.2 | +0.5 | +0.1 | +0.0 | -0.0 | -0.009 |
| zufällig | 0.1 | 40 | -2.0 | -1.5 | +0.3 | +0.6 | +0.0 | -0.3 | -0.011 |
| zufällig | 0.1 | 80 | -0.9 | -1.6 | -0.3 | +1.0 | +0.0 | -0.8 | -0.011 |
| zufällig | 0.25 **(Betrieb)** | 5 | -3.5 | -2.7 | +0.4 | +1.5 | +0.0 | -0.6 | -0.004 |
| zufällig | 0.25 **(Betrieb)** | 10 | -4.3 | -3.2 | +0.6 | +1.8 | +0.2 | -0.6 | -0.008 |
| zufällig | 0.25 **(Betrieb)** | 20 | -3.8 | -3.2 | +0.3 | +2.2 | +0.1 | -0.9 | -0.009 |
| zufällig | 0.25 **(Betrieb)** | 40 | -3.6 | -2.7 | +0.7 | +2.8 | +0.1 | -1.4 | -0.008 |
| zufällig | 0.25 **(Betrieb)** | 80 | -2.7 | -3.1 | +0.4 | +2.2 | +0.0 | -1.7 | -0.006 |
| zufällig | 0.5 | 5 | -5.6 | -4.0 | +1.0 | +4.0 | +0.2 | -1.3 | -0.002 |
| zufällig | 0.5 | 10 | -7.7 | -4.8 | +2.2 | +4.2 | +0.3 | -1.4 | -0.007 |
| zufällig | 0.5 | 20 | -8.1 | -4.7 | +2.4 | +4.2 | +0.3 | -1.6 | -0.008 |
| zufällig | 0.5 | 40 | -6.3 | -3.9 | +2.3 | +4.0 | +0.2 | -1.8 | -0.008 |
| zufällig | 0.5 | 80 | -3.9 | -4.1 | +1.3 | +2.2 | +0.0 | -1.8 | -0.004 |
| zufällig | 1.0 | 5 | -14.1 | -6.8 | +5.4 | +5.2 | +0.3 | -1.6 | -0.009 |
| zufällig | 1.0 | 10 | -14.7 | -7.4 | +6.1 | +5.7 | +0.5 | -1.8 | -0.014 |
| zufällig | 1.0 | 20 | -13.1 | -7.0 | +5.2 | +5.4 | +0.5 | -1.9 | -0.017 |
| zufällig | 1.0 | 40 | -10.1 | -5.6 | +4.6 | +4.3 | +0.7 | -1.7 | -0.011 |
| zufällig | 1.0 | 80 | -7.0 | -4.4 | +3.9 | +2.0 | +0.4 | -1.4 | +0.000 |

## Was die Zahlen sagen

Über alle 80 Läufe (einzelne Quartale, nicht gemittelt):

- **Ungeschützt** ändert sich die Liste um -57 bis +28 Urteile und der Recall um -66.7 bis +0.0 Prozentpunkte. Von den Entscheiden «von oben» lauten 51 % «nicht relevant»; das Lernen senkt dann ganze Rechtsgebiete und Kriterien, und Urteile nahe der Schwelle fallen von der Liste, auch solche, die Suva gewählt hat.
- **Geschützt** ändert sich die Liste um +0 bis +28 Urteile, der Recall um +0.0 bis +1.3 Prozentpunkte: Es geht kein von Suva gewähltes Urteil verloren.
- Die Rangfolge (AP, geschützt) ändert sich um -0.097 bis +0.065. Die Werte streuen in beide Richtungen um null: Ein Gewinn in der Rangfolge ist mit diesen Daten **nicht nachgewiesen**.

Folgerung für den Betrieb: Gelernt wird mit Listenschutz. Entscheide verändern die Bewertung ähnlicher Urteile (gleiches Rechtsgebiet, gleiche Kriterien) und damit die Reihenfolge, können Urteile auf die Liste bringen, aber keines von der Liste nehmen. Grenzen: zwei Quartale mit je rund 30 von Suva gewählten Urteilen sind wenig; Rechtsgebiet und Kriterien sind grobe Merkmale. Ein feineres Merkmal (Ähnlichkeit der Urteilstexte) ist der nächste Schritt.
