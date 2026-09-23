"""
Stand: 03.03.2026

Kamera Client fuer den Rover. Dieser Code hoert auf ein Multicast Trigger Signal
und macht danach zwei Bilder. Beide Bilder werden anschliessend ueber eine
einzige TCP Verbindung an den Server gesendet.

Kurze Info:
Die Uebertragung nutzt ein einfaches eigenes Protokoll (erst Laenge des Headers,
dann Header, dann Bilddaten). Das wurde so angepasst, dass es exakt zum Server
passt, damit beide Seiten sauber zusammenarbeiten.

Autor: Leon Key, Hannes Kroeger
"""

import socket
import json
import struct
import time
import threading
import hashlib
from datetime import datetime, timezone

import psutil
import cv2
from picamera2 import Picamera2

from BH1750 import get_illumination
from BME280 import get_temperature, get_pressure, get_humidity
from orientation_web import start_webserver, get_selected_gps, get_selected_orientation


# ---------------------------------------------------------------------------
# Grundeinstellungen fuer das Kamerasystem
# Hier legen wir fest, wohin gesendet wird, welche Aufloesung genutzt wird
# und wie sich das Timing verhalten soll.
# ---------------------------------------------------------------------------
MULTICAST_GROUP = "224.1.1.1"
TRIGGER_PORT = 5001

SERVER_IP = "192.168.178.5"
SERVER_PORT = 5000

CAMERA_ID = "pi3"
POSITION = {"x": 0.0, "y": 0.0, "z": 1.2}

RESOLUTION = (4056, 3040)  # (width, height)
CAPTURE_DELAY_S = 0.020    # zeit zwischen den beiden frames
CLOCK_SOURCE = "NTP"

# Netzwerk Timeouts, damit die Verbindungen nicht haengen bleiben
UDP_RECV_BYTES = 2048
TCP_CONNECT_TIMEOUT_S = 10.0
TCP_IO_TIMEOUT_S = 60.0

# JPEG einstellung fuer Qualitaet der bilder 
JPEG_QUALITY = 85  # kleiner = schneller -> 80 bei handleClientError auf webserver
ENCODE_PARAMS = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]

# Verhalten beim Warten auf den Trigger Zeitpunkt
COARSE_SLEEP_S = 0.05
FINE_SLEEP_S = 0.001
FINE_WINDOW_S = 0.20

SOFTWARE_VERSION = "0.9.2"
PROTOCOL_VERSION = "2.0"


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# Hier liegen kleinere Funktionen, die wir im Ablauf mehrmals brauchen.
# ---------------------------------------------------------------------------
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def wait_until(trigger_time_utc: datetime) -> None:
    # Wartet bis zum Trigger Zeitpunkt.
    while True:
        now = utc_now()
        dt = (trigger_time_utc - now).total_seconds()
        if dt <= 0:
            return
        time.sleep(COARSE_SLEEP_S if dt > FINE_WINDOW_S else FINE_SLEEP_S)


def send_frame_v2(sock: socket.socket, header: dict, img_bytes: bytes) -> None:
    # Schickt ein einzelnes Bild nach dem v2 Protokoll (zuerst Header, dann Bilddaten).
    header_bytes = json.dumps(header, ensure_ascii=False).encode("utf-8")
    sock.sendall(struct.pack("!I", len(header_bytes)))
    sock.sendall(header_bytes)
    sock.sendall(struct.pack("!I", len(img_bytes)))
    sock.sendall(img_bytes)


def build_base_header(trigger_time: datetime, event_id: str, gps: dict, orientation: dict,
                      lux, temp, press, hum, alt_mode: str) -> dict:
    # Baut den Grund Header fuer jedes Event zusammen (Sensorwerte, Zeit, Position usw.)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "software_version": SOFTWARE_VERSION,
        "clock_source": CLOCK_SOURCE,
        "trigger_time": trigger_time.isoformat(),
        "event_id": event_id,
        "camera_id": CAMERA_ID,
        "position": POSITION,
        "gps": gps,
        "orientation": orientation,
        "helligkeit": lux,
        "temperatur": temp,
        "luftdruck": press,
        "luftfeuchtigkeit": hum,
        "alt_mode": alt_mode,
        "resolution": [int(RESOLUTION[0]), int(RESOLUTION[1])],
        "system_uptime_s": round(time.time() - psutil.boot_time(), 1),
    }




def setup_multicast_socket() -> socket.socket:
    def _get_default_ip() -> str:
        tmp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Im Rover Netz: Verbindung zum Mini PC (bei Bedarf anpassen)
            tmp.connect(("192.168.178.5", 80))
            return tmp.getsockname()[0]
        finally:
            tmp.close()

    iface_ip = _get_default_ip()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", TRIGGER_PORT))

    mreq = socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton(iface_ip)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    return s

# Initialisiert die Kamera und waehlt die Aufloesung fuer Standbilder aus
def setup_camera() -> Picamera2:
    picam = Picamera2()
    cfg = picam.create_still_configuration(main={"size": RESOLUTION})
    picam.configure(cfg)
    picam.start()
    time.sleep(1.0)
    return picam


# ---------------------------------------------------------------------------
# Hauptablauf des Programms
# Hier wird der Webserver gestartet, auf Trigger gehoert und die Bilder
# aufgenommen und versendet.
# ---------------------------------------------------------------------------
def main() -> None:
    # Start webserver (altitude mode UI)
    web_thread = threading.Thread(target=start_webserver, daemon=True)
    web_thread.start()
    print("[WEB] Webserver gestartet auf http://<pi-ip>:8080")

    # Socket fuer Multicast Trigger einrichten (hier kommen die Ausloeser an)
    udp_sock = setup_multicast_socket()
    print(f"[{CAMERA_ID}] Warte auf Trigger auf {MULTICAST_GROUP}:{TRIGGER_PORT} ...")

    # Kamera initialisieren (Aufloesung und Konfiguration setzen)
    picam = setup_camera()
    print(f"[{CAMERA_ID}] Kamera aktiv ({RESOLUTION[0]}x{RESOLUTION[1]}) = bereit.")

    while True:
        data, _ = udp_sock.recvfrom(UDP_RECV_BYTES)
        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception as e:
            print(f"[{CAMERA_ID}] Trigger JSON decode error: {e}")
            continue

        if "trigger_time" not in msg:
            print(f"[{CAMERA_ID}] Trigger ohne trigger_time: {msg}")
            continue

        event_id = msg.get("event_id", "no_id")

        try:
            trigger_time = datetime.fromisoformat(msg["trigger_time"])
        except Exception as e:
            print(f"[{CAMERA_ID}] Invalid trigger_time '{msg.get('trigger_time')}': {e}")
            continue

        if trigger_time.tzinfo is None:
            trigger_time = trigger_time.replace(tzinfo=timezone.utc)

        print(f"[{CAMERA_ID}] Trigger empfangen: {trigger_time.isoformat()} event_id={event_id}")

        # Warten bis der genaue Trigger Zeitpunkt erreicht ist
        wait_until(trigger_time)

        # Erstes Bild aufnehmen
        cap1_start = utc_now()
        frame1 = picam.capture_array()
        cap1_end = utc_now()

        # Zweites Bild aufnehmen (kurzer Delay dazwischen)
        time.sleep(CAPTURE_DELAY_S)
        cap2_start = utc_now()
        frame2 = picam.capture_array()
        cap2_end = utc_now()

        # Sensorwerte einmal pro Event erfassen (GPS, Ausrichtung, Temperatur usw.)
        gps_selected, gps_mode = get_selected_gps()
        ori_selected, imu_mode = get_selected_orientation()

        lux = get_illumination()
        temp = get_temperature()
        press = get_pressure()
        hum = get_humidity()

        base_header = build_base_header(
            trigger_time=trigger_time,
            event_id=event_id,
            gps=gps_selected,
            orientation=ori_selected,
            lux=lux,
            temp=temp,
            press=press,
            hum=hum,
            alt_mode=gps_mode,  
        )
        base_header["imu_mode"] = imu_mode # Info, aus welchem Modus die IMU Daten stammen

        frames = [
            (0, frame1, cap1_start, cap1_end),
            (1, frame2, cap2_start, cap2_end),
        ]

        # Beide Bilder ueber eine einzige TCP Verbindung zum Server schicken
        try:
            tcp = socket.create_connection((SERVER_IP, SERVER_PORT), timeout=TCP_CONNECT_TIMEOUT_S)
            tcp.settimeout(TCP_IO_TIMEOUT_S)

            for frame_index, img, t_start, t_end in frames:
                # Farbformat umwandeln
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

                ok, img_jpeg = cv2.imencode(".jpg", img, ENCODE_PARAMS)
                if not ok:
                    raise RuntimeError("cv2.imencode failed")

                img_bytes = img_jpeg.tobytes()
                checksum = hashlib.sha256(img_bytes).hexdigest()
                latency_ms = (t_start - trigger_time).total_seconds() * 1000.0

                header = dict(base_header)
                header.update({
                    "frame_index": int(frame_index),
                    "timestamp": t_end.isoformat(),
                    "latency_estimate_ms": round(latency_ms, 3),
                    "image_size": len(img_bytes),
                    "checksum": checksum,
                })

                send_frame_v2(tcp, header, img_bytes)
                print(f"[{CAMERA_ID}] Sent frame={frame_index} bytes={len(img_bytes)}")

            try:
                tcp.shutdown(socket.SHUT_WR)
            except Exception:
                pass
            tcp.close()

        except Exception as e:
            print(f"[{CAMERA_ID}] ERROR sending event_id={event_id}: {e}")
            try:
                tcp.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()