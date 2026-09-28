# Vogelerkennung – Simulation

Dieses Projekt simuliert die Erkennung und Lokalisierung von Vögeln mithilfe mehrerer virtueller Kameras. Dabei werden Vögel und Kameraperspektiven simuliert, aus den 2D-Bildpunkten 3D-Positionen rekonstruiert und anschließend die Flugbahnen analysiert.

## Installation

### Windows

Die folgenden Befehle können in der Eingabeaufforderung (CMD) oder im VS-Code-Terminal ausgeführt werden:

```cmd
git clone https://github.com/covo02/vogelerkennung-simulation.git
cd vogelerkennung-simulation

python -m venv .dev
.dev\Scripts\activate.bat

pip install -r Requirements.txt
python webinterface.py
```
Die Applikation kann dann über http://localhost:8060 geöffnet werden.

### macOS / Linux

```bash
git clone https://github.com/covo02/vogelerkennung-simulation.git
cd vogelerkennung-simulation

python3 -m venv .dev
source .dev/bin/activate

pip install -r Requirements.txt
python3 webinterface.py
```
Die Applikation kann dann über http://localhost:8060 geöffnet werden.

## Simulationsübersicht

![Pipeline der Vogelerkennung](Dokumentation/Bilder/pipeline_abbildung_simulation.png)

## Dokumentation

Weitere Informationen befinden sich im [Nutzerhandbuch](Dokumentation/Nutzerhandbuch_Simulation.pdf).
