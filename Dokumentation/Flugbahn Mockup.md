### Dokumentation der Vogelflugbahn-Simulation

Dieses Kapitel beschreibt die Funktionsweise des Moduls `bird_generation.py`. Das Modul dient der realitätsnahen Simulation von Vogelflugbahnen und ist speziell für die Integration in eine Dash-Weboberfläche konzipiert. Über die Weboberfläche können Benutzer diverse physikalische und stochastische Parameter anpassen. Die daraus resultierenden Flugbahnen werden anschließend sowohl in 2D- als auch in 3D-Plots visualisiert.

#### Kernfunktion: Flugbahngenerierung (`generate_birds`)

Die Hauptfunktion `generate_birds` bildet das Herzstück der Simulation. Sie erzeugt eine Liste von Datensätzen (Records), wobei jeder Record eine Positionsmessung eines Vogels zu einem bestimmten Zeitpunkt repräsentiert.

##### 1. Initialisierung und Zufallsgenerator
Um die Simulationen reproduzierbar zu machen, wird ein Zufallsgenerator (`random.Random`) initialisiert. Falls ein Seed-Wert übergeben wird, nutzt die Funktion diesen. Andernfalls wird der aktuelle Unix-Zeitstempel verwendet, sodass jeder Durchlauf unterschiedliche Ergebnisse liefert.

##### 2. Definition der Startwerte

Für jeden Vogel im angegebenen Parameter `number_of_birds` (1 bis 500) werden zufällige Startbedingungen generiert:

- **Anzahl der Messungen:** Jeder Vogel erhält zwischen 3 und 29 simulierte Messpunkte.
- **Startzeitpunkt:** Ein zufälliger Zeitpunkt am `sim_date` (zwischen 00:00 und 23:30 Uhr).
- **Startposition:** Die X- und Y-Koordinaten werden gleichmäßig (uniform) innerhalb der Raumgrenzen verteilt und zusätzlich um einen zufälligen Wert aus dem Bereich der `initial_spread` verschoben. Die Z-Koordinate (Höhe) wird rein innerhalb der Z-Grenzen gewählt.
- **Bewegungsvektoren:** Ein initialer Flugwinkel (0 bis 2π), eine Startgeschwindigkeit (zwischen `min_speed` und `max_speed`) und eine initiale vertikale Steiggeschwindigkeit (zwischen -0.05 und 0.05) werden bestimmt

##### 3. Entstehung der Flugbahn (Iterative Bewegungsberechnung)

Die eigentliche Flugbahn entsteht durch eine schrittweise, stochastische Fortbewegung. Für jeden Messpunkt eines Vogels wird eine innere Schleife `time_interval`-mal durchlaufen (dieser Wert simuliert den zeitlichen Abstand zwischen zwei Messungen in Sekunden).

In jedem dieser inneren Zeitschritte passiert Folgendes:

1. **Richtungsänderung:** Der Flugwinkel wird um einen gaußverteilten Zufallswert (Mittelwert 0, Varianz `variance_angle`) modifiziert.
2. **Geschwindigkeitsänderung:** Die Geschwindigkeit wird ebenfalls durch gaußsches Rauschen (`variance_speed`) angepasst und anschließend strikt auf den Bereich zwischen `min_speed` und `max_speed` begrenzt.
3. **Horizontale Bewegung:** Die X- und Y-Koordinaten werden basierend auf dem neuen Winkel und der neuen Geschwindigkeit mittels Kosinus und Sinus aktualisiert:

``` python
x += math.cos(angle) * speed
y += math.sin(angle) * speed
```

1. **Vertikale Bewegung:** Die aktuelle Höhe (`z`) wird um die `vertical_speed` erhöht und zusätzlich um gaußsches Rauschen (`variance_z`) ergänzt. Die Höhe wird auf die Raumgrenzen (`z_min`, `z_max`) beschnitten. Im Anschluss wird die vertikale Steiggeschwindigkeit selbst durch gaußsches Rauschen (`variance_vertical_speed`) verändert, was zu einem realistischen Auf- und Absteigen führt.

##### 4. Datensatz-Erstellung und Rauschen

Nach Ablauf der `time_interval`-Schritte wird ein Datensatz für den aktuellen Messpunkt erstellt. Bevor die Koordinaten als ENU-Werte (East, North, Up) gespeichert werden, wird ein finales Positionsrauschen (`position_noise`) addiert, um Messungenauigkeiten realer Sensoren zu simulieren. Die Z-Koordinate erhält ein festes Rauschen von 0.03.

Jeder Datensatz enthält:

- `bird_id`: Fortlaufende ID des Vogels (z.B. `bird_0001`)
- `determined_bird_id`: Statisch auf `bird_0000` gesetzt (Platzhalter für spätere Klassifizierung)
- `timestamp`: Exakter ISO 8601 Zeitstempel
- `enu_e`, `enu_n`, `enu_u`: Die gerundeten Ost-, Nord- und Höhenkoordinaten.

#### Hilfsfunktionen zur Generierung
##### `parse_seed`
Diese Funktion ermöglicht es dem Nutzer, Seeds entweder als Zahl oder als Textwort zu übergeben. Numerische Seeds werden direkt als Integer verwendet. Bei Text-Strings wird ein SHA-256-Hash erstellt, aus dem die ersten 8 Bytes in einen Integer umgewandelt werden. Dies garantiert, dass textbasierte Seeds immer denselben Zahlenwert und damit denselben Simulationsverlauf erzeugen.

##### `build_pi_devices`

Um Referenzpunkte im simulierten Raum zu haben, generiert diese Funktion drei virtuelle Raspberry-Pi-Geräte. Diese werden an den Grenzen des Raumes platziert:

- Ursprung (`x_min`, `y_min`, Z=0)
- X-Achse (`x_max`, `y_min`, Z=0)
- Y-Achse (`x_min`, `y_max`, Z=0)

#### Validierung und Schnittstelle zur Weboberfläche (`parse_generation_parameters`)

Diese Funktion fungiert als Bindeglied zwischen den Dash-Eingabefeldern und der Generierungslogik. Da Web-Formulare standardmäßig Strings zurückgeben, müssen alle Eingaben typsicher konvertiert und validiert werden.

- **Typkonvertierung:** Die internen Helfer `as_float` und `as_integer` prüfen, ob die Eingaben gültige, endliche Zahlen sind.
- **Grenzwertprüfung:** Alle Parameter werden gegen strikte Grenzen geprüft. Beispielsweise muss die Anzahl der Vögel zwischen 1 und 500 liegen, das Positionsrauschen zwischen 0 und 1, und die Richtungsänderung (`variance_angle`) zwischen 0 und 0.3.
- **Logikprüfung:** Es wird sichergestellt, dass Mindestwerte nicht größer als Maximalwerte sind (z.B. `x_min` <= `x_max`).

Sollte ein Wert ungültig sein, wirft die Funktion eine `ValueError`-Exception mit einer klaren Fehlermeldung, die in der Dash-Oberfläche abgefangen und dem Nutzer angezeigt werden kann. Bei erfolgreicher Prüfung wird ein typisiertes Dictionary zurückgegeben, das direkt an `generate_birds` übergeben wird.