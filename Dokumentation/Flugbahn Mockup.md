### Dokumentation der Vogelflugbahn-Generierung

Dieses Kapitel beschreibt das Modul `bird_generation.py`. Es erzeugt simulierte Flugbahnen für allgemeine Vögel und kann zusätzlich Flugbahnen von Feldlerchen über die Funktion `generate_skylarks` aus `helper_functions/skylark_generation.py` ergänzen.

Die Ausgabe besteht aus Positionsmessungen im ENU-Koordinatensystem:

- `enu_e`: Ostkoordinate (*East*)
- `enu_n`: Nordkoordinate (*North*)
- `enu_u`: Höhenkoordinate (*Up*)

#### Kernfunktion: Vögel generieren (`generate_birds`)

Die Funktion `generate_birds` erzeugt für jeden gewöhnlichen Vogel eine stochastische Flugbahn. Anschließend werden optional Feldlerchen-Flugbahnen an die Ergebnisliste angehängt.

##### 1. Zufallsinitialisierung

Für die normalen Vogelflugbahnen wird ein eigener Zufallsgenerator erzeugt:

- Wenn `seed_value` gesetzt ist, wird dieser Wert als Seed verwendet.
- Wenn `seed_value` den Wert `None` hat, wird der aktuelle Unix-Zeitstempel als Seed verwendet.

Bei identischen Eingabeparametern und identischem Seed entstehen reproduzierbare Flugbahnen für die gewöhnlichen Vögel.
##### 2. Startwerte pro Vogel

Für jeden Vogel wird eine eindeutige ID im Format `bird_XXXX` erzeugt, beispielsweise `bird_0001`.

Anschließend werden folgende Startwerte zufällig bestimmt:

- **Anzahl der Messpunkte:** zwischen 3 und 29 Messungen.
- **Startzeitpunkt:** zufällige Uhrzeit am Datum von `sim_date`.
  - Stunde zwischen 00 und 23 Uhr
  - Minute zwischen 00 und 30 Minuten
  - Sekunde immer `00`
- **Startposition:**  
  - `x` und `y` werden zunächst innerhalb der jeweiligen Raumgrenzen gewählt.
  - Anschließend wird auf beide horizontalen Koordinaten eine zufällige Verschiebung im Bereich von `-initial_spread` bis `+initial_spread` angewendet.
  - `z` wird direkt zwischen `z_min` und `z_max` gewählt.
- **Flugrichtung:** zufälliger Winkel zwischen `0` und `2π`.
- **Startgeschwindigkeit:** zufälliger Wert zwischen `min_speed` und `max_speed`.
- **Vertikale Startgeschwindigkeit:** zufälliger Wert zwischen `-0.05` und `0.05`.
##### 3. Iterative Bewegungsberechnung

Für jeden gespeicherten Messpunkt wird die Bewegung `time_interval`-mal aktualisiert. Die innere Schleife repräsentiert damit die Bewegungsentwicklung zwischen zwei Messungen.

Pro internem Bewegungsschritt passiert Folgendes:

1. **Richtungsänderung:**  
   Der Flugwinkel wird durch gaußverteiltes Rauschen verändert.

   `angle += rng.gauss(0, variance_angle)`

2. **Geschwindigkeitsänderung:**  
   Die Geschwindigkeit wird ebenfalls zufällig verändert und danach auf den Bereich zwischen `min_speed` und `max_speed` begrenzt.

   `speed += rng.gauss(0, variance_speed)`

3. **Horizontale Bewegung:**  
   Die neue Position ergibt sich aus Flugwinkel und Geschwindigkeit.

   `x += cos(angle) * speed`  
   `y += sin(angle) * speed`

4. **Vertikale Bewegung:**  
   Die Höhe wird um die aktuelle vertikale Geschwindigkeit sowie zusätzliches gaußsches Rauschen verändert.

   `z += vertical_speed`  
   `z += rng.gauss(0, variance_z)`

   Anschließend wird die interne Höhe auf den Bereich von `z_min` bis `z_max` begrenzt.

5. **Änderung der vertikalen Geschwindigkeit:**  
   Die vertikale Geschwindigkeit wird selbst durch gaußsches Rauschen verändert.

   `vertical_speed += rng.gauss(0, variance_vertical_speed)`

Die Parameter mit dem Präfix `variance_` werden trotz ihrer Bezeichnung als Standardabweichung für `rng.gauss()` verwendet und nicht als mathematische Varianz.
##### 4. Erstellung der Positionsdatensätze

Nach den internen Bewegungsschritten wird ein Datensatz erzeugt.

Vor dem Speichern wird zusätzliches Messrauschen auf die Position angewendet:

- Für `enu_e` und `enu_n` wird gaußförmiges Rauschen mit der Standardabweichung `position_noise` verwendet.
- Für `enu_u` wird unabhängig davon ein festes gaußförmiges Rauschen mit einer Standardabweichung von `0.03` verwendet.

Die Koordinaten werden anschließend auf zwei Nachkommastellen gerundet.

Der Zeitstempel eines Datensatzes wird wie folgt berechnet:

`start_time + sample_index * time_interval`
##### 5. Ergänzung von Feldlerchen-Flugbahnen

Nach der Generierung aller gewöhnlichen Vögel ruft `generate_birds` die Funktion `generate_skylarks` auf.

Dabei werden unter anderem folgende Werte weitergegeben:

- Anzahl der Feldlerchen
- Messintervall
- Simulationsdatum
- Raumgrenzen
- Positionsrauschen
- Seed

Die Feldlerchen-Datensätze werden nach allen normalen Vogel-Datensätzen an die Rückgabeliste angehängt.

Die detaillierte Fluglogik für Feldlerchen, beispielsweise Steigflug, Singflug und spiralförmiger Sinkflug, ist in `helper_functions/skylark_generation.py` implementiert.

Ein Plot der von der Funktion erzeugten Vögel, sieht wie folgt aus:

![[plotted_birds.png|654]]
#### Hilfsfunktionen

##### `parse_seed`

Die Funktion wandelt einen eingegebenen Seed in einen reproduzierbaren Integer um.

- `None` oder ein leerer Text wird zu `None`.
- Numerische Eingaben werden direkt in einen Integer umgewandelt.
- Textwerte werden mittels SHA-256 gehasht.
- Die ersten acht Bytes des Hashes werden als Integer verwendet.

Dadurch können sowohl Zahlen als auch beliebige Textwerte, beispielsweise `testlauf-01`, als reproduzierbarer Seed verwendet werden.

##### `as_float`

Diese Funktion prüft, ob ein Eingabewert in eine endliche Fließkommazahl umgewandelt werden kann.

Ungültige, nicht numerische oder unendliche Werte führen zu einem `ValueError`.

##### `as_integer`

Diese Funktion verwendet zunächst `as_float` und prüft anschließend, ob der Wert eine ganze Zahl ist.

Werte wie `4.0` sind zulässig, während Werte wie `4.5` zu einem `ValueError` führen.

#### Validierung und Schnittstelle zur Benutzeroberfläche (`parse_generation_parameters`)

Die Funktion `parse_generation_parameters` verarbeitet Eingaben aus einem Formular, prüft deren Datentypen und gibt ein Dictionary zurück, das direkt an `generate_birds` übergeben werden kann.

Bei ungültigen Eingaben wird ein `ValueError` mit einer verständlichen Fehlermeldung ausgelöst.

Für Feldlerchen gelten zusätzlich die Validierungsregeln von `generate_skylarks`. Insbesondere muss bei einer positiven Anzahl von Feldlerchen `z_max` größer als `z_min` sein.

#### Parameter und Rückgabewert (`generate_birds`)

| Parameter | Standardwert | Beschreibung |
| --- | ---: | --- |
| `number_of_birds` | – | Anzahl der gewöhnlichen Vögel. Über `parse_generation_parameters` sind Werte von 1 bis 500 zulässig. |
| `time_interval` | – | Zeitlicher Abstand zwischen zwei Messungen in Sekunden. Der Wert bestimmt zugleich die Anzahl interner Bewegungsschritte je Messung. |
| `sim_date` | – | `datetime`-Objekt, dessen Datum für die Simulation verwendet wird. Die Uhrzeit wird pro Vogel zufällig gesetzt. |
| `x_min` | – | Untere Grenze der Ostkoordinate. |
| `x_max` | – | Obere Grenze der Ostkoordinate. |
| `y_min` | – | Untere Grenze der Nordkoordinate. |
| `y_max` | – | Obere Grenze der Nordkoordinate. |
| `z_min` | – | Untere Grenze der Höhe. |
| `z_max` | – | Obere Grenze der Höhe. |
| `min_speed` | – | Minimale horizontale Geschwindigkeit pro internem Bewegungsschritt. |
| `max_speed` | – | Maximale horizontale Geschwindigkeit pro internem Bewegungsschritt. |
| `position_noise` | – | Standardabweichung des finalen horizontalen Positionsrauschens für `enu_e` und `enu_n`. |
| `seed_value` | `None` | Optionaler Seed für reproduzierbare Simulationen. Bei `None` wird der aktuelle Zeitstempel verwendet. |
| `initial_spread` | – | Zusätzliche zufällige Verschiebung der horizontalen Startposition in X- und Y-Richtung. |
| `variance_angle` | – | Standardabweichung der zufälligen Richtungsänderung pro internem Bewegungsschritt. |
| `variance_speed` | – | Standardabweichung der zufälligen Geschwindigkeitsänderung pro internem Bewegungsschritt. |
| `variance_z` | – | Standardabweichung des zusätzlichen vertikalen Rauschens pro internem Bewegungsschritt. |
| `variance_vertical_speed` | – | Standardabweichung der Änderung der vertikalen Geschwindigkeit pro internem Bewegungsschritt. |
| `number_of_larks` | `0` | Anzahl der zusätzlich zu generierenden Feldlerchen. |

#### Validierungsregeln von `parse_generation_parameters`

| Eingabe | Zulässige Werte bzw. Bedingung |
| --- | --- |
| `number_of_birds` | Ganze Zahl zwischen 1 und 500. |
| `number_of_larks` | Ganze Zahl zwischen 0 und 500. |
| `time_interval` | Ganze Zahl zwischen 1 und 60 Sekunden. |
| `simulation_date_value` | Gültiges ISO-8601-Datum. |
| `x_min`, `x_max` | `x_min` darf nicht größer als `x_max` sein. |
| `y_min`, `y_max` | `y_min` darf nicht größer als `y_max` sein. |
| `z_min`, `z_max` | `z_min` darf nicht größer als `z_max` sein. Für Feldlerchen muss `z_max` zusätzlich größer als `z_min` sein. |
| `min_speed`, `max_speed` | `min_speed` darf nicht größer als `max_speed` sein. |
| `initial_spread` | Wert zwischen 0 und 2000. |
| `position_noise` | Wert zwischen 0 und 1. |
| `variance_angle` | Wert zwischen 0 und 0.3. |
| `variance_speed` | Wert zwischen 0 und 1. |
| `variance_z` | Wert zwischen 0 und 1. |
| `variance_vertical_speed` | Wert zwischen 0 und 0.1. |

#### Struktur eines erzeugten Datensatzes

| Feld                 | Beschreibung                                                                                                                                                                         |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `bird_id`            | Eindeutige Vogel-ID. Normale Vögel verwenden das Format `bird_XXXX`, Feldlerchen das Format `lark_XXXX`.                                                                             |
| `timestamp`          | ISO-8601-Zeitstempel der Positionsmessung mit angehängtem `Z`.                                                                                                                       |
| `enu_e`              | Simulierte Ostkoordinate, auf zwei Nachkommastellen gerundet.                                                                                                                        |
| `enu_n`              | Simulierte Nordkoordinate, auf zwei Nachkommastellen gerundet.                                                                                                                       |
| `enu_u`              | Simulierte Höhenkoordinate, auf zwei Nachkommastellen gerundet.                                                                                                                      |

