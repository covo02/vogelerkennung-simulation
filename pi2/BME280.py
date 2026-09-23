# ------------------------------------------------------------------------------
# BME280.py – Temperatur-, Luftdruck- und Feuchtigkeitssensor (I2C)
#
# Autoren:              Jonas Wigge, Jens Kuper
# Erstellungszeitraum:  20.11.2025
# Status:               Final
# Freigabe:             Ja
#
# Dieses Modul kapselt die komplette Kommunikation mit dem BME280-Sensor über
# den I2C-Bus. Der BME280 misst:
#   - Temperatur (°C)
#   - Luftdruck (hPa)
#   - relative Luftfeuchtigkeit (%)
#
# Der Sensor muss über Hersteller-Kalibrierwerte kompensiert werden, die beim
# Start einmalig ausgelesen werden. Die Formeln zur Berechnung der realen
# Messwerte entsprechen exakt der Bosch-BME280-Referenzimplementierung.
#
# Besonderheiten dieses Moduls:
# - Automatischer Scan nach möglichen I2C-Adressen (0x76 und 0x77)
# - Robustes Fehlerhandling (None-Werte statt Absturz)
# - Schutz gegen Messfehler und Zeitüberschreitungen
# - Hohe Genauigkeit durch vollständige Implementierung aller Bosch-Formeln
#
# Dieses Modul wird in:
#   - orientation_web.py (Umweltanzeige)
#   - trigger.py (Header-Daten im Kamerasystem)
# verwendet.
# ------------------------------------------------------------------------------


import smbus2
import time

bus = smbus2.SMBus(3)		    # i2c-1 ist tot deswegen i2c-3

BME280_ADDRS = [0x76, 0x77]         # Adresse ist Platinenlayout abhängig
BME280_ADDR = None
SENSOR_AVAILABLE = False

# 1) Sensor suchen
# Der BME280 besitzt eine feste Chip-ID (0x60), die im Register 0xD0 steht.
# Der Code versucht, bei beiden möglichen Adressen zu lesen.
# Falls eine Adresse antwortet und die ID stimmt → Sensor gefunden.
#
# Fehler (I2C-Zeitüberschreitungen, keine Antwort) werden ignoriert,
# da diese nur bedeuten, dass an dieser Adresse kein Sensor sitzt.
for addr in BME280_ADDRS:
    try:
        chip_id = bus.read_byte_data(addr, 0xD0)
        if chip_id == 0x60:
            BME280_ADDR = addr
            SENSOR_AVAILABLE = True
            break
    except:
        pass

# Wenn kein Sensor vorhanden ist:
#   - wird ein Hinweis ausgegeben
#   - Kalibrierdaten bleiben auf None
#   - Alle Messfunktionen geben später None zurück
#
# Dies verhindert Abstürze und erlaubt den Betrieb auch ohne BME280.
if not SENSOR_AVAILABLE:
    print("[BME280]: Kein BME280 gefunden, sende Null-Werte.")
    # Wichtig: Kalibrierdaten auf None lassen
    CAL_T = None
    CAL_P = None
    CAL_H = None
else:
    # 2) Kalibrierung NUR lesen wenn Sensor vorhanden
    # ------------------------------------------------------------------------------
    # read_calibration()
    # ------------------------------------------------------------------------------
    # Der BME280 benötigt umfangreiche, chipinterne Kalibrierdaten,
    # um reale Messwerte aus Rohdaten berechnen zu können.
    #
    # Diese Funktion:
    #   - liest die Temperatur-, Druck- und Feuchte-Kalibrierblöcke aus den
    #     Registern 0x88–0xA1 sowie 0xE1–0xE7
    #   - setzt sie korrekt zusammen (teilweise bitverschränkt)
    #   - gibt drei Blöcke zurück:
    #       CAL_T → (dig_T1, dig_T2, dig_T3)
    #       CAL_P → [dig_P1 ... dig_P9]
    #       CAL_H → (dig_H1 ... dig_H6)
    #
    # Falls beim Lesen ein Fehler auftritt → Rückgabe (None, None, None)
    #
    # Wichtig:
    #   Die Bitoperationen entsprechen exakt dem Bosch-Datenblatt.
    # ------------------------------------------------------------------------------
    def read_calibration():
        try:
            calib = bus.read_i2c_block_data(BME280_ADDR, 0x88, 24)
            h1 = bus.read_byte_data(BME280_ADDR, 0xA1)
            hcal = bus.read_i2c_block_data(BME280_ADDR, 0xE1, 7)
        except:
            return None, None, None

        dig_T1 = calib[1] << 8 | calib[0]
        dig_T2 = (calib[3] << 8) | calib[2]
        dig_T3 = (calib[5] << 8) | calib[4]

        dig_P = []
        for i in range(6, 24, 2):
            dig_P.append((calib[i+1] << 8) | calib[i])
        # Die Temperatur- und Druckkalibrierung besteht aus 26 Bytes im Block 0x88–0xA1.
        # Die Feuchtekalibrierung verteilt sich auf mehrere Register → einzeln lesen.
        dig_H1 = h1
        dig_H2 = (hcal[1] << 8) | hcal[0]
        dig_H3 = hcal[2]
        dig_H4 = (hcal[3] << 4) | (hcal[4] & 0x0F)
        dig_H5 = (hcal[5] << 4) | (hcal[4] >> 4)
        dig_H6 = hcal[6]

        return (dig_T1, dig_T2, dig_T3), dig_P, (dig_H1, dig_H2, dig_H3, dig_H4, dig_H5, dig_H6)

    CAL_T, CAL_P, CAL_H = read_calibration()


# 3) Rohdaten lesen
# ------------------------------------------------------------------------------
# read_raw()
# ------------------------------------------------------------------------------
# Startet eine vollständige Einzelmessung des BME280 und liest die Rohregister aus.
#
# Ablauf:
#   1. Feuchtemessung aktivieren   (Register 0xF2)
#   2. Temperatur/Druck starten    (Register 0xF4)
#   3. kurz warten bis Messung fertig (ca. 40–50 ms)
#   4. Registerblock 0xF7–0xFE lesen (8 Bytes)
#
# Rückgabe:
#   (temp_raw, press_raw, hum_raw)
#
# Fehlerfall:
#   Rückgabe (None, None, None)
#
# Hinweis:
#   Der BME280 liefert für Temperatur & Druck jeweils 20-Bit Messwerte.
# ------------------------------------------------------------------------------
def read_raw():
    if not SENSOR_AVAILABLE or BME280_ADDR is None:
        return None, None, None

    start = time.time()
    while True:
        try:
            # Leseversuche werden für maximal 0.1 Sekunden wiederholt.
            # Hintergrund: Der BME280 kann gelegentlich kurze Busy-Phasen haben.
            # Nach Timeout → Abbruch ohne Absturz.
            bus.write_byte_data(BME280_ADDR, 0xF2, 0x01)
            bus.write_byte_data(BME280_ADDR, 0xF4, 0x27)
            time.sleep(0.05)

            data = bus.read_i2c_block_data(BME280_ADDR, 0xF7, 8)

            press_raw = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
            temp_raw  = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
            hum_raw   = (data[6] << 8) | data[7]

            return temp_raw, press_raw, hum_raw

        except:
            if time.time() - start > 0.1:
                return None, None, None
            time.sleep(0.01)


# 4) Temperatur
# ------------------------------------------------------------------------------
# get_temperature()
# ------------------------------------------------------------------------------
# Berechnet die Temperatur in °C aus dem Rohwert temp_raw.
#
# Formel:
#   Direkt aus dem offiziellen Bosch-Datenblatt entnommen.
#
# Schritte:
#   1. Temperaturrohwert lesen
#   2. t_fine berechnen (wichtiger Zwischenwert)
#   3. finale Temperatur bestimmen
#
# Rückgabe:
#   gerundete Temperatur (float) oder None.
# ------------------------------------------------------------------------------
def get_temperature():
    if not SENSOR_AVAILABLE or CAL_T is None:
        return None

    temp_raw, _, _ = read_raw()
    if temp_raw is None:
        return None

    dig_T1, dig_T2, dig_T3 = CAL_T

    var1 = (temp_raw / 16384.0 - dig_T1 / 1024.0) * dig_T2
    var2 = ((temp_raw / 131072.0 - dig_T1 / 8192.0) *
            (temp_raw / 131072.0 - dig_T1 / 8192.0)) * dig_T3

    t_fine = var1 + var2
    temperature = t_fine / 5120.0

    return round(temperature, 2)


# 5) Luftdruck
# ------------------------------------------------------------------------------
# get_pressure()
# ------------------------------------------------------------------------------
# Berechnet den Luftdruck in Hektopascal (hPa).
#
# Der BME280 gibt den Druck als 20-Bit Rohwert aus.
# Dieser Wert wird mit Hilfe der dig_P-Kalibrierdaten und dem zuvor
# berechneten t_fine Wert in realen Druck umgerechnet.
#
# Die Berechnung ist komplex und folgt der Bosch-Referenzimplementierung:
#   - mehrere Zwischenvariablen
#   - division protection (var1 == 0)
#   - finale Skalierung auf hPa
# ------------------------------------------------------------------------------
def get_pressure():
    if not SENSOR_AVAILABLE or CAL_T is None or CAL_P is None:
        return None

    temp_raw, press_raw, _ = read_raw()
    if temp_raw is None or press_raw is None:
        return None

    dig_T1, dig_T2, dig_T3 = CAL_T
    dig_P = CAL_P

    # var1 / var2 sind Zwischenschritte der Bosch-Kompensationsformel.
    # t_fine ist ein zentraler Korrekturwert, der auch bei Druck & Feuchte genutzt wird.
    var1 = (temp_raw / 16384.0 - dig_T1 / 1024.0) * dig_T2
    var2 = ((temp_raw / 131072.0 - dig_T1 / 8192.0) *
            (temp_raw / 131072.0 - dig_T1 / 8192.0)) * dig_T3
    t_fine = var1 + var2

    var1 = t_fine / 2.0 - 64000.0
    var2 = var1 * var1 * dig_P[5] / 32768.0
    var2 = var2 + var1 * dig_P[4] * 2
    var2 = var2 / 4.0 + dig_P[3] * 65536.0
    var1 = (dig_P[2] * var1 * var1 / 524288.0 +
            dig_P[1] * var1) / 524288.0
    var1 = (1.0 + var1 / 32768.0) * dig_P[0]

    if var1 == 0:
        # Sicherheitsabfrage: division by zero verhindern.
        # Falls var1 == 0 → Druck kann nicht berechnet werden.
        return None

    p = (1048576.0 - press_raw)
    p = (p - var2 / 4096.0) * 6250.0 / var1
    var1 = dig_P[8] * p * p / 2147483648.0
    var2 = p * dig_P[7] / 32768.0
    p = p + (var1 + var2 + dig_P[6]) / 16.0

    return round(p / 100.0, 2)


# 6) Feuchtigkeit
# ------------------------------------------------------------------------------
# get_humidity()
# ------------------------------------------------------------------------------
# Berechnet die relative Luftfeuchtigkeit in %.
#
# Ablauf:
#   1. Feuchte-Rohwert lesen
#   2. t_fine erneut berechnen (Temperaturabhängig!)
#   3. dig_H-Koeffizienten korrekt kombinieren
#   4. Ergebnisbereich auf 0–100% clampen
# ------------------------------------------------------------------------------
def get_humidity():
    if not SENSOR_AVAILABLE or CAL_H is None:
        return None

    temp_raw, _, hum_raw = read_raw()
    if temp_raw is None or hum_raw is None:
        return None

    dig_H1, dig_H2, dig_H3, dig_H4, dig_H5, dig_H6 = CAL_H
    dig_T1, dig_T2, dig_T3 = CAL_T

    var1 = (temp_raw / 16384.0 - dig_T1 / 1024.0) * dig_T2
    var2 = ((temp_raw / 131072.0 - dig_T1 / 8192.0) *
            (temp_raw / 131072.0 - dig_T1 / 8192.0)) * dig_T3
    t_fine = var1 + var2

    # var_h ist der nichtlineare Zwischenwert der Bosch-Kompensation.
    # Dieser Wert kann rechnerisch leicht über 100% oder unter 0% liegen.
    # Deshalb wird der Bereich am Ende begrenzt.
    var_h = t_fine - 76800.0
    var_h = (hum_raw - (dig_H4 * 64.0 + dig_H5 / 16384.0 * var_h)) * \
            (dig_H2 / 65536.0 *
             (1.0 + dig_H6 / 67108864.0 * var_h *
              (1.0 + dig_H3 / 67108864.0 * var_h)))

    var_h = var_h * (1.0 - dig_H1 * var_h / 524288.0)

    if var_h > 100.0:
        var_h = 100.0
    if var_h < 0.0:
        var_h = 0.0

    return round(var_h, 2)

# get_altitude_baro() --> Berechnet Barometrische Höhe über NN
def get_altitude_baro(sea_level_pressure_hpa=1013.25):
    # Berechnet Höhe über NN aus dem Luftdruck.
    # Formel: Internationale Höhenformel
    p = get_pressure()
    if p is None:
        return None

    # Höhe in Metern
    altitude = 44330.0 * (1.0 - (p / sea_level_pressure_hpa) ** (1/5.255))
    return round(altitude, 2)
