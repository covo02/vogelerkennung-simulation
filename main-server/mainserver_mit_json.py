"""
UmweltRover - Mainserver (Integration)

Zweck:
- Empfaengt pro Trigger 2 Frames pro Kamera (TCP Receiver).
- Fuehrt Motion Detection und Triangulation aus.
- Stellt eine Dash-Weboberflaeche fuer Live- und Event-Analyse bereit.
- Optional: Multicast-Trigger-Sender und Power-Aktionen (Pi reboot/shutdown).

Wichtiger Hinweis (Herkunft / Integration):
- Diese Datei ist eine Zusammenfuehrung aus:
  (1) der Mainserver-/Receiver-Software (Empfang, Event-Handling, Speicherung, Web-UI) und
  (2) der Triangulationssoftware.
- Die Triangulationslogik (Module/Funktionen in triangulation_3.py, z.B. Kamera,
  geodetic_to_enu, trianguliere_motion_pixels, Sichtlinien-Visualisierung) wurde von
  Saki und Moritz entworfen.
- Die Integrationsarbeit (Receiver, EventStore/Timeouts, Disk-Layout, Dash UI, Trigger/Automation,
  Logging, Betrieb via systemd) wurde im Projektkontext von Leon umgesetzt/erweitert.

Schnittstellen:
- TCP Receiver: 0.0.0.0:5000
- Dash UI:      0.0.0.0:8050
- Trigger:      224.1.1.1:5001 (UDP multicast)

Datenablage (relativ zum WorkingDirectory, i.d.R. /opt/umweltrover):
- images/<event_id>/...           (Frames + Header JSON + Plot HTML)
- differenzbilder/...             (Motion/Diff Debug)
- triangulation_results.csv       (Triangulationspunkte)

Betrieb:
- systemd service: umweltrover-mainserver.service
- User: rover
- WorkingDirectory: /opt/umweltrover

Version:
- 2026-03-07.1

Parameter fuer die Vogel erkennung:

min_area=1 
THRESHOLD = 10 vorher 1
MIN_BLOB_AREA = 120  fienjustierung zw 60..300 bie zu viel rauschen 
PREVIEW_MIN_MOTION_PIXELS = 1 # oder 100, je nach Noise

"""
import struct
import socket
import threading
import json
import os
import queue
import signal
import time
import csv
import yaml
import uuid
import shutil
import glob
import subprocess
from flask import send_from_directory
import tempfile
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timezone, timedelta
from collections import deque

import cv2
import numpy as np
import plotly.graph_objects as go

from dash import Dash, dcc, html, Input, Output, State, no_update, callback_context
from dash import dash_table
import webbrowser
from threading import Timer

# Local libraries (must be next to this file)
import motion_detector as md
import triangulation_3 as tri


# ============================================================================
# CONFIGURATION
# ============================================================================
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5000
REQUIRED_CAMS = 3
EVENT_TIMEOUT = 60.0
SAVE_TO_DISK = True
DASH_PORT = 8050
IMAGE_FOLDER = "images"
DEBUG_FOLDER = "differenzbilder"
SHOW_TIMINGS = True
TRIANGULATION_MAX_DISTANCE = 100
CALIBRATION_FOLDER = "calibration"
RESULTS_CSV = "triangulation_results.csv"

# ================
# Ergänzung Colin
# ================

ML_EVENTS_FILE = "ml_events.jsonl"
ml_lock = threading.Lock()

# ================
# 
# ================

THRESHHOLD = 10 #vorher 1
GPS_REFERENCE_CAMERA_CANDIDATES = ("pi1", "cam1")
DASH_UPDATE_INTERVAL = 1000
DEVICE_UI_LINKS = [
    ("Pi1 UI", "http://192.168.178.11:8080"),
    ("Pi2 UI", "http://192.168.178.22:8080"),
    ("Pi3 UI", "http://192.168.178.33:8080"),
]
MAX_EVENTS_IN_MEMORY = 500
LOG_BUFFER_SIZE = 800
PI_SSH_USER = "pi"
PI_HOSTS = {
    "pi1": "192.168.178.11",
    "pi2": "192.168.178.22",
    "pi3": "192.168.178.33",
}
SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=2",
]
# ============================================================================
# TRIGGER CONFIG (Multicast)
# ============================================================================
TRIGGER_MULTICAST_GROUP = "224.1.1.1"
TRIGGER_PORT = 5001
TRIGGER_TTL = 2

TRIGGER_DEFAULT_INTERVAL_S = 5.0
TRIGGER_DEFAULT_LEAD_S = 3.0
TRIGGER_MIN_INTERVAL_S = 0.5  # protects against absurdly small values
TRIGGER_MIN_LEAD_S = 0.2
# Hard minimum time between sends (extra safety)
TRIGGER_MIN_GAP_S = 0.25

# Optional single-process lock (Linux). Prevents multiple mainserver processes from sending.
TRIGGER_PROCESS_LOCK_PATH = os.path.join(tempfile.gettempdir(), "umweltrover_trigger_sender.lock")

# Keep live figure from growing without bound
LIVE_TRACE_LIMIT = 1800
LIVE_REBUILD_KEEP_EVENTS = 200

# Disk retention (safe defaults)
RETENTION_KEEP_WITH_POINTS = True          # never delete events with points_count > 0
RETENTION_KEEP_LAST_N_EVENTS = 200         # keep newest N events regardless (setup buffer)

RETENTION_NO_MOTION_SEC = 15 * 60          # delete events with cameras_with_motion == 0 after 15 min
RETENTION_MOTION_NO_POINTS_SEC = 2 * 60 * 60  # delete motion events without points after 2 h

DISK_MIN_FREE_GB = 5.0                     # if free disk below this: cleanup becomes more aggressive
CLEANUP_INTERVAL_SEC = 30
# ============================================================================


# ============================================================================
# Utilities
# ============================================================================
CAMERA_COLORS = {
    0: "red",
    1: "green",
    2: "blue",
    3: "yellow",
    4: "purple",
    5: "cyan",
}


def get_camera_color(camera_index: int) -> str:
    return CAMERA_COLORS.get(camera_index % len(CAMERA_COLORS), "rgb(128,128,128)")


def safe_int(x, default=0) -> int:
    try:
        if x is None or x == "":
            return default
        return int(x)
    except Exception:
        return default


def safe_float(x, default=None):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================================
# Live log buffer (web view)
# ============================================================================
log_buffer = deque(maxlen=LOG_BUFFER_SIZE)
log_lock = threading.Lock()


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with log_lock:
        log_buffer.append(line)
    print(line, flush=True)


# ============================================================================
# Thread-safe UI stores
# ============================================================================
processed_events = deque(maxlen=MAX_EVENTS_IN_MEMORY)  # summaries
processed_events_lock = threading.Lock()

processed_events_by_id: Dict[str, Dict[str, Any]] = {}
processed_events_by_id_lock = threading.Lock()

camera_status: Dict[str, Dict[str, Any]] = {}
camera_status_lock = threading.Lock()

# Counter to tell Dash when new data arrived
processed_counter = 0
processed_counter_lock = threading.Lock()

# Remember camera positions in ENU for drawing in live view
camera_positions_enu: Dict[str, Tuple[float, float, float]] = {}
camera_positions_lock = threading.Lock()


# ============================================================================
# Trigger state (controlled from Dash)
# ============================================================================
trigger_lock = threading.Lock()
trigger_enabled = False
trigger_interval_s = float(TRIGGER_DEFAULT_INTERVAL_S)
trigger_lead_s = float(TRIGGER_DEFAULT_LEAD_S)

trigger_count = 0
trigger_last_sent: Optional[Dict[str, Any]] = None

# event_id -> meta for measuring timings
trigger_events_lock = threading.Lock()
trigger_events: Dict[str, Dict[str, Any]] = {}
# trigger_events[event_id] = {
#   "sent_perf": float,
#   "sent_utc": str,
#   "trigger_time_utc": str,
#   "interval_s": float,
#   "lead_s": float,
# }

# Trigger send guards (within process)
trigger_send_guard_lock = threading.Lock()
trigger_last_send_perf = 0.0

# Trigger thread guard (within process)
_trigger_thread_started = False
_trigger_thread_started_lock = threading.Lock()

# Inter-process sender lock handle (Linux best-effort)
_trigger_lock_fp = None
_trigger_lock_ok = False


def _try_acquire_trigger_process_lock() -> bool:
    """
    Cross-platform trigger-sender lock:
      - Windows: msvcrt.locking (real OS lock, no stale lockfiles)
      - Linux/Unix: fcntl.flock (real OS lock, no stale lockfiles)

    Returns True only for the one process that is allowed to send triggers.
    Other processes keep UI running but do not send.
    """
    global _trigger_lock_fp, _trigger_lock_ok

    # Idempotent: if we already hold the lock in this process, keep it.
    if _trigger_lock_ok and _trigger_lock_fp is not None:
        return True


    if os.name == "nt":
        try:
            import msvcrt

            fp = open(TRIGGER_PROCESS_LOCK_PATH, "a+")
            fp.seek(0)
            fp.truncate(0)
            fp.write(str(os.getpid()))
            fp.flush()

            fp.seek(0)
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)

            _trigger_lock_fp = fp
            _trigger_lock_ok = True
            log(f"[Trigger] Acquired process lock (msvcrt): {TRIGGER_PROCESS_LOCK_PATH}")
            return True
        except Exception as e:
            try:
                fp.close()
            except Exception:
                pass
            _trigger_lock_fp = None
            _trigger_lock_ok = False
            log(f"[Trigger] WARNING: Could not acquire process lock (msvcrt). Another sender may be running. Details: {e}")
            return False

    try:
        import fcntl  # type: ignore

        fp = open(TRIGGER_PROCESS_LOCK_PATH, "w")
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fp.write(str(os.getpid()))
        fp.flush()

        _trigger_lock_fp = fp
        _trigger_lock_ok = True
        log(f"[Trigger] Acquired process lock (flock): {TRIGGER_PROCESS_LOCK_PATH}")
        return True
    except Exception as e:
        try:
            fp.close()
        except Exception:
            pass
        _trigger_lock_fp = None
        _trigger_lock_ok = False
        log(f"[Trigger] WARNING: Could not acquire process lock (flock). Another sender may be running. Details: {e}")
        return False


def _release_trigger_process_lock():
    """Release the process lock (best-effort)."""
    global _trigger_lock_fp, _trigger_lock_ok
    if _trigger_lock_fp is None:
        _trigger_lock_ok = False
        return

    try:
        if os.name == "nt":
            try:
                import msvcrt
                _trigger_lock_fp.seek(0)
                msvcrt.locking(_trigger_lock_fp.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        else:
            try:
                import fcntl  # type: ignore
                fcntl.flock(_trigger_lock_fp.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
    finally:
        try:
            _trigger_lock_fp.close()
        except Exception:
            pass
        _trigger_lock_fp = None
        _trigger_lock_ok = False



# ============================================================================
# Dash globals (we keep a global figure to avoid view resets)
# ============================================================================
dash_app = None

dash_live_figure = go.Figure()
dash_live_figure_lock = threading.Lock()

dash_last_sent_counter = 0
dash_last_filter_state = None  # type: Optional[Tuple[int, Optional[float], int, int]]


def make_base_live_figure() -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        title="Live Triangulation",
        scene=dict(
            xaxis_title="East (m)",
            yaxis_title="North (m)",
            zaxis_title="Up (m)",
            aspectmode="data",
        ),
        showlegend=True,
        hovermode="closest",
        uirevision="keep-view",
    )
    return fig


def reset_live_figure_keep_cameras():
    global dash_live_figure
    with dash_live_figure_lock:
        dash_live_figure = make_base_live_figure()

        with camera_positions_lock:
            cams = list(camera_positions_enu.items())

        for cam_id, (e, n, u) in cams:
            dash_live_figure.add_trace(
                go.Scatter3d(
                    x=[e],
                    y=[n],
                    z=[u],
                    mode="markers+text",
                    marker=dict(size=7, symbol="circle"),
                    text=[cam_id],
                    textposition="top center",
                    name=f"Camera {cam_id}",
                    showlegend=True,
                )
            )


def add_points_to_live_figure(points_payload: List[Dict[str, Any]], event_id: str, timestamp: str):
    global dash_live_figure
    if not points_payload:
        return

    with dash_live_figure_lock:
        # Safety: if too many traces, rebuild from recent events (keeps cameras)
        if len(dash_live_figure.data) > LIVE_TRACE_LIMIT:
            fig = make_base_live_figure()
            with camera_positions_lock:
                cams = list(camera_positions_enu.items())
            for cam_id, (e, n, u) in cams:
                fig.add_trace(
                    go.Scatter3d(
                        x=[e],
                        y=[n],
                        z=[u],
                        mode="markers+text",
                        marker=dict(size=7, symbol="circle"),
                        text=[cam_id],
                        textposition="top center",
                        name=f"Camera {cam_id}",
                        showlegend=True,
                    )
                )

            with processed_events_lock:
                evs = list(processed_events)[:LIVE_REBUILD_KEEP_EVENTS]

            for ev in reversed(evs):
                ev_id = ev.get("event_id")
                if not ev_id:
                    continue
                with processed_events_by_id_lock:
                    det = processed_events_by_id.get(ev_id)
                if not det:
                    continue
                ts = det.get("timestamp", "")
                for p in det.get("points", []):
                    hover_text = (
                        f"Event: {ev_id}<br>"
                        f"Time: {ts}<br>"
                        f"ENU: E={p['enu_e']:.2f}, N={p['enu_n']:.2f}, U={p['enu_u']:.2f}<br>"
                        f"MeanDist: {p['mean_distance']:.3f} m<br>"
                        f"CamsUsed: {p['cameras_used']}"
                    )
                    fig.add_trace(
                        go.Scatter3d(
                            x=[p["enu_e"]],
                            y=[p["enu_n"]],
                            z=[p["enu_u"]],
                            mode="markers",
                            marker=dict(size=6),
                            hovertext=hover_text,
                            hoverinfo="text",
                            name="Bird",
                            showlegend=False,
                        )
                    )
            dash_live_figure = fig

        for p in points_payload:
            hover_text = (
                f"Event: {event_id}<br>"
                f"Time: {timestamp}<br>"
                f"ENU: E={p['enu_e']:.2f}, N={p['enu_n']:.2f}, U={p['enu_u']:.2f}<br>"
                f"MeanDist: {p['mean_distance']:.3f} m<br>"
                f"CamsUsed: {p['cameras_used']}"
            )
            dash_live_figure.add_trace(
                go.Scatter3d(
                    x=[p["enu_e"]],
                    y=[p["enu_n"]],
                    z=[p["enu_u"]],
                    mode="markers",
                    marker=dict(size=6),
                    hovertext=hover_text,
                    hoverinfo="text",
                    name="Bird",
                    showlegend=False,
                )
            )
        dash_live_figure.update_layout(uirevision="keep-view")


# ============================================================================
# Event assembly store for incoming frames
# ============================================================================
@dataclass
class ClientData:
    image1: Optional[bytes] = None
    image2: Optional[bytes] = None
    header: Optional[dict] = None
    created_at: datetime = field(default_factory=datetime.now)
    receive_time_ms: float = 0.0

    # first byte of this camera/event received on server
    first_received_utc: Optional[str] = None
    first_received_perf: Optional[float] = None

    def is_complete(self) -> bool:
        return all([self.image1 is not None, self.image2 is not None, self.header is not None])


class EventDataStore:
    def __init__(self, required_clients: int = 3, event_timeout: float = 300.0):
        self._data: Dict[str, Dict[str, ClientData]] = {}
        self._event_created_at: Dict[str, datetime] = {}
        self._lock = threading.Lock()
        self._complete_events = queue.Queue()
        self._required_clients = required_clients
        self._notified_events = set()
        self._event_timeout = event_timeout
        self._shutdown = False

    def add_data(self, event_id: str, client_id: str, data_type: str, data):
        with self._lock:
            if event_id not in self._data:
                self._data[event_id] = {}
                self._event_created_at[event_id] = datetime.now()
                log(f"[EventStore] New event created: {event_id}")

            if client_id not in self._data[event_id]:
                self._data[event_id][client_id] = ClientData()
                log(f"[EventStore] New camera registered: {client_id} for event {event_id}")

            cd = self._data[event_id][client_id]
            if cd.first_received_utc is None:
                cd.first_received_utc = utc_now().isoformat()
            if cd.first_received_perf is None:
                cd.first_received_perf = time.perf_counter()

            setattr(cd, data_type, data)

            if self._is_event_complete_unsafe(event_id):
                if event_id not in self._notified_events:
                    self._complete_events.put(event_id)
                    self._notified_events.add(event_id)
                    log(f"[EventStore] Event complete: {event_id} ({len(self._data[event_id])} cameras)")

    def add_receive_time(self, event_id: str, client_id: str, receive_time_ms: float):
        with self._lock:
            if event_id in self._data and client_id in self._data[event_id]:
                self._data[event_id][client_id].receive_time_ms += receive_time_ms

    def _is_event_complete_unsafe(self, event_id: str) -> bool:
        if event_id not in self._data:
            return False
        event_data = self._data[event_id]
        if len(event_data) < self._required_clients:
            return False
        return all(cd.is_complete() for cd in event_data.values())

    def pop_event(self, event_id: str) -> Optional[Dict[str, ClientData]]:
        with self._lock:
            if event_id in self._notified_events:
                self._notified_events.remove(event_id)
            if event_id in self._event_created_at:
                del self._event_created_at[event_id]
            return self._data.pop(event_id, None)

    def cleanup_old_events(self) -> int:
        now = datetime.now()
        deleted = 0
        with self._lock:
            to_delete = []
            for event_id, created_at in self._event_created_at.items():
                if (now - created_at).total_seconds() > self._event_timeout:
                    to_delete.append(event_id)

            for event_id in to_delete:
                del self._data[event_id]
                del self._event_created_at[event_id]
                if event_id in self._notified_events:
                    self._notified_events.remove(event_id)
                deleted += 1
                log(f"[EventStore] Event deleted (timeout): {event_id}")
        return deleted

    def get_complete_event(self, block: bool = True, timeout: Optional[float] = None) -> Optional[str]:
        try:
            return self._complete_events.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def shutdown(self):
        self._shutdown = True
        self._complete_events.put(None)

    def is_shutdown(self) -> bool:
        return self._shutdown


event_store = EventDataStore(required_clients=REQUIRED_CAMS, event_timeout=EVENT_TIMEOUT)
shutdown_event = threading.Event()

csv_lock = threading.Lock()
calibration_cache: Dict[str, Dict[str, np.ndarray]] = {}

gps_reference_point = None
gps_reference_lock = threading.Lock()


# ============================================================================
# CSV helpers
# ============================================================================
def init_csv_file():
    if not os.path.exists(RESULTS_CSV):
        with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "timestamp",
                    "event_id",
                    "point_index",
                    "latitude",
                    "longitude",
                    "altitude",
                    "enu_east",
                    "enu_north",
                    "enu_up",
                    "mean_distance_m",
                    "cameras_count",
                    "camera_ids",
                    "motion_pixels_count",
                    "trigger_sent_utc",
                    "trigger_time_utc",
                    "trigger_to_first_rx_ms",
                    "trigger_to_done_ms",
                ]
            )
        log(f"[CSV] Created: {RESULTS_CSV}")


def save_triangulation_to_csv(event_id: str, results: list, timestamp: Optional[str] = None, extra: Optional[Dict[str, Any]] = None):
    if not results:
        return
    if timestamp is None:
        timestamp = datetime.now().isoformat()
    if extra is None:
        extra = {}

    with csv_lock:
        with open(RESULTS_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for idx, result in enumerate(results, 1):
                camera_ids = ",".join(
                    [
                        str(cam.gps_koordinate) if hasattr(cam, "gps_koordinate") else "unknown"
                        for cam in result.cameras_used
                    ]
                )
                total_motion_pixels = sum(len(lst) for lst in result.motion_pixels if lst)

                writer.writerow(
                    [
                        timestamp,
                        event_id,
                        idx,
                        f"{result.gps_coords[0]:.8f}",
                        f"{result.gps_coords[1]:.8f}",
                        f"{result.gps_coords[2]:.2f}",
                        f"{result.point_3d[0]:.3f}",
                        f"{result.point_3d[1]:.3f}",
                        f"{result.point_3d[2]:.3f}",
                        f"{result.mean_distance:.4f}",
                        len(result.cameras_used),
                        camera_ids,
                        total_motion_pixels,
                        extra.get("trigger_sent_utc"),
                        extra.get("trigger_time_utc"),
                        extra.get("trigger_to_first_rx_ms"),
                        extra.get("trigger_to_done_ms"),
                    ]
                )

# ================
# Ergänzung Colin
# ================
# ============================================================================
# ML Event Logger
# ============================================================================

def save_event_for_ml(event_data: Dict[str, Any]):
    """
    Speichert genau ein Event als JSON-Zeile.
    JSONL = eine JSON-Struktur pro Zeile.
    Kann beliebig wachsen und ist append-only.
    """

    try:
        with ml_lock:
            with open(ML_EVENTS_FILE, "a", encoding="utf-8") as f:
                json.dump(event_data, f, ensure_ascii=False)
                f.write("\n")
    except Exception as e:
        log(f"[ML] Save error: {e}")
# ================
# 
# ================

# ============================================================================
# Calibration loader
# ============================================================================
def load_calibration_files():
    global calibration_cache

    if not os.path.exists(CALIBRATION_FOLDER):
        raise FileNotFoundError(CALIBRATION_FOLDER)

    count = 0
    for filename in os.listdir(CALIBRATION_FOLDER):
        if not filename.endswith("_calibration.yaml"):
            continue

        camera_id = filename.replace("_calibration.yaml", "")
        filepath = os.path.join(CALIBRATION_FOLDER, filename)

        with open(filepath, "r", encoding="utf-8") as f:
            calib_data = yaml.safe_load(f)

        calibration_cache[camera_id] = {
            "camera_matrix": np.array(calib_data["camera_matrix"]),
            "dist_coefs": np.array(calib_data["dist_coefs"]).flatten(),
        }
        count += 1
        log(f"[Calibration] Loaded: {camera_id}")

    log(f"[Calibration] Loaded {count} calibration file(s)")


# ============================================================================
# Camera status updates
# ============================================================================
def update_camera_status_on_receive(camera_id: str, addr: Any, header: Dict[str, Any], receive_time_ms: float):
    now_iso = datetime.now().isoformat()

    gps = header.get("gps", {}) if isinstance(header, dict) else {}
    ori = header.get("orientation", {}) if isinstance(header, dict) else {}

    with camera_status_lock:
        st = camera_status.get(camera_id, {})
        st["camera_id"] = camera_id
        st["last_seen"] = now_iso
        st["last_ip"] = str(addr[0]) if isinstance(addr, tuple) and len(addr) > 0 else str(addr)
        st["last_rx_ms"] = float(receive_time_ms)
        st["gps_lat"] = gps.get("lat", None)
        st["gps_lon"] = gps.get("lon", None)
        st["gps_alt"] = gps.get("alt", None)
        st["yaw"] = ori.get("yaw", None)
        st["pitch"] = ori.get("pitch", None)
        st["roll"] = ori.get("roll", None)

        rx_hist = st.get("_rx_hist", deque(maxlen=50))
        rx_hist.append(float(receive_time_ms))
        st["_rx_hist"] = rx_hist
        st["avg_rx_ms_50"] = float(sum(rx_hist) / max(1, len(rx_hist)))

        camera_status[camera_id] = st

def _recv_exact(conn, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"Connection closed while receiving {n} bytes (got {len(buf)})")
        buf += chunk
    return buf
# ============================================================================
# Networking: receive client data
# ============================================================================
def handle_client(conn, addr):
    receive_start_time = time.perf_counter()
    try:
        # Timeout hoch stellen, weil grosse Bilder fuehren zu WLAN/CPU schwankungen
        conn.settimeout(60.0)

        while True:
            # 4 bytes header_len lesen (EOF => fertig)
            first = conn.recv(4)
            if not first:
                break
            if len(first) < 4:
                first += _recv_exact(conn, 4 - len(first))

            header_len = struct.unpack("!I", first)[0]
            if header_len <= 0 or header_len > 256 * 1024:
                raise ValueError(f"Invalid header_len={header_len} from {addr}")

            header_json = _recv_exact(conn, header_len)
            try:
                header = json.loads(header_json.decode("utf-8", errors="strict"))
            except Exception as e:
                raise ValueError(f"Invalid header JSON from {addr}: {e}")

            camera_id = header.get("camera_id", "unknown")
            event_id = header.get("event_id", "unknown")
            frame_index = int(header.get("frame_index", 0))

            # 4 bytes image_len + image bytes
            image_len = struct.unpack("!I", _recv_exact(conn, 4))[0]
            if image_len <= 0 or image_len > 50 * 1024 * 1024:
                raise ValueError(f"Invalid image_len={image_len} from {addr} cam={camera_id} event={event_id}")

            log(f"[RXHDR] cam={camera_id} event={event_id} frame={frame_index} size={image_len} from={addr}")

            image_data = _recv_exact(conn, image_len)

            receive_time_ms = (time.perf_counter() - receive_start_time) * 1000.0
            update_camera_status_on_receive(camera_id, addr, header, receive_time_ms)

            if SAVE_TO_DISK:
                folder = os.path.join(IMAGE_FOLDER, event_id)
                os.makedirs(folder, exist_ok=True)
                ts = header.get("timestamp", datetime.now().isoformat()).replace(":", "-")
                filename = f"{camera_id}_{ts}_frame{frame_index}.jpg"
                filepath = os.path.join(folder, filename)
                try:
                    with open(filepath, "wb") as f:
                        f.write(image_data)
                    with open(filepath.replace(".jpg", ".json"), "w", encoding="utf-8") as f:
                        json.dump(header, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    log(f"[Server] Save warning: {e}")

            data_type = "image1" if frame_index == 0 else "image2"
            event_store.add_data(event_id, camera_id, data_type, image_data)
            event_store.add_data(event_id, camera_id, "header", header)
            event_store.add_receive_time(event_id, camera_id, receive_time_ms)

    except Exception as e:
        log(f"[Server] handle_client error from {addr}: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
# ============================================================================
# Trigger sender (integrated)

# ============================================================================
def _trigger_send_message(sock: socket.socket, trigger_time_utc: datetime, event_id: str, interval_s: float, lead_s: float) -> bool:
    """
    Sends exactly one multicast UDP trigger message.
    Returns True if sent, False if suppressed by guards.
    """
    global trigger_last_sent, trigger_count, trigger_last_send_perf

    # Inter-process lock not held -> don't send (prevents multiple processes spamming)
    if not _trigger_lock_ok:
        return False

    # Burst/rate guard within process
    with trigger_send_guard_lock:
        now_perf = time.perf_counter()
        min_gap = max(TRIGGER_MIN_GAP_S, float(interval_s) * 0.5)
        if (now_perf - trigger_last_send_perf) < min_gap:
            # Suppress / delay outside if caller wants, but we just suppress here.
            return False
        trigger_last_send_perf = now_perf

    # Pi-side: if tzinfo missing they set UTC; timezone-aware is fine.
    msg = json.dumps(
        {
            "trigger_time": trigger_time_utc.isoformat(),
            "event_id": event_id,
            "event": "capture",
        }
    ).encode("utf-8")

    sent_perf = time.perf_counter()
    sent_utc = utc_now().isoformat()

    sock.sendto(msg, (TRIGGER_MULTICAST_GROUP, TRIGGER_PORT))

    with trigger_events_lock:
        trigger_events[event_id] = {
            "sent_perf": sent_perf,
            "sent_utc": sent_utc,
            "trigger_time_utc": trigger_time_utc.isoformat(),
            "interval_s": float(interval_s),
            "lead_s": float(lead_s),
        }

    with trigger_lock:
        trigger_last_sent = {"event_id": event_id, "sent_utc": sent_utc, "trigger_time_utc": trigger_time_utc.isoformat()}
        trigger_count += 1

    log(f"[Trigger] Sent event_id={event_id} trigger_time={trigger_time_utc.isoformat()}")
    return True


def _next_aligned_send_time(now_utc_dt: datetime, interval_s: float) -> datetime:
    """
    Next aligned SEND time based on UNIX epoch alignment.
    The trigger_time will be send_time + lead_s.
    """
    now_ts = now_utc_dt.timestamp()
    interval_s = max(float(TRIGGER_MIN_INTERVAL_S), float(interval_s))
    next_boundary = (int(now_ts // interval_s) + 1) * interval_s
    return datetime.fromtimestamp(next_boundary, tz=timezone.utc)


def trigger_worker():
    log(f"[Trigger] Trigger thread started pid={os.getpid()} tid={threading.get_ident()}")

    can_send = _try_acquire_trigger_process_lock()
    if not can_send:
        log("[Trigger] Process lock NOT acquired -> this process will NOT send triggers.")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, TRIGGER_TTL)

    while not shutdown_event.is_set():
        try:
            with trigger_lock:
                enabled = bool(trigger_enabled)
                interval_s = max(float(TRIGGER_MIN_INTERVAL_S), float(trigger_interval_s))
                lead_s = max(float(TRIGGER_MIN_LEAD_S), float(trigger_lead_s))

            if not enabled:
                time.sleep(0.1)
                continue

            # Next slot (aligned) computed fresh each cycle: no catch-up bursts possible
            nowu = utc_now()
            send_at = _next_aligned_send_time(nowu, interval_s)
            trigger_time_utc = send_at + timedelta(seconds=lead_s)

            # Wait until next_fire (react quickly to stop/settings changes)
            while not shutdown_event.is_set():
                with trigger_lock:
                    enabled2 = bool(trigger_enabled)
                    interval_s2 = max(float(TRIGGER_MIN_INTERVAL_S), float(trigger_interval_s))
                    lead_s2 = max(float(TRIGGER_MIN_LEAD_S), float(trigger_lead_s))
                if not enabled2:
                    break

                # If UI changed settings: recompute next aligned time
                if interval_s2 != interval_s or lead_s2 != lead_s:
                    interval_s, lead_s = interval_s2, lead_s2
                    send_at = _next_aligned_send_time(utc_now(), interval_s2)
                    trigger_time_utc = send_at + timedelta(seconds=lead_s2)

                dt = (send_at - utc_now()).total_seconds()
                if dt <= 0.0:
                    break
                time.sleep(min(0.2, dt))

            with trigger_lock:
                if not trigger_enabled:
                    continue
                interval_s = max(float(TRIGGER_MIN_INTERVAL_S), float(trigger_interval_s))
                lead_s = max(float(TRIGGER_MIN_LEAD_S), float(trigger_lead_s))

            # Send exactly once for this cycle; if suppressed by guard, wait a bit and retry next cycle.
            event_id = str(uuid.uuid4())
            ok = _trigger_send_message(sock, trigger_time_utc, event_id, interval_s, lead_s)
            if not ok:
                time.sleep(0.2)

        except Exception as e:
            log(f"[Trigger] Error: {e}")
            time.sleep(0.5)

    try:
        sock.close()
    except Exception:
        pass

    log("[Trigger] Trigger thread stopped")


# ============================================================================
# Processing pipeline
# ============================================================================
def processing_worker():
    global processed_counter
    log("[Processor] Processing thread started")

    while not shutdown_event.is_set():
        try:
            event_id = event_store.get_complete_event(block=True, timeout=1.0)
            if event_id is None:
                if event_store.is_shutdown():
                    break
                continue

            event_data = event_store.pop_event(event_id)
            if not event_data:
                continue

            log(f"[Processor] Processing event {event_id} with {len(event_data)} cameras")

            # Trigger timings lookup (if event_id came from our trigger)
            with trigger_events_lock:
                trigger_meta = trigger_events.get(event_id)

            sent_perf = trigger_meta.get("sent_perf") if trigger_meta else None
            sent_utc = trigger_meta.get("sent_utc") if trigger_meta else None
            trigger_time_utc = trigger_meta.get("trigger_time_utc") if trigger_meta else None

            # earliest first-received perf
            first_rx_perf = None
            try:
                perfs = [cd.first_received_perf for cd in event_data.values() if cd.first_received_perf is not None]
                if perfs:
                    first_rx_perf = min(perfs)
            except Exception:
                first_rx_perf = None

            global gps_reference_point

            reference_camera_id = None
            for candidate in GPS_REFERENCE_CAMERA_CANDIDATES:
                if candidate in event_data:
                    reference_camera_id = candidate
                    break
            if reference_camera_id is None:
                # Stabiler Fallback statt Ankunftsreihenfolge
                reference_camera_id = sorted(event_data.keys())[0]

            reference_camera_data = event_data[reference_camera_id]

            with gps_reference_lock:
                if gps_reference_point is None:
                    if reference_camera_data.header is None:
                        log("[GPS] Error: header missing for reference point")
                        continue
                    gps_reference_point = (
                        reference_camera_data.header["gps"]["lat"],
                        reference_camera_data.header["gps"]["lon"],
                        reference_camera_data.header["gps"]["alt"],
                    )
                    log(
                        f"[GPS] Reference set from {reference_camera_id}: "
                        f"{gps_reference_point[0]:.6f},{gps_reference_point[1]:.6f},{gps_reference_point[2]:.1f}m"
                    )
                gps_ref = gps_reference_point

            # --- Camera positions (ENU) for this event (independent of motion) ---
            camera_positions_enu_event: Dict[str, Tuple[float, float, float]] = {}

            
            for cam_id, cd in event_data.items():
                try:
                    if not cd.header:
                        continue
                    g = cd.header.get("gps", {})
                    lat = g.get("lat")
                    lon = g.get("lon")
                    alt = g.get("alt")
                    if lat is None or lon is None or alt is None:
                        continue

                    e, n, u = tri.geodetic_to_enu(lat, lon, alt, gps_ref[0], gps_ref[1], gps_ref[2])
                    camera_positions_enu_event[cam_id] = (float(e), float(n), float(u))
                except Exception as e:
                    log(f"[Processor] Camera ENU compute error: {e}")

            # --- Camera view directions (ENU) for this event (short ray), independent of motion ---
            camera_view_dir_enu_event: Dict[str, Tuple[float, float, float]] = {}

            try:
                for cam_id, cd in event_data.items():
                    if not cd.header:
                        continue

                    if cam_id not in calibration_cache:
                        continue

                    g = cd.header.get("gps", {})
                    o = cd.header.get("orientation", {})
                    lat, lon, alt = g.get("lat"), g.get("lon"), g.get("alt")
                    yaw, pitch, roll = o.get("yaw"), o.get("pitch"), o.get("roll")
                    if lat is None or lon is None or alt is None or yaw is None or pitch is None or roll is None:
                        continue

                    calib = calibration_cache[cam_id]
                    K = np.asarray(calib["camera_matrix"], dtype=float)
                    principal_point = (float(K[0, 2]), float(K[1, 2]))

                    cam_tmp = tri.Kamera(
                        motion_pixels=[],
                        gps_koordinate=(lat, lon, alt),
                        gps_ref=gps_ref,
                        orientierung=(yaw, pitch, roll),
                        intrinsisch=K,
                        distortion=calib["dist_coefs"],
                    )

                    vec = cam_tmp.bildpunkt_zu_richtungsvektor(principal_point)
                    camera_view_dir_enu_event[cam_id] = (float(vec[0]), float(vec[1]), float(vec[2]))
            except Exception as e:
                log(f"[Processor] Camera view dir compute error: {e}")
            # Optional: Cache for LIVE view (keine Pflicht fuer Event-Plot, aber sinnvoll)
            if camera_positions_enu_event:
                with camera_positions_lock:
                    for cam_id, pos in camera_positions_enu_event.items():
                        camera_positions_enu[cam_id] = pos

            # Disk figure
            fig_disk = go.Figure()
            fig_disk.update_layout(
                title=f"Event {event_id} - Full Analysis",
                scene=dict(
                    xaxis_title="East (m)",
                    yaxis_title="North (m)",
                    zaxis_title="Up (m)",
                    aspectmode="data",
                ),
                uirevision="keep-view",
            )

            camera_timings: Dict[str, Dict[str, float]] = {}
            motion_pixels_per_camera: List[List[Any]] = []
            kamera_objekte: List[Any] = []
            camera_ids_in_event: List[str] = []
            total_motion_pixels_all = 0

            for idx, (camera_id, client_data) in enumerate(event_data.items()):
                camera_ids_in_event.append(camera_id)
                try:
                    t0 = time.perf_counter()

                    img1_array = np.frombuffer(client_data.image1, dtype=np.uint8)
                    img2_array = np.frombuffer(client_data.image2, dtype=np.uint8)

                    t_decode0 = time.perf_counter()
                    frame1 = cv2.imdecode(img1_array, cv2.IMREAD_COLOR)
                    frame2 = cv2.imdecode(img2_array, cv2.IMREAD_COLOR)
                    decode_ms = (time.perf_counter() - t_decode0) * 1000.0

                    if frame1 is not None:
                        log(f"[DEBUG] {camera_id} frame1.shape = {frame1.shape}")
                    if frame2 is not None:
                        log(f"[DEBUG] {camera_id} frame2.shape = {frame2.shape}")

                    if frame1 is None or frame2 is None:
                        log(f"[Processor] Decode error for {camera_id}")
                        continue

                    os.makedirs(DEBUG_FOLDER, exist_ok=True)

                    t_m0 = time.perf_counter()
                    motion_pixels = md.detect_motion_simple(
                        frame1=frame1,
                        frame2=frame2,
                        threshold=THRESHHOLD,
                        min_area=1,
                        save_debug_image=True,
                        debug_path=f"{DEBUG_FOLDER}/{event_id}_{camera_id}_motion.jpg",
                        threshold_mode="fixed",
                    )

                    motion_ms = (time.perf_counter() - t_m0) * 1000.0
                    
                    

                    total_motion_pixels_all += int(len(motion_pixels))  # now: blob count, not pixel count
                    total_ms = (time.perf_counter() - t0) * 1000.0

                    camera_timings[camera_id] = {
                        "receive_ms": float(client_data.receive_time_ms),
                        "decode_ms": float(decode_ms),
                        "motion_ms": float(motion_ms),
                        "total_ms": float(total_ms),
                        "motion_pixels": float(len(motion_pixels)),  # now: blob count, not pixel count
                    }

                    if len(motion_pixels) > 0:
                        if camera_id not in calibration_cache:
                            log(f"[Processor] Calibration missing for {camera_id}")
                            continue
                        if client_data.header is None:
                            log(f"[Processor] Header missing for {camera_id}")
                            continue

                        calib = calibration_cache[camera_id]
                        header = client_data.header

                        kamera = tri.Kamera(
                            motion_pixels=motion_pixels,
                            gps_koordinate=(header["gps"]["lat"], header["gps"]["lon"], header["gps"]["alt"]),
                            gps_ref=gps_ref,
                            orientierung=(header["orientation"]["yaw"], header["orientation"]["pitch"], header["orientation"]["roll"]),
                            intrinsisch=calib["camera_matrix"],
                            distortion=calib["dist_coefs"],
                        )
                        K_dbg = np.asarray(calib["camera_matrix"], dtype=float)
                        cx_dbg = float(K_dbg[0, 2])
                        cy_dbg = float(K_dbg[1, 2])

                        for mp in motion_pixels:
                            try:
                                if hasattr(mp, "center_x") and hasattr(mp, "center_y"):
                                    px = float(mp.center_x)
                                    py = float(mp.center_y)
                                else:
                                    px = float(mp[0])
                                    py = float(mp[1])

                                vec_dbg = kamera.bildpunkt_zu_richtungsvektor((px, py))
                                elev_dbg = np.degrees(np.arctan2(vec_dbg[2], np.sqrt(vec_dbg[0]**2 + vec_dbg[1]**2)))
                                az_dbg = np.degrees(np.arctan2(vec_dbg[0], vec_dbg[1]))

                                log(
                                    f"[RAYDBG] cam={camera_id} "
                                    f"pixel=({px:.1f},{py:.1f}) "
                                    f"principal=({cx_dbg:.1f},{cy_dbg:.1f}) "
                                    f"yaw={header['orientation']['yaw']:.2f} "
                                    f"pitch={header['orientation']['pitch']:.2f} "
                                    f"roll={header['orientation']['roll']:.2f} "
                                    f"az={az_dbg:.2f} elev={elev_dbg:.2f} "
                                    f"vec=({vec_dbg[0]:.4f},{vec_dbg[1]:.4f},{vec_dbg[2]:.4f})"
                                )
                            except Exception as e:
                                log(f"[RAYDBG] cam={camera_id} error: {e}")
                        cam_color = get_camera_color(idx)
                        kamera.zeichne_position(fig_disk, cam_color, camera_id)
                        kamera.zeichne_sichtlinien(fig_disk, cam_color, camera_id, laenge=100)

                        # Cache camera positions for LIVE graph (ENU)
                        try:
                            enu = kamera.enu_koordinate
                            if isinstance(enu, (list, tuple)) and len(enu) >= 3:
                                with camera_positions_lock:
                                    camera_positions_enu[camera_id] = (float(enu[0]), float(enu[1]), float(enu[2]))
                        except Exception:
                            pass

                        kamera_objekte.append(kamera)
                        motion_pixels_per_camera.append(motion_pixels)

                except Exception as e:
                    log(f"[Processor] Camera loop error for {camera_id}: {e}")

            # Triangulation
            results = []
            triangulation_ms = None
            if len(kamera_objekte) >= 2:
                t_tri0 = time.perf_counter()
                try:
                    results = tri.trianguliere_motion_pixels(
                        kameras=kamera_objekte,
                        motion_pixels_per_camera=motion_pixels_per_camera,
                        max_abstand=TRIANGULATION_MAX_DISTANCE,
                        min_kameras=2,
                    )
                except Exception as e:
                    log(f"[Triangulation] Error: {e}")
                triangulation_ms = (time.perf_counter() - t_tri0) * 1000.0

            # Timestamp: prefer camera header timestamp (frame time)
            event_timestamp = None
            try:
                if reference_camera_data.header:
                    event_timestamp = reference_camera_data.header.get("timestamp", None)
            except Exception:
                event_timestamp = None
            if not event_timestamp:
                event_timestamp = datetime.now().isoformat()

            # Build points payload + disk plot points
            points_payload = []
            mean_distances = []

            if results:
                for i, result in enumerate(results, 1):
                    mdist = float(result.mean_distance)
                    mean_distances.append(mdist)
                    pt = {
                        "point_index": i,
                        "enu_e": float(result.point_3d[0]),
                        "enu_n": float(result.point_3d[1]),
                        "enu_u": float(result.point_3d[2]),
                        "gps_lat": float(result.gps_coords[0]),
                        "gps_lon": float(result.gps_coords[1]),
                        "gps_alt": float(result.gps_coords[2]),
                        "mean_distance": mdist,
                        "cameras_used": int(len(result.cameras_used)),
                    }
                    points_payload.append(pt)

                    fig_disk.add_trace(
                        go.Scatter3d(
                            x=[pt["enu_e"]],
                            y=[pt["enu_n"]],
                            z=[pt["enu_u"]],
                            mode="markers",
                            marker=dict(size=8, color="red", symbol="diamond"),
                            name=f"Point {i}",
                            showlegend=True,
                        )
                    )

            # Trigger->timings
            trigger_to_first_rx_ms = None
            trigger_to_done_ms = None
            if sent_perf is not None:
                try:
                    trigger_to_done_ms = (time.perf_counter() - float(sent_perf)) * 1000.0
                except Exception:
                    trigger_to_done_ms = None
            if sent_perf is not None and first_rx_perf is not None:
                try:
                    trigger_to_first_rx_ms = (float(first_rx_perf) - float(sent_perf)) * 1000.0
                except Exception:
                    trigger_to_first_rx_ms = None

            # Save CSV
            if results:
                save_triangulation_to_csv(
                    event_id,
                    results,
                    event_timestamp,
                    extra={
                        "trigger_sent_utc": sent_utc,
                        "trigger_time_utc": trigger_time_utc,
                        "trigger_to_first_rx_ms": round(float(trigger_to_first_rx_ms), 3) if trigger_to_first_rx_ms is not None else None,
                        "trigger_to_done_ms": round(float(trigger_to_done_ms), 3) if trigger_to_done_ms is not None else None,
                    },
                )

            # Save disk html
            plot_html_path = None
            if SAVE_TO_DISK:
                try:
                    folder = os.path.join(IMAGE_FOLDER, event_id)
                    os.makedirs(folder, exist_ok=True)
                    plot_html_path = os.path.join(folder, f"plotly_{event_id}.html")
                    fig_disk.write_html(plot_html_path)
                except Exception as e:
                    log(f"[Processor] Plot save warning: {e}")

            # Store event summary + detail for UI tables
            avg_mean_distance = (sum(mean_distances) / len(mean_distances)) if mean_distances else None
            points_count = int(len(points_payload))

            event_summary = {
                "timestamp": event_timestamp,
                "event_id": event_id,
                "cameras_total": int(len(event_data)),
                "cameras_with_motion": int(len(kamera_objekte)),
                "motion_pixels_total": int(total_motion_pixels_all),
                "points_count": points_count,
                "avg_mean_distance": float(avg_mean_distance) if avg_mean_distance is not None else None,
                "triangulation_ms": float(triangulation_ms) if triangulation_ms is not None else None,
                "plot_html_path": plot_html_path,
                "event_folder": os.path.join(IMAGE_FOLDER, event_id),
                # Trigger telemetry
                "trigger_sent_utc": sent_utc,
                "trigger_time_utc": trigger_time_utc,
                "trigger_to_first_rx_ms": float(trigger_to_first_rx_ms) if trigger_to_first_rx_ms is not None else None,
                "trigger_to_done_ms": float(trigger_to_done_ms) if trigger_to_done_ms is not None else None,
            }

            # --- disk retention metadata (do not delete relevant events) ---
            now_ts = time.time()
            if event_summary.get("points_count", 0) > 0 and RETENTION_KEEP_WITH_POINTS:
                expires_at = None  # never auto-delete
            elif event_summary.get("cameras_with_motion", 0) > 0:
                expires_at = now_ts + float(RETENTION_MOTION_NO_POINTS_SEC)
            else:
                expires_at = now_ts + float(RETENTION_NO_MOTION_SEC)
            event_detail = {
                **event_summary,
                "camera_timings": camera_timings,
                "points": points_payload,
                "debug_images": {cam_id: os.path.join(DEBUG_FOLDER, f"{event_id}_{cam_id}_motion.jpg") for cam_id in camera_ids_in_event},
                "camera_positions_enu_event": camera_positions_enu_event,
                "camera_view_dir_enu_event": camera_view_dir_enu_event,
                "expires_at": expires_at,
                "images_deleted": False,
            }
            # ================
            # Ergänzung Colin
            # ================
            ml_event = {
                "timestamp": event_timestamp,
                "event_id": event_id,

                "summary": {
                    "cameras_total": int(len(event_data)),
                    "cameras_with_motion": int(len(kamera_objekte)),
                    "motion_pixels_total": int(total_motion_pixels_all),
                    "points_count": int(len(points_payload)),
                    "avg_mean_distance": avg_mean_distance,
                    "triangulation_ms": triangulation_ms,
                },

                "points": points_payload,
            }

            save_event_for_ml(ml_event)

            # ================
            # 
            # ================

            with processed_events_lock:
                dropped_event_id = None

                # Wenn deque voll ist, wird beim appendleft automatisch das letzte Element rausgeschoben.
                # Das merken wir uns vorher, damit wir den Detail-Store synchron halten.
                if processed_events.maxlen is not None and len(processed_events) == processed_events.maxlen:
                    last = processed_events[-1]
                    if isinstance(last, dict):
                        dropped_event_id = last.get("event_id")

                processed_events.appendleft(event_summary)

            with processed_events_by_id_lock:
                processed_events_by_id[event_id] = event_detail

                # Detail-Dict auf gleiche Groesse halten wie die deque
                if dropped_event_id:
                    processed_events_by_id.pop(dropped_event_id, None)

            # Update LIVE figure (append points; cameras are synced in Dash too)
            add_points_to_live_figure(points_payload, event_id, event_timestamp)

            with processed_counter_lock:
                processed_counter += 1

            if SHOW_TIMINGS:
                log(
                    f"[Timing] Event {event_id} cams_total={event_summary['cameras_total']} "
                    f"cams_motion={event_summary['cameras_with_motion']} points={points_count} "
                    f"t_first_rx_ms={event_summary['trigger_to_first_rx_ms']} t_done_ms={event_summary['trigger_to_done_ms']}"
                )
                log(f"UI stores: deque={len(processed_events)} dict={len(processed_events_by_id)}")

            # release trigger meta (avoid growth)
            with trigger_events_lock:
                trigger_events.pop(event_id, None)

            log(f"[Processor] Event done: {event_id}")

        except Exception as e:
            log(f"[Processor] Main loop error: {e}")
            time.sleep(1.0)

    log("[Processor] Processing thread stopped")


def cleanup_worker():
    log("[Cleanup] Cleanup thread started")
    while not shutdown_event.is_set():
        try:
            if shutdown_event.wait(60):
                break
            deleted = event_store.cleanup_old_events()
            if deleted > 0:
                log(f"[Cleanup] Deleted {deleted} old events")
        except Exception as e:
            log(f"[Cleanup] Error: {e}")
    log("[Cleanup] Cleanup thread stopped")

def _get_free_gb(path: str) -> float:
    try:
        st = shutil.disk_usage(path)
        return float(st.free) / (1024.0 ** 3)
    except Exception:
        return 9999.0

def disk_cleanup_worker():
    base_path = os.path.abspath(".")  # WorkingDirectory (/opt/umweltrover)
    while not shutdown_event.is_set():
        try:
            free_gb = _get_free_gb(base_path)
            now_ts = time.time()

            # build deletion list (never delete points events when configured)
            candidates = []
            with processed_events_lock:
                keep_ids = set()
                # keep newest N events as buffer
                for ev in list(processed_events)[:RETENTION_KEEP_LAST_N_EVENTS]:
                    ev_id = ev.get("event_id") if isinstance(ev, dict) else None
                    if ev_id:
                        keep_ids.add(ev_id)

            with processed_events_by_id_lock:
                for ev_id, detail in list(processed_events_by_id.items()):
                    if ev_id in keep_ids:
                        continue
                    if detail.get("images_deleted"):
                        continue

                    pts = int(detail.get("points_count", 0) or 0)
                    if pts > 0 and RETENTION_KEEP_WITH_POINTS:
                        continue

                    exp = detail.get("expires_at")
                    if exp is None:
                        continue

                    # normal expiry
                    if now_ts > float(exp):
                        candidates.append((ev_id, detail))

            # if disk critically full: also delete oldest non-point events even if not expired yet
            if free_gb < float(DISK_MIN_FREE_GB):
                extra = []
                with processed_events_by_id_lock:
                    for ev_id, detail in list(processed_events_by_id.items()):
                        if ev_id in keep_ids:
                            continue
                        if detail.get("images_deleted"):
                            continue
                        pts = int(detail.get("points_count", 0) or 0)
                        if pts > 0 and RETENTION_KEEP_WITH_POINTS:
                            continue
                        extra.append((ev_id, detail))
                # delete a limited number extra (oldest first based on timestamp string)
                extra.sort(key=lambda x: str(x[1].get("timestamp", "")))
                candidates.extend(extra[:50])

            # perform deletions
            for ev_id, detail in candidates:
                folder = detail.get("event_folder")
                if folder and os.path.isdir(folder):
                    try:
                        shutil.rmtree(folder, ignore_errors=True)
                    except Exception:
                        pass

                dbg = detail.get("debug_images", {}) or {}
                for _, p in dbg.items():
                    try:
                        if p and os.path.exists(p):
                            os.remove(p)
                        # also remove thumb if present
                        base, _ext = os.path.splitext(p) if p else ("", "")
                        thumb = f"{base}_thumb.jpg" if base else ""
                        if thumb and os.path.exists(thumb):
                            os.remove(thumb)
                    except Exception:
                        pass

                with processed_events_by_id_lock:
                    if ev_id in processed_events_by_id:
                        processed_events_by_id[ev_id]["images_deleted"] = True

        except Exception:
            pass

        time.sleep(float(CLEANUP_INTERVAL_SEC))
# ============================================================================
# Dash: event detail figure builder
# ============================================================================
def build_event_detail_figure(event_id: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        title=f"Event detail: {event_id}",
        scene=dict(
            xaxis_title="East (m)",
            yaxis_title="North (m)",
            zaxis_title="Up (m)",
            aspectmode="data",
        ),
        uirevision="keep-view",
    )

    with processed_events_by_id_lock:
        detail = processed_events_by_id.get(event_id)

    if not detail:
        return fig

    pts = detail.get("points", [])

    # --- Cameras (always) ---
    cams = detail.get("camera_positions_enu_event", {}) or {}
    if cams:
        cam_items = sorted(cams.items(), key=lambda kv: str(kv[0]))
        xs = [pos[0] for _, pos in cam_items]
        ys = [pos[1] for _, pos in cam_items]
        zs = [pos[2] for _, pos in cam_items]
        labels = [cam_id for cam_id, _ in cam_items]

        fig.add_trace(
            go.Scatter3d(
                x=xs,
                y=ys,
                z=zs,
                mode="markers+text",
                marker=dict(size=7, symbol="circle"),
                text=labels,
                textposition="top center",
                name="Cameras",
                showlegend=True,
            )
        )
    # --- Camera view rays (short) ---
    dirs = detail.get("camera_view_dir_enu_event", {}) or {}
    if cams and dirs:
        L = 100.0  # meters
        xs, ys, zs = [], [], []

        for cam_id, pos in sorted(cams.items(), key=lambda kv: str(kv[0])):
            d = dirs.get(cam_id)
            if not d:
                continue
            e, n, u = float(pos[0]), float(pos[1]), float(pos[2])
            de, dn, du = float(d[0]), float(d[1]), float(d[2])

            xs += [e, e + L * de, None]
            ys += [n, n + L * dn, None]
            zs += [u, u + L * du, None]

        if xs:
            fig.add_trace(
                go.Scatter3d(
                    x=xs,
                    y=ys,
                    z=zs,
                    mode="lines",
                    line=dict(width=4),
                    name="View rays",
                    showlegend=True,
                )
            )

    for p in pts:
        hover_text = (
            f"Point: {p['point_index']}<br>"
            f"ENU: E={p['enu_e']:.2f}, N={p['enu_n']:.2f}, U={p['enu_u']:.2f}<br>"
            f"MeanDist: {p['mean_distance']:.3f} m<br>"
            f"CamsUsed: {p['cameras_used']}"
        )
        fig.add_trace(
            go.Scatter3d(
                x=[p["enu_e"]],
                y=[p["enu_n"]],
                z=[p["enu_u"]],
                mode="markers",
                marker=dict(size=8, symbol="diamond"),
                hovertext=hover_text,
                hoverinfo="text",
                showlegend=False,
            )
        )

    return fig
# ============================================================================
# Thumbnail-Helper
# ============================================================================
THUMB_WIDTH = 1200
THUMB_JPEG_QUALITY = 70

def ensure_thumb(full_path: str) -> str:
    """
    Returns thumb path for a given jpg; creates it if missing.
    Thumb file is stored next to original as *_thumb.jpg
    """
    if not full_path or not os.path.exists(full_path):
        return ""

    base, ext = os.path.splitext(full_path)
    thumb_path = f"{base}_thumb.jpg"

    if os.path.exists(thumb_path):
        return thumb_path

    try:
        img = cv2.imread(full_path, cv2.IMREAD_COLOR)
        if img is None:
            return ""

        h, w = img.shape[:2]
        if w > THUMB_WIDTH:
            scale = THUMB_WIDTH / float(w)
            img = cv2.resize(img, (THUMB_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA)

        cv2.imwrite(thumb_path, img, [int(cv2.IMWRITE_JPEG_QUALITY), int(THUMB_JPEG_QUALITY)])
        return thumb_path
    except Exception:
        return ""
    
# ============================================================================
# PowerOff und Reboot
# ============================================================================

def _run_remote_power(host: str, action: str) -> None:
    cmd = [
        "ssh",
        *SSH_OPTS,
        f"{PI_SSH_USER}@{host}",
        f"sudo -n /sbin/{action}",
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _host_reachable(host: str) -> bool:
    r = subprocess.run(["ping", "-c", "1", "-W", "1", host],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0

# ============================================================================
# Dash app
# ============================================================================
def init_dash_app():
    """
    Dash UI includes:
    - Live view with stable camera
    - Events + detail
    - Cameras
    - Logs
    - Trigger control panel (start/stop, interval, lead)
    """

    global dash_app, dash_last_sent_counter, dash_last_filter_state

    dash_app = Dash(__name__)

    server = dash_app.server

    @server.route("/img/<event_id>/<path:filename>")
    def serve_event_image(event_id, filename):
        directory = os.path.join(IMAGE_FOLDER, event_id)
        return send_from_directory(directory, filename)

    @server.route("/dbg/<path:filename>")
    def serve_debug_image(filename):
        return send_from_directory(DEBUG_FOLDER, filename)

    # Ensure base figure exists
    try:
        reset_live_figure_keep_cameras()
    except Exception:
        pass

    # -------------------------------
    # DataTable styling (dark + stable)
    # -------------------------------
    table_style_cell = {
        "backgroundColor": "transparent",
        "color": "#e7eaf0",
        "border": "none",
        "fontSize": "12px",
        "padding": "8px",
        "whiteSpace": "nowrap",
    }

    table_style_header = {
        "backgroundColor": "rgba(255,255,255,0.04)",
        "color": "#e7eaf0",
        "fontWeight": "800",
        "borderBottom": "1px solid #2a3240",
    }

    table_style_data_conditional = [
        {"if": {"row_index": "odd"}, "backgroundColor": "rgba(255,255,255,0.02)"},
        {"if": {"state": "hover"}, "backgroundColor": "rgba(42,98,255,0.20)"},
        {"if": {"state": "active"}, "backgroundColor": "rgba(42,98,255,0.28)"},
        {"if": {"state": "selected"}, "backgroundColor": "rgba(42,98,255,0.24)"},
    ]

    # -------------------------------
    # Layout
    # -------------------------------

    dash_app.layout = html.Div(
        className="container",
        children=[
            html.Div(
                className="header",
                children=[
                    html.Div(
                        className="title",
                        children=[
                            html.H1("Triangulation Webserver", className="h1"),
                            html.P("Live view, events, camera status, logs, CSV export, Trigger control", className="sub"),
                        ],
                    ),
                    html.Div(
                        className="row",
                        children=[
                            html.Button("CSV export", id="btn-export-csv", n_clicks=0, className="btn"),
                            dcc.Download(id="download-csv"),
                            html.Span("Status: running", className="badge"),
                            *[
                                html.A(label, href=url, target="_blank", className="btn")
                                for (label, url) in DEVICE_UI_LINKS
                            ],
                        ],
                    ),
                ],
            ),
            dcc.Store(id="camera-store-live", data=None),
            dcc.Store(id="selected-event-id", data=None),
            dcc.Store(id="camera-store-detail", data=None),
            dcc.Interval(id="interval", interval=DASH_UPDATE_INTERVAL, n_intervals=0),
            html.Div(
                className="tabWrap",
                children=[
                    dcc.Tabs(
                        id="tabs",
                        value="tab-live",
                        className="custom-tabs",
                        children=[
                            dcc.Tab(label="Live", value="tab-live", className="custom-tab", selected_className="custom-tab--selected"),
                            dcc.Tab(label="Events", value="tab-events", className="custom-tab", selected_className="custom-tab--selected"),
                            dcc.Tab(label="Cameras", value="tab-cameras", className="custom-tab", selected_className="custom-tab--selected"),
                            dcc.Tab(label="Trigger", value="tab-trigger", className="custom-tab", selected_className="custom-tab--selected"),
                            dcc.Tab(label="Logs", value="tab-logs", className="custom-tab", selected_className="custom-tab--selected"),
                        ],
                    ),
                    html.Div(
                        style={"padding": "12px"},
                        children=[
                            # LIVE TAB
                            html.Div(
                                id="tab-live-content",
                                children=[
                                    html.Div(
                                        className="card",
                                        children=[
                                            html.Div(
                                                className="cardTitle",
                                                children=[html.H2("Filters"), html.Span("Only affects visualization", className="badge")],
                                            ),
                                            html.Div(
                                                className="row",
                                                children=[
                                                    html.Span("min cams with motion", className="label"),
                                                    dcc.Input(id="filter-min-cams", type="number", value=0, min=0, step=1, className="input"),
                                                    html.Span("max avg mean dist (m)", className="label"),
                                                    dcc.Input(id="filter-max-avg-dist", type="number", value=None, min=0, step=0.1, className="input"),
                                                    html.Span("min motion pixels total", className="label"),
                                                    dcc.Input(id="filter-min-motion", type="number", value=0, min=0, step=1, className="input"),
                                                    html.Span("max events shown", className="label"),
                                                    dcc.Input(id="filter-max-events", type="number", value=200, min=1, step=1, className="input"),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(
                                        className="card",
                                        children=[
                                            html.Div(className="cardTitle", children=[html.H2("Live statistics")]),
                                            html.Div(
                                                className="kpis",
                                                children=[
                                                    html.Div(
                                                        className="kpi",
                                                        children=[html.Div("Events in memory", className="kpiLabel"), html.Div(id="stat-events-total", className="kpiValue")],
                                                    ),
                                                    html.Div(
                                                        className="kpi",
                                                        children=[html.Div("Events per minute (last 60s)", className="kpiLabel"), html.Div(id="stat-events-per-min", className="kpiValue")],
                                                    ),
                                                    html.Div(
                                                        className="kpi",
                                                        children=[html.Div("Avg accuracy (avg mean dist)", className="kpiLabel"), html.Div(id="stat-avg-accuracy", className="kpiValue")],
                                                    ),
                                                    html.Div(
                                                        className="kpi",
                                                        children=[html.Div("Avg triangulation time", className="kpiLabel"), html.Div(id="stat-avg-tri-ms", className="kpiValue")],
                                                    ),
                                                    html.Div(
                                                        className="kpi",
                                                        children=[html.Div("Max trigger?done (ms)", className="kpiLabel"), html.Div(id="stat-max-trigger-done", className="kpiValue")],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(
                                        className="card",
                                        children=[
                                            html.Div(
                                                className="cardTitle",
                                                children=[html.H2("Live 3D view"), html.Span("Rotate/zoom stays stable", className="badge")],
                                            ),
                                            dcc.Graph(id="live-graph", figure=make_base_live_figure(), style={"height": "62vh"}, config={"displayModeBar": True}),
                                        ],
                                    ),
                                ],
                            ),
                            # EVENTS TAB
                            html.Div(
                                id="tab-events-content",
                                style={"display": "none"},
                                children=[
                                    html.Div(
                                        className="card",
                                        children=[
                                            html.Div(
                                                className="cardTitle",
                                                children=[html.H2("Event table"), html.Span("Select a row to inspect details", className="badge")],
                                            ),
                                            dash_table.DataTable(
                                                id="event-table",
                                                columns=[
                                                    {"name": "timestamp", "id": "timestamp"},
                                                    {"name": "event_id", "id": "event_id"},
                                                    {"name": "cams_total", "id": "cameras_total"},
                                                    {"name": "cams_motion", "id": "cameras_with_motion"},
                                                    {"name": "motion_px", "id": "motion_pixels_total"},
                                                    {"name": "points", "id": "points_count"},
                                                    {"name": "avg_mean_dist", "id": "avg_mean_distance"},
                                                    {"name": "tri_ms", "id": "triangulation_ms"},
                                                    {"name": "t_first_rx_ms", "id": "trigger_to_first_rx_ms"},
                                                    {"name": "t_done_ms", "id": "trigger_to_done_ms"},
                                                ],
                                                data=[],
                                                page_size=15,
                                                row_selectable="single",
                                                selected_rows=[],
                                                fixed_rows={"headers": True},
                                                style_table={"overflowX": "auto"},
                                                style_as_list_view=True,
                                                style_cell=table_style_cell,
                                                style_header=table_style_header,
                                                style_data={"backgroundColor": "transparent", "color": "#e7eaf0"},
                                                style_data_conditional=table_style_data_conditional,
                                                css=[{"selector": "tr:hover", "rule": "background-color: inherit !important;"}],
                                            ),
                                        ],
                                    ),
                                    html.Div(
                                        className="card",
                                        children=[
                                            html.Div(className="cardTitle", children=[html.H2("Event detail"), html.Div(id="event-detail-header", className="badge")]),
                                            dcc.Graph(id="event-detail-graph", figure=go.Figure(), style={"height": "52vh"}, config={"displayModeBar": True}),
                                            html.Div(id="event-detail-links", className="mono", style={"marginTop": "10px"}),
                                            html.Div(className="divider"),
                                            html.Div(className="cardTitle", children=[html.H2("Timings per camera")]),
                                            dash_table.DataTable(
                                                id="timings-table",
                                                columns=[
                                                    {"name": "camera_id", "id": "camera_id"},
                                                    {"name": "receive_ms", "id": "receive_ms"},
                                                    {"name": "decode_ms", "id": "decode_ms"},
                                                    {"name": "motion_ms", "id": "motion_ms"},
                                                    {"name": "total_ms", "id": "total_ms"},
                                                    {"name": "motion_pixels", "id": "motion_pixels"},
                                                ],
                                                data=[],
                                                page_size=10,
                                                fixed_rows={"headers": True},
                                                style_table={"overflowX": "auto"},
                                                style_as_list_view=True,
                                                style_cell=table_style_cell,
                                                style_header=table_style_header,
                                                style_data={"backgroundColor": "transparent", "color": "#e7eaf0"},
                                                style_data_conditional=table_style_data_conditional,
                                                css=[{"selector": "tr:hover", "rule": "background-color: inherit !important;"}],
                                            ),
                                        ],
                                    ),
                                    html.Div(
                                        className="card",
                                        children=[
                                            html.Div(className="cardTitle", children=[html.H2("Preview (on demand)"), html.Span("Thumbs", className="badge")]),
                                            html.Div(
                                                className="row",
                                                children=[
                                                    dcc.Checklist(
                                                        id="preview-show-all",
                                                        options=[{"label": "Setup/Debug: auch ohne Motion", "value": "all"}],
                                                        value=[],
                                                        style={"marginRight": "12px"},
                                                    ),
                                                    dcc.Dropdown(id="preview-camera", options=[], value=None, clearable=False, style={"width": "260px"}),
                                                ],
                                            ),
                                            html.Div(
                                                className="row",
                                                style={"gap": "12px", "flexWrap": "wrap"},
                                                children=[
                                                    html.Div([
                                                        html.Div("Frame 0", className="badge"),
                                                        html.A(html.Img(id="preview-img0", style={"maxWidth": "420px"}), id="preview-link0", target="_blank"),
                                                    ]),
                                                    html.Div([
                                                        html.Div("Frame 1", className="badge"),
                                                        html.A(html.Img(id="preview-img1", style={"maxWidth": "420px"}), id="preview-link1", target="_blank"),
                                                    ]),
                                                    html.Div([
                                                        html.Div("Diff/Motion", className="badge"),
                                                        html.A(html.Img(id="preview-imgd", style={"maxWidth": "420px"}), id="preview-linkd", target="_blank"),
                                                    ]),
                                                ],
                                            ),
                                            html.Div(id="preview-hint", className="mono"),
                                        ],
                                    ),
                                ],
                            ),
                            # CAMERAS TAB
                            html.Div(
                                id="tab-cameras-content",
                                style={"display": "none"},
                                children=[
                                    html.Div(
                                        className="card",
                                        children=[
                                            html.Div(
                                                className="cardTitle",
                                                children=[html.H2("Camera status"), html.Span("Last seen, RX timing, GPS/orientation", className="badge")],
                                            ),
                                            dash_table.DataTable(
                                                id="camera-table",
                                                columns=[
                                                    {"name": "camera_id", "id": "camera_id"},
                                                    {"name": "last_seen", "id": "last_seen"},
                                                    {"name": "last_ip", "id": "last_ip"},
                                                    {"name": "last_rx_ms", "id": "last_rx_ms"},
                                                    {"name": "avg_rx_ms_50", "id": "avg_rx_ms_50"},
                                                    {"name": "gps_lat", "id": "gps_lat"},
                                                    {"name": "gps_lon", "id": "gps_lon"},
                                                    {"name": "gps_alt", "id": "gps_alt"},
                                                    {"name": "yaw", "id": "yaw"},
                                                    {"name": "pitch", "id": "pitch"},
                                                    {"name": "roll", "id": "roll"},
                                                ],
                                                data=[],
                                                page_size=12,
                                                fixed_rows={"headers": True},
                                                style_table={"overflowX": "auto"},
                                                style_as_list_view=True,
                                                style_cell=table_style_cell,
                                                style_header=table_style_header,
                                                style_data={"backgroundColor": "transparent", "color": "#e7eaf0"},
                                                style_data_conditional=table_style_data_conditional,
                                                css=[{"selector": "tr:hover", "rule": "background-color: inherit !important;"}],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            # TRIGGER TAB
                            html.Div(
                                id="tab-trigger-content",
                                style={"display": "none"},
                                children=[
                                    html.Div(
                                        className="card",
                                        children=[
                                            html.Div(
                                                className="cardTitle",
                                                children=[
                                                    html.H2("Trigger control"),
                                                    html.Span("Multicast trigger sender", className="badge"),
                                                ],
                                            ),
                                            html.Div(
                                                className="row",
                                                children=[
                                                    html.Span("interval (s)", className="label"),
                                                    dcc.Input(
                                                        id="trigger-interval",
                                                        type="number",
                                                        value=TRIGGER_DEFAULT_INTERVAL_S,
                                                        min=TRIGGER_MIN_INTERVAL_S,
                                                        step=0.1,
                                                        className="input",
                                                    ),
                                                    html.Span("lead time (s)", className="label"),
                                                    dcc.Input(
                                                        id="trigger-lead",
                                                        type="number",
                                                        value=TRIGGER_DEFAULT_LEAD_S,
                                                        min=0.0,
                                                        step=0.1,
                                                        className="input",
                                                    ),
                                                    html.Button("Start", id="btn-trigger-start", n_clicks=0, className="btn"),
                                                    html.Button("Stop", id="btn-trigger-stop", n_clicks=0, className="btn"),
                                                ],
                                            ),
                                            html.Div(className="divider"),
                                            html.Div(id="trigger-status", className="mono"),
                                        ],
                                    ),
                                    html.Div(
                                        className="card",
                                        children=[
                                            html.Div(
                                                className="cardTitle",
                                                children=[html.H2("Power / Reboot"), html.Span("Danger", className="badge")],
                                            ),

                                            html.Div(className="row", children=[
                                                dcc.ConfirmDialogProvider(
                                                    id="confirm-reboot-pi1",
                                                    message="Pi1 reboot ausloesen?",
                                                    children=html.Button("Reboot Pi1", className="btn"),
                                                ),
                                                dcc.ConfirmDialogProvider(
                                                    id="confirm-poweroff-pi1",
                                                    message="Pi1 shutdown ausloesen?",
                                                    children=html.Button("Shutdown Pi1", className="btn"),
                                                ),
                                            ]),

                                            html.Div(className="row", children=[
                                                dcc.ConfirmDialogProvider(
                                                    id="confirm-reboot-pi2",
                                                    message="Pi2 reboot ausloesen?",
                                                    children=html.Button("Reboot Pi2", className="btn"),
                                                ),
                                                dcc.ConfirmDialogProvider(
                                                    id="confirm-poweroff-pi2",
                                                    message="Pi2 shutdown ausloesen?",
                                                    children=html.Button("Shutdown Pi2", className="btn"),
                                                ),
                                            ]),

                                            html.Div(className="row", children=[
                                                dcc.ConfirmDialogProvider(
                                                    id="confirm-reboot-pi3",
                                                    message="Pi3 reboot ausloesen?",
                                                    children=html.Button("Reboot Pi3", className="btn"),
                                                ),
                                                dcc.ConfirmDialogProvider(
                                                    id="confirm-poweroff-pi3",
                                                    message="Pi3 shutdown ausloesen?",
                                                    children=html.Button("Shutdown Pi3", className="btn"),
                                                ),
                                            ]),

                                            html.Div(id="power-status", className="mono", style={"marginTop": "10px"}),
                                        ],
                                    ),
                                ],
                            ),
                            # LOGS TAB
                            html.Div(
                                id="tab-logs-content",
                                style={"display": "none"},
                                children=[
                                    html.Div(
                                        className="card",
                                        children=[
                                            html.Div(className="cardTitle", children=[html.H2("Server log (live)"), html.Span("Buffered lines", className="badge")]),
                                            dcc.Textarea(id="log-view", value="", className="logbox", readOnly=True),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    # -------------------------------
    # Tab switch callback
    @dash_app.callback(
        Output("tab-live-content", "style"),
        Output("tab-events-content", "style"),
        Output("tab-cameras-content", "style"),
        Output("tab-trigger-content", "style"),
        Output("tab-logs-content", "style"),
        Input("tabs", "value"),
    )
    def _switch_tabs(tab):
        show = {"display": "block"}
        hide = {"display": "none"}
        if tab == "tab-live":
            return show, hide, hide, hide, hide
        if tab == "tab-events":
            return hide, show, hide, hide, hide
        if tab == "tab-cameras":
            return hide, hide, show, hide, hide
        if tab == "tab-trigger":
            return hide, hide, hide, show, hide
        if tab == "tab-logs":
            return hide, hide, hide, hide, show
        return show, hide, hide, hide, hide

    # -------------------------------
    # CSV export
    @dash_app.callback(Output("download-csv", "data"), Input("btn-export-csv", "n_clicks"), prevent_initial_call=True)
    def export_csv(n_clicks):
        if not os.path.exists(RESULTS_CSV):
            return no_update
        return dcc.send_file(RESULTS_CSV)

    # -------------------------------
    # Helper: keep camera markers present in the global live figure WITHOUT rebuilding
    def _sync_cameras_into_global_live_figure():
        global dash_live_figure
        with dash_live_figure_lock:
            existing = set()
            for tr_ in dash_live_figure.data:
                nm = getattr(tr_, "name", None)
                if isinstance(nm, str) and nm.startswith("Camera "):
                    existing.add(nm.replace("Camera ", "", 1))

        with camera_positions_lock:
            cams = list(camera_positions_enu.items())

        to_add = []
        for cam_id, (e, n, u) in cams:
            if cam_id not in existing:
                to_add.append((cam_id, e, n, u))

        if not to_add:
            return

        with dash_live_figure_lock:
            for cam_id, e, n, u in to_add:
                dash_live_figure.add_trace(
                    go.Scatter3d(
                        x=[e],
                        y=[n],
                        z=[u],
                        mode="markers+text",
                        marker=dict(size=7, symbol="circle"),
                        text=[cam_id],
                        textposition="top center",
                        name=f"Camera {cam_id}",
                        showlegend=True,
                    )
                )
            dash_live_figure.update_layout(uirevision="keep-view")

    # -------------------------------
    # Live graph update WITHOUT resetting view
    @dash_app.callback(
        Output("live-graph", "figure"),
        Output("camera-store-live", "data"),
        Input("interval", "n_intervals"),
        Input("live-graph", "relayoutData"),
        Input("filter-min-cams", "value"),
        Input("filter-max-avg-dist", "value"),
        Input("filter-min-motion", "value"),
        Input("filter-max-events", "value"),
        State("camera-store-live", "data"),
    )
    def update_live_graph(n, relayout_data, min_cams, max_avg_dist, min_motion, max_events, camera_store):
        global dash_last_sent_counter, dash_last_filter_state


        min_cams_i = safe_int(min_cams, 0)
        max_avg_dist_f = safe_float(max_avg_dist, None)
        min_motion_i = safe_int(min_motion, 0)
        max_events_i = max(1, safe_int(max_events, 200))
        filter_state = (min_cams_i, max_avg_dist_f, min_motion_i, max_events_i)

        with processed_counter_lock:
            cur_counter = processed_counter

        data_changed = (cur_counter != dash_last_sent_counter)
        filter_changed = (dash_last_filter_state != filter_state)

        _sync_cameras_into_global_live_figure()

        if not data_changed and not filter_changed and camera_store is None:
            return no_update, camera_store

        if filter_changed:
            fig = make_base_live_figure()

            with camera_positions_lock:
                cams = list(camera_positions_enu.items())

            for cam_id, (e, nn, u) in cams:
                fig.add_trace(
                    go.Scatter3d(
                        x=[e],
                        y=[nn],
                        z=[u],
                        mode="markers+text",
                        marker=dict(size=7, symbol="circle"),
                        text=[cam_id],
                        textposition="top center",
                        name=f"Camera {cam_id}",
                        showlegend=True,
                    )
                )

            with processed_events_lock:
                evs = list(processed_events)[:max_events_i]

            for ev in reversed(evs):
                if safe_int(ev.get("cameras_with_motion"), 0) < min_cams_i:
                    continue
                if safe_int(ev.get("motion_pixels_total"), 0) < min_motion_i:
                    continue
                if max_avg_dist_f is not None:
                    a = ev.get("avg_mean_distance")
                    if a is None or float(a) > float(max_avg_dist_f):
                        continue

                ev_id = ev.get("event_id")
                if not ev_id:
                    continue

                with processed_events_by_id_lock:
                    detail = processed_events_by_id.get(ev_id)

                if not detail:
                    continue

                ts = detail.get("timestamp", "")
                for p in detail.get("points", []):
                    hover_text = (
                        f"Event: {ev_id}<br>"
                        f"Time: {ts}<br>"
                        f"ENU: E={p['enu_e']:.2f}, N={p['enu_n']:.2f}, U={p['enu_u']:.2f}<br>"
                        f"MeanDist: {p['mean_distance']:.3f} m<br>"
                        f"CamsUsed: {p['cameras_used']}"
                    )
                    fig.add_trace(
                        go.Scatter3d(
                            x=[p["enu_e"]],
                            y=[p["enu_n"]],
                            z=[p["enu_u"]],
                            mode="markers",
                            marker=dict(size=6),
                            hovertext=hover_text,
                            hoverinfo="text",
                            showlegend=False,
                        )
                    )

            if camera_store is not None:
                fig.update_layout(scene_camera=camera_store)
            fig.update_layout(uirevision="keep-view")

            dash_last_filter_state = filter_state
            dash_last_sent_counter = cur_counter
            return fig, camera_store

        with dash_live_figure_lock:
            fig_out = go.Figure(dash_live_figure)

        if camera_store is not None:
            fig_out.update_layout(scene_camera=camera_store)
        fig_out.update_layout(uirevision="keep-view")

        dash_last_sent_counter = cur_counter
        return fig_out, camera_store

    # -------------------------------
    # Stats panel
    @dash_app.callback(
        Output("stat-events-total", "children"),
        Output("stat-events-per-min", "children"),
        Output("stat-avg-accuracy", "children"),
        Output("stat-avg-tri-ms", "children"),
        Output("stat-max-trigger-done", "children"),
        Input("interval", "n_intervals"),
    )
    def update_stats(n):
        with processed_events_lock:
            evs = list(processed_events)

        total = len(evs)
        now = datetime.now()
        last_60 = 0
        acc_vals = []
        tri_vals = []
        tdone_vals = []

        for ev in evs:
            ts = ev.get("timestamp")
            try:
                dt = datetime.fromisoformat(ts)
            except Exception:
                continue

            if (now - dt).total_seconds() <= 60.0:
                last_60 += 1

            a = ev.get("avg_mean_distance")
            if a is not None:
                acc_vals.append(float(a))

            tms = ev.get("triangulation_ms")
            if tms is not None:
                tri_vals.append(float(tms))

            td = ev.get("trigger_to_done_ms")
            if td is not None:
                tdone_vals.append(float(td))

        avg_acc = (sum(acc_vals) / len(acc_vals)) if acc_vals else None
        avg_tri = (sum(tri_vals) / len(tri_vals)) if tri_vals else None
        max_tdone = max(tdone_vals) if tdone_vals else None

        s1 = f"{total}"
        s2 = f"{float(last_60):.1f}"
        s3 = f"{avg_acc:.3f} m" if avg_acc is not None else "n/a"
        s4 = f"{avg_tri:.1f} ms" if avg_tri is not None else "n/a"
        s5 = f"{max_tdone:.1f}" if max_tdone is not None else "n/a"
        return s1, s2, s3, s4, s5

    # -------------------------------
    # Camera table
    @dash_app.callback(Output("camera-table", "data"), Input("interval", "n_intervals"))
    def update_camera_table(n):
        with camera_status_lock:
            cams = list(camera_status.values())

        out = []
        for c in cams:
            out.append(
                {
                    "camera_id": c.get("camera_id"),
                    "last_seen": c.get("last_seen"),
                    "last_ip": c.get("last_ip"),
                    "last_rx_ms": round(float(c.get("last_rx_ms", 0.0)), 1) if c.get("last_rx_ms") is not None else None,
                    "avg_rx_ms_50": round(float(c.get("avg_rx_ms_50", 0.0)), 1) if c.get("avg_rx_ms_50") is not None else None,
                    "gps_lat": c.get("gps_lat"),
                    "gps_lon": c.get("gps_lon"),
                    "gps_alt": c.get("gps_alt"),
                    "yaw": c.get("yaw"),
                    "pitch": c.get("pitch"),
                    "roll": c.get("roll"),
                }
            )
        out.sort(key=lambda r: str(r.get("camera_id", "")))
        return out

    # -------------------------------
    # Event table (respects filters)
    @dash_app.callback(
        Output("event-table", "data"),
        Input("interval", "n_intervals"),
        Input("filter-min-cams", "value"),
        Input("filter-max-avg-dist", "value"),
        Input("filter-min-motion", "value"),
        Input("filter-max-events", "value"),
    )
    def update_event_table(n, min_cams, max_avg_dist, min_motion, max_events):
        min_cams_i = safe_int(min_cams, 0)
        min_motion_i = safe_int(min_motion, 0)
        max_events_i = max(1, safe_int(max_events, 200))
        max_avg_dist_f = safe_float(max_avg_dist, None)

        with processed_events_lock:
            evs = list(processed_events)[:max_events_i]

        out = []
        for ev in evs:
            if safe_int(ev.get("cameras_with_motion"), 0) < min_cams_i:
                continue
            if safe_int(ev.get("motion_pixels_total"), 0) < min_motion_i:
                continue
            if max_avg_dist_f is not None:
                a = ev.get("avg_mean_distance")
                if a is None or float(a) > float(max_avg_dist_f):
                    continue

            out.append(
                {
                    "timestamp": ev.get("timestamp"),
                    "event_id": ev.get("event_id"),
                    "cameras_total": ev.get("cameras_total"),
                    "cameras_with_motion": ev.get("cameras_with_motion"),
                    "motion_pixels_total": ev.get("motion_pixels_total"),
                    "points_count": ev.get("points_count"),
                    "avg_mean_distance": round(float(ev["avg_mean_distance"]), 4) if ev.get("avg_mean_distance") is not None else None,
                    "triangulation_ms": round(float(ev["triangulation_ms"]), 1) if ev.get("triangulation_ms") is not None else None,
                    "trigger_to_first_rx_ms": round(float(ev["trigger_to_first_rx_ms"]), 1) if ev.get("trigger_to_first_rx_ms") is not None else None,
                    "trigger_to_done_ms": round(float(ev["trigger_to_done_ms"]), 1) if ev.get("trigger_to_done_ms") is not None else None,
                }
            )
        return out

    @dash_app.callback(
    Output("selected-event-id", "data"),
    Input("event-table", "selected_rows"),
    State("event-table", "data"),
    )
    def store_selected_event_id(selected_rows, table_data):
        if not selected_rows or not table_data:
            return None
        idx = selected_rows[0]
        if idx < 0 or idx >= len(table_data):
            return None
        return table_data[idx].get("event_id")

    @dash_app.callback(
    Output("preview-camera", "options"),
    Output("preview-camera", "value"),
    Output("preview-img0", "src"),
    Output("preview-img1", "src"),
    Output("preview-imgd", "src"),
    Output("preview-link0", "href"),
    Output("preview-link1", "href"),
    Output("preview-linkd", "href"),
    Output("preview-hint", "children"),
    Input("selected-event-id", "data"),
    Input("preview-camera", "value"),
    Input("preview-show-all", "value"),
    )
    def update_preview(event_id, cam_value, show_all):
        if not event_id:
            return [], None, "", "", "", "", "", "", "Kein Event gewaehlt."

        with processed_events_by_id_lock:
            detail = processed_events_by_id.get(event_id)
        if not detail:
            return [], None, "", "", "", "", "", "", f"Event nicht mehr im RAM: {event_id}"

        timings = detail.get("camera_timings", {}) or {}
        all_cams = sorted(timings.keys(), key=str)

        allow_all = ("all" in (show_all or []))
        if not allow_all:
            PREVIEW_MIN_MOTION_PIXELS = 1 # oder 100, je nach Noise

            cams = [c for c in all_cams if int(timings.get(c, {}).get("motion_pixels", 0)) >= PREVIEW_MIN_MOTION_PIXELS]
        else:
            cams = all_cams

        options = [{"label": c, "value": c} for c in cams]

        if not cams:
            return [], None, "", "", "", "", "", "", "Keine Motion in diesem Event (Setup/Debug aktivieren, um trotzdem Frames zu sehen)."

        if cam_value not in cams:
            cam_value = cams[0]

        folder = detail.get("event_folder")  # z.B. images/<event_id>
        if not folder or not os.path.exists(folder):
            return options, cam_value, "", "", "", "", "", "", "Event-Ordner fehlt auf Disk."

        # Frames per Pattern finden (minimal-invasiv)
        f0_list = glob.glob(os.path.join(folder, f"{cam_value}_*_frame0.jpg"))
        f1_list = glob.glob(os.path.join(folder, f"{cam_value}_*_frame1.jpg"))

        # robust: nimm die neueste Datei (falls mehrere)
        f0 = max(f0_list, key=os.path.getmtime) if f0_list else ""
        f1 = max(f1_list, key=os.path.getmtime) if f1_list else ""

        # Debug/Diff Bild
        dbg_map = detail.get("debug_images", {}) or {}
        df = dbg_map.get(cam_value, "")

        # Thumbs erzeugen (on demand)
        f0_t = ensure_thumb(f0)
        f1_t = ensure_thumb(f1)
        df_t = ensure_thumb(df)

        # URLs (Routes aus 3.3)
        def img_url(path: str) -> str:
            return f"/img/{event_id}/{os.path.basename(path)}" if path else ""

        def dbg_url(path: str) -> str:
            return f"/dbg/{os.path.basename(path)}" if path else ""

        src0 = img_url(f0_t)
        src1 = img_url(f1_t)
        srcd = dbg_url(df_t)

        href0 = img_url(f0)
        href1 = img_url(f1)
        hrefd = dbg_url(df)

        hint = f"Event {event_id} | Cam {cam_value} | {'Setup/Debug' if allow_all else 'Motion-only'}"
        return options, cam_value, src0, src1, srcd, href0, href1, hrefd, hint
    
    # -------------------------------
    # Event detail camera store (prevents plot reset while rotating)
    @dash_app.callback(
        Output("camera-store-detail", "data"),
        Input("event-detail-graph", "relayoutData"),
        State("camera-store-detail", "data"),
        prevent_initial_call=True,
    )
    def store_event_detail_camera(relayout_data, camera_store_detail):
        if isinstance(relayout_data, dict):
            cam = relayout_data.get("scene.camera")
            if cam is not None:
                return cam
        return no_update

    # -------------------------------
    # Event detail (and keep its 3D view stable too)
    @dash_app.callback(
    Output("event-detail-header", "children"),
    Output("event-detail-graph", "figure"),
    Output("event-detail-links", "children"),
    Output("timings-table", "data"),
    Input("selected-event-id", "data"),
    State("camera-store-detail", "data"),
    )
    def update_event_detail(event_id, camera_store_detail):
        if not event_id:
            fig = go.Figure()
            fig.update_layout(uirevision="keep-view")
            return "Select an event in the table to see details.", fig, "", []

        with processed_events_by_id_lock:
            detail = processed_events_by_id.get(event_id)

        
        if not detail:
            fig = go.Figure()
            fig.update_layout(uirevision="keep-view")
            return f"Event not found in memory: {event_id}", fig, "", []

        header = (
            f"Event: {event_id} | time: {detail.get('timestamp')} | "
            f"cams_total: {detail.get('cameras_total')} | cams_motion: {detail.get('cameras_with_motion')} | "
            f"motion_px: {detail.get('motion_pixels_total')} | points: {detail.get('points_count')} | "
            f"avg_mean_dist: {detail.get('avg_mean_distance')} | "
            f"t_first_rx_ms: {detail.get('trigger_to_first_rx_ms')} | t_done_ms: {detail.get('trigger_to_done_ms')}"
        )

        fig = build_event_detail_figure(event_id)
        if camera_store_detail is not None:
            fig.update_layout(scene_camera=camera_store_detail)
        fig.update_layout(uirevision="keep-view")

        links = []
        folder = detail.get("event_folder")
        plot_path = detail.get("plot_html_path")
        if folder:
            links.append(html.Div(f"Event folder: {folder}", className="mono"))
        if plot_path:
            links.append(html.Div(f"Plot HTML: {plot_path}", className="mono"))
        if detail.get("trigger_sent_utc") or detail.get("trigger_time_utc"):
            links.append(html.Div(f"Trigger sent: {detail.get('trigger_sent_utc')}", className="mono"))
            links.append(html.Div(f"Trigger time: {detail.get('trigger_time_utc')}", className="mono"))

        timings = []
        ct = detail.get("camera_timings", {})
        for cam_id, t in ct.items():
            timings.append(
                {
                    "camera_id": cam_id,
                    "receive_ms": round(float(t.get("receive_ms", 0.0)), 1),
                    "decode_ms": round(float(t.get("decode_ms", 0.0)), 1),
                    "motion_ms": round(float(t.get("motion_ms", 0.0)), 1),
                    "total_ms": round(float(t.get("total_ms", 0.0)), 1),
                    "motion_pixels": int(float(t.get("motion_pixels", 0.0))),
                }
            )
        timings.sort(key=lambda r: str(r.get("camera_id", "")))

        return header, fig, links, timings

    # -------------------------------
    # POWERundRESET
    @dash_app.callback(
    Output("power-status", "children"),
    Input("confirm-reboot-pi1", "submit_n_clicks"),
    Input("confirm-poweroff-pi1", "submit_n_clicks"),
    Input("confirm-reboot-pi2", "submit_n_clicks"),
    Input("confirm-poweroff-pi2", "submit_n_clicks"),
    Input("confirm-reboot-pi3", "submit_n_clicks"),
    Input("confirm-poweroff-pi3", "submit_n_clicks"),
    prevent_initial_call=True,
    )
    def power_actions(*_):
        trig = callback_context.triggered[0]["prop_id"].split(".")[0]

        mapping = {
            "confirm-reboot-pi1": ("pi1", "reboot"),
            "confirm-poweroff-pi1": ("pi1", "poweroff"),
            "confirm-reboot-pi2": ("pi2", "reboot"),
            "confirm-poweroff-pi2": ("pi2", "poweroff"),
            "confirm-reboot-pi3": ("pi3", "reboot"),
            "confirm-poweroff-pi3": ("pi3", "poweroff"),
        }

        if trig not in mapping:
            return no_update

        dev, action = mapping[trig]
        host = PI_HOSTS.get(dev)
        if not host:
            return f"Fehler: Host fehlt fuer {dev}"

        # Ohne Pis: sauberer Status statt "tut nix"
        try:
            if not _host_reachable(host):
                return f"{dev} ({host}) nicht erreichbar (ping)."
        except Exception:
            pass

        _run_remote_power(host, action)
        return f"{dev} ({host}) {action} ausgeloest."

    # -------------------------------
    # Live log view
    @dash_app.callback(Output("log-view", "value"), Input("interval", "n_intervals"))
    def update_log_view(n):
        with log_lock:
            if len(log_buffer) == 0:
                return "No log lines buffered yet."
            return "\n".join(log_buffer)

    # -------------------------------
    # Trigger control (Start/Stop + status)
    @dash_app.callback(
        Output("btn-trigger-start", "style"),
        Output("btn-trigger-stop", "style"),
        Output("trigger-status", "children"),
        Input("btn-trigger-start", "n_clicks"),
        Input("btn-trigger-stop", "n_clicks"),
        Input("interval", "n_intervals"),
        State("trigger-interval", "value"),
        State("trigger-lead", "value"),
        prevent_initial_call=False,
    )
    def trigger_start_stop(n_start, n_stop, n_tick, interval_val, lead_val):
        trig = None
        try:
            trig = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else None
        except Exception:
            trig = None
        global trigger_enabled, trigger_interval_s, trigger_lead_s
        interval_s = float(interval_val) if interval_val is not None else TRIGGER_DEFAULT_INTERVAL_S
        lead_s = float(lead_val) if lead_val is not None else TRIGGER_DEFAULT_LEAD_S
        interval_s = max(TRIGGER_MIN_INTERVAL_S, interval_s)
        lead_s = max(float(TRIGGER_MIN_LEAD_S), float(trigger_lead_s))

        

        # Apply latest interval/lead always (so UI changes take effect without extra Apply button)
        with trigger_lock:
            trigger_interval_s = float(interval_s)
            trigger_lead_s = float(lead_s)

        if trig == "btn-trigger-start":
            with trigger_lock:
                trigger_enabled = True
            log(f"[Trigger] START interval_s={interval_s:.3f} lead_s={lead_s:.3f}")

        elif trig == "btn-trigger-stop":
            with trigger_lock:
                trigger_enabled = False
            log("[Trigger] STOP")

        with trigger_lock:
            en = bool(trigger_enabled)
            interval_cur = float(trigger_interval_s)
            lead_cur = float(trigger_lead_s)
            cnt = int(trigger_count)
            last = dict(trigger_last_sent) if isinstance(trigger_last_sent, dict) else None

        if en:
            start_style = {"backgroundColor": "#1f8b4c", "color": "white", "border": "none"}
            stop_style = {"backgroundColor": "rgba(255,255,255,0.08)", "color": "#e7eaf0", "border": "none"}
            state_txt = "RUNNING"
        else:
            start_style = {"backgroundColor": "rgba(255,255,255,0.08)", "color": "#e7eaf0", "border": "none"}
            stop_style = {"backgroundColor": "#b3261e", "color": "white", "border": "none"}
            state_txt = "STOPPED"

        lock_txt = "LOCK=OK" if _trigger_lock_ok else "LOCK=NO (another sender?)"
        if last:
            last_line = f" | last={last.get('event_id')} @ {last.get('sent_utc')}"
        else:
            last_line = ""

        status = f"Trigger: {state_txt} | interval={interval_cur:.2f}s | lead={lead_cur:.2f}s | sent={cnt} | {lock_txt}{last_line}"
        return start_style, stop_style, status

    return dash_app


def run_dash_server():
    log(f"[Dash] Starting Dash server on port {DASH_PORT}")

    def open_browser():
        try:
            webbrowser.open_new(f"http://localhost:{DASH_PORT}/")
        except Exception:
            pass

    Timer(1.5, open_browser).start()
    dash_app.run(host="0.0.0.0", port=DASH_PORT, debug=False, use_reloader=False)


# ============================================================================
# Signal handling
# ============================================================================
def signal_handler(signum, frame):
    log("[Main] Shutdown signal received")
    shutdown_event.set()
    event_store.shutdown()


# ============================================================================
# Main
# ============================================================================
def main():
    log("=" * 70)
    log("EVENT DATA SERVER - Motion Detection and Triangulation Pipeline")
    log("=" * 70)
    log(f"[Config] Server: {SERVER_HOST}:{SERVER_PORT}")
    log(f"[Config] Required cameras: {REQUIRED_CAMS}")
    log(f"[Config] Event timeout: {EVENT_TIMEOUT}s")
    log(f"[Config] Save to disk: {SAVE_TO_DISK}")
    log(f"[Config] Triangulation max distance: {TRIANGULATION_MAX_DISTANCE}m")
    log(f"[Config] Calibration folder: {CALIBRATION_FOLDER}")
    log(f"[Config] Results CSV: {RESULTS_CSV}")
    log(f"[Config] Dash port: {DASH_PORT}")
    log(f"[Trigger] Multicast: {TRIGGER_MULTICAST_GROUP}:{TRIGGER_PORT} TTL={TRIGGER_TTL}")
    log(f"[Trigger] Defaults: interval={TRIGGER_DEFAULT_INTERVAL_S}s lead={TRIGGER_DEFAULT_LEAD_S}s (starts OFF)")
    log("=" * 70)

    os.makedirs(IMAGE_FOLDER, exist_ok=True)
    os.makedirs(DEBUG_FOLDER, exist_ok=True)

    load_calibration_files()
    init_csv_file()

    init_dash_app()
    dash_thread = threading.Thread(target=run_dash_server, name="Dash", daemon=True)
    dash_thread.start()
    log(f"[Dash] Running on http://localhost:{DASH_PORT}")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    threads = []

    t_proc = threading.Thread(target=processing_worker, name="Processor")
    t_proc.start()
    threads.append(t_proc)

    t_clean = threading.Thread(target=cleanup_worker, name="Cleanup")
    t_clean.start()
    threads.append(t_clean)

    t_disk = threading.Thread(target=disk_cleanup_worker, name="DiskCleanup", daemon=True)
    t_disk.start()
    threads.append(t_disk)
    
    # Trigger thread (guarded: starts once per process)
    with _trigger_thread_started_lock:
        global _trigger_thread_started
        if not _trigger_thread_started:
            _trigger_thread_started = True
            t_trig = threading.Thread(target=trigger_worker, name="Trigger")
            t_trig.start()
            threads.append(t_trig)
        else:
            log("[Trigger] WARNING: trigger thread already started; skipping second start")

    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((SERVER_HOST, SERVER_PORT))
        s.listen(5)
        s.settimeout(1.0)

        log(f"[Server] Listening on {SERVER_HOST}:{SERVER_PORT} (Ctrl+C to stop)")

        while not shutdown_event.is_set():
            try:
                conn, addr = s.accept()
                threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
            except socket.timeout:
                continue
            except Exception as e:
                if not shutdown_event.is_set():
                    log(f"[Server] Accept error: {e}")

    except Exception as e:
        log(f"[Server] Critical error: {e}")

    finally:
        log("[Server] Shutting down...")
        try:
            if s:
                s.close()
        except Exception:
            pass

        for th in threads:
            th.join(timeout=5)

        log("[Server] Clean shutdown complete")


if __name__ == "__main__":
    main()
