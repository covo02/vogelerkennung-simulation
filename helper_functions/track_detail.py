"""
track_detail.py

Aufbereitung eines einzelnen Tracks fuer das Detail-Seitenpanel im
Webinterface: Hoehenprofil z(t) mit eingefaerbten Flugphasen sowie die
Darstellung der Merkmale und der Klassifikationsbegruendung.

Die Phasengrenzen werden nach derselben Plateau-Definition bestimmt wie in
skylark_classifier.extract_features(): Ein Punkt gehoert zur Plateau-/
Singflugphase, wenn seine Hoehe im obersten PLATEAU_HEIGHT_FRACTION-Anteil
des Hoehengewinns liegt. Alles davor gilt als Steigflug, alles danach als
Sturzflug. Die Faerbung zeigt also genau die Struktur, auf die der
Klassifikator seine Entscheidung stuetzt.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import plotly.graph_objects as go


PLATEAU_HEIGHT_FRACTION = 0.15

PHASE_COLORS = {
    "climb": "rgba(34, 197, 94, 0.12)",
    "plateau": "rgba(250, 204, 21, 0.12)",
    "descent": "rgba(239, 68, 68, 0.12)",
}

PHASE_LABELS = {
    "climb": "Steigflug",
    "plateau": "Singflug",
    "descent": "Sturzflug",
}


def _parse_timestamp(timestamp: str) -> datetime:
    return datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))


def _sorted_samples(records: Sequence[Dict[str, Any]]) -> List[Tuple[float, float]]:
    """Liefert (Sekunden seit Trackbeginn, Hoehe) zeitlich sortiert."""
    parsed = []

    for record in records:
        try:
            parsed.append(
                (
                    _parse_timestamp(record["timestamp"]),
                    float(record["enu_u"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    if not parsed:
        return []

    parsed.sort(key=lambda item: item[0])
    start = parsed[0][0]

    return [
        ((timestamp - start).total_seconds(), altitude)
        for timestamp, altitude in parsed
    ]


def detect_phases(
    samples: Sequence[Tuple[float, float]],
) -> List[Tuple[str, float, float]]:
    """
    Bestimmt die Zeitgrenzen der drei Flugphasen.

    Rueckgabe: Liste von (Phasenname, Startzeit, Endzeit). Phasen ohne
    Ausdehnung werden weggelassen - bei einem Track, der nur die
    Plateauphase enthaelt, bleibt entsprechend nur ein Band uebrig.
    """
    if len(samples) < 2:
        return []

    altitudes = [altitude for _seconds, altitude in samples]
    altitude_max = max(altitudes)
    altitude_gain = altitude_max - min(altitudes)

    threshold = altitude_max - PLATEAU_HEIGHT_FRACTION * max(altitude_gain, 1e-6)
    plateau_indices = [
        index
        for index, (_seconds, altitude) in enumerate(samples)
        if altitude >= threshold
    ]

    if not plateau_indices:
        return []

    first_plateau = plateau_indices[0]
    last_plateau = plateau_indices[-1]

    start_time = samples[0][0]
    end_time = samples[-1][0]

    phases = []

    if first_plateau > 0:
        phases.append(("climb", start_time, samples[first_plateau][0]))

    phases.append(("plateau", samples[first_plateau][0], samples[last_plateau][0]))

    if last_plateau < len(samples) - 1:
        phases.append(("descent", samples[last_plateau][0], end_time))

    return [
        (name, phase_start, phase_end)
        for name, phase_start, phase_end in phases
        if phase_end > phase_start
    ]


def build_altitude_profile_figure(
    records: Optional[Sequence[Dict[str, Any]]] = None,
    track_id: str = "",
    message: Optional[str] = None,
) -> go.Figure:
    """
    Baut das Hoehenprofil z(t) eines Tracks mit farbig hinterlegten
    Flugphasen. Ohne Records wird eine leere Figur mit Hinweistext
    zurueckgegeben.
    """
    figure = go.Figure()

    samples = _sorted_samples(records or [])

    if not samples:
        figure.add_annotation(
            text=message or "Zeile in der Tabelle anklicken",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(size=12, color="#a8b0bf"),
        )
        figure.update_layout(
            template="plotly_dark",
            height=210,
            margin=dict(l=44, r=12, t=10, b=30),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
        )
        return figure

    seconds = [item[0] for item in samples]
    altitudes = [item[1] for item in samples]

    for name, phase_start, phase_end in detect_phases(samples):
        figure.add_vrect(
            x0=phase_start,
            x1=phase_end,
            fillcolor=PHASE_COLORS[name],
            line_width=0,
            layer="below",
            annotation_text=PHASE_LABELS[name],
            annotation_position="bottom left",
            annotation_font_size=10,
        )

    figure.add_trace(
        go.Scatter(
            x=seconds,
            y=altitudes,
            mode="lines",
            line=dict(width=2, color="#22c55e"),
            name=str(track_id) or "z(t)",
            hovertemplate="t = %{x:.0f} s<br>z = %{y:.1f} m<extra></extra>",
        )
    )

    figure.update_layout(
        template="plotly_dark",
        height=210,
        margin=dict(l=44, r=12, t=10, b=34),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        xaxis=dict(title=dict(text="Zeit [s]", font=dict(size=10)), gridcolor="#1e2530"),
        yaxis=dict(title=dict(text="z [m]", font=dict(size=10)), gridcolor="#1e2530"),
    )

    return figure


# ============================================================================
# AUFBEREITUNG DER MERKMALE
# ============================================================================
FEATURE_ROWS = (
    ("duration_s", "Dauer", "{:.0f} s"),
    ("sample_count", "Samples", "{:.0f}"),
    ("altitude_gain", "Hoehengewinn", "{:.1f} m"),
    ("max_climb_rate", "max. Steigrate", "{:.2f} m/s"),
    ("max_descent_rate", "max. Sinkrate", "{:.2f} m/s"),
    ("hover_fraction", "Plateauanteil", "{:.0%}"),
    ("horizontal_path_length", "Bahnlaenge", "{:.0f} m"),
    ("return_ratio", "Rueckkehr-Verhaeltnis", "{:.3f}"),
    ("max_radius_from_centroid", "Aktionsradius", "{:.1f} m"),
)


def features_to_dict(features: Any) -> Dict[str, float]:
    """
    Wandelt ein FlightFeatures-Objekt in ein serialisierbares Dict um und
    ergaenzt das abgeleitete Rueckkehr-Verhaeltnis, das der Klassifikator
    intern berechnet, aber nicht als Merkmal ablegt.
    """
    path_length = float(getattr(features, "horizontal_path_length", 0.0))
    net_displacement = float(getattr(features, "net_horizontal_displacement", 0.0))
    return_ratio = net_displacement / path_length if path_length > 1e-6 else 1.0

    return {
        "duration_s": float(getattr(features, "duration_s", 0.0)),
        "sample_count": float(getattr(features, "sample_count", 0)),
        "altitude_min": float(getattr(features, "altitude_min", 0.0)),
        "altitude_max": float(getattr(features, "altitude_max", 0.0)),
        "altitude_gain": float(getattr(features, "altitude_gain", 0.0)),
        "max_climb_rate": float(getattr(features, "max_climb_rate", 0.0)),
        "max_descent_rate": float(getattr(features, "max_descent_rate", 0.0)),
        "hover_fraction": float(getattr(features, "hover_fraction", 0.0)),
        "horizontal_path_length": path_length,
        "net_horizontal_displacement": net_displacement,
        "return_ratio": return_ratio,
        "max_radius_from_centroid": float(getattr(features, "max_radius_from_centroid", 0.0)),
        "centroid_e": float(getattr(features, "centroid_e", 0.0)),
        "centroid_n": float(getattr(features, "centroid_n", 0.0)),
    }


def format_feature_rows(features: Optional[Dict[str, float]]) -> List[Tuple[str, str]]:
    """Liefert (Beschriftung, formatierter Wert) fuer die Anzeige im Panel."""
    if not features:
        return []

    rows = []
    for key, label, pattern in FEATURE_ROWS:
        value = features.get(key)
        if value is None:
            continue
        try:
            rows.append((label, pattern.format(value)))
        except (TypeError, ValueError):
            rows.append((label, str(value)))

    return rows
