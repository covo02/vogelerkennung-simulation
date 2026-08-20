Folgene Befehle ausführen um die Entwicklungsumgebung vorzubereiten:

```
python3 -m venv .dev
source .dev/bin/activate
pip install -r Requirements.txt
```


## Streamlit Applikation starten:

mit folgendem Befehl die Weboberfläche der Vogeldatengenerierung öffnen:
```
streamlit run app.py
```
Die Applikation kann dann über http://localhost:8051 geöffnet werden.

## Weboberfläche für Trajektorieberechnung öffnen:

mit folgendem Befehl die Weboberfläche starten, wo man die Trajektorieberechnung auslösen kann:
```
python3 webinterface.py
```
Die Applikation kann dann über http://localhost:8060 geöffnet werden.
## Erklärung Dateien:

| Name               | Beschreibung                                                                                                                                                                                                                                                                |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| app.py             | Führt mehrere Streamlit Seiten zu einer einzigen Multipagewebsite zusammen.                                                                                                                                                                                                 |
| generate_birds.py  | Streamlit-Seite, wo man anhand von Parametern eigene Vögelflugbahenen simulieren kann. Die Daten werden in eine Datei namens "vogel_flugbahnen.json" geschrieben.                                                                                                           |
| debug_algorithm.py | Diese Streamlit-Seite ist nur dafür da, um einen Plot anzuzeigen, der beschreibt, ob die Trajektorie-Berechnung erfolgreich war. (Vergleich soll-wert mit ist-wert). War bei der Entwicklung der "trajectory.py" zum debuggen da.                                           |
| trajectory.py      | Dieses Skript nimmt die generierten Vogelflugbahnen (vogel_flugbahnen.json) und berechnet die Trajtektorien. Seine Gruppierung der Datenpunkte schreibt er in eine neue Datei namens "vogel_flugbahnen_determined.py".                                                      |
| webinterface.py    | Ist ein Simples Webinterface im Style des Webinterfaces des mainservers. Dieses hat ledeglich einen Knopf, der "trajectory.py" ausführt und die resultierenden Trajektorien in einem 3D-Plot ausgibt. Ebenso befindet sich dort ein Knopf wo man die JSON runterladen kann. |
| Requirements.txt   | Alle benötigten Bibs für das Projekt.                                                                                                                                                                                                                                       |
| asset-Ordner       | Der ist für "webinterface.py". Die Bibliothek kann nur CSS Dateien lesen, wenn sie in einem Ordner namens "assets" liegen.                                                                                                                                                  |
