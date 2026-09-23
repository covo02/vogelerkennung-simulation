# ------------------------------------------------------------------------------
# BH1750.py – I2C-Umgebungslichtsensor
#
# Dieses Modul ermöglicht das komfortable Auslesen des BH1750-Lichtsensors über
# den I2C-Bus des Raspberry Pi. Der BH1750 misst die Beleuchtungsstärke in Lux
# (lx) und ist für präzise Messungen bei Tageslicht wie auch bei künstlichem
# Licht geeignet.
#
# Eigenschaften des BH1750:
# - 16-bit Auflösung
# - Kontinuierlicher High-Resolution-Modus: 1 lx Genauigkeit
# - Sehr geringe Stromaufnahme
# - Direkte Ausgabe des Messwerts ohne weitere Kalibrierung
#
# Das Modul ist robust geschrieben:
# - Im Fehlerfall wird None zurückgegeben statt eine Exception auszulösen.
# - Dadurch kann der restliche Code (Trigger, Webserver) unverändert weiterlaufen.
#
# Dieses Modul wird u.a. von `trigger.py` und `orientation_web.py` verwendet,
# um aktuelle Beleuchtungsdaten in Header bzw. Weboberfläche einzubinden.

# Autoren:              Jonas Wigge, Jens Kuper
# Erstellungszeitraum:  20.11.2025
# Status:               Final
# Freigabe:             Ja
# ------------------------------------------------------------------------------


import smbus

# I2C initialisieren
bus = smbus.SMBus(3)
BH1750_ADDR = 0x23
CONTINUOUS_HIGH_RES_MODE = 0x10


# ------------------------------------------------------------------------------
# get_illumination()
# ------------------------------------------------------------------------------
# Liest die aktuelle Beleuchtungsstärke in Lux (lx) vom BH1750 aus.
#
# Ablauf:
#   1. Der Sensor wird mit CONTINUOUS_HIGH_RES_MODE angesprochen.
#   2. Der BH1750 sendet zwei Bytes zurück (High, Low Byte).
#   3. Der Rohwert (0..65535) wird mit dem festen Faktor 1.2 skaliert.
#
# Rückgabewert:
#   - lux (float) → Lichtstärke in Lux, gerundet auf zwei Nachkommastellen
#   - None → wenn ein I2C-Fehler auftritt oder der Sensor nicht antwortet
#
# Hinweis:
# Der Faktor „1.2“ stammt aus dem offiziellen BH1750-Datenblatt und entspricht:
#   LUX = RAW / 1.2
#
# Dieser Wert korrigiert die interne Verstärkung des Sensors.
# ------------------------------------------------------------------------------

def get_illumination():
    try:
        data = bus.read_i2c_block_data(BH1750_ADDR, CONTINUOUS_HIGH_RES_MODE, 2)
        raw = (data[0] << 8) | data[1]
        lux = raw / 1.2
        return round(lux, 2)
    except Exception as e:
        print("[BH1750]: Fehler beim auslesen:", e)
        return None
