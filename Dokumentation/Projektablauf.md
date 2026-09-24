# Projektablauf

1. **Analysephase**
   Zunächst haben wir uns mit dem bestehenden Projekt vertraut gemacht und Code sowie Funktionalität analysiert.
   2. Dabei stießen wir auf Probleme durch fehlende Zugangsdaten für die Raspberry Pis und den Hauptserver.
   3. Zudem erhielten wir von der Vorgängergruppe einen umfangreichen Ordner mit verschiedenen Codeversionen, ohne dass erkennbar war, welche davon die aktuelle bzw. finale Version darstellte.

4. **Testphase**
   Es folgte eine Testphase, in der der Hardwareaufbau sowie die Weboberflächen untersucht wurden.

5. **Parallele Entwicklung einer Simulation**
   Da der fehlerhafte Hardwareaufbau keine Extraktion realistischer Vogeldaten ermöglichte, wurde parallel mit der Entwicklung einer Simulation der Vogelflugdaten begonnen.

6. **Weitere Codeanalyse**
   Der Code des Originalprojekts wurde weiter analysiert, um die zugrunde liegenden Fehler zu identifizieren.

7. **Entscheidung für eine digitale Abbildung**
   Da sich eine Korrektur des Originalprojekts als äußerst schwierig erwies, wurde beschlossen, das Projekt vollständig digital in einer Weboberfläche abzubilden.
   8. Dafür wurde die algorithmische Generierung der Vögel verbessert und mit anpassbaren Parametern versehen, um die Flugbahnen flexibel gestalten zu können. Die generierten Vögel sind dabei Punkte im dreidimensionalen Raum.
   9. Anschließend wurde ein Algorithmus konzipiert, der die Flugbahnen der Vögel mittels eines Greedy-Verfahrens berechnet.
   10. Dabei kamen Bedenken auf, dass der Flugbahn-Algorithmus sämtliche generierten Punkte berücksichtigt, was im Vergleich zu realen Aufnahmen nicht realistisch ist.
   11. Daraufhin wurden die Funktionalitäten der Pi-Kameras und des Hauptservers digital nachgebildet. Dazu zählten folgende Funktionen:
      1. Platzierung und Ausrichtung der Kameras im dreidimensionalen Raum.
      2. Überführung der 3D-Punkte in die 2D-Sichtpunkte der jeweiligen simulierten Kameras.
      3. Aussortierung von Punkten außerhalb der Sichtfelder der Kameras.
      4. Rückführung der 2D-Punkte in 3D-Positionen mittels Triangulation, unter der Annahme, dass ein Vogel von mindestens zwei Kameras erfasst wird.
   12. Da die Kernidee des Projekts darin bestand, den Singflug einer Feldlerche zu ermitteln, wurde die Vogelgenerierung um einen Algorithmus ergänzt, der dieses typische Flugmuster digital abbildet.
   13. Zusätzlich wurde ein weiterer Algorithmus implementiert, der diesen Singflug deterministisch anhand der simulierten Flugbahnen erkennt.

# Arbeitsanteil
## Colin Voigt
- Test des Versuchaufbaus (4)
- Implementierung der Vogelgenerierung (6, 8.6)
- Implementierung des Greedy-Algorithmus zur bestimmung der Flugbahnen (9)