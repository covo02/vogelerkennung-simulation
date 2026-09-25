# ------------------------------------------------------------------------------
# gps_reader.py – GPS-Empfänger, Filterung & Mittelwertbildung
# ------------------------------------------------------------------------------
# Dieses Modul liest kontinuierlich NMEA-Datensätze eines GPS-Empfängers über
# eine serielle Schnittstelle (z. B. /dev/ttyUSB0) ein. Die Rohdaten werden
# verarbeitet, gefiltert und in zwei Formen bereitgestellt:
#
#   1) RAW GPS-Daten → letzter gültiger Messpunkt
#   2) AVG GPS-Daten → gleitender Mittelwert über die letzten 5 Stunden
#
# Funktionen des Moduls:
# - Automatische Erkennung von GPS-Ausfällen
# - Reconnect, wenn der USB-Port kurzzeitig getrennt wird
# - Parsing der NMEA-Sätze mit pynmea2
# - Mittelwertbildung von Latitude/Longitude zur Rauschreduktion
# - Optionales Setzen der Systemzeit anhand der GPS-Zeit
# - Thread-sichere Zugriffsfunktionen für andere Module
#
# Dieses Modul wird u.a. verwendet von:
# - Trigger-System (Header-Daten)
# - Webinterface (Live-GPS-Ansicht)
#
# Das Modul startet bei Import automatisch einen Hintergrund-Thread,
# der kontinuierlich die GPS-Daten aktualisiert.
#
# Autoren:              Jonas Wigge, Jens Kuper
# Erstellungszeitraum:  20.11.2025
# Status:               Final
# Freigabe:             Ja
# ------------------------------------------------------------------------------
 

import serial
import pynmea2
import time
from datetime import datetime, timedelta, timezone
import threading
import subprocess
from collections import deque

SAMPLE_WINDOW_SIZE = 200   # Anzahl Samples für Mittelung (muss übereinstimmen mit orientation_web)

history_lat = deque(maxlen=SAMPLE_WINDOW_SIZE)
history_lon = deque(maxlen=SAMPLE_WINDOW_SIZE)
history_alt_gps = deque(maxlen=SAMPLE_WINDOW_SIZE)


_last_valid_raw = {"lat": None, "lon": None, "alt": None, "timestamp": None}


# ======= Konfiguration =======
SERIAL_PORT = "/dev/ttyAMA0"    # /dev/ttyUSB0 - GPS-Port
BAUD_RATE = 9600
UPDATE_INTERVAL = 5             # Sekunden fuer Debugausgabe
TIME_SYNC_INTERVAL = 10         # Sekunden fuer Systemzeit-Update
DEBUG = True                    # True = alle UPDATE_INTERVAL Sek. auf Konsole
ENABLE_NTP_SERVER = False       # True = GPS-Pi als NTP-Server

# ======= Interner Zustand =======
# history:
#   Liste aller gültigen GPS-Punkte der letzten 5 Stunden.
#   Format: (lat, lon, timestamp)
#
# _last_fix:
#   Enthält den letzten berechneten Durchschnittswert.
#
# _last_valid_avg:
#   Wird gespeichert, falls das GPS-Modul ausfällt.
#   → Verhindert Sprünge auf 0 oder None in den Programmen, die GPS-Daten nutzen.
#
# _sensor_ok:
#   True  → GPS sendet valide Daten
#   False → Fehler, kein Fix, Serial-Disconnect, Null-Koordinaten usw.


# Zustandsflag für Sensor
_sensor_ok = False

_lock = threading.Lock()
_last_sync = None


# ======= Hilfsfunktionen =======
# ------------------------------------------------------------------------------
# calc_average(history)
# ------------------------------------------------------------------------------
# Berechnet aus der History-Liste den arithmetischen Mittelwert für
# Latitude und Longitude.
#
# Diese Mittelung reduziert GPS-Rauschen deutlich und verbessert die
# Standortstabilität (durchschnittliche Position über mehrere Stunden).
#
# Rückgabe:
#   (avg_lat, avg_lon) oder (None, None) wenn Liste leer
# ------------------------------------------------------------------------------

def calc_average(history):
    if not history:
        return None, None
    lat = sum([h[0] for h in history]) / len(history)
    lon = sum([h[1] for h in history]) / len(history)
    return lat, lon


# ------------------------------------------------------------------------------
# set_system_time_from_gps()
# ------------------------------------------------------------------------------
# Setzt die Systemzeit des Raspberry Pi auf die präzise GPS-Zeit.
# Dies ist nützlich, wenn mehrere Geräte synchronisiert laufen sollen.
#
# Die Zeit wird im Format "YYYY-MM-DD HH:MM:SS" gesetzt.
# ------------------------------------------------------------------------------

def set_system_time_from_gps(gps_datetime):
    # Dieser Pi setzt seine Zeit nicht ueber GPS.
    # Zeit kommt ausschliesslich reliable per NTP von Pi1.
    return

# ------------------------------------------------------------------------------
# start_ntp_server()
# ------------------------------------------------------------------------------
# Falls ENABLE_NTP_SERVER = True gesetzt ist, wird ein lokaler NTP-Server
# (chronyd) gestartet, der andere Geräte im Netzwerk mit GPS-Zeit versorgen kann.
#
# In diesem Projekt optional, normalerweise deaktiviert.
# ------------------------------------------------------------------------------

def start_ntp_server():
    if not ENABLE_NTP_SERVER:
        return
    try:
        result = subprocess.run(["which", "chronyd"], capture_output=True, text=True)
        if result.returncode != 0:
            print("[GPS] chronyd nicht installiert. Installiere mit: sudo apt install chrony")
            return
        conf = """
server 127.127.1.0 minpoll 4 maxpoll 4
fudge 127.127.1.0 stratum 1 refid GPS
allow 192.168.0.0/16
allow 10.0.0.0/8
allow 172.16.0.0/12
local stratum 1
driftfile /var/lib/chrony/chrony.drift
logdir /var/log/chrony
"""
        with open("/tmp/chrony_gps.conf", "w") as f:
            f.write(conf)
        subprocess.run(["sudo", "chronyd", "-f", "/tmp/chrony_gps.conf"], check=False)
        if DEBUG:
            print("[GPS] Lokaler NTP-Server gestartet.")
    except Exception as e:
        print(f"[GPS] Fehler beim Starten des NTP-Servers: {e}")


# ======= GPS-Loop =======
# ------------------------------------------------------------------------------
# _gps_loop()
# ------------------------------------------------------------------------------
# Dies ist der zentrale Hintergrundthread, der:
#   - NMEA-Daten vom GPS-Empfänger einliest
#   - Werte parsed (RMC + GGA)
#   - Fehler erkennt (Kein Fix, Null-Koordinaten, Disconnect)
#   - Mittelwert-History aktualisiert
#   - Zeit-Synchronisation durchführt
#
# Das GPS-Modul kann jederzeit kurzzeitig ausfallen (z.B. USB-Wackler),
# deshalb ist der gesamte Ablauf extrem fehlertolerant implementiert.
#
# Der Thread läuft dauerhaft, bis das Programm beendet wird.
# ------------------------------------------------------------------------------

def _gps_loop():
    global history, _last_fix, _last_sync, _last_valid_avg, _sensor_ok
    try:
        # Öffnet die serielle Schnittstelle zum GPS-Empfänger.
        # Falls der USB-Port beim Programmstart noch nicht verfügbar ist,
        # wird dies hier abgefangen und das Modul beendet sich sauber.
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    except serial.SerialException as e:
        # Startfehler wie vorher: nur melden und beenden
        print(f"[GPS] Fehler beim aeffnen von {SERIAL_PORT}: {e}")
        return

    last_print = datetime.now(timezone.utc)

    try:
        while True:
            try:
                # Liest eine einzelne NMEA-Zeile.
                # errors='ignore' verhindert Abstürze bei beschädigten Bytes.
                line = ser.readline().decode('ascii', errors='ignore').strip()
            except serial.SerialException as e:
                # Wie bisher melden:
                print(f"[GPS] Serial-Fehler: {e}, reconnect...")
                # Sensor fehlerhaft markieren -> RAW=0, AVG bleibt letzter gültiger
                with _lock:
                    _sensor_ok = False

                # Verbindung sauber schließen
                try:
                    ser.close()
                except Exception:
                    pass

                # Robust reconnecten bis Port wieder da ist (keine extra Ausgaben)
                while True:
                    try:
                        ser.open()
                        break
                    except serial.SerialException:
                        time.sleep(1)
                        # Falls das Serial-Objekt 'closed' ist und open() nicht geht, neu anlegen:
                        try:
                            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
                            break
                        except serial.SerialException:
                            time.sleep(1)
                continue

            if not line:
                continue

            try:
                # pynmea2 verarbeitet alle gängigen NMEA-Sätze (RMC, GGA, VTG usw.).
                # Fehlerhafte Zeilen werden ignoriert (GPS kann bei schlechtem Empfang Müll senden).
                msg = pynmea2.parse(line)
            except pynmea2.ParseError:
                # Parser-Fehler: Sensorzustand unbekannt lassen (kein Hard-Fail)
                continue

            lat, lon, alt, gps_time = None, None, None, None

            if isinstance(msg, (pynmea2.GGA, pynmea2.RMC)):
                lat = msg.latitude
                lon = msg.longitude
                alt = getattr(msg, "altitude", None)

            if isinstance(msg, pynmea2.RMC):
                if msg.status == 'A' and msg.datestamp and msg.timestamp:
                    gps_time = datetime.combine(msg.datestamp, msg.timestamp).replace(tzinfo=timezone.utc)

            # Validität prüfen: nur echte Fixe in History lassen
            if lat is None or lon is None or lat == 0.0 or lon == 0.0:
                # Kein Fix -> als Fehlerzustand behandeln: RAW=0, AVG unangetastet
                with _lock:
                    _sensor_ok = False
                continue

            # Ab hier: gültige Daten → Sensor OK
            # Die History wird auf exakt die letzten 5 Stunden begrenzt.
            # Alte Daten werden „herausgeschnitten“, damit die Mittelung stabil bleibt
            # und nicht durch historische Werte beeinflusst wird.
            now = datetime.now(timezone.utc)
            # gültige Werte hinzufügen
            if lat not in (0, None) and lon not in (0, None):
                history_lat.append(lat)
                history_lon.append(lon)

            if alt not in (0, None):
                history_alt_gps.append(alt)


            with _lock:
                _sensor_ok = True
                

            # Debugausgabe
            if DEBUG and (datetime.now(timezone.utc) - last_print).total_seconds() >= UPDATE_INTERVAL:
                msg_txt = (
                    f"AVG: lat={avg(history_lat)}, lon={avg(history_lon)}, alt={avg(history_alt_gps)}\n"
                    f"GPS Time: {gps_time if gps_time else 'N/A'}\n"
                    f"RAW: lat={lat}, lon={lon}, alt={alt}\n"
                    + "-"*40
                )
                print(msg_txt)
                last_print = datetime.now(timezone.utc)

            # Systemzeit
            if gps_time and (_last_sync is None or (datetime.now(timezone.utc) - _last_sync).total_seconds() > TIME_SYNC_INTERVAL):
                set_system_time_from_gps(gps_time)
                _last_sync = datetime.now(timezone.utc)

    except KeyboardInterrupt:
        pass
    finally:
        ser.close()

# ======= Thread-safe Funktion =======
# ------------------------------------------------------------------------------
# get_gps_data()
# ------------------------------------------------------------------------------
# Liefert die gemittelten GPS-Daten der letzten 5 Stunden.
#
# Falls das GPS-Modul aktuell keine gueltigen Daten liefert (kein Fix,
# USB-Disconnect o.Ae.), wird der letzte gueltige Durchschnitt zurueckgegeben.
#
# Verwendung:
#   - Trigger/Foto-Header
#   - Webinterface
# ------------------------------------------------------------------------------

def get_gps_data():
    with _lock:
        lat = avg(history_lat)
        lon = avg(history_lon)
        alt = avg(history_alt_gps)

        return {
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "timestamp": None,
            "samples_lat": len(history_lat),
            "samples_lon": len(history_lon),
            "samples_alt_gps": len(history_alt_gps),
        }


    
    
# ------------------------------------------------------------------------------
# get_gps_raw()
# ------------------------------------------------------------------------------
# Gibt die zuletzt gelesenen GPS-Daten direkt (ohne Mittelung) zurück.
#
# Dies ist nuetzlich zur Fehlersuche (z.B. im Webinterface),
# da hier sichtbar ist, ob der GPS-Empfänger aktuell valide Daten liefert.
#
# Bei Fehlern  Rueckgabe von 0.0 / None
# ------------------------------------------------------------------------------


def avg(dq):
    return sum(dq) / len(dq) if dq else None


def get_gps_raw():
    global _last_valid_raw

    with _lock:
        if _sensor_ok and history_lat:
            lat = history_lat[-1]
            lon = history_lon[-1]
            alt = history_alt_gps[-1] if history_alt_gps else 0
            ts = datetime.now().isoformat()

            _last_valid_raw = {
                "lat": lat,
                "lon": lon,
                "alt": alt,
                "timestamp": ts,
            }
            return dict(_last_valid_raw)

        # GPS hat kurz keinen Fix → letzten gültigen Wert behalten
        if _last_valid_raw["lat"] is not None:
            return dict(_last_valid_raw)

        # wenn noch nie ein gültiger Wert existierte:
        return {"lat": 0.0, "lon": 0.0, "alt": 0.0, "timestamp": None}



# ======= Start NTP-Server falls aktiviert =======
if ENABLE_NTP_SERVER:
    start_ntp_server()

# ======= Thread starten =======
_thread = threading.Thread(target=_gps_loop, daemon=True)
_thread.start()

# ======= Direktlauf faer Test/Debug =======
if __name__ == "__main__":
    print("[GPS] gps_reader laeuft. CTRL+C zum Beenden.")
    try:
        while True:
            data = get_gps_data()
            print(f"[GPS] Aktueller GPS-Mittelwert: {data}")
            time.sleep(UPDATE_INTERVAL)
    except KeyboardInterrupt:
        print("[GPS] Beendet.")
