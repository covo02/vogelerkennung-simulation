"""
pi_map.py

Interaktive Satellitenkarte zum Platzieren der Pi-Stationen.

KARTENQUELLE
------------
Verwendet werden die Luftbildkacheln von Esri World Imagery. Die sind
ohne API-Schlüssel abrufbar. Google Maps und Google Earth lassen sich
nicht auf diesem Weg einbinden: deren JavaScript-API verlangt einen
kostenpflichtigen Schlüssel, und das Einbetten der Kacheln außerhalb der
API ist nicht zulässig. Die Darstellung ist inhaltlich gleichwertig —
es sind Luftbilder derselben Flächen, nur von einem anderen Anbieter.

BEDIENUNG
---------
Plotly meldet Klicks nur auf Datenpunkten, nicht auf freier Kartenfläche.
Statt "irgendwohin klicken" liegt deshalb ein festes Fadenkreuz in der
Kartenmitte: Karte verschieben, bis die gewünschte Stelle darunter liegt,
dann die Station dorthin setzen. Das ist dieselbe Bedienung wie bei der
Standortwahl in gängigen Karten-Apps.
"""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import plotly.graph_objects as go


ESRI_WORLD_IMAGERY = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)

ATTRIBUTION = "Luftbilder: Esri World Imagery"

DEFAULT_ZOOM = 15.5

PI_COLORS = ["#ef4444", "#f59e0b", "#a855f7"]


def _to_float(value: Any, fallback: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def pi_coordinates(pi_rows: Sequence[Dict[str, Any]]) -> List[Optional[Tuple[float, float]]]:
    """Breiten-/Längengrad je Station, None wenn die Zeile keine hat."""
    coordinates: List[Optional[Tuple[float, float]]] = []

    for row in pi_rows or []:
        latitude = _to_float(row.get("lat"))
        longitude = _to_float(row.get("lon"))

        if latitude is None or longitude is None:
            coordinates.append(None)
        else:
            coordinates.append((latitude, longitude))

    return coordinates


def fit_view(
    coordinates: Sequence[Optional[Tuple[float, float]]],
) -> Tuple[float, float, float]:
    """
    Kartenmittelpunkt und Zoomstufe, die alle gesetzten Stationen zeigen.
    Ohne Stationen wird auf einen neutralen Ausschnitt zurückgefallen.
    """
    known = [item for item in coordinates if item is not None]

    if not known:
        return (52.0302, 8.5325, 13.0)

    latitudes = [item[0] for item in known]
    longitudes = [item[1] for item in known]

    center_latitude = (min(latitudes) + max(latitudes)) / 2.0
    center_longitude = (min(longitudes) + max(longitudes)) / 2.0

    if len(known) == 1:
        return (center_latitude, center_longitude, DEFAULT_ZOOM)

    # Grobe Abschätzung der Ausdehnung in Metern.
    span_north = (max(latitudes) - min(latitudes)) * 111320.0
    span_east = (
        (max(longitudes) - min(longitudes))
        * 111320.0
        * math.cos(math.radians(center_latitude))
    )
    span = max(span_north, span_east, 50.0)

    # Zoom so wählen, dass die Ausdehnung etwa 60 % der Karte einnimmt.
    zoom = math.log2(156543.03 * math.cos(math.radians(center_latitude)) * 600 / (span * 1.7))
    zoom = max(10.0, min(18.0, zoom))

    return (center_latitude, center_longitude, zoom)


def build_pi_map_figure(
    pi_rows: Sequence[Dict[str, Any]],
    center: Optional[Dict[str, float]] = None,
    height: int = 460,
) -> go.Figure:
    """
    Satellitenkarte mit den Pi-Standorten.

    center: {"lat", "lon", "zoom"} erhält den aktuellen Kartenausschnitt
    über ein Neuzeichnen hinweg. None passt den Ausschnitt an die
    Stationen an.
    """
    coordinates = pi_coordinates(pi_rows)
    figure = go.Figure()

    known_indices = [
        index for index, item in enumerate(coordinates) if item is not None
    ]

    # Verbindungslinien vom Ursprung zu den anderen Stationen: sie zeigen
    # die Achsen des ENU-Systems und damit die aufgespannte Fläche.
    if 0 in known_indices and len(known_indices) > 1:
        origin = coordinates[0]
        for index in known_indices[1:]:
            target = coordinates[index]
            figure.add_trace(
                go.Scattermap(
                    lat=[origin[0], target[0]],
                    lon=[origin[1], target[1]],
                    mode="lines",
                    line=dict(width=2, color="#14b8a6"),
                    hoverinfo="skip",
                    showlegend=False,
                )
            )

    for index, item in enumerate(coordinates):
        if item is None:
            continue

        name = str(
            (pi_rows[index].get("name") or pi_rows[index].get("id") or f"Pi {index + 1}")
        )

        figure.add_trace(
            go.Scattermap(
                lat=[item[0]],
                lon=[item[1]],
                mode="markers+text",
                marker=dict(size=14, color=PI_COLORS[index % len(PI_COLORS)]),
                text=[f"  {index + 1}"],
                textposition="middle right",
                textfont=dict(size=13, color="#ffffff"),
                name=name,
                hovertemplate=(
                    f"<b>{name}</b><br>%{{lat:.6f}}, %{{lon:.6f}}<extra></extra>"
                ),
                showlegend=False,
            )
        )

    if center and center.get("lat") is not None:
        center_latitude = float(center["lat"])
        center_longitude = float(center["lon"])
        zoom = float(center.get("zoom") or DEFAULT_ZOOM)
    else:
        center_latitude, center_longitude, zoom = fit_view(coordinates)

    figure.update_layout(
        map=dict(
            style="white-bg",
            center=dict(lat=center_latitude, lon=center_longitude),
            zoom=zoom,
            layers=[
                dict(
                    below="traces",
                    sourcetype="raster",
                    sourceattribution=ATTRIBUTION,
                    source=[ESRI_WORLD_IMAGERY],
                )
            ],
        ),
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        uirevision="pi-map",
    )

    # Fadenkreuz in der Kartenmitte. Es liegt im Papierkoordinatensystem
    # und bleibt daher beim Verschieben der Karte genau mittig.
    figure.add_shape(
        type="line", xref="paper", yref="paper",
        x0=0.5, x1=0.5, y0=0.44, y1=0.56,
        line=dict(color="#facc15", width=2),
    )
    figure.add_shape(
        type="line", xref="paper", yref="paper",
        x0=0.44, x1=0.56, y0=0.5, y1=0.5,
        line=dict(color="#facc15", width=2),
    )
    figure.add_shape(
        type="circle", xref="paper", yref="paper",
        x0=0.472, x1=0.528, y0=0.472, y1=0.528,
        line=dict(color="#facc15", width=2),
    )

    return figure


def center_from_relayout(
    relayout_data: Optional[Dict[str, Any]],
    previous: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, float]]:
    """
    Liest Mittelpunkt und Zoom aus den relayoutData der Karte.

    Plotly liefert je nach Aktion unterschiedliche Formen — mal
    "map.center" als Dict, mal die Einzelschlüssel "map.center.lat" und
    "map.center.lon". Beide werden unterstützt; fehlt etwas, bleibt der
    vorherige Wert stehen.
    """
    if not isinstance(relayout_data, dict):
        return previous

    result = dict(previous or {})

    center = relayout_data.get("map.center")
    if isinstance(center, dict):
        if center.get("lat") is not None:
            result["lat"] = float(center["lat"])
        if center.get("lon") is not None:
            result["lon"] = float(center["lon"])

    if relayout_data.get("map.center.lat") is not None:
        result["lat"] = float(relayout_data["map.center.lat"])

    if relayout_data.get("map.center.lon") is not None:
        result["lon"] = float(relayout_data["map.center.lon"])

    if relayout_data.get("map.zoom") is not None:
        result["zoom"] = float(relayout_data["map.zoom"])

    if "lat" not in result or "lon" not in result:
        return previous

    return result
