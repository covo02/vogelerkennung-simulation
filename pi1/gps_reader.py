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
# Autoren:              Jonas Wigge, Jens Kuper, Leon Key
# Erstellungszeitraum:  20.11.2025
# Status:               Final
# Freigabe:             Ja
# ------------------------------------------------------------------------------
# Aenderung vom 03.03.2026:
# - Liefert RAW + AVG wie vorher
# - Thread startet bei Import
# - Quelle: gpsd (localhost:2947) statt /dev/ttyAMA0
# - Bewusst kompatibel gehalten fuer Trigger/Header/Webinterface
# ------------------------------------------------------------------------------

import time
import socket 
import threading
from datetime import datetime, timezone
from collections import deque

# ======= Konfiguration =======
SAMPLE_WINDOW_SIZE = 200          # Anzahl Samples für Mittelung
UPDATE_INTERVAL = 5               # Sekunden für Debugausgabe
DEBUG = False                      # Debugprint
ENABLE_NTP_SERVER = False         # bleibt als Flag, Funktion existiert noch (unused)

# ======= History / Zustand =======
history_lat = deque(maxlen=SAMPLE_WINDOW_SIZE)
history_lon = deque(maxlen=SAMPLE_WINDOW_SIZE)
history_alt_gps = deque(maxlen=SAMPLE_WINDOW_SIZE)

_sensor_ok = False
_lock = threading.Lock()

_last_valid_raw = {"lat": None, "lon": None, "alt": None, "timestamp": None}

# optional: zuletzt vom GPS gemeldete Zeit (nicht Systemzeit)
_last_gps_time_iso = None


def avg(dq):
    return sum(dq) / len(dq) if dq else None


def set_system_time_from_gps(gps_datetime):
    # Zeit wird nicht mehr durch Python gesetzt
    return


def start_ntp_server():
    # Alt-Funktion bewusst drin gelassen, aber in deinem Setup nutzt du chrony sauber.
    return


def _iso_now_utc():
    return datetime.now(timezone.utc).isoformat()


def _gps_loop():
    """
    Liest kontinuierlich TPV Reports aus gpsd.
    Updates:
      - history_lat/lon/alt_gps (AVG)
      - _last_valid_raw (RAW)
      - _sensor_ok
    """
    global _sensor_ok, _last_valid_raw, _last_gps_time_iso

    try:
        import gps  # python3-gps
    except Exception as e:
        print(f"[GPS] FEHLER: python gps module fehlt oder kaputt: {e}")
        print("[GPS] Install: sudo apt install -y python3-gps gpsd gpsd-clients")
        return

    # gpsd Session
    try:
        session = gps.gps(mode=gps.WATCH_ENABLE | gps.WATCH_NEWSTYLE)
        try:
            session.sock.settimeout(1.0)  # verhindert "ewig blockieren"
        except Exception:
            pass
    except Exception as e:
        print(f"[GPS] FEHLER: kann gpsd nicht öffnen (läuft gpsd?): {e}")
        _sensor_ok = False
        return

    last_print = time.time()

    while True:
        try:
            report = session.next()
        except socket.timeout:
            with _lock:
                _sensor_ok = False
            time.sleep(0.05)
            continue
        except Exception:
            with _lock:
                _sensor_ok = False
            time.sleep(0.2)
            continue

        # Wir interessieren uns primär für TPV
        if getattr(report, "class", None) != "TPV":
            time.sleep(0.02)
            continue

        # gpsd liefert mode: 0/1/2/3
        mode = getattr(report, "mode", 0)

        lat = getattr(report, "lat", None)
        lon = getattr(report, "lon", None)

        # alt: gpsd kann alt (meist MSL) liefern; altMSL gibt’s nicht immer im python wrapper
        alt = getattr(report, "alt", None)
        if alt is None:
            alt = 0.0

        # GPS time (aus gpsd), nicht Systemzeit
        gps_time = getattr(report, "time", None)  # meist ISO string "....Z"
        if gps_time:
            _last_gps_time_iso = str(gps_time)

        # Validität prüfen wie vorher (kein Fix / 0/0 rausfiltern)
        valid_fix = (mode >= 2) and (lat is not None) and (lon is not None) and (lat != 0.0) and (lon != 0.0)

        with _lock:
            if valid_fix:
                history_lat.append(float(lat))
                history_lon.append(float(lon))
                if alt not in (None, 0, 0.0):
                    history_alt_gps.append(float(alt))

                _sensor_ok = True

                _last_valid_raw = {
                    "lat": float(lat),
                    "lon": float(lon),
                    "alt": float(alt) if alt is not None else 0.0,
                    "timestamp": _iso_now_utc(),
                }
            else:
                _sensor_ok = False

        # Debugausgabe (ähnlich alt)
        if DEBUG and (time.time() - last_print) >= UPDATE_INTERVAL:
            with _lock:
                msg_txt = (
                    f"AVG: lat={avg(history_lat)}, lon={avg(history_lon)}, alt={avg(history_alt_gps)}\n"
                    f"GPS Time: {_last_gps_time_iso if _last_gps_time_iso else 'N/A'}\n"
                    f"RAW: lat={lat}, lon={lon}, alt={alt}, mode={mode}\n"
                    + "-" * 40
                )
            print(msg_txt)
            last_print = time.time()

        time.sleep(0.05)


def get_gps_data():
    """
    Wie vorher: liefert gemittelte Daten + Sample-Zähler.
    """
    with _lock:
        lat = avg(history_lat)
        lon = avg(history_lon)
        alt = avg(history_alt_gps)

        # Fallback: wenn noch keine Samples, nutze last_valid_raw oder 0.0
        if lat is None or lon is None:
            if _last_valid_raw["lat"] is not None and _last_valid_raw["lon"] is not None:
                lat = _last_valid_raw["lat"]
                lon = _last_valid_raw["lon"]
            else:
                lat, lon = 0.0, 0.0

        if alt is None:
            if _last_valid_raw["alt"] is not None:
                alt = _last_valid_raw["alt"]
            else:
                alt = 0.0

        return {
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "timestamp": None,  # bewusst wie vorher
            "samples_lat": len(history_lat),
            "samples_lon": len(history_lon),
            "samples_alt_gps": len(history_alt_gps),
        }


def get_gps_raw():
    """
    Wie vorher:
    - Wenn Sensor OK: letzter Punkt
    - Wenn nicht OK: letzter gültiger Punkt (Fallback)
    - Wenn nie gültig: 0/0/0
    """
    with _lock:
        if _sensor_ok and _last_valid_raw["lat"] is not None:
            return dict(_last_valid_raw)

        if _last_valid_raw["lat"] is not None:
            return dict(_last_valid_raw)

        return {"lat": 0.0, "lon": 0.0, "alt": 0.0, "timestamp": None}


# ======= optional: Start NTP-Server falls aktiviert (bei dir i.d.R. AUS) =======
if ENABLE_NTP_SERVER:
    start_ntp_server()

# ======= Thread starten =======
_thread = threading.Thread(target=_gps_loop, daemon=True)
_thread.start()

# ======= Direktlauf für Test/Debug =======
if __name__ == "__main__":
    print("[GPS] gps_reader läuft (gpsd). CTRL+C zum Beenden.")
    try:
        while True:
            print(f"[GPS] AVG: {get_gps_data()}")
            print(f"[GPS] RAW: {get_gps_raw()}")
            time.sleep(UPDATE_INTERVAL)
    except KeyboardInterrupt:
        print("[GPS] Beendet.")