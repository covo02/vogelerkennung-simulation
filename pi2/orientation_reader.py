# ------------------------------------------------------------------------------
# orientation_reader.py - Orientierung vom ESP32/BNO085 ueber I2C
#
# Autoren:              Jonas Wigge, Jens Kuper
# Erstellungszeitraum:  20.11.2025
# Status:               Final
# Freigabe:             Ja
#
# Dieses Modul liest Orientierungsdaten vom ESP32 aus, der wiederum den BNO085
# IMU-Sensor verarbeitet. Die Kommunikation zwischen Raspberry Pi und ESP32
# erfolgt ueber I2C, wobei der ESP32 als I2C-Slave (Adresse 0x42) fungiert und
# CSV-formatierte Datensaetze sendet.
#
# Hauptfunktionen dieses Moduls:
#   - Lesen von Yaw, Pitch, Roll + Accuracy vom ESP32
#   - Bildung eines stabilen Mittelwerts ueber ORI_SAMPLE_COUNT Samples
#   - Thread-sichere Bereitstellung der Werte fuer Webinterface & Trigger-System
#   - Automatische Fehlererkennung mit Reset des I2C-Handles
#
# Gesamtarchitektur:
#   ESP32 (BNO085 + Quaternion -> Euler)  -> I2C ->  Raspberry Pi
#
# Dieses Modul laeuft vollstaendig im Hintergrundthread und kann jederzeit
# abgefragt werden, ohne dass Messungen unterbrochen werden.
#
#   
#
# Diese Version ist von dem 07-03-2026 Ueberarbeitet von Leon Key
#
#
#
# ------------------------------------------------------------------------------


import time
import threading
import math
from datetime import datetime, timezone
from smbus2 import SMBus



# ======= Configuration =======
ORI_DEBUG = True            # Konsolenausgabe aktivieren
ORI_SAMPLE_COUNT = 200      # Anzahl IMU-Messungen fuer Mittelwertbildung
I2C_ADDR = 0x42     
ERROR_THRESHOLD = 5         # Anzahl der gedulten fehlgeschlagenen Leseversuche an I2C

# ======= Internal state =======
# _last_orientation:
#   Speichert die zuletzt gemittelten Winkel (Yaw, Pitch, Roll) sowie Zusatzinfos:
#     - timestamp: ISO-UTC Zeitstring
#     - source: immer "ESP32_I2C"
#     - accuracy: vom BNO085 (0-3)
#     - sample_count: wie viele Samples bisher gesammelt wurden
#
# Die weiteren Variablen (_sum_yaw etc.) dienen dazu, die Messreihe
# aufzusummieren, bevor ein Durchschnitt berechnet wird.
#
# _measurement_active:
#   True, sobald eine neue Messreihe laeuft (nach reset_orientation_measurement())
#
# _orientation_ready:
#   True, sobald alle Samples gesammelt wurden und ein finaler Mittelwert vorliegt.

_last_orientation = {
    "yaw": None,
    "pitch": None,
    "roll": None,
    "timestamp": None,
    "source": "ESP32_I2C",
    "accuracy": None,
    "sample_count": 0,
}

_sum_yaw = 0.0
_sum_pitch = 0.0
_sum_roll = 0.0
_sum_acc = 0.0
_sample_count = 0
_sum_yaw_sin = 0.0
_sum_yaw_cos = 0.0

_measurement_active = False
_orientation_ready = False

# ======= Zero offset (Set Zero) =======
_zero_set = False
_zero_yaw = 0.0
_zero_pitch = 0.0
_zero_roll = 0.0

def _wrap_deg(a: float) -> float:
    # [-180, 180)
    return (a + 180.0) % 360.0 - 180.0

def _apply_zero(yaw: float, pitch: float, roll: float):
    if not _zero_set:
        return yaw, pitch, roll
    return _wrap_deg(yaw - _zero_yaw), (pitch - _zero_pitch), (roll - _zero_roll)

def set_zero_now() -> bool:
    """
    Sets current orientation as zero reference.
    Returns True if successful.
    """
    global _zero_set, _zero_yaw, _zero_pitch, _zero_roll
    with _ori_lock:
        y = _last_orientation.get("yaw_raw")
        p = _last_orientation.get("pitch_raw")
        r = _last_orientation.get("roll_raw")
        if y is None or p is None or r is None:
            return False
        _zero_yaw = float(y)
        _zero_pitch = float(p)
        _zero_roll = float(r)
        _zero_set = True
        return True



_ori_lock = threading.Lock()

_error_count = 0

# I2C handle
I2C_BUS = 3
I2C_ADDR = 0x42

_i2c = None


# ------------------------------------------------------------------------------
# _read_esp32_values()
# ------------------------------------------------------------------------------
# Liest die vom ESP32 gesendeten Orientierungsmesswerte ueber I2C.
#
# Format des vom ESP32 gelieferten Textes:
#   "yaw,pitch,roll,accuracy,calibrationState"
#
# Die Werte sind jeweils:
#   yaw  -> Kompassrichtung in Grad (0-360)
#   pitch/roll -> Neigung in Grad
#   accuracy  -> Magnetometerkalibrierung (0-3)
#   cal -> 1 = vollstaendig kalibriert, 0 = nicht kalibriert
#
# Ablauf:
#   - Es wird ein 64 Byte grosser Buffer vom ESP32 gelesen.
#   - Nullbytes werden entfernt.
#   - Der Text wird in CSV-Werte zerlegt.
#
# Rueckgabe:
#   (yaw, pitch, roll, accuracy, cal)
#
# Fehlerfall:
#   None -> beim naechsten Loop-Durchlauf erneut versuchen.
# ------------------------------------------------------------------------------

def _read_esp32_values():
    global _i2c

    try:
        if _i2c is None:
            _i2c = SMBus(I2C_BUS)

        data = _i2c.read_i2c_block_data(I2C_ADDR, 0x00, 32)

        text = bytes(data).decode("utf-8", errors="ignore").strip("\x00").strip()

        if not text:
            return None

        parts = text.split(",")

        if len(parts) < 5:
            return None

        yaw = float(parts[0])
        pitch = float(parts[1])
        roll = float(parts[2])
        acc = float(parts[3])
        cal = int(parts[4])

        return yaw, pitch, roll, acc, cal

    except Exception as e:
        return None


# ------------------------------------------------------------------------------
# reset_orientation_measurement()
# ------------------------------------------------------------------------------
# Startet eine neue Messreihe.
#
# Dabei werden:
#   - alle Summenvariablen zurueckgesetzt
#   - sample_count = 0
#   - Mittelwertstatus = noch nicht bereit
#
# Der Hintergrundthread beginnt sofort wieder neue Samples zu sammeln.
# Diese Funktion wird vom Webinterface ("/reset") ausgeloest.
# ------------------------------------------------------------------------------

def reset_orientation_measurement():
    global _sum_yaw, _sum_pitch, _sum_roll, _sum_acc, _sample_count
    global _sum_yaw_sin, _sum_yaw_cos
    global _measurement_active, _orientation_ready

    with _ori_lock:
        _sum_yaw = 0.0
        _sum_pitch = 0.0
        _sum_roll = 0.0
        _sum_acc = 0.0
        _sample_count = 0
        _sum_yaw_sin = 0.0
        _sum_yaw_cos = 0.0
        _measurement_active = True
        _orientation_ready = False

        _last_orientation.update({
            "yaw": None,
            "pitch": None,
            "roll": None,
            "acc": None,
            "timestamp": None,
            "sample_count": 0,
        })

    if ORI_DEBUG:
        print("[ORI] Measurement reset. Collecting {} samples...".format(ORI_SAMPLE_COUNT))

# Gibt die zuletzt verfuegbaren Orientierungsdaten zurueck.
# Diese Funktion ist thread-sicher (durch Lock geschuetzt).
def get_orientation_data():
    with _ori_lock:
        return dict(_last_orientation)

# Alias-Wrapper fuer get_orientation_data(), nur zur API-Vollstaendigkeit.
def get_orientation_raw():
    return get_orientation_data()


# ------------------------------------------------------------------------------
# _orientation_loop()
# ------------------------------------------------------------------------------
# Hintergrundthread, der kontinuierlich:
#   - Werte ueber I2C vom ESP32 liest
#   - Fehler zaehlt & I2C-Reset durchfuehrt
#   - Mittelwert von Yaw/Pitch/Roll bildet
#   - den finalen Mittelwert in _last_orientation speichert
#
# Ablauf:
#   1. Messreihe aktiv? Wenn nein -> warten
#   2. Versuch, neue Werte vom ESP32 zu lesen
#   3. Bei Fehlern:
#        - ERROR_THRESHOLD ueberschritten? -> I2C resetten
#   4. Werte aufaddieren, sample_count erhoehen
#   5. Wenn sample_count >= ORI_SAMPLE_COUNT:
#        - Mittelwerte berechnen
#        - Orientierung als „ready“ markieren
# ------------------------------------------------------------------------------

def _orientation_loop():
    global _error_count
    global _sum_yaw_sin, _sum_yaw_cos, _sum_pitch, _sum_roll, _sample_count
    global _zero_set, _zero_yaw, _zero_pitch, _zero_roll
    global _measurement_active, _orientation_ready, _last_orientation

    while True:

        if not _measurement_active:
            time.sleep(0.1)
            continue

        values = _read_esp32_values()

        if values is None:
            _error_count += 1
            if ORI_DEBUG:
                print("[ORI] I2C read error", _error_count)
            if _error_count >= ERROR_THRESHOLD:
                if ORI_DEBUG:
                    print("[ORI] Too many I2C errors, resetting I2C bus...")
                try:
                    time.sleep(0.5)
                except:
                    pass
                _error_count = 0
            time.sleep(0.05)
            continue

        yaw, pitch, roll, acc, cal = values
        yaw_raw = float(yaw)
        pitch_raw = float(pitch)
        roll_raw = float(roll)
        _error_count = 0

        now = datetime.now(timezone.utc).isoformat()

        with _ori_lock:

            if _measurement_active and not _orientation_ready:
                rad = math.radians(float(yaw))
                _sum_yaw_sin += math.sin(rad)
                _sum_yaw_cos += math.cos(rad)
                _sum_pitch += float(pitch)
                _sum_roll += float(roll)
                _sample_count += 1

                # APPLY ZERO for live values shown in the UI
                yaw_adj, pitch_adj, roll_adj = _apply_zero(float(yaw), float(pitch), float(roll))

                _last_orientation.update({
                    "yaw": yaw_adj,
                    "pitch": pitch_adj,
                    "roll": roll_adj,
                    "yaw_raw": yaw_raw,
                    "pitch_raw": pitch_raw,
                    "roll_raw": roll_raw,
                    "timestamp": now,
                    "source": "ESP32_I2C",
                    "accuracy": acc,
                    "sample_count": _sample_count,
                })

                if _sample_count >= ORI_SAMPLE_COUNT:

                    avg_yaw = (math.degrees(math.atan2(_sum_yaw_sin, _sum_yaw_cos)) + 360.0) % 360.0
                    avg_pitch = _sum_pitch / _sample_count
                    avg_roll = _sum_roll / _sample_count

                    # APPLY ZERO for final averaged values (the ones you freeze)
                    avg_yaw, avg_pitch, avg_roll = _apply_zero(float(avg_yaw), float(avg_pitch), float(avg_roll))

                    _last_orientation.update({
                        "yaw": avg_yaw,
                        "pitch": avg_pitch,
                        "roll": avg_roll,
                        "yaw_raw": yaw_raw,
                        "pitch_raw": pitch_raw,
                        "roll_raw": roll_raw,
                        "acc": acc,
                        "timestamp": now,
                        "sample_count": _sample_count,
                    })

                    _orientation_ready = True
                    _measurement_active = False

                    if ORI_DEBUG:
                        print(
                            "[ORI] Averaging done ({} samples): yaw={:.2f}, pitch={:.2f}, roll={:.2f}, acc={:.2f}"
                            .format(_sample_count, avg_yaw, avg_pitch, avg_roll, acc)
                        )

        time.sleep(0.05)

# start thread
_thread = threading.Thread(target=_orientation_loop, daemon=True)
_thread.start()
