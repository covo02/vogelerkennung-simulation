# ------------------------------------------------------------------------------
# orientation_web.py - Lokaler Diagnose-Webserver fuer Orientierung & Sensorik
#
# Autoren:              Jonas Wigge, Jens Kuper, Leon Key
# Erstellungszeitraum:  20.11.2025
# Status:               Final
# Freigabe:             Ja
#
# Dieses Modul stellt einen leichtgewichtigen Webserver bereit, der alle lokalen
# Sensordaten des Raspberry Pi anzeigt:
#
#   - Orientierung (Yaw, Pitch, Roll)
#   - GPS-Daten (RAW + gemittelt)
#   - BME280-Umweltwerte (Temperatur, Druck, Feuchte)
#   - BH1750-Luxwert
#   - Systemstatus (Sample Count, Accuracy, Zeitstempel)
#
# Nutzer koennen ueber die Weboberflaeche ausserdem:
#   die IMU-Messreihe zuruecksetzen ("Reset Orientation")
#
# Der Webserver ermoeglicht eine Live-Diagnose, was fuer die Entwicklung,
# Inbetriebnahme und Fehleranalyse des Gesamtsystems extrem wichtig ist.
#
#Version: 07-03-2026
#
#
# Das Modul wird in einem eigenen Thread vom trigger.py-Hauptprozess gestartet.
# ------------------------------------------------------------------------------

from BME280 import get_temperature, get_pressure, get_humidity, get_altitude_baro
from BH1750 import get_illumination
from gps_reader import get_gps_data, get_gps_raw
from flask import Flask, render_template_string, jsonify
from orientation_reader import (
    reset_orientation_measurement,
    get_orientation_data,
    set_zero_now,
    ORI_SAMPLE_COUNT
)
from collections import deque

SAMPLE_WINDOW_SIZE = 200   # Anzahl Samples fuer Mittelung (muss uebereinstimmen mit gps_reader)
history_alt_bme = deque(maxlen=SAMPLE_WINDOW_SIZE)


# --- Erweiterungen fuer alternative Hoehe ---
# --- GPS Auswahl (statt nur Hoehe) ---
GPS_MODE = "avg"   # "avg" oder "manual"
MANUAL_GPS = {"lat": None, "lon": None, "alt": None}

# --- IMU Auswahl ---
IMU_MODE = "sensor"  # "sensor" oder "manual"
MANUAL_IMU = {"yaw": None, "pitch": None, "roll": None, "acc": None}


# Die Flask-App wird erstellt. Es werden keine externen Templates benoetigt,
# da alle HTML-Elemente per render_template_string generiert werden.
app = Flask(__name__)

def avg(d):
    return sum(d)/len(d) if d else None


# HTML-Template fuer die Weboberflaeche.
# Es enthaelt einfache Styling-Variablen und ein JavaScript,
# welches alle 500 ms die Daten vom /status-Endpunkt abruft.
#
# Die Daten werden anschliessend im DOM aktualisiert - dadurch entsteht
# ein Live-Monitor ohne Seiten-Reload.
HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>PI3 - Dashboard</title>
  <link rel="icon" type="image/svg+xml"
      href="data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2032%2032'%3E%3Crect%20width='32'%20height='32'%20rx='7'%20fill='%23020a1a'/%3E%3Cpath%20d='M8%2014c5-5%2011-5%2016%200'%20fill='none'%20stroke='%239ca3af'%20stroke-width='2.4'%20stroke-linecap='round'/%3E%3Cpath%20d='M11%2017c3-3%207-3%2010%200'%20fill='none'%20stroke='%239ca3af'%20stroke-width='2.4'%20stroke-linecap='round'/%3E%3Cpath%20d='M14%2020c1-1%203-1%204%200'%20fill='none'%20stroke='%239ca3af'%20stroke-width='2.4'%20stroke-linecap='round'/%3E%3Cpath%20d='M10%2010%20L22%2022'%20stroke='%23ef4444'%20stroke-width='3'%20stroke-linecap='round'/%3E%3Ccircle%20cx='16'%20cy='24'%20r='1.8'%20fill='%239ca3af'/%3E%3C/svg%3E">
  <style>
    :root {
      --bg: #0f172a;
      --bg-card: #111827;
      --accent: #3b82f6;
      --accent-soft: rgba(59,130,246,0.15);
      --text: #e5e7eb;
      --text-soft: #9ca3af;
      --danger: #ef4444;
      --success: #22c55e;
      --border: #1f2937;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top left, #1f2937, #020617);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .wrapper {
      width: 100%;
      max-width: 900px;
      padding: 24px;
      position: relative;
    }

    /* top navigation buttons */
    .top-buttons {
      position: absolute;
      top: -10px;
      left: 0;
      right: 0;
      display: flex;
      justify-content: space-between;
      padding: 0 8px;
    }
    .nav-btn {
      padding: 6px 14px;
      border-radius: 999px;
      background: rgba(255,255,255,0.08);
      border: 1px solid rgba(255,255,255,0.15);
      color: #e5e7eb;
      font-size: 13px;
      font-weight: 500;
      text-decoration: none;
      backdrop-filter: blur(6px);
      transition: all 0.12s ease;
    }
    .nav-btn:hover { background: rgba(255,255,255,0.18); transform: translateY(-1px); }
    .nav-btn:active { transform: translateY(0); background: rgba(255,255,255,0.12); }

    .card {
      background: var(--bg-card);
      border-radius: 16px;
      border: 1px solid var(--border);
      box-shadow: 0 25px 50px -12px rgba(15,23,42,0.7);
      padding: 24px 24px 20px;
      margin-top: 32px;
    }
    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 16px;
    }
    .title-group h1 { margin: 0; font-size: 20px; letter-spacing: 0.03em; }
    .title-group p { margin: 4px 0 0; font-size: 13px; color: var(--text-soft); }

    .status-pill {
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .status-pill.ready {
      background: rgba(34,197,94,0.15);
      color: var(--success);
      border: 1px solid rgba(34,197,94,0.4);
    }
    .status-pill.idle {
      background: rgba(148,163,184,0.12);
      color: var(--text-soft);
      border: 1px solid rgba(148,163,184,0.4);
    }
    .status-dot { width: 7px; height: 7px; border-radius: 999px; background: currentColor; }

    @keyframes pulseGreen {
        0%, 100% { opacity: 1; filter: brightness(1); }
        50%      { opacity: 0.25; filter: brightness(1.3); }
    }

    /* immer gruen blinkend */
    .status-pill.always-running {
        background: rgba(34,197,94,0.15);
        color: var(--success);
        border: 1px solid rgba(34,197,94,0.45);
        animation: pulseGreen 3s ease-in-out infinite;
    }

    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-auto-rows: auto;
      gap: 10px;
      margin-top: 8px;
    }
    .section {
      border-radius: 12px;
      border: 1px solid var(--border);
      padding: 14px 16px 12px;
      background: linear-gradient(135deg, #020617 0%, #020617 40%, #030712 100%);
    }
    .section h2 {
      margin: 0 0 8px;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--text-soft);
    }
    .section-full { grid-column: 1 / -1; }

    .angles {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-top: 4px;
    }
    .angles-bottom {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
      margin-top: 4px;
    }
    .gps-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }

    .angle-card {
      padding: 10px 10px 10px;
      border-radius: 10px;
      background: radial-gradient(circle at top, rgba(59,130,246,0.13), rgba(15,23,42,0.7));
      border: 1px solid rgba(31,41,55,0.9);
    }
    .angle-label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      color: var(--text-soft);
      margin-bottom: 2px;
    }
    .angle-value { font-size: 20px; font-weight: 600; }
    .angle-unit { font-size: 11px; color: var(--text-soft); margin-left: 4px; }
    .angle-sub { font-size: 11px; color: var(--text-soft); margin-top: 6px; }

    .meta-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-top: 10px;
      font-size: 11px;
      color: var(--text-soft);
    }
    .timestamp { font-family: "JetBrains Mono", monospace; font-size: 11px; }

    .button-row { display: flex; justify-content: flex-end; gap: 10px; }

    .btn-primary {
      border: none;
      border-radius: 999px;
      padding: 8px 16px;
      font-size: 13px;
      font-weight: 600;
      background: linear-gradient(135deg, #2563eb, #38bdf8);
      color: #e5f2ff;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      box-shadow: 0 10px 30px rgba(37,99,235,0.6);
      transition: transform 0.08s ease, box-shadow 0.08s ease, filter 0.08s ease;
    }
    .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 18px 40px rgba(37,99,235,0.75); filter: brightness(1.05); }
    .btn-primary:active { transform: translateY(0px); box-shadow: 0 10px 24px rgba(37,99,235,0.7); filter: brightness(0.98); }

    .btn-primary-icon {
      width: 14px; height: 14px;
      border-radius: 999px;
      border: 2px solid rgba(226,239,255,0.9);
      box-shadow: 0 0 0 3px rgba(15,23,42,0.9);
      position: relative;
    }
    .btn-primary-icon::after {
      content: "";
      position: absolute;
      inset: 2px;
      border-radius: inherit;
      background: rgba(226,239,255,0.95);
    }

    .btn-active {
      background: linear-gradient(135deg, #22c55e, #4ade80) !important;
      color: white !important;
      box-shadow: 0 0 10px rgba(34,197,94,0.5);
    }

    .progress-wrap { margin-top: 10px; }
    .progress-bar-bg {
      width: 100%;
      height: 6px;
      border-radius: 999px;
      background: rgba(31,41,55,0.95);
      overflow: hidden;
      position: relative;
    }
    .progress-bar-fill {
      height: 100%;
      width: 0%;
      border-radius: 999px;
      background: linear-gradient(90deg, #22c55e, #a3e635);
      transition: width 0.15s ease-out;
    }
    .progress-text {
      font-size: 11px;
      margin-top: 6px;
      color: var(--text-soft);
      display: flex;
      justify-content: space-between;
    }

    .status-raw {
      margin-top: 12px;
      border-radius: 10px;
      background: rgba(15,23,42,0.9);
      border: 1px dashed rgba(55,65,81,0.9);
      padding: 8px 10px;
      font-size: 11px;
      max-height: 160px;
      overflow: auto;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      color: #9ca3af;
    }

    .pill-small {
      padding: 2px 7px;
      border-radius: 999px;
      border: 1px solid rgba(148,163,184,0.5);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--text-soft);
    }

    .signature {
      position: fixed;
      bottom: 14px;
      right: 20px;
      padding: 6px 12px;
      font-size: 12px;
      font-weight: 500;
      color: rgba(255,255,255,0.65);
      border-radius: 999px;
      background: rgba(255,255,255,0.05);
      backdrop-filter: blur(4px);
      border: 1px solid rgba(255,255,255,0.12);
      box-shadow: 0 0 15px rgba(0,0,0,0.35);
      transition: 0.2s ease;
      user-select: none;
    }
    .signature:hover {
      background: rgba(255,255,255,0.12);
      color: rgba(255,255,255,0.9);
      transform: translateY(-1px);
      box-shadow: 0 0 18px rgba(0,0,0,0.45);
    }

    input[type="number"]{
      width:100%;
      padding:6px;
      border-radius:6px;
      background:#0f172a;
      color:#e5e7eb;
      border:1px solid #1f2937;
      outline: none;
    }
  </style>
</head>

<body>
  <div class="wrapper">

    <div class="top-buttons">
      <a href="http://192.168.178.11:8080" target="_blank" class="nav-btn">zu PI1</a>
      <a href="http://192.168.178.5:8050" target="_blank" class="nav-btn">zum Server-Dashboard</a>
      <a href="http://192.168.178.22:8080" target="_blank" class="nav-btn">zu PI2</a>
    </div>

    <div class="card">
      <div class="header">
        <div class="title-group">
          <h1>Dashboard - PI Camera 3</h1>
        </div>
        <div id="status-pill" class="status-pill always-running">
          <span class="status-dot"></span>
          <span id="status-label">online</span>
        </div>
      </div>

      <div class="grid">

        <!-- IMU averaged -->
        <div class="section">
          <h2>IMU — Averaged</h2>
          <div class="angles">
            <div class="angle-card">
              <div class="angle-label">Yaw</div>
              <div class="angle-value">
                <span id="val-yaw">--.--</span><span class="angle-unit">deg</span>
              </div>
              <div class="angle-sub">Heading (north)</div>
            </div>

            <div class="angle-card">
              <div class="angle-label">Pitch</div>
              <div class="angle-value">
                <span id="val-pitch">--.--</span><span class="angle-unit">deg</span>
              </div>
              <div class="angle-sub">Tilt forward / backward</div>
            </div>

            <div class="angle-card">
              <div class="angle-label">Roll</div>
              <div class="angle-value">
                <span id="val-roll">--.--</span><span class="angle-unit">deg</span>
              </div>
              <div class="angle-sub">Tilt left / right</div>
            </div>

            <div class="angle-card">
              <div class="angle-label">Accuracy</div>
              <div class="angle-value">
                <span id="val-acc">--</span><span class="angle-unit"> out of 3</span>
              </div>
              <div class="angle-sub">Move in 8-figure to increase</div>
            </div>
          </div>

          <div class="meta-row">
            <span class="timestamp" id="val-timestamp">timestamp: --</span>
            <span class="pill-small" id="val-source">source: --</span>
          </div>
        </div>

        <!-- GPS averaged -->
        <div class="section">
          <h2>GPS — Averaged</h2>
          <div class="angles gps-grid">
            <div class="angle-card">
              <div class="angle-label">Lat</div>
              <div class="angle-value"><span id="gps-avg-lat">--</span></div>
            </div>
            <div class="angle-card">
              <div class="angle-label">Lon</div>
              <div class="angle-value"><span id="gps-avg-lon">--</span></div>
            </div>
            <div class="angle-card">
              <div class="angle-label">Alt</div>
              <div class="angle-value"><span id="gps-avg-alt">--</span></div>
            </div>
          </div>
          <h2>----------------------------------------------------</h2>
          <!-- GPS raw -->
            <h2>GPS — RAW</h2>
            <div class="angles gps-grid">
                <div class="angle-card">
                <div class="angle-label">Lat</div>
                <div class="angle-value"><span id="gps-raw-lat">--</span></div>
                </div>
                <div class="angle-card">
                <div class="angle-label">Lon</div>
                <div class="angle-value"><span id="gps-raw-lon">--</span></div>
                </div>
                <div class="angle-card">
                <div class="angle-label">Alt</div>
                <div class="angle-value"><span id="gps-raw-alt">--</span></div>
                </div>
            </div>
        </div>

        

        <!-- Measurement control -->
        <div class="section section-full">
          <h2>Measurement control for IMU</h2>
          <div class="button-row">
            <button id="btn-reset" class="btn-primary" onclick="triggerReset()">
              <span class="btn-primary-icon"></span>
              <span>Start / Reset measurement</span>
            </button>
            <button id="btn-zero" class="btn-primary" onclick="triggerSetZero()">
              <span class="btn-primary-icon"></span>
              <span>Set Zero (use current pose)</span>
            </button>
          </div>

          <div class="progress-wrap">
            <div class="progress-bar-bg">
              <div class="progress-bar-fill" id="progress-bar"></div>
            </div>
            <div class="progress-text">
              <span id="progress-label">Waiting for data...</span>
              <span id="progress-count">0 / 0 samples</span>
            </div>
          </div>

          <div class="status-raw" id="status-raw">Waiting for data...</div>
        </div>

        <!-- GPS Source -->
        <div class="section section-full">
          <h2>GPS Source</h2>

          <div class="angles">
            <div class="angle-card">
              <div class="angle-label">Manual Lat</div>
              <input id="manual-gps-lat" type="number" step="0.0000001">
            </div>

            <div class="angle-card">
              <div class="angle-label">Manual Lon</div>
              <input id="manual-gps-lon" type="number" step="0.0000001">
            </div>

            <div class="angle-card">
              <div class="angle-label">Manual Alt (m)</div>
              <input id="manual-gps-alt" type="number" step="0.1">
            </div>

            <div class="angle-card" style="display:flex; flex-direction:column; justify-content:center; gap:8px;">
              <button id="btn-gps-avg" class="btn-primary" onclick="useGpsAvg()">Use Averaged GPS</button>
              <button id="btn-gps-man" class="btn-primary" onclick="useGpsManual()">Use Manual GPS</button>
            </div>
          </div>

          <div class="angle-sub">
            Active GPS mode: <span id="gps-mode-label">avg</span>
            — Used GPS: <span id="gps-used-label">--</span>
          </div>
        </div>

        <!-- IMU Source -->
        <div class="section section-full">
          <h2>IMU Source</h2>

          <div class="angles">
            <div class="angle-card">
              <div class="angle-label">Manual Yaw</div>
              <input id="manual-imu-yaw" type="number" step="0.01">
            </div>

            <div class="angle-card">
              <div class="angle-label">Manual Pitch</div>
              <input id="manual-imu-pitch" type="number" step="0.01">
            </div>

            <div class="angle-card">
              <div class="angle-label">Manual Roll</div>
              <input id="manual-imu-roll" type="number" step="0.01">
            </div>

            <div class="angle-card" style="display:flex; flex-direction:column; justify-content:center; gap:8px;">
              <button id="btn-imu-sensor" class="btn-primary" onclick="useImuSensor()">Use Sensor IMU</button>
              <button id="btn-imu-man" class="btn-primary" onclick="useImuManual()">Use Manual IMU</button>
            </div>
          </div>

          <div class="angle-sub">
            Active IMU mode: <span id="imu-mode-label">sensor</span>
          </div>
        </div>

        <!-- Environment Sensors -->
        <div class="section section-full">
          <h2>Environment Sensors — RAW</h2>

          <div class="angles-bottom">
            <div class="angle-card">
              <div class="angle-label">Temp</div>
              <div class="angle-value"><span id="env-temp">--</span><span class="angle-unit">°C</span></div>
            </div>
            <div class="angle-card">
              <div class="angle-label">Pressure</div>
              <div class="angle-value"><span id="env-press">--</span><span class="angle-unit">hPa</span></div>
            </div>
            <div class="angle-card">
              <div class="angle-label">Humidity</div>
              <div class="angle-value"><span id="env-hum">--</span><span class="angle-unit">%</span></div>
            </div>
            <div class="angle-card">
              <div class="angle-label">Light</div>
              <div class="angle-value"><span id="env-light">--</span><span class="angle-unit">Lux</span></div>
            </div>
            <div class="angle-card">
              <div class="angle-label">BME280 Alt</div>
              <div class="angle-value"><span id="env-bme-alt">--</span><span class="angle-unit">m</span></div>
            </div>
          </div>
        </div>

      </div><!-- /.grid -->
    </div><!-- /.card -->
  </div><!-- /.wrapper -->


<script>
const SAMPLE_TARGET = {{ sample_target | default(0) }};

function formatNumber(v) {
    if (v === null || v === undefined) return "--.--";
    if (isNaN(v)) return "--.--";
    return v.toFixed(3);
}
function formatWholeNumber(v) {
    if (v === null || v === undefined) return "--.--";
    if (isNaN(v)) return "--.--";
    return v.toFixed(0);
}

function triggerReset() {
    fetch("/reset", {method:"POST"})
        .then(function() {
            var btn = document.querySelector(".btn-primary");
            btn.style.filter = "brightness(1.15)";
            setTimeout(function() { btn.style.filter = ""; }, 120);
        })
        .catch(function(err) { console.log("reset error", err); });
}
function triggerSetZero() {
    fetch("/set_zero", {method: "POST"})
        .then(function(r){ return r.text(); })
        .then(function(t){
            var btn = document.getElementById("btn-zero");
            if (btn) {
                btn.style.filter = "brightness(1.15)";
                setTimeout(function(){ btn.style.filter = ""; }, 120);
            }
            console.log("set_zero:", t);
        })
        .catch(function(err){ console.log("set_zero error", err); });
}

function updateStatus() {
    fetch("/status")
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var yaw = data.yaw;
            var pitch = data.pitch;
            var roll = data.roll;
            var acc = data.acc;
            var ts = data.timestamp || "--";
            var ready = data.ready || false;
            var count = data.sample_count || 0;
            var source = data.source || "unknown";

            // GPS averaged
            var gavg = data.gps_avg || {};
            document.getElementById("gps-avg-lat").innerText = formatNumber(gavg.lat);
            document.getElementById("gps-avg-lon").innerText = formatNumber(gavg.lon);
            document.getElementById("gps-avg-alt").innerText = formatNumber(gavg.alt);

            // GPS raw
            var graw = data.gps_raw || {};
            document.getElementById("gps-raw-lat").innerText = formatNumber(graw.lat);
            document.getElementById("gps-raw-lon").innerText = formatNumber(graw.lon);
            document.getElementById("gps-raw-alt").innerText = formatNumber(graw.alt);


            // Environmental sensors
            document.getElementById("env-temp").innerText = formatWholeNumber(data.temperature);
            document.getElementById("env-press").innerText = formatWholeNumber(data.pressure);
            document.getElementById("env-hum").innerText = formatWholeNumber(data.humidity);
            document.getElementById("env-light").innerText = formatWholeNumber(data.illumination);
            document.getElementById("env-bme-alt").innerText = formatNumber(data.bme_alt);


            document.getElementById("val-yaw").innerText = formatNumber(yaw);
            document.getElementById("val-pitch").innerText = formatNumber(pitch);
            document.getElementById("val-roll").innerText = formatNumber(roll);
            document.getElementById("val-acc").innerText = formatNumber(acc);
            document.getElementById("val-timestamp").innerText = "timestamp: " + ts;
            document.getElementById("val-source").innerText = "source: " + source;


            // ----- GPS UI -----
            document.getElementById("btn-gps-avg").classList.remove("btn-active");
            document.getElementById("btn-gps-man").classList.remove("btn-active");

            if (data.gps_mode === "avg") document.getElementById("btn-gps-avg").classList.add("btn-active");
            if (data.gps_mode === "manual") document.getElementById("btn-gps-man").classList.add("btn-active");

            document.getElementById("gps-mode-label").innerText = data.gps_mode || "avg";
            document.getElementById("gps-used-label").innerText =
            `${formatNumber(data.gps_used?.lat)}, ${formatNumber(data.gps_used?.lon)}, ${formatNumber(data.gps_used?.alt)}`;

            // Inputs wie bei dir: im AVG frei, im MANUAL gesperrt + Serverwert anzeigen
            let inLat = document.getElementById("manual-gps-lat");
            let inLon = document.getElementById("manual-gps-lon");
            let inAlt = document.getElementById("manual-gps-alt");

            if (data.gps_mode === "avg") {
            inLat.disabled = false; inLon.disabled = false; inAlt.disabled = false;
            } else if (data.gps_mode === "manual") {
            inLat.disabled = true; inLon.disabled = true; inAlt.disabled = true;
            inLat.value = data.gps_manual?.lat ?? "";
            inLon.value = data.gps_manual?.lon ?? "";
            inAlt.value = data.gps_manual?.alt ?? "";
            }


            // ----- IMU UI -----
            document.getElementById("btn-imu-sensor").classList.remove("btn-active");
            document.getElementById("btn-imu-man").classList.remove("btn-active");

            if (data.imu_mode === "sensor") document.getElementById("btn-imu-sensor").classList.add("btn-active");
            if (data.imu_mode === "manual") document.getElementById("btn-imu-man").classList.add("btn-active");

            document.getElementById("imu-mode-label").innerText = data.imu_mode || "sensor";
            document.getElementById("status-label").innerText = "online";

            let iYaw = document.getElementById("manual-imu-yaw");
            let iPitch = document.getElementById("manual-imu-pitch");
            let iRoll = document.getElementById("manual-imu-roll");

            if (data.imu_mode === "sensor") {
            iYaw.disabled=false; iPitch.disabled=false; iRoll.disabled=false;
            } else if (data.imu_mode === "manual") {
            iYaw.disabled=true; iPitch.disabled=true; iRoll.disabled=true; 
            iYaw.value = data.imu_manual?.yaw ?? "";
            iPitch.value = data.imu_manual?.pitch ?? "";
            iRoll.value = data.imu_manual?.roll ?? "";
            }


            var target = SAMPLE_TARGET > 0 ? SAMPLE_TARGET : 1;
            var ratio = Math.max(0, Math.min(1, count / target));
            var percent = Math.round(ratio * 100);

            document.getElementById("progress-bar").style.width = percent + "%";
            document.getElementById("progress-label").innerText = ready
                ? "Averaging done"
                : "Collecting samples...";
            document.getElementById("progress-count").innerText =
                count + " / " + SAMPLE_TARGET + " samples";

            var rawBox = document.getElementById("status-raw");
            rawBox.textContent = JSON.stringify(data, null, 2);
        })
        .catch(function(err) {
            console.log("status error", err);
        });
}


function useGpsAvg() {
  fetch("/set_gps_mode/avg");
}

function useGpsManual() {
  let lat = document.getElementById("manual-gps-lat").value;
  let lon = document.getElementById("manual-gps-lon").value;
  let alt = document.getElementById("manual-gps-alt").value;

  if (lat === "" || lon === "" || alt === "") {
    // wie bei deiner Hoehe: leere Felder -> 0 (oder du laesst ERR zurueckgeben)
    if (lat === "") lat = "0";
    if (lon === "") lon = "0";
    if (alt === "") alt = "0";
  }

  fetch(`/set_manual_gps/${lat}/${lon}/${alt}`)
    .then(() => fetch("/set_gps_mode/manual"))
    .catch(err => console.log("manual gps error", err));
}

function useImuSensor() {
  fetch("/set_imu_mode/sensor");
}

function useImuManual() {
  let yaw = document.getElementById("manual-imu-yaw").value;
  let pitch = document.getElementById("manual-imu-pitch").value;
  let roll = document.getElementById("manual-imu-roll").value;

  if (yaw === "") yaw = "0";
  if (pitch === "") pitch = "0";
  if (roll === "") roll = "0";

  fetch(`/set_manual_imu/${yaw}/${pitch}/${roll}`)
    .then(() => fetch("/set_imu_mode/manual"))
    .catch(err => console.log("manual imu error", err));
}


setInterval(updateStatus, 500);
updateStatus();
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML, sample_target=ORI_SAMPLE_COUNT)


@app.route("/reset", methods=["POST", "GET"])
def reset():
    reset_orientation_measurement()
    return "OK"

@app.route("/set_zero", methods=["POST", "GET"])
def set_zero_route():
    ok = set_zero_now()
    return ("OK" if ok else "NO_DATA"), (200 if ok else 409)

@app.route("/status")
def status():
    global GPS_MODE, MANUAL_GPS, IMU_MODE, MANUAL_IMU

    ori = get_orientation_data()
    gps_avg = get_gps_data()
    gps_raw = get_gps_raw()

    # --- GPS: used = avg oder manual ---
    gps_used = dict(gps_avg)
    if GPS_MODE == "manual":
        gps_used["lat"] = MANUAL_GPS["lat"]
        gps_used["lon"] = MANUAL_GPS["lon"]
        gps_used["alt"] = MANUAL_GPS["alt"]

    # --- IMU: used = sensor oder manual ---
    ori_used = dict(ori)
    if IMU_MODE == "manual":
        if MANUAL_IMU["yaw"] is not None:   ori_used["yaw"] = MANUAL_IMU["yaw"]
        if MANUAL_IMU["pitch"] is not None: ori_used["pitch"] = MANUAL_IMU["pitch"]
        if MANUAL_IMU["roll"] is not None:  ori_used["roll"] = MANUAL_IMU["roll"]
        ori_used["source"] = "manual"

    # --- BME Alt + History ---
    bme_alt = get_altitude_baro()
    if bme_alt not in (None, 0):
        history_alt_bme.append(bme_alt)

    # --- Environment ---
    ori_used["temperature"] = get_temperature()
    ori_used["pressure"] = get_pressure()
    ori_used["humidity"] = get_humidity()
    ori_used["illumination"] = get_illumination()
    ori_used["bme_alt"] = bme_alt

    # --- GPS structs fuers UI ---
    ori_used["gps_avg"] = dict(gps_avg)   # echte avg Anzeige
    ori_used["gps_raw"] = dict(gps_raw)   # raw Anzeige

    # Samples (wie vorher)
    ori_used["samples_lat"] = gps_avg.get("samples_lat", 0)
    ori_used["samples_lon"] = gps_avg.get("samples_lon", 0)
    ori_used["samples_alt_gps"] = gps_avg.get("samples_alt_gps", 0)
    ori_used["samples_alt_bme"] = len(history_alt_bme)
    ori_used["sample_window_size"] = SAMPLE_WINDOW_SIZE

    # NEU: GPS Mode + Manual Werte + Used Werte fuers UI
    ori_used["gps_mode"] = GPS_MODE
    ori_used["gps_manual"] = dict(MANUAL_GPS)
    ori_used["gps_used"] = {
        "lat": gps_used.get("lat"),
        "lon": gps_used.get("lon"),
        "alt": gps_used.get("alt"),
    }

    # NEU: IMU Mode + Manual Werte
    ori_used["imu_mode"] = IMU_MODE
    ori_used["imu_manual"] = dict(MANUAL_IMU)

    return jsonify(ori_used)

@app.route("/set_gps_mode/<mode>")
def set_gps_mode(mode):
    global GPS_MODE
    if mode in ("avg", "manual"):
        GPS_MODE = mode
        return "OK"
    return "ERR"


@app.route("/set_manual_gps/<lat>/<lon>/<alt>")
def set_manual_gps(lat, lon, alt):
    global MANUAL_GPS
    try:
        MANUAL_GPS["lat"] = float(lat)
        MANUAL_GPS["lon"] = float(lon)
        MANUAL_GPS["alt"] = float(alt)
        return "OK"
    except:
        return "ERR"


@app.route("/set_imu_mode/<mode>")
def set_imu_mode(mode):
    global IMU_MODE
    if mode in ("sensor", "manual"):
        IMU_MODE = mode
        return "OK"
    return "ERR"


@app.route("/set_manual_imu/<yaw>/<pitch>/<roll>")
def set_manual_imu(yaw, pitch, roll):
    global MANUAL_IMU
    try:
        MANUAL_IMU["yaw"] = float(yaw)
        MANUAL_IMU["pitch"] = float(pitch)
        MANUAL_IMU["roll"] = float(roll)
        return "OK"
    except:
        return "ERR"
    



# -------------------------------------------------------------------------
# Hilfsfunktionen
# -------------------------------------------------------------------------

def get_visible_altitude():
    global ALTITUDE_MODE, MANUAL_ALTITUDE
    gps_avg = get_gps_data()

    if ALTITUDE_MODE == "manual" and MANUAL_ALTITUDE is not None:
        return MANUAL_ALTITUDE

    if ALTITUDE_MODE == "bme":
        return get_altitude_baro()

    return gps_avg.get("alt")


def get_altitude_mode():
    return ALTITUDE_MODE


def get_manual_altitude():
    return MANUAL_ALTITUDE


def start_webserver():
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)


def get_selected_gps() -> tuple[dict, str]:
    """GPS fuer Versand (avg oder manual) + mode."""
    gps = get_gps_data()
    if GPS_MODE == "manual":
        gps = dict(gps)
        gps["lat"] = MANUAL_GPS["lat"]
        gps["lon"] = MANUAL_GPS["lon"]
        gps["alt"] = MANUAL_GPS["alt"]
    return gps, GPS_MODE


def get_selected_orientation() -> tuple[dict, str]:
    """IMU fuer Versand (sensor oder manual) + mode."""
    ori = get_orientation_data()
    if IMU_MODE == "manual":
        ori = dict(ori)
        if MANUAL_IMU["yaw"] is not None:   ori["yaw"] = MANUAL_IMU["yaw"]
        if MANUAL_IMU["pitch"] is not None: ori["pitch"] = MANUAL_IMU["pitch"]
        if MANUAL_IMU["roll"] is not None:  ori["roll"] = MANUAL_IMU["roll"]
        ori["source"] = "manual"
    return ori, IMU_MODE