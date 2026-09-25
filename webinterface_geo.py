import json
import math
import subprocess
import sys
from pathlib import Path

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, dash_table, dcc, html, no_update
from helper_functions.pi_view_simulation import (
    compute_view_vector,
    default_pi_setup,
    normalize_pi_setup,
    perpendicular_square_corners,
    point_in_pi_view_volume,
    points_in_view_volume_for_each_pi,
    project_point_to_pi_image_coordinates,
    project_point_to_pi_plane,
    to_float,
)

from helper_functions.additional_styles import ERROR_STATUS_STYLE, SUCCESS_STATUS_STYLE
from helper_functions.bird_generation import generate_birds, parse_generation_parameters
from helper_functions.bird_generation_layout import bird_generator_tab
from helper_functions.detected_positions import (
    add_detected_vectors_to_figure,
    build_detected_positions_payload,
    load_detected_positions_payload,
    save_detected_positions_payload,
)
from helper_functions.generate_plots import create_bird_stats, create_birds_2d_figure, create_birds_3d_figure, status_figure
# HINWEIS: Falls die Datei lokal in "skylark_classifier.py" umbenannt wurde,
# diese Zeile entsprechend anpassen:
# from helper_functions.skylark_classifier import classify_tracks, evaluate_against_ground_truth
from helper_functions.skylark_classifier import classify_tracks, evaluate_against_ground_truth
from helper_functions.territory_estimation import (
    AVAILABLE_METHODS,
    DEFAULT_METHOD,
    METHOD_LABELS,
    estimate_territories,
    evaluate_estimates,
)
from helper_functions.track_detail import (
    build_altitude_profile_figure,
    features_to_dict,
    format_feature_rows,
)
from helper_functions.geo_export import (
    derive_pi_positions,
    enu_to_wgs84,
    format_coordinates,
    maps_directions_url,
    maps_satellite_url,
    maps_url,
    reference_from_pi_rows,
    territories_to_geojson,
)
from helper_functions.pi_map import (
    build_pi_map_figure,
    center_from_relayout,
)


# ============================================================================
# KONFIGURATION
# ============================================================================
# Testversion mit GeoJSON-Export. Läuft auf einem eigenen Port, damit sie
# parallel zur unveränderten webinterface.py gestartet werden kann.
APP_PORT = 8061

# ============================================================================
# GEOREFERENZ
# ============================================================================
# Frei gewählter Referenzpunkt für den Kartenexport: die Position, an der
# pi_1 stehen würde. pi_1 liegt im ENU-System bei (0, 0, 0), damit ist
# dieser eine Punkt der Ortsbezug der gesamten simulierten Fläche.
#
# Die Vorbelegung liegt auf einer großen zusammenhängenden Ackerfläche
# etwa 11 km nördlich von Bielefeld. Sie wurde ausgewählt, indem in
# OpenStreetMap nach Flächen mit landuse=farmland gesucht wurde, in die
# ein Quadrat von 300 x 300 m vollständig hineinpasst, und das Ergebnis
# anschließend auf Luftbildern geprüft wurde. Damit stehen alle drei
# Stationen auf offenem Feld und nicht in einer Hecke oder Bebauung —
# passend zum Lebensraum der Feldlerche.
#
# ACHTUNG: Hier wurde nichts vor Ort eingemessen. Der Punkt dient nur
# dazu, die Simulationsergebnisse überhaupt auf einer Karte darstellen
# zu können. Über die Karte im Tab "Kameras" lässt er sich beliebig
# verschieben.
GEO_REFERENCE_LAT = 52.124165
GEO_REFERENCE_LON = 8.500326


def default_pi_setup_with_coordinates() -> list[dict]:
    """
    Standard-Pi-Aufbau, ergänzt um Breiten- und Längengrad je Station.

    Die erste Station definiert den ENU-Ursprung. Die weiteren Werte sind
    so gewählt, dass sie zu den Metern des Standardaufbaus passen
    (300 m östlich bzw. 300 m nördlich der ersten Station).
    """
    rows = default_pi_setup()

    for row in rows:
        latitude, longitude = enu_to_wgs84(
            float(row["x"]), float(row["y"]), GEO_REFERENCE_LAT, GEO_REFERENCE_LON
        )
        row["lat"] = round(latitude, 6)
        row["lon"] = round(longitude, 6)

    return rows


BASE_DIR = Path(__file__).resolve().parent
TRAJECTORY_SCRIPT = BASE_DIR / "trajectory.py"
TRAJECTORY_JSON = (
    BASE_DIR / "vogel_flugbahnen_determined.json"
)

BIRDS_JSON = BASE_DIR / "vogel_flugbahnen.json"
BIRD_IMAGES_JSON = BASE_DIR / "vogel_bilder.json"
DETECTED_POSITIONS_JSON = BASE_DIR / "vogel_position_erkannt.json"


SCRIPT_TIMEOUT_SECONDS = 300
CAMERA_PLANE_SIDE_LENGTH = 600.0

app = dash.Dash(__name__)
server = app.server


# ============================================================================
# HILFSFUNKTIONEN
# ============================================================================

def load_dataframe_from_json(
    json_path: Path,
    required_columns: set[str] | None = None,
    group_column: str = "bird_id",
) -> pd.DataFrame | None:
    if not json_path.is_file():
        return None

    payload = load_json_payload(json_path)

    if isinstance(payload, dict):
        data = payload.get("simulated_birds")
    else:
        data = payload

    if not isinstance(data, list) or not data:
        return None

    df = pd.DataFrame(data)

    required = required_columns or {
        group_column,
        "timestamp",
        "enu_e",
        "enu_n",
        "enu_u",
    }

    missing_columns = required - set(df.columns)
    if missing_columns:
        return None

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    for coordinate in ("enu_e", "enu_n", "enu_u"):
        df[coordinate] = pd.to_numeric(df[coordinate], errors="coerce")

    df = df.dropna(
        subset=[
            group_column,
            "timestamp",
            "enu_e",
            "enu_n",
            "enu_u",
        ]
    )

    df = df.sort_values([group_column, "timestamp"])

    return df if not df.empty else None


def load_trajectory_dataframe() -> pd.DataFrame | None:
    return load_dataframe_from_json(
        TRAJECTORY_JSON,
        group_column="determined_track_id",
    )


def load_birds_dataframe() -> pd.DataFrame | None:
    return load_dataframe_from_json(BIRDS_JSON)


def load_json_payload(json_path: Path) -> dict | list | None:
    if not json_path.is_file():
        return None

    with json_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_trajectory_payload() -> dict | list | None:
    return load_json_payload(TRAJECTORY_JSON)


def load_bird_images_payload() -> dict | None:
    payload = load_json_payload(BIRD_IMAGES_JSON)
    return payload if isinstance(payload, dict) else None


def extract_trajectory_records(payload: dict | list | None) -> list[dict]:
    if isinstance(payload, dict):
        records = payload.get("simulated_birds")
    else:
        records = payload

    return records if isinstance(records, list) else []


def save_payload(json_path: Path, payload: dict | list | None, records: list[dict]) -> None:
    if isinstance(payload, dict):
        payload_to_save = dict(payload)
        payload_to_save["simulated_birds"] = records
    else:
        payload_to_save = records

    with json_path.open("w", encoding="utf-8") as file:
        json.dump(payload_to_save, file, ensure_ascii=False, indent=2)


def save_trajectory_payload(payload: dict | list | None, records: list[dict]) -> None:
    save_payload(TRAJECTORY_JSON, payload, records)


def save_birds_payload(payload: dict | list | None, records: list[dict]) -> None:
    save_payload(BIRDS_JSON, payload, records)


def build_time_filter_data(payload: dict | None) -> tuple[list[str], dict[int, str]]:
    if payload is None:
        return [], {}

    pi_images = payload.get("pi_images") if isinstance(payload, dict) else None
    if not isinstance(pi_images, list):
        return [], {}

    unique_times = set()
    for pi_entry in pi_images:
        if not isinstance(pi_entry, dict):
            continue

        projected_points = pi_entry.get("projected_points")
        if not isinstance(projected_points, list):
            continue

        for point in projected_points:
            if not isinstance(point, dict):
                continue
            timestamp = point.get("timestamp")
            if isinstance(timestamp, str) and timestamp:
                unique_times.add(timestamp)

    sorted_times = sorted(unique_times)
    return sorted_times, {index: timestamp for index, timestamp in enumerate(sorted_times)}


def build_time_slider_marks(sorted_times: list[str]) -> dict[int, str]:
    if not sorted_times:
        return {0: "Alle"}

    marks: dict[int, str] = {}
    last_index = len(sorted_times) - 1
    sample_count = min(4, len(sorted_times))
    if sample_count == 1:
        sampled_indices = [0]
    else:
        sampled_indices = sorted({round(i * last_index / (sample_count - 1)) for i in range(sample_count)})

    for index in sampled_indices:
        marks[index] = sorted_times[index][11:19]

    marks[len(sorted_times)] = "Alle"
    return marks


def build_camera_2d_figure(pi_entry: dict, points: list[dict], plane_side_length: float) -> go.Figure:
    half_side = plane_side_length / 2.0

    x_values = [to_float(point.get("image_x"), 0.0) for point in points if isinstance(point, dict)]
    y_values = [to_float(point.get("image_y"), 0.0) for point in points if isinstance(point, dict)]
    time_values = [str(point.get("timestamp", "")) for point in points if isinstance(point, dict)]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x_values,
            y=y_values,
            mode="markers",
            marker=dict(size=8, color="#fb923c", line=dict(width=1, color="#1f2937")),
            text=time_values,
            hovertemplate=(
                "Zeit: %{text}<br>"
                "x: %{x}<br>"
                "y: %{y}<extra></extra>"
            ),
            name="Projizierte Punkte",
        )
    )

    fig.update_layout(
        template="plotly_dark",
        width=600,
        height=600,
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=False,
        xaxis=dict(
            range=[-half_side, half_side],
            zeroline=True,
            zerolinecolor="#9ca3af",
            scaleanchor="y",
            scaleratio=1,
            showgrid=False,
            showticklabels=False,
            title=None,
        ),
        yaxis=dict(
            range=[-half_side, half_side],
            zeroline=True,
            zerolinecolor="#9ca3af",
            showgrid=False,
            showticklabels=False,
            title=None,
        ),
    )

    return fig


def build_camera_2d_children(time_index: int | None = None) -> tuple[list, str, int, int, dict[int, str], int, bool, str]:
    payload = load_bird_images_payload()
    if payload is None:
        return (
            [html.Div("Keine Projektionen gefunden. Bitte zuerst filtern.", className="mono")],
            "Status: Keine Projektionen verfügbar.",
            0,
            0,
            {0: "Alle"},
            0,
            True,
            "Zeitfilter: Keine Daten",
        )

    pi_images = payload.get("pi_images") if isinstance(payload, dict) else None
    if not isinstance(pi_images, list) or not pi_images:
        return (
            [html.Div("Keine Pi-Bilddaten gefunden.", className="mono")],
            "Status: Keine Pi-Bilddaten verfügbar.",
            0,
            0,
            {0: "Alle"},
            0,
            True,
            "Zeitfilter: Keine Daten",
        )

    meta = payload.get("projection_meta", {}) if isinstance(payload, dict) else {}
    plane_side_length = to_float(meta.get("plane_side_length"), 600.0)
    sorted_times, index_to_time = build_time_filter_data(payload)

    slider_min = 0
    slider_max = len(sorted_times)
    slider_disabled = len(sorted_times) == 0

    if time_index is None:
        current_index = slider_max
    else:
        current_index = max(slider_min, min(time_index, slider_max))

    show_all_times = current_index == slider_max
    selected_time = None if show_all_times else index_to_time.get(current_index)
    time_label = "Zeitfilter: Alle Zeiten" if show_all_times else f"Zeitfilter: {selected_time}"
    marks = build_time_slider_marks(sorted_times)

    children = []
    total_points = 0
    for pi_entry in pi_images:
        if not isinstance(pi_entry, dict):
            continue

        pi_id = str(pi_entry.get("pi_id", "pi"))
        projected_points_raw = pi_entry.get("projected_points", [])
        projected_points = projected_points_raw if isinstance(projected_points_raw, list) else []
        if selected_time is not None:
            projected_points = [
                point for point in projected_points
                if isinstance(point, dict) and str(point.get("timestamp", "")) == selected_time
            ]

        point_count = len(projected_points)
        total_points += point_count
        figure = build_camera_2d_figure(pi_entry, projected_points, plane_side_length)

        children.append(
            html.Div(
                style={"marginBottom": "16px", "display": "flex", "flexDirection": "column", "alignItems": "center"},
                children=[
                    html.Div(
                        f"Pi: {pi_id}",
                        className="mono",
                        style={"marginBottom": "8px", "fontWeight": "700", "color": "var(--text)"},
                    ),
                    dcc.Graph(
                        figure=figure,
                        config={"responsive": False},
                        style={"width": "600px", "height": "600px"},
                    ),
                ],
            )
        )

    status_text = f"Status: {len(children)} Pi-Ansichten mit insgesamt {total_points} projizierten Punkten geladen."
    return (
        children or [html.Div("Keine Pi-Bilddaten gefunden.", className="mono")],
        status_text,
        slider_min,
        slider_max,
        marks,
        current_index,
        slider_disabled,
        time_label,
    )


def save_projected_bird_images(
    pi_setup: list[dict],
    points_by_pi: dict[str, list[dict]],
    plane_side_length: float,
    source_file: str,
) -> None:
    projection_payload: dict[str, object] = {
        "projection_meta": {
            "image_center": {"x": 0.0, "y": 0.0},
            "plane_side_length": plane_side_length,
            "source_file": source_file,
        },
        "pi_images": [],
    }

    pi_by_id = {str(pi.get("id", pi.get("name", "pi"))): pi for pi in pi_setup}

    for pi_id, points in points_by_pi.items():
        pi = pi_by_id.get(pi_id)
        if pi is None:
            continue

        yaw_deg = to_float(pi.get("yaw_deg", pi.get("view_horizontal_deg")), 0.0)
        pitch_deg = to_float(pi.get("pitch_deg", pi.get("view_vertical_deg")), 0.0)
        roll_deg = to_float(pi.get("roll_deg"), 0.0)
        view_length = to_float(pi.get("view_length"), 400.0)
        view_x, view_y, view_z = compute_view_vector(yaw_deg, pitch_deg, roll_deg, view_length)
        pi_x = to_float(pi.get("x"), 0.0)
        pi_y = to_float(pi.get("y"), 0.0)
        pi_z = to_float(pi.get("z"), 0.0)

        projected_points = []
        for point in points:
            image_coordinates = project_point_to_pi_image_coordinates(
                pi,
                point,
                plane_side_length,
            )
            if image_coordinates is None:
                continue

            timestamp = point.get("timestamp")
            projected_points.append(
                {
                    "timestamp": timestamp,
                    "image_x": round(image_coordinates[0], 6),
                    "image_y": round(image_coordinates[1], 6),
                }
            )

        projection_payload["pi_images"].append(
            {
                "pi_id": pi_id,
                "pi_position": {
                    "x": round(pi_x, 6),
                    "y": round(pi_y, 6),
                    "z": round(pi_z, 6),
                },
                "view_direction_vector": {
                    "x": round(view_x, 6),
                    "y": round(view_y, 6),
                    "z": round(view_z, 6),
                },
                "projected_points": projected_points,
            }
        )

    with BIRD_IMAGES_JSON.open("w", encoding="utf-8") as file:
        json.dump(projection_payload, file, ensure_ascii=False, indent=2)


def collect_visible_bird_positions(records: list[dict]) -> dict[str, list[tuple[float, float, float]]]:
    grouped_positions: dict[str, list[tuple[float, float, float]]] = {}

    for record in records:
        bird_id = str(record.get("bird_id") or record.get("determined_track_id") or "unbekannt")
        x = to_float(record.get("enu_e"), 0.0)
        y = to_float(record.get("enu_n"), 0.0)
        z = to_float(record.get("enu_u"), 0.0)
        grouped_positions.setdefault(bird_id, []).append((x, y, z))

    return dict(sorted(grouped_positions.items()))


def summarize_visible_birds(records: list[dict]) -> list:
    bird_positions = collect_visible_bird_positions(records)

    if not bird_positions:
        return [html.Div("Keine sichtbaren Vogelpositionen.", className="mono")]

    summary_items = []
    for bird_id, positions in bird_positions.items():
        point_text = "; ".join(
            f"({x:.2f}, {y:.2f}, {z:.2f})"
            for x, y, z in positions[:5]
        )
        if len(positions) > 5:
            point_text += " ..."
        summary_items.append(
            html.Div(
                f"{bird_id}: {len(positions)} Position(en) — {point_text}",
                style={"marginBottom": "4px", "fontFamily": "monospace"},
            )
        )

    return summary_items


def add_pi_to_bird_vectors(fig: go.Figure, pi_setup: list[dict], records: list[dict], plane_side_length: float) -> None:
    if not pi_setup or not records:
        return

    for pi in pi_setup:
        pi_id = str(pi.get("id", pi.get("name", "pi")))
        origin_x = to_float(pi.get("x"), 0.0)
        origin_y = to_float(pi.get("y"), 0.0)
        origin_z = to_float(pi.get("z"), 0.0)

        for record in records:
            if not point_in_pi_view_volume(pi, record, plane_side_length):
                continue

            bird_id = str(record.get("bird_id") or record.get("determined_track_id") or "unbekannt")
            projected_point = project_point_to_pi_plane(pi, record, plane_side_length)
            if projected_point is None:
                continue

            fig.add_trace(
                go.Scatter3d(
                    x=[origin_x, projected_point[0]],
                    y=[origin_y, projected_point[1]],
                    z=[origin_z, projected_point[2]],
                    mode="lines",
                    line=dict(width=2, color="#34d399"),
                    name=f"{pi_id} → {bird_id}",
                    showlegend=False,
                    hovertemplate=(
                        f"<b>{pi_id}</b><br>"
                        f"Bird: {bird_id}<br>"
                        "Von Pi bis Projektion auf der orange Kameraebene<br>"
                        "X: %{x}<br>"
                        "Y: %{y}<br>"
                        "Z: %{z}<extra></extra>"
                    ),
                )
            )

            fig.add_trace(
                go.Scatter3d(
                    x=[projected_point[0]],
                    y=[projected_point[1]],
                    z=[projected_point[2]],
                    mode="markers",
                    marker=dict(size=5, color="orange", symbol="square"),
                    name=f"{pi_id} Bildpunkt {bird_id}",
                    showlegend=False,
                    hovertemplate=(
                        f"<b>{pi_id}</b><br>"
                        f"Bird: {bird_id}<br>"
                        "Projektionspunkt auf der orange Kameraebene<br>"
                        "X: %{x}<br>"
                        "Y: %{y}<br>"
                        "Z: %{z}<extra></extra>"
                    ),
                )
            )


def add_territories_to_figure(fig: go.Figure, territories: list[dict] | None) -> None:
    """
    Zeichnet die geschaetzten Revierzentren in die 3D-Figur:

    - Raute am geschaetzten Zentrum
    - gestrichelter Revierkreis auf Bodenhoehe (z = 0)
    - Kreuz am wahren Nest (nur in der Simulation vorhanden)
    - Verbindungslinie zwischen Schaetzung und Ground Truth als Fehlermass

    territories: Liste serialisierter TerritoryEstimate-Dicts aus dem Store.
    """
    if not territories:
        return

    center_x: list[float] = []
    center_y: list[float] = []
    center_z: list[float] = []
    center_text: list[str] = []

    circle_x: list[float | None] = []
    circle_y: list[float | None] = []
    circle_z: list[float | None] = []

    nest_x: list[float] = []
    nest_y: list[float] = []
    nest_z: list[float] = []
    nest_text: list[str] = []

    error_x: list[float | None] = []
    error_y: list[float | None] = []
    error_z: list[float | None] = []

    for territory in territories:
        estimated_e = to_float(territory.get("center_e"), 0.0)
        estimated_n = to_float(territory.get("center_n"), 0.0)
        radius = to_float(territory.get("radius"), 0.0)
        track_id = str(territory.get("track_id", ""))
        method_label = str(territory.get("method_label", ""))

        center_x.append(estimated_e)
        center_y.append(estimated_n)
        center_z.append(0.0)
        center_text.append(
            f"{track_id}<br>Verfahren: {method_label}<br>Radius: {radius:.1f} m"
        )

        # Revierkreis auf Bodenhoehe.
        steps = 60
        for step in range(steps + 1):
            angle = 2 * math.pi * step / steps
            circle_x.append(estimated_e + radius * math.cos(angle))
            circle_y.append(estimated_n + radius * math.sin(angle))
            circle_z.append(0.0)
        circle_x.append(None)
        circle_y.append(None)
        circle_z.append(None)

        true_e = territory.get("true_nest_e")
        true_n = territory.get("true_nest_n")
        if true_e is None or true_n is None:
            continue

        true_e = to_float(true_e, 0.0)
        true_n = to_float(true_n, 0.0)
        error_value = to_float(territory.get("error_m"), 0.0)

        nest_x.append(true_e)
        nest_y.append(true_n)
        nest_z.append(0.0)
        nest_text.append(f"Wahres Nest ({track_id})<br>Abweichung: {error_value:.2f} m")

        error_x.extend([estimated_e, true_e, None])
        error_y.extend([estimated_n, true_n, None])
        error_z.extend([0.0, 0.0, None])

    if circle_x:
        fig.add_trace(
            go.Scatter3d(
                x=circle_x,
                y=circle_y,
                z=circle_z,
                mode="lines",
                line=dict(width=3, color="#14b8a6", dash="dash"),
                name="Revierkreis",
                legendgroup="revier",
                hoverinfo="skip",
            )
        )

    if center_x:
        fig.add_trace(
            go.Scatter3d(
                x=center_x,
                y=center_y,
                z=center_z,
                mode="markers",
                marker=dict(size=7, color="#14b8a6", symbol="diamond"),
                name="Revierzentrum (geschaetzt)",
                legendgroup="revier",
                text=center_text,
                hovertemplate="%{text}<br>X: %{x:.1f}<br>Y: %{y:.1f}<extra></extra>",
            )
        )

    if error_x:
        fig.add_trace(
            go.Scatter3d(
                x=error_x,
                y=error_y,
                z=error_z,
                mode="lines",
                line=dict(width=4, color="#facc15"),
                name="Abweichung zur Ground Truth",
                legendgroup="revier",
                hoverinfo="skip",
            )
        )

    if nest_x:
        fig.add_trace(
            go.Scatter3d(
                x=nest_x,
                y=nest_y,
                z=nest_z,
                mode="markers",
                marker=dict(size=6, color="#facc15", symbol="x"),
                name="Wahres Nest (Ground Truth)",
                legendgroup="revier",
                text=nest_text,
                hovertemplate="%{text}<br>X: %{x:.1f}<br>Y: %{y:.1f}<extra></extra>",
            )
        )


def build_figure(
    trajectory_df: pd.DataFrame | None,
    pi_setup: list[dict],
    message: str | None = None,
    aspect_mode: str = "data",
    group_by_bird: bool = True,
    show_projection: bool = False,
    show_detected_vectors_only: bool = False,
    detected_vectors_payload: dict | None = None,
    group_column: str = "bird_id",
    trajectory_lines: bool = False,
    track_visibility: dict[str, bool] | None = None,
    territories: list[dict] | None = None,
) -> go.Figure:
    """
    track_visibility: optionales Mapping track_id -> bool. True bedeutet
    die Trajektorie ist beim Laden sichtbar, False bedeutet sie ist in der
    Legende vorhanden, aber ausgeblendet ("legendonly") -- der Nutzer kann
    sie jederzeit per Klick auf den Legendeneintrag manuell wieder
    einblenden, unabhaengig vom Bulk-Filter. Fehlt ein Track im Mapping
    oder ist track_visibility None, gilt er als sichtbar.
    """
    fig = go.Figure()

    if show_detected_vectors_only:
        add_detected_vectors_to_figure(fig, detected_vectors_payload)
    elif trajectory_df is not None:
        if group_by_bird:
            for bird_id, group in trajectory_df.groupby(group_column, sort=False):
                is_visible = True
                if track_visibility is not None:
                    is_visible = track_visibility.get(str(bird_id), True)
                trace_visible = True if is_visible else "legendonly"

                fig.add_trace(
                    go.Scatter3d(
                        x=group["enu_e"],
                        y=group["enu_n"],
                        z=group["enu_u"],
                        mode="lines+markers" if trajectory_lines else "markers",
                        marker=dict(symbol="circle", size=3),
                        line=dict(width=3) if trajectory_lines else None,
                        name=str(bird_id),
                        visible=trace_visible,
                        text=group["timestamp"].astype(str),
                        hovertemplate=(
                            "<b>%{fullData.name}</b><br>"
                            "Zeit: %{text}<br>"
                            "X: %{x}<br>"
                            "Y: %{y}<br>"
                            "Z: %{z}<extra></extra>"
                        ),
                    )
                )

                dx = group["enu_e"].shift(-1) - group["enu_e"]
                dy = group["enu_n"].shift(-1) - group["enu_n"]
                dz = group["enu_u"].shift(-1) - group["enu_u"]
                vector_mask = dx.notna() & dy.notna() & dz.notna()

                if vector_mask.any():
                    fig.add_trace(
                        go.Cone(
                            x=group.loc[vector_mask, "enu_e"],
                            y=group.loc[vector_mask, "enu_n"],
                            z=group.loc[vector_mask, "enu_u"],
                            u=dx.loc[vector_mask],
                            v=dy.loc[vector_mask],
                            w=dz.loc[vector_mask],
                            sizemode="absolute",
                            sizeref=20,
                            anchor="tail",
                            showscale=False,
                            visible=trace_visible,
                            name=f"{bird_id} Richtung",
                        )
                    )
        else:
            fig.add_trace(
                go.Scatter3d(
                    x=trajectory_df["enu_e"],
                    y=trajectory_df["enu_n"],
                    z=trajectory_df["enu_u"],
                    mode="markers",
                    marker=dict(symbol="circle", size=4, color="#60a5fa"),
                    name="Vogelpositionen",
                    text=trajectory_df["bird_id"].astype(str),
                    hovertemplate=(
                        "<b>Vogel</b>: %{text}<br>"
                        "X: %{x}<br>"
                        "Y: %{y}<br>"
                        "Z: %{z}<extra></extra>"
                    ),
                )
            )
            if show_projection:
                add_pi_to_bird_vectors(fig, pi_setup, trajectory_df.to_dict(orient="records"), CAMERA_PLANE_SIDE_LENGTH)

    if pi_setup and not show_detected_vectors_only:
        pi_x = []
        pi_y = []
        pi_z = []
        pi_names = []
        view_u = []
        view_v = []
        view_w = []

        for pi in pi_setup:
            yaw_deg = to_float(pi.get("yaw_deg", pi.get("view_horizontal_deg")), 0.0)
            pitch_deg = to_float(pi.get("pitch_deg", pi.get("view_vertical_deg")), 0.0)
            roll_deg = to_float(pi.get("roll_deg"), 0.0)
            view_length = to_float(pi.get("view_length"), 400.0)
            pi_x.append(to_float(pi.get("x"), 0.0))
            pi_y.append(to_float(pi.get("y"), 0.0))
            pi_z.append(to_float(pi.get("z"), 0.0))
            pi_names.append(f"{pi.get('name', pi.get('id', 'Pi'))} ({pi.get('id', 'pi')})")

            # 1) Richtung bestimmen, 2) auf feste Länge skalieren.
            view_x, view_y, view_z = compute_view_vector(
                yaw_deg,
                pitch_deg,
                roll_deg,
                view_length,
            )
            view_u.append(view_x)
            view_v.append(view_y)
            view_w.append(view_z)

        fig.add_trace(
            go.Scatter3d(
                x=pi_x,
                y=pi_y,
                z=pi_z,
                mode="markers+text",
                marker=dict(size=5, color="red", symbol="diamond"),
                text=pi_names,
                textposition="top center",
                name="Raspberry Pis",
                legendgroup="setup",
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "X: %{x}<br>"
                    "Y: %{y}<br>"
                    "Z: %{z}<extra></extra>"
                ),
            )
        )

        for pi, x, y, z, u, v, w in zip(pi_setup, pi_x, pi_y, pi_z, view_u, view_v, view_w):
            view_end_x = x + u
            view_end_y = y + v
            view_end_z = z + w

            fig.add_trace(
                go.Scatter3d(
                    x=[x, view_end_x],
                    y=[y, view_end_y],
                    z=[z, view_end_z],
                    mode="lines",
                    line=dict(width=3, color="#f59e0b"),
                    name=f"{pi.get('name', pi.get('id', 'Pi'))} Blick",
                    showlegend=False,
                    legendgroup="setup",
                    hovertemplate=(
                        f"<b>{pi.get('name', pi.get('id', 'Pi'))}</b><br>"
                        f"Yaw: {yaw_deg:.1f}°<br>"
                        f"Pitch: {pitch_deg:.1f}°<br>"
                        f"Roll: {roll_deg:.1f}°<br>"
                        "X: %{x}<br>"
                        "Y: %{y}<br>"
                        "Z: %{z}<extra></extra>"
                    ),
                )
            )

            plane_side_length = CAMERA_PLANE_SIDE_LENGTH
            square_corners = perpendicular_square_corners(
                view_end_x,
                view_end_y,
                view_end_z,
                u,
                v,
                w,
                plane_side_length,
            )

            fig.add_trace(
                go.Mesh3d(
                    x=[corner[0] for corner in square_corners],
                    y=[corner[1] for corner in square_corners],
                    z=[corner[2] for corner in square_corners],
                    i=[0, 0],
                    j=[1, 2],
                    k=[2, 3],
                    color="#f59e0b",
                    opacity=0.22,
                    flatshading=True,
                    name=f"{pi.get('name', pi.get('id', 'Pi'))} Fläche",
                    showlegend=False,
                    hoverinfo="skip",
                    legendgroup="setup",
                )
            )

    add_territories_to_figure(fig, territories)

    if message:
        fig.add_annotation(
            text=message,
            x=0.5,
            y=0.98,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(size=14),
            bgcolor="rgba(0,0,0,0.35)",
        )

    fig.update_layout(
        template="plotly_dark",
        scene=dict(
            xaxis_title="X",
            yaxis_title="Y",
            zaxis_title="Z",
            aspectmode=aspect_mode,
            camera=dict(projection=dict(type="orthographic")),
        ),
        height=1000,
        legend=dict(itemsizing="constant"),
    )

    return fig


def short_error_message(error) -> str:
    """Kürzt Fehlermeldungen für die Anzeige im Interface."""
    message = " ".join(str(error or "Kein Fehlertext verfügbar.").split())
    return message[-600:]


def classification_rows(store_data) -> list[dict]:
    """
    Liest die Tabellenzeilen aus dem Klassifikations-Store.

    Der Store enthält seit der Erweiterung um das Detail-Panel ein Dict mit
    den Schlüsseln "rows" und "details". Die ältere Form (eine reine Liste)
    wird weiterhin akzeptiert.
    """
    if isinstance(store_data, dict):
        rows = store_data.get("rows")
        return rows if isinstance(rows, list) else []

    if isinstance(store_data, list):
        return store_data

    return []


def classification_details(store_data) -> dict:
    """Liefert die Merkmale und Begründungen je Track aus dem Store."""
    if isinstance(store_data, dict):
        details = store_data.get("details")
        return details if isinstance(details, dict) else {}

    return {}


# ============================================================================
# SEITENPANEL: REVIERSCHÄTZUNG UND TRACK-DETAIL
# ============================================================================
PANEL_CARD_STYLE = {
    "border": "1px solid var(--border)",
    "borderRadius": "14px",
    "background": "linear-gradient(180deg, var(--panel), var(--panel2))",
    "padding": "14px",
}

PANEL_TITLE_STYLE = {
    "fontSize": "13px",
    "fontWeight": "700",
    "color": "var(--text)",
    "marginBottom": "8px",
}

PANEL_HINT_STYLE = {
    "fontSize": "12px",
    "color": "var(--muted)",
    "lineHeight": "1.5",
}


def build_side_panel() -> html.Div:
    """
    Rechte Panelspalte neben dem 3D-Plot.

    Oben die Revierschätzung (Verfahrenswahl, Fehlerstatistik, eine Karte je
    Track), darunter das Detail zum Track, dessen Zeile in der
    Klassifikationstabelle angeklickt wurde.
    """
    return html.Div(
        style={
            "width": "340px",
            "flex": "0 0 340px",
            "display": "flex",
            "flexDirection": "column",
            "gap": "12px",
        },
        children=[
            html.Div(
                style=PANEL_CARD_STYLE,
                children=[
                    html.Div("Revierschätzung", style=PANEL_TITLE_STYLE),
                    html.Div(
                        (
                            "Geschätzt wird das Revierzentrum aus den als Feldlerche "
                            "klassifizierten Tracks. Der Singflug markiert das Revier, "
                            "nicht punktgenau das Nest."
                        ),
                        style=PANEL_HINT_STYLE,
                    ),
                    html.Button(
                        "Revierzentren schätzen",
                        id="btn-estimate-territories",
                        n_clicks=0,
                        className="btn",
                        style={"marginTop": "12px", "width": "100%"},
                    ),
                    html.Div(
                        "Schätzverfahren",
                        className="label",
                        style={"marginTop": "14px", "marginBottom": "6px"},
                    ),
                    dcc.RadioItems(
                        id="territory-method",
                        options=[
                            {"label": METHOD_LABELS[method], "value": method}
                            for method in AVAILABLE_METHODS
                        ],
                        value=DEFAULT_METHOD,
                        labelStyle={
                            "display": "block",
                            "color": "var(--text)",
                            "fontSize": "12px",
                            "marginBottom": "5px",
                        },
                        inputStyle={"marginRight": "7px"},
                    ),
                    html.Div(
                        "Status: noch nicht geschätzt",
                        id="territory-status",
                        className="mono",
                        style={
                            "marginTop": "12px",
                            "fontSize": "11px",
                            "color": "var(--muted)",
                            "lineHeight": "1.5",
                        },
                    ),
                    html.Div(
                        id="territory-cards",
                        style={
                            "marginTop": "12px",
                            "display": "flex",
                            "flexDirection": "column",
                            "gap": "8px",
                            "maxHeight": "260px",
                            "overflowY": "auto",
                        },
                        children=[],
                    ),
                    html.Div(
                        style={
                            "height": "1px",
                            "background": "var(--border)",
                            "margin": "14px 0 12px",
                        }
                    ),
                    html.Button(
                        "Als GeoJSON exportieren",
                        id="btn-export-geojson",
                        n_clicks=0,
                        className="btn",
                        style={"width": "100%"},
                    ),
                    dcc.Download(id="geojson-download"),
                    html.Div(
                        "Öffnet sich in QGIS oder Google Earth. Referenzpunkt im Tab „Kameras“.",
                        id="geojson-status",
                        style={
                            "marginTop": "8px",
                            "fontSize": "11px",
                            "color": "var(--muted)",
                            "lineHeight": "1.5",
                        },
                    ),
                ],
            ),
            html.Div(
                style=PANEL_CARD_STYLE,
                children=[
                    html.Div(
                        "Track-Detail",
                        id="track-detail-title",
                        style=PANEL_TITLE_STYLE,
                    ),
                    html.Div(
                        "Zeile in der Klassifikationstabelle anklicken.",
                        id="track-detail-subtitle",
                        style=PANEL_HINT_STYLE,
                    ),
                    dcc.Graph(
                        id="track-detail-profile",
                        figure=build_altitude_profile_figure(),
                        config={"displayModeBar": False, "responsive": True},
                        style={"height": "210px", "marginTop": "8px"},
                    ),
                    html.Div(
                        id="track-detail-features",
                        style={"marginTop": "4px"},
                        children=[],
                    ),
                    html.Div(
                        id="track-detail-reasons",
                        style={"marginTop": "12px"},
                        children=[],
                    ),
                ],
            ),
        ],
    )


# ============================================================================
# LAYOUT
# ============================================================================
app.layout = html.Div(
    className="container",
    children=[
        dcc.Store(id="lark-classification-store", data=None),
        dcc.Store(id="territory-store", data=None),
        dcc.Store(id="pi-map-center", data=None),

        html.Div(
            className="header",
            children=[
                html.Div(
                    className="title",
                    children=[
                        html.H1("Flugtrajektorien", className="h1"),
                        html.Span(
                            "Testversion mit GeoJSON-Export · Port 8061",
                            className="badge",
                            style={"marginLeft": "10px"},
                        ),
                        html.P(
                            "Berechnung und 3D-Visualisierung",
                            className="sub",
                        ),
                    ],
                ),
                html.Span("Dash / Plotly", className="badge"),
            ],
        ),

        html.Div(
            className="tabWrap",
            children=[
                dcc.Tabs(
                    id="tabs",
                    value="tab-1",
                    className="custom-tabs",
                    children=[
                        dcc.Tab(
                            label="Vogelgenerierung",
                            value="tab-generate",
                            className="custom-tab",
                            selected_className="custom-tab--selected",
                        ),
                        dcc.Tab(
                            label="Kameras",
                            value="tab-3",
                            className="custom-tab",
                            selected_className="custom-tab--selected",
                        ),
                        dcc.Tab(
                            label="Übersicht",
                            value="tab-1",
                            className="custom-tab",
                            selected_className="custom-tab--selected",
                        ),
                        dcc.Tab(
                            label="Trajektorie berechnen",
                            value="tab-2",
                            className="custom-tab",
                            selected_className="custom-tab--selected",
                        ),
                        dcc.Tab(
                            label="2D-Kamerabild",
                            value="tab-4",
                            className="custom-tab",
                            selected_className="custom-tab--selected",
                        ),
                    ],
                ),

                html.Div(
                    style={"padding": "12px"},
                    children=[
                        bird_generator_tab(),
                        html.Div(
                            id="tab-3-content",
                            style={"display": "none"},
                            children=[
                                html.Div(
                                    className="card",
                                    children=[
                                        html.Div(
                                            className="cardTitle",
                                            children=[
                                                html.H2("Kameras"),
                                                html.Span("editierbar", className="badge"),
                                            ],
                                        ),

                                        html.Div(
                                            style={
                                                "border": "1px solid var(--border)",
                                                "borderRadius": "12px",
                                                "background": "rgba(255,255,255,0.02)",
                                                "padding": "12px 14px",
                                                "marginBottom": "14px",
                                            },
                                            children=[
                                                html.Div(
                                                    "Standorte auf der Karte setzen",
                                                    className="label",
                                                    style={"marginBottom": "6px"},
                                                ),
                                                html.Div(
                                                    (
                                                        "Karte verschieben, bis das gelbe Fadenkreuz auf der "
                                                        "gewünschten Stelle liegt, dann die Station dorthin setzen. "
                                                        "Die erste Station bildet den Ursprung des ENU-Systems, "
                                                        "also (0/0) — die Meterwerte der übrigen Stationen werden "
                                                        "beim Setzen automatisch neu berechnet."
                                                    ),
                                                    className="sub",
                                                ),
                                                dcc.Graph(
                                                    id="pi-map",
                                                    figure=build_pi_map_figure(
                                                        default_pi_setup_with_coordinates()
                                                    ),
                                                    config={
                                                        "displayModeBar": False,
                                                        "scrollZoom": True,
                                                        "responsive": True,
                                                    },
                                                    style={
                                                        "height": "460px",
                                                        "marginTop": "10px",
                                                        "borderRadius": "12px",
                                                        "overflow": "hidden",
                                                    },
                                                ),
                                                html.Div(
                                                    className="row",
                                                    style={"marginTop": "10px"},
                                                    children=[
                                                        html.Button(
                                                            "Pi 1 hierhin",
                                                            id="btn-place-pi-1",
                                                            n_clicks=0,
                                                            className="btn",
                                                        ),
                                                        html.Button(
                                                            "Pi 2 hierhin",
                                                            id="btn-place-pi-2",
                                                            n_clicks=0,
                                                            className="btn",
                                                        ),
                                                        html.Button(
                                                            "Pi 3 hierhin",
                                                            id="btn-place-pi-3",
                                                            n_clicks=0,
                                                            className="btn",
                                                        ),
                                                        html.Button(
                                                            "X / Y aus Koordinaten berechnen",
                                                            id="btn-derive-pi-positions",
                                                            n_clicks=0,
                                                            className="btn",
                                                        ),
                                                    ],
                                                ),
                                                html.Div(
                                                    (
                                                        "Nur nötig, wenn die Standorte per GPS aufgenommen wurden. "
                                                        "Achtung: handelsübliches GPS streut um 2–5 m, und für die "
                                                        "Triangulation zählt die relative Geometrie der Stationen. "
                                                        "Wo die Abstände direkt messbar sind (Maßband, Laser, RTK), "
                                                        "sind die eingetragenen Meter genauer als der Umweg über GPS. "
                                                        "Für den Kartenbezug allein reicht grobes GPS völlig. "
                                                        "Die voreingestellten Koordinaten sind ein Platzhalter im "
                                                        "Raum Bielefeld und wurden nicht eingemessen."
                                                    ),
                                                    id="pi-geo-status",
                                                    className="sub",
                                                    style={"marginTop": "8px"},
                                                ),
                                            ],
                                        ),

                                        dash_table.DataTable(
                                            id="pi-setup-table",
                                            data=default_pi_setup_with_coordinates(),
                                            columns=[
                                                {"name": "Name", "id": "name", "editable": False},
                                                {"name": "X", "id": "x", "type": "numeric"},
                                                {"name": "Y", "id": "y", "type": "numeric"},
                                                {"name": "Z", "id": "z", "type": "numeric"},
                                                {"name": "Yaw um Z [°]", "id": "yaw_deg", "type": "numeric"},
                                                {"name": "Pitch hoch/runter [°]", "id": "pitch_deg", "type": "numeric"},
                                                {"name": "Roll [°]", "id": "roll_deg", "type": "numeric"},
                                                {"name": "Breite", "id": "lat", "type": "numeric"},
                                                {"name": "Länge", "id": "lon", "type": "numeric"},
                                            ],
                                            editable=True,
                                            row_deletable=False,
                                            sort_action="none",
                                            style_table={"overflowX": "auto"},
                                            style_cell={
                                                "backgroundColor": "rgba(255,255,255,0.02)",
                                                "color": "var(--text)",
                                                "border": "1px solid var(--border)",
                                                "padding": "8px",
                                                "fontFamily": "inherit",
                                                "fontSize": "13px",
                                            },
                                            style_header={
                                                "backgroundColor": "rgba(255,255,255,0.05)",
                                                "fontWeight": "700",
                                                "color": "var(--text)",
                                                "border": "1px solid var(--border)",
                                            },
                                            style_data_conditional=[
                                                {"if": {"row_index": "odd"}, "backgroundColor": "rgba(255,255,255,0.015)"},
                                                {"if": {"column_id": "name"}, "fontWeight": "700"},
                                                {
                                                    "if": {"state": "active"},
                                                    "backgroundColor": "rgba(28,35,48,0.96)",
                                                    "color": "#e7eaf0",
                                                    "border": "1px solid rgba(42,98,255,0.45)",
                                                },
                                                {
                                                    "if": {"state": "selected"},
                                                    "backgroundColor": "rgba(34,42,58,0.96)",
                                                    "color": "#e7eaf0",
                                                    "border": "1px solid rgba(42,98,255,0.45)",
                                                },
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        html.Div(
                            id="tab-1-content",
                            children=[
                                html.Div(
                                    className="card",
                                    children=[
                                        html.Div(
                                            className="cardTitle",
                                            children=[
                                                html.H2("Triangulation simulieren"),
                                                html.Span(
                                                    "trajectory.py",
                                                    className="badge",
                                                ),
                                            ],
                                        ),

                                        html.Div(
                                            className="row",
                                            children=[
                                                html.Button(
                                                    "Vogel-JSON laden",
                                                    id="btn-load-birds-json",
                                                    n_clicks=0,
                                                    className="btn",
                                                ),
                                                html.Button(
                                                    "Punkte in Pyramiden filtern",
                                                    id="btn-filter-pyramid-points",
                                                    n_clicks=0,
                                                    className="btn",
                                                ),
                                                html.Button(
                                                    "Position bestimmen",
                                                    id="btn-determine-position",
                                                    n_clicks=0,
                                                    className="btn",
                                                ),
                                            ],
                                        ),

                                        html.Div(className="divider"),

                                        html.Div(
                                            id="overview-action-output",
                                            children="Status: bereit",
                                            className="mono",
                                            style={
                                                "marginBottom": "12px",
                                                "color": "var(--muted)",
                                            },
                                        ),

                                        html.Div(
                                            id="bird-position-summary",
                                            children=[
                                                html.Div(
                                                    "Keine sichtbaren Vogelpositionen.",
                                                    className="mono",
                                                )
                                            ],
                                            style={
                                                "marginTop": "8px",
                                                "marginBottom": "12px",
                                                "padding": "8px 10px",
                                                "border": "1px solid var(--border)",
                                                "borderRadius": "6px",
                                                "backgroundColor": "rgba(255,255,255,0.02)",
                                            },
                                        ),

                                        html.Div(
                                            className="row",
                                            children=[
                                                html.Span("Achsenverhältnis", className="label"),
                                                dcc.RadioItems(
                                                    id="aspect-mode-toggle",
                                                    options=[
                                                        {"label": "Cube", "value": "cube"},
                                                        {"label": "Data", "value": "data"},
                                                    ],
                                                    value="data",
                                                    inline=True,
                                                    labelStyle={
                                                        "marginRight": "12px",
                                                        "color": "var(--text)",
                                                        "fontWeight": "700",
                                                    },
                                                    inputStyle={"marginRight": "6px"},
                                                ),
                                            ],
                                        ),

                                        html.Div(
                                            className="cardTitle",
                                            children=[html.H2("3D-Flugtrajektorien")],
                                        ),

                                        dcc.Loading(
                                            type="default",
                                            children=dcc.Graph(
                                                id="main-graph",
                                                figure=build_figure(
                                                    load_trajectory_dataframe(),
                                                    default_pi_setup(),
                                                    "Klicke auf „Trajektorie berechnen“, um die Trajektorien zu laden.",
                                                    aspect_mode="cube",
                                                ),
                                                config={"responsive": True},
                                                style={"height": "1000px"},
                                            ),
                                        ),
                                    ],
                                ),
                            ],
                        ),

                        html.Div(
                            id="tab-2-content",
                            style={"display": "none"},
                            children=[
                                html.Div(
                                    className="card",
                                    children=[
                                        html.Div(
                                            className="cardTitle",
                                            children=[
                                                html.H2("Datenquelle"),
                                                html.Span(
                                                    "JSON-Ausgabe",
                                                    className="badge",
                                                ),
                                            ],
                                        ),
                                        html.P(
                                            str(
                                                TRAJECTORY_JSON.relative_to(BASE_DIR)
                                            ),
                                            className="sub",
                                        ),
                                        html.Div(
                                            className="row",
                                            children=[
                                                html.Button(
                                                    "Trajektorie berechnen",
                                                    id="btn-run-trajectory",
                                                    n_clicks=0,
                                                    className="btn",
                                                ),
                                            ],
                                        ),
                                        html.Div(
                                            "Status: bereit",
                                            id="action-output",
                                            className="mono",
                                            style={
                                                "marginLeft": "10px",
                                                "color": "var(--muted)",
                                            },
                                        ),
                                        html.Div(className="divider"),

                                        html.Div(
                                            className="cardTitle",
                                            children=[html.H2("Berechnete Trajektorien")],
                                        ),

                                        html.Div(
                                            className="row",
                                            children=[
                                                html.Span("Sichtbarkeit der Tracks", className="label"),
                                                dcc.RadioItems(
                                                    id="track-visibility-filter",
                                                    options=[
                                                        {"label": "Alle Tracks anzeigen", "value": "all"},
                                                        {"label": "Nur Feldlerchen anzeigen", "value": "only_larks"},
                                                        {"label": "Alle Tracks ausblenden", "value": "none"},
                                                    ],
                                                    value="all",
                                                    inline=True,
                                                    labelStyle={
                                                        "marginRight": "16px",
                                                        "color": "var(--text)",
                                                        "fontWeight": "700",
                                                    },
                                                    inputStyle={"marginRight": "6px"},
                                                ),
                                            ],
                                        ),
                                        html.Div(
                                            (
                                                "Hinweis: Alle Tracks bleiben immer in der Legende sichtbar. "
                                                "Diese Auswahl setzt nur die Anfangssichtbarkeit — jeder Track "
                                                "kann jederzeit durch Anklicken seines Legendeneintrags einzeln "
                                                "ein- oder ausgeblendet werden, unabhängig von dieser Auswahl. "
                                                "„Nur Feldlerchen anzeigen“ setzt zusätzlich voraus, dass zuvor "
                                                "„Feldlerchen klassifizieren“ ausgeführt wurde."
                                            ),
                                            className="sub",
                                            style={"marginTop": "6px", "marginBottom": "10px"},
                                        ),

                                        html.Div(
                                            style={
                                                "display": "flex",
                                                "gap": "14px",
                                                "alignItems": "stretch",
                                            },
                                            children=[
                                                html.Div(
                                                    style={"flex": "1 1 auto", "minWidth": "0"},
                                                    children=dcc.Loading(
                                                        type="default",
                                                        children=dcc.Graph(
                                                            id="trajectory-graph",
                                                            figure=build_figure(
                                                                load_trajectory_dataframe(),
                                                                default_pi_setup(),
                                                                "Klicke auf „Trajektorie berechnen“, um die Trajektorien zu laden.",
                                                                aspect_mode="data",
                                                                group_by_bird=True,
                                                                group_column="determined_track_id",
                                                                trajectory_lines=True,
                                                            ),
                                                            config={"responsive": True},
                                                            style={"height": "1000px"},
                                                        ),
                                                    ),
                                                ),
                                                build_side_panel(),
                                            ],
                                        ),

                                        html.Div(className="divider"),

                                        html.Div(
                                            className="cardTitle",
                                            children=[html.H2("Feldlerchen-Klassifikation")],
                                        ),

                                        html.Div(
                                            className="row",
                                            children=[
                                                html.Button(
                                                    "Feldlerchen klassifizieren",
                                                    id="btn-classify-larks",
                                                    n_clicks=0,
                                                    className="btn",
                                                ),
                                            ],
                                        ),

                                        html.Div(
                                            "Status: bereit",
                                            id="classification-status",
                                            className="mono",
                                            style={
                                                "marginTop": "10px",
                                                "color": "var(--muted)",
                                            },
                                        ),

                                        dash_table.DataTable(
                                            id="lark-classification-table",
                                            columns=[
                                                {"name": "Track", "id": "track_id"},
                                                {"name": "Feldlerche?", "id": "is_feldlerche"},
                                                {"name": "Score", "id": "score"},
                                                {"name": "Pfad", "id": "classification_path"},
                                            ],
                                            data=[],
                                            page_size=15,
                                            sort_action="native",
                                            style_table={"overflowX": "auto"},
                                            style_cell={
                                                "backgroundColor": "rgba(255,255,255,0.02)",
                                                "color": "var(--text)",
                                                "border": "1px solid var(--border)",
                                                "padding": "8px",
                                                "fontFamily": "inherit",
                                                "fontSize": "13px",
                                            },
                                            style_header={
                                                "backgroundColor": "rgba(255,255,255,0.05)",
                                                "fontWeight": "700",
                                                "color": "var(--text)",
                                                "border": "1px solid var(--border)",
                                            },
                                            style_data_conditional=[
                                                {"if": {"row_index": "odd"}, "backgroundColor": "rgba(255,255,255,0.015)"},
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        html.Div(
                            id="tab-4-content",
                            style={"display": "none"},
                            children=[
                                html.Div(
                                    id="camera-2d-status",
                                    children="",
                                    style={"display": "none"},
                                ),
                                html.Div(
                                    id="camera-time-label",
                                    children="Zeitfilter: Alle Zeiten",
                                    className="mono",
                                    style={"marginBottom": "8px", "color": "var(--muted)"},
                                ),
                                html.Div(
                                    style={"marginBottom": "12px"},
                                    children=[
                                        dcc.Slider(
                                            id="camera-time-slider",
                                            min=0,
                                            max=0,
                                            step=1,
                                            value=0,
                                            marks={0: "Alle"},
                                            included=False,
                                            updatemode="drag",
                                            disabled=True,
                                        ),
                                    ],
                                ),
                                html.Div(
                                    id="camera-2d-container",
                                    style={"display": "flex", "flexDirection": "column", "alignItems": "center"},
                                    children=[
                                        html.Div("Keine Projektionen vorhanden.", className="mono")
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


# ============================================================================
# CALLBACKS
# ============================================================================
@app.callback(
    Output("tab-1-content", "style"),
    Output("tab-2-content", "style"),
    Output("tab-3-content", "style"),
    Output("tab-4-content", "style"),
    Output("tab-generate-content", "style"),
    Input("tabs", "value"),
)
def render_tab(tab):
    visible = {"display": "block"}
    hidden = {"display": "none"}
    if tab == "tab-1":
        return visible, hidden, hidden, hidden, hidden

    if tab == "tab-2":
        return hidden, visible, hidden, hidden, hidden

    if tab == "tab-3":
        return hidden, hidden, visible, hidden, hidden

    if tab == "tab-4":
        return hidden, hidden, hidden, visible, hidden

    return hidden, hidden, hidden, hidden, visible

@app.callback(
    Output("camera-2d-container", "children"),
    Output("camera-2d-status", "children"),
    Output("camera-time-slider", "min"),
    Output("camera-time-slider", "max"),
    Output("camera-time-slider", "marks"),
    Output("camera-time-slider", "value"),
    Output("camera-time-slider", "disabled"),
    Output("camera-time-label", "children"),
    Input("tabs", "value"),
    Input("btn-filter-pyramid-points", "n_clicks"),
    Input("camera-time-slider", "value"),
)
def update_camera_2d_tab(tab_value, _filter_clicks, slider_value):
    if tab_value != "tab-4":
        return no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update

    return build_camera_2d_children(slider_value)

@app.callback(
    Output("main-graph", "figure"),
    Output("overview-action-output", "children"),
    Output("bird-position-summary", "children"),
    Input("btn-load-birds-json", "n_clicks"),
    Input("btn-filter-pyramid-points", "n_clicks"),
    Input("btn-determine-position", "n_clicks"),
    Input("pi-setup-table", "data"),
    Input("aspect-mode-toggle", "value"),
)
def update_overview(
    load_birds_clicks,
    filter_clicks,
    determine_position_clicks,
    pi_setup_rows,
    aspect_mode,
):
    pi_setup = normalize_pi_setup(pi_setup_rows)

    for pi in pi_setup:
        pi["view_length"] = 400.0

    triggered = ctx.triggered_id
    status_message = "Status: bereit"
    summary_children = [html.Div("Keine sichtbaren Vogelpositionen.", className="mono")]

    if triggered == "btn-load-birds-json" and (load_birds_clicks or 0) > 0:
        birds_df = load_birds_dataframe()

        if birds_df is None:
            return (
                build_figure(
                    None,
                    pi_setup,
                    "Keine Vogel-JSON-Datei gefunden.",
                    aspect_mode=aspect_mode or "data",
                    group_by_bird=False,
                ),
                "Status: Keine Vogel-JSON-Datei gefunden.",
                summary_children,
            )

        status_message = (
            "Status: vogel_flugbahnen.json geladen. "
            "Bitte anschließend „Punkte in Pyramiden filtern“ klicken."
        )

        return (
            build_figure(
                birds_df,
                pi_setup,
                status_message,
                aspect_mode=aspect_mode or "data",
                group_by_bird=False,
                show_projection=False,
            ),
            status_message,
            summarize_visible_birds(birds_df.to_dict(orient="records")),
        )

    if triggered == "btn-filter-pyramid-points" and (filter_clicks or 0) > 0:
        payload = load_json_payload(BIRDS_JSON)
        source_file = BIRDS_JSON.name

        if payload is None:
            payload = load_trajectory_payload()
            source_file = TRAJECTORY_JSON.name

        records = extract_trajectory_records(payload)

        if not records:
            return (
                build_figure(
                    None,
                    pi_setup,
                    "Keine Trajektoriendaten zum Filtern gefunden.",
                    aspect_mode=aspect_mode or "data",
                    group_by_bird=False,
                ),
                "Status: Keine Trajektoriendaten zum Filtern gefunden.",
                summary_children,
            )

        points_by_pi = points_in_view_volume_for_each_pi(
            pi_setup,
            records,
            CAMERA_PLANE_SIDE_LENGTH,
        )

        matching_point_ids = {
            id(point)
            for pi_points in points_by_pi.values()
            for point in pi_points
        }

        filtered_records = [
            record for record in records
            if id(record) in matching_point_ids
        ]

        if BIRDS_JSON.is_file():
            save_birds_payload(payload, filtered_records)
        elif TRAJECTORY_JSON.is_file():
            save_trajectory_payload(payload, filtered_records)

        save_projected_bird_images(
            pi_setup,
            points_by_pi,
            CAMERA_PLANE_SIDE_LENGTH,
            source_file,
        )

        filtered_df = pd.DataFrame(filtered_records)
        deleted_points = len(records) - len(filtered_records)

        status_message = (
            f"Status: {len(filtered_records)} Punkte innerhalb der Pyramiden behalten, "
            f"{deleted_points} Punkte entfernt. "
            f"Projektionen in {BIRD_IMAGES_JSON.name} gespeichert."
        )

        return (
            build_figure(
                filtered_df,
                pi_setup,
                status_message,
                aspect_mode=aspect_mode or "data",
                group_by_bird=False,
                show_projection=True,
            ),
            status_message,
            summarize_visible_birds(filtered_records),
        )

    if triggered == "btn-determine-position" and (determine_position_clicks or 0) > 0:
        detected_payload = build_detected_positions_payload(BIRD_IMAGES_JSON)

        if not isinstance(detected_payload, dict):
            message = f"Status: Keine gültigen Daten in {BIRD_IMAGES_JSON.name} gefunden."

            return (
                build_figure(
                    None,
                    pi_setup,
                    message,
                    aspect_mode=aspect_mode or "data",
                    show_detected_vectors_only=True,
                    detected_vectors_payload=None,
                ),
                message,
                [html.Div("Keine erkannten Vektorpositionen verfügbar.", className="mono")],
            )

        save_detected_positions_payload(DETECTED_POSITIONS_JSON, detected_payload)

        vectors = detected_payload.get("detected_vectors", [])
        vector_count = len(vectors) if isinstance(vectors, list) else 0

        message = (
            f"Status: {vector_count} erkannte Vektoren wurden in "
            f"{DETECTED_POSITIONS_JSON.name} gespeichert."
        )

        return (
            build_figure(
                None,
                pi_setup,
                message,
                aspect_mode=aspect_mode or "data",
                show_detected_vectors_only=True,
                detected_vectors_payload=detected_payload,
            ),
            message,
            [html.Div(f"Erkannte Positionen: {vector_count} Vektoren.", className="mono")],
        )

    trajectory_df = load_trajectory_dataframe()

    return (
        build_figure(
            trajectory_df,
            pi_setup,
            status_message,
            aspect_mode=aspect_mode or "data",
            group_by_bird=False,
        ),
        status_message,
        summarize_visible_birds(
            trajectory_df.to_dict(orient="records")
        ) if trajectory_df is not None else summary_children,
    )

@app.callback(
    Output("trajectory-graph", "figure"),
    Output("action-output", "children"),
    Input("btn-run-trajectory", "n_clicks"),
    State("pi-setup-table", "data"),
    State("aspect-mode-toggle", "value"),
    prevent_initial_call=True,
)
def run_trajectory_plot(n_clicks, pi_setup_rows, aspect_mode):
    pi_setup = normalize_pi_setup(pi_setup_rows)

    for pi in pi_setup:
        pi["view_length"] = 400.0

    figure_options = {
        "aspect_mode": aspect_mode or "data",
        "group_by_bird": True,
        "group_column": "determined_track_id",
        "trajectory_lines": True,
    }

    if not TRAJECTORY_SCRIPT.is_file():
        message = f"Status: Datei nicht gefunden: {TRAJECTORY_SCRIPT}"

        return (
            build_figure(
                load_trajectory_dataframe(),
                pi_setup,
                "trajectory.py wurde nicht gefunden.",
                **figure_options,
            ),
            message,
        )

    try:
        subprocess.run(
            [sys.executable, str(TRAJECTORY_SCRIPT)],
            cwd=str(BASE_DIR),
            check=True,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_SECONDS,
        )

        trajectory_df = load_trajectory_dataframe()
        message = "Status: trajectory.py wurde erfolgreich ausgeführt."

        return (
            build_figure(
                trajectory_df,
                pi_setup,
                message,
                **figure_options,
            ),
            message,
        )

    except subprocess.TimeoutExpired:
        message = f"Status: Abbruch nach {SCRIPT_TIMEOUT_SECONDS} Sekunden."

        return (
            build_figure(
                load_trajectory_dataframe(),
                pi_setup,
                "Die Berechnung hat das Zeitlimit überschritten.",
                **figure_options,
            ),
            message,
        )

    except subprocess.CalledProcessError as error:
        details = short_error_message(error.stderr or error.stdout)
        message = f"Status: trajectory.py ist fehlgeschlagen: {details}"

        return (
            build_figure(
                load_trajectory_dataframe(),
                pi_setup,
                "Fehler beim Ausführen von trajectory.py.",
                **figure_options,
            ),
            message,
        )

    except Exception as error:
        message = f"Status: Fehler beim Laden der Daten: {short_error_message(error)}"

        return (
            build_figure(
                load_trajectory_dataframe(),
                pi_setup,
                "Die Trajektoriendaten konnten nicht geladen werden.",
                **figure_options,
            ),
            message,
        )


@app.callback(
    Output("lark-classification-table", "data"),
    Output("classification-status", "children"),
    Output("lark-classification-store", "data"),
    Input("btn-classify-larks", "n_clicks"),
    prevent_initial_call=True,
)
def classify_larks(_n_clicks):
    """
    Klassifiziert alle Trajektorien aus vogel_flugbahnen_determined.json
    danach, ob sie dem Feldlerchen-Flugmuster (Steigflug -> Singflug ->
    Sturzflug) entsprechen. Nutzt skylark_classifier.py. Das Ergebnis
    wird zusaetzlich in lark-classification-store abgelegt, damit der
    Sichtbarkeitsfilter im Trajektorien-Graph darauf zugreifen kann.
    """
    records = extract_trajectory_records(load_trajectory_payload())

    if not records:
        return [], "Status: Keine Trajektoriendaten gefunden. Bitte zuerst „Trajektorie berechnen“ ausführen.", None

    results = classify_tracks(records, group_column="determined_track_id")

    table_data = [
        {
            "track_id": result.track_id,
            "is_feldlerche": "Ja" if result.is_feldlerche else "Nein",
            "score": result.score,
            "classification_path": result.classification_path,
        }
        for result in results
    ]

    # Merkmale und Begruendungen je Track fuer das Detail-Seitenpanel.
    # Bisher wurden result.reasons und result.features berechnet und wieder
    # verworfen - hier landen sie im Store, damit das Panel sie ohne eine
    # zweite Klassifikation anzeigen kann.
    details = {
        result.track_id: {
            "is_feldlerche": "Ja" if result.is_feldlerche else "Nein",
            "score": result.score,
            "classification_path": result.classification_path,
            "features": features_to_dict(result.features),
            "criteria": result.criteria,
        }
        for result in results
    }

    evaluation = evaluate_against_ground_truth(records, results)

    status = f"Status: {len(results)} Tracks klassifiziert."
    if evaluation:
        status += (
            f" (Recall={evaluation['recall']}, "
            f"Precision={evaluation['precision']}, "
            f"Accuracy={evaluation['accuracy']})"
        )

    return table_data, status, {"rows": table_data, "details": details}


@app.callback(
    Output("trajectory-graph", "figure", allow_duplicate=True),
    Input("track-visibility-filter", "value"),
    Input("lark-classification-store", "data"),
    Input("territory-store", "data"),
    State("pi-setup-table", "data"),
    State("aspect-mode-toggle", "value"),
    prevent_initial_call=True,
)
def update_trajectory_visibility(
    visibility_filter,
    classification_data,
    territory_data,
    pi_setup_rows,
    aspect_mode,
):
    """
    Setzt die Anfangssichtbarkeit aller Tracks im 3D-Graph basierend auf
    dem gewaehlten Modus:
    - "all": alle Tracks sichtbar (Standard)
    - "only_larks": nur als Feldlerche klassifizierte Tracks sichtbar,
      alle anderen als "legendonly" (in der Legende vorhanden, aber
      ausgeblendet)
    - "none": alle Tracks als "legendonly"

    WICHTIG: Es werden immer ALLE Tracks geplottet und bleiben in der
    Legende vorhanden - unabhaengig vom gewaehlten Modus. Der Bulk-Filter
    setzt nur die initiale Sichtbarkeit; jeder Track kann jederzeit per
    Klick auf seinen Legendeneintrag individuell ein- oder ausgeblendet
    werden, unabhaengig von dieser Auswahl.
    """
    pi_setup = normalize_pi_setup(pi_setup_rows)

    for pi in pi_setup:
        pi["view_length"] = 400.0

    figure_options = {
        "aspect_mode": aspect_mode or "data",
        "group_by_bird": True,
        "group_column": "determined_track_id",
        "trajectory_lines": True,
        "territories": territory_data,
    }

    trajectory_df = load_trajectory_dataframe()

    if trajectory_df is None:
        return build_figure(
            None,
            pi_setup,
            "Keine Trajektoriendaten vorhanden. Bitte zuerst „Trajektorie berechnen“ ausführen.",
            **figure_options,
        )

    all_track_ids = {str(track_id) for track_id in trajectory_df["determined_track_id"].unique()}

    if visibility_filter == "none":
        track_visibility = {track_id: False for track_id in all_track_ids}
        return build_figure(
            trajectory_df,
            pi_setup,
            "Status: Alle Tracks anfangs ausgeblendet (ueber Legende einzeln wieder einblendbar).",
            track_visibility=track_visibility,
            **figure_options,
        )

    if visibility_filter == "only_larks":
        if not classification_data:
            track_visibility = {track_id: False for track_id in all_track_ids}
            return build_figure(
                trajectory_df,
                pi_setup,
                "Status: Bitte zuerst „Feldlerchen klassifizieren“ ausführen.",
                track_visibility=track_visibility,
                **figure_options,
            )

        lark_track_ids = {
            str(item.get("track_id"))
            for item in classification_rows(classification_data)
            if item.get("is_feldlerche") == "Ja"
        }

        track_visibility = {
            track_id: (track_id in lark_track_ids)
            for track_id in all_track_ids
        }

        message = (
            f"Status: {len(lark_track_ids)} Feldlerchen-Track(s) anfangs sichtbar, "
            "restliche Tracks ueber Legende einzeln einblendbar."
            if lark_track_ids
            else "Status: Keine Tracks als Feldlerche klassifiziert."
        )

        return build_figure(
            trajectory_df,
            pi_setup,
            message,
            track_visibility=track_visibility,
            **figure_options,
        )

    # visibility_filter == "all" (Standard)
    track_visibility = {track_id: True for track_id in all_track_ids}
    return build_figure(
        trajectory_df,
        pi_setup,
        None,
        track_visibility=track_visibility,
        **figure_options,
    )


# ============================================================================
# CALLBACKS: REVIERSCHÄTZUNG UND TRACK-DETAIL
# ============================================================================
@app.callback(
    Output("pi-map-center", "data"),
    Input("pi-map", "relayoutData"),
    State("pi-map-center", "data"),
    prevent_initial_call=True,
)
def remember_map_center(relayout_data, previous_center):
    """
    Merkt sich Mittelpunkt und Zoom der Karte.

    Plotly meldet Klicks nur auf Datenpunkten, nicht auf freier
    Kartenfläche. Statt zu klicken verschiebt man deshalb die Karte unter
    das feste Fadenkreuz — und dafür muss bekannt sein, wo die Mitte
    gerade liegt. Der Wert hält außerdem den Ausschnitt fest, wenn die
    Karte nach einer Änderung neu gezeichnet wird.
    """
    center = center_from_relayout(relayout_data, previous_center)

    return center if center else no_update


@app.callback(
    Output("pi-setup-table", "data", allow_duplicate=True),
    Output("pi-geo-status", "children", allow_duplicate=True),
    Input("btn-place-pi-1", "n_clicks"),
    Input("btn-place-pi-2", "n_clicks"),
    Input("btn-place-pi-3", "n_clicks"),
    State("pi-map-center", "data"),
    State("pi-setup-table", "data"),
    prevent_initial_call=True,
)
def place_pi_on_map(_c1, _c2, _c3, center, pi_setup_rows):
    """
    Setzt die gewählte Station auf die aktuelle Kartenmitte und rechnet
    anschließend die Meterwerte aller Stationen neu.
    """
    if not center or center.get("lat") is None:
        return no_update, (
            "Verschiebe zuerst die Karte — erst danach ist die Zielposition bekannt."
        )

    if not pi_setup_rows:
        return no_update, "Keine Pi-Stationen vorhanden."

    index_by_button = {
        "btn-place-pi-1": 0,
        "btn-place-pi-2": 1,
        "btn-place-pi-3": 2,
    }
    index = index_by_button.get(ctx.triggered_id)

    if index is None or index >= len(pi_setup_rows):
        return no_update, "Diese Station gibt es in der Tabelle nicht."

    rows = [dict(row) for row in pi_setup_rows]
    rows[index]["lat"] = round(float(center["lat"]), 6)
    rows[index]["lon"] = round(float(center["lon"]), 6)

    updated, error = derive_pi_positions(rows)

    if error:
        return rows, error

    name = str(updated[index].get("name", f"Pi {index + 1}"))
    status = (
        f"{name} auf {rows[index]['lat']:.6f}, {rows[index]['lon']:.6f} gesetzt. "
        f"X / Y neu berechnet: "
        + ", ".join(
            f"{row.get('name', '?')} = {row.get('x')} / {row.get('y')}"
            for row in updated
        )
        + ". Gemessene Abstände kannst du in der Tabelle weiterhin von Hand "
        "überschreiben — sie sind genauer als die Kartenposition."
    )

    return updated, status


@app.callback(
    Output("pi-map", "figure"),
    Input("pi-setup-table", "data"),
    State("pi-map-center", "data"),
)
def update_pi_map(pi_setup_rows, center):
    """Zeichnet die Karte neu, wenn sich Standorte geändert haben."""
    return build_pi_map_figure(pi_setup_rows or [], center=center)


@app.callback(
    Output("pi-setup-table", "data", allow_duplicate=True),
    Output("pi-geo-status", "children"),
    Input("btn-derive-pi-positions", "n_clicks"),
    State("pi-setup-table", "data"),
    prevent_initial_call=True,
)
def derive_positions_from_coordinates(_n_clicks, pi_setup_rows):
    """
    Rechnet die GPS-Koordinaten der Stationen in lokale ENU-Meter um und
    schreibt sie in die Spalten X und Y. Die erste Zeile wird zum
    Ursprung (0/0).

    Das ist die Richtung, die ein Livesystem braucht: im Feld nimmt man
    Breiten- und Längengrad auf, gerechnet wird danach in Metern.
    """
    updated, error = derive_pi_positions(pi_setup_rows or [])

    if error:
        return no_update, error

    distances = []
    origin = updated[0]

    for row in updated[1:]:
        try:
            distance = math.hypot(
                float(row["x"]) - float(origin["x"]),
                float(row["y"]) - float(origin["y"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        distances.append(f"{row.get('name', row.get('id', '?'))}: {distance:.1f} m")

    status = "X und Y aus den Koordinaten berechnet."
    if distances:
        status += " Abstand zur ersten Station — " + ", ".join(distances) + "."
    status += (
        " Vergleiche diese Werte mit den tatsächlich gemessenen Abständen: "
        "weichen sie stark ab, war das GPS zu ungenau für die Triangulation."
    )

    return updated, status


def territory_map_links(estimate, reference) -> html.Div | None:
    """
    Koordinate und Kartenlinks zu einem geschätzten Revierzentrum.

    Ohne Georeferenz in der Pi-Tabelle gibt es nichts anzuzeigen, dann
    wird None zurückgegeben.
    """
    if reference is None:
        return None

    reference_latitude, reference_longitude = reference

    latitude, longitude = enu_to_wgs84(
        estimate.center_e, estimate.center_n, reference_latitude, reference_longitude
    )

    link_style = {"fontSize": "11px", "marginRight": "10px"}

    return html.Div(
        style={
            "marginTop": "9px",
            "paddingTop": "9px",
            "borderTop": "1px solid var(--border)",
        },
        children=[
            html.Div(
                format_coordinates(latitude, longitude),
                style={
                    "fontFamily": "ui-monospace, monospace",
                    "fontSize": "11px",
                    "color": "var(--text)",
                    "marginBottom": "6px",
                },
            ),
            html.Div(
                children=[
                    html.A(
                        "Karte",
                        href=maps_url(latitude, longitude),
                        target="_blank",
                        style=link_style,
                    ),
                    html.A(
                        "Satellit",
                        href=maps_satellite_url(latitude, longitude),
                        target="_blank",
                        style=link_style,
                    ),
                    html.A(
                        "Navigation",
                        href=maps_directions_url(latitude, longitude),
                        target="_blank",
                        style=link_style,
                    ),
                ]
            ),
        ],
    )


def build_territory_card(estimate, reference=None) -> html.Div:
    """Eine Karte je geschätztem Revierzentrum für das Seitenpanel."""
    if estimate.error_m is None:
        error_child = html.Span("keine Ground Truth", style={"color": "var(--muted)"})
    elif estimate.error_m <= 5.0:
        error_child = html.Span(f"{estimate.error_m:.1f} m", style={"color": "var(--good)"})
    elif estimate.error_m <= 15.0:
        error_child = html.Span(f"{estimate.error_m:.1f} m", style={"color": "var(--warn)"})
    else:
        error_child = html.Span(f"{estimate.error_m:.1f} m", style={"color": "var(--bad)"})

    value_style = {"fontFamily": "ui-monospace, monospace", "color": "var(--text)"}
    label_style = {"color": "var(--muted)"}

    children = [
        html.Div(
            estimate.track_id,
            style={
                "fontFamily": "ui-monospace, monospace",
                "fontSize": "12px",
                "color": "var(--text)",
                "marginBottom": "7px",
            },
        ),
        html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "1fr 1fr",
                "gap": "6px 10px",
                "fontSize": "11px",
            },
            children=[
                html.Div([html.Div("Zentrum", style=label_style),
                          html.Div(f"{estimate.center_e:.1f} / {estimate.center_n:.1f}", style=value_style)]),
                html.Div([html.Div("Radius", style=label_style),
                          html.Div(f"{estimate.radius:.1f} m", style=value_style)]),
                html.Div([html.Div("Punkte", style=label_style),
                          html.Div(str(estimate.sample_count), style=value_style)]),
                html.Div([html.Div("Abweichung", style=label_style),
                          html.Div(error_child, style={"fontFamily": "ui-monospace, monospace"})]),
            ],
        ),
    ]

    if estimate.note:
        children.append(
            html.Div(
                estimate.note,
                style={"fontSize": "10px", "color": "var(--warn)", "marginTop": "6px"},
            )
        )

    map_links = territory_map_links(estimate, reference)
    if map_links is not None:
        children.append(map_links)

    return html.Div(
        style={
            "border": "1px solid var(--border)",
            "borderRadius": "12px",
            "background": "rgba(255,255,255,0.02)",
            "padding": "11px",
        },
        children=children,
    )


@app.callback(
    Output("territory-store", "data"),
    Output("territory-status", "children"),
    Output("territory-cards", "children"),
    Input("btn-estimate-territories", "n_clicks"),
    Input("territory-method", "value"),
    State("lark-classification-store", "data"),
    State("pi-setup-table", "data"),
    prevent_initial_call=True,
)
def estimate_territory_centers(_n_clicks, method, classification_data, pi_setup_rows):
    """
    Schätzt aus den als Feldlerche klassifizierten Tracks je ein
    Revierzentrum und legt das Ergebnis im Store ab, damit der 3D-Plot
    Zentrum, Revierkreis und Abweichung zur Ground Truth zeichnen kann.

    Ein Wechsel des Schätzverfahrens rechnet direkt neu — so lassen sich
    die drei Verfahren ohne erneute Klassifikation vergleichen.
    """
    rows = classification_rows(classification_data)

    if not rows:
        return None, "Status: Bitte zuerst „Feldlerchen klassifizieren“ ausführen.", []

    records = extract_trajectory_records(load_trajectory_payload())

    if not records:
        return None, "Status: Keine Trajektoriendaten gefunden.", []

    lark_track_ids = [
        str(row.get("track_id"))
        for row in rows
        if row.get("is_feldlerche") == "Ja"
    ]

    if not lark_track_ids:
        return None, "Status: Keine Tracks als Feldlerche klassifiziert.", []

    selected_method = method if method in AVAILABLE_METHODS else DEFAULT_METHOD

    estimates = estimate_territories(
        records,
        track_ids=lark_track_ids,
        group_column="determined_track_id",
        method=selected_method,
    )

    if not estimates:
        return None, "Status: Zu wenige Punkte je Track für eine Schätzung.", []

    serialised = [
        {
            "track_id": estimate.track_id,
            "center_e": estimate.center_e,
            "center_n": estimate.center_n,
            "radius": estimate.radius,
            "method": estimate.method,
            "method_label": estimate.method_label,
            "sample_count": estimate.sample_count,
            "true_nest_e": estimate.true_nest_e,
            "true_nest_n": estimate.true_nest_n,
            "error_m": estimate.error_m,
            "note": estimate.note,
        }
        for estimate in estimates
    ]

    evaluation = evaluate_estimates(estimates)

    status = (
        f"Status: {len(estimates)} Revierzentren geschätzt "
        f"({METHOD_LABELS.get(selected_method, selected_method)})."
    )

    if evaluation:
        status += (
            f" Abweichung zur Ground Truth: Median {evaluation['median_error']} m, "
            f"p90 {evaluation['p90_error']} m, max {evaluation['max_error']} m."
        )
    else:
        status += (
            " Keine Nest-Ground-Truth in den Daten — die Abweichung lässt sich "
            "nur für neu generierte Datensätze berechnen."
        )

    reference = reference_from_pi_rows(pi_setup_rows)

    if reference is None:
        status += (
            " Für Koordinaten und Kartenlinks braucht die erste Pi-Station "
            "Breite und Länge (Tab „Kameras“)."
        )

    return (
        serialised,
        status,
        [build_territory_card(estimate, reference) for estimate in estimates],
    )


@app.callback(
    Output("geojson-download", "data"),
    Output("geojson-status", "children"),
    Input("btn-export-geojson", "n_clicks"),
    State("territory-store", "data"),
    State("pi-setup-table", "data"),
    prevent_initial_call=True,
)
def export_territories_geojson(_n_clicks, territory_data, pi_setup_rows):
    """
    Exportiert die geschätzten Reviere als GeoJSON-Datei zum Download.

    Die ENU-Koordinaten der Simulation werden dafür über den Standort der
    ersten Pi-Station nach WGS84 umgerechnet — sie definiert ENU (0/0).

    Die Datei ist in den Metadaten und in jedem einzelnen Feature als
    Simulationsergebnis gekennzeichnet, damit sie nicht mit einer echten
    Brutvogelkartierung verwechselt werden kann.
    """
    if not territory_data:
        return no_update, "Bitte zuerst Revierzentren schätzen."

    reference = reference_from_pi_rows(pi_setup_rows)

    if reference is None:
        return no_update, "Die erste Pi-Station braucht Breite und Länge (Tab „Kameras“)."

    reference_latitude, reference_longitude = reference

    if not -90.0 <= reference_latitude <= 90.0:
        return no_update, "Breitengrad muss zwischen -90 und 90 liegen."

    if not -180.0 <= reference_longitude <= 180.0:
        return no_update, "Längengrad muss zwischen -180 und 180 liegen."

    payload = territories_to_geojson(
        territory_data,
        reference_latitude,
        reference_longitude,
        pi_setup=normalize_pi_setup(pi_setup_rows),
    )

    status = (
        f"{len(territory_data)} Reviere exportiert, Bezugspunkt "
        f"{reference_latitude:.4f} / {reference_longitude:.4f}. "
        "Die Datei ist als Simulationsergebnis gekennzeichnet."
    )

    return (
        dict(
            content=json.dumps(payload, ensure_ascii=False, indent=2),
            filename="reviere_simulation.geojson",
        ),
        status,
    )


@app.callback(
    Output("track-detail-title", "children"),
    Output("track-detail-subtitle", "children"),
    Output("track-detail-profile", "figure"),
    Output("track-detail-features", "children"),
    Output("track-detail-reasons", "children"),
    Input("lark-classification-table", "active_cell"),
    State("lark-classification-table", "derived_viewport_data"),
    State("lark-classification-store", "data"),
    prevent_initial_call=True,
)
def show_track_detail(active_cell, viewport_data, classification_data):
    """
    Füllt das Detail-Panel für den Track, dessen Zeile angeklickt wurde:
    Höhenprofil z(t) mit eingefärbten Flugphasen, die Merkmale und die
    Begründung der Klassifikation.
    """
    empty = (
        "Track-Detail",
        "Zeile in der Klassifikationstabelle anklicken.",
        build_altitude_profile_figure(),
        [],
        [],
    )

    if not active_cell or not viewport_data:
        return empty

    row_index = active_cell.get("row")
    if row_index is None or row_index >= len(viewport_data):
        return empty

    track_id = str(viewport_data[row_index].get("track_id", ""))
    if not track_id:
        return empty

    records = [
        record
        for record in extract_trajectory_records(load_trajectory_payload())
        if str(record.get("determined_track_id")) == track_id
    ]

    if not records:
        return (
            track_id,
            "Keine Punkte zu diesem Track gefunden.",
            build_altitude_profile_figure(message="Keine Daten"),
            [],
            [],
        )

    detail = classification_details(classification_data).get(track_id, {})
    is_feldlerche = detail.get("is_feldlerche", "?")
    score = detail.get("score")
    path = detail.get("classification_path", "")

    badge_color = "var(--good)" if is_feldlerche == "Ja" else "var(--muted)"
    title = html.Div(
        style={"display": "flex", "alignItems": "center", "gap": "8px"},
        children=[
            html.Span(
                track_id,
                style={"fontFamily": "ui-monospace, monospace", "fontSize": "13px"},
            ),
            html.Span(
                f"{'Feldlerche' if is_feldlerche == 'Ja' else 'keine Feldlerche'}"
                + (f" · {score}" if score is not None else ""),
                style={
                    "fontSize": "11px",
                    "fontWeight": "700",
                    "color": badge_color,
                },
            ),
        ],
    )

    subtitle = f"{len(records)} Punkte · Entscheidungspfad: {path or 'unbekannt'}"

    figure = build_altitude_profile_figure(records, track_id=track_id)

    feature_rows = format_feature_rows(detail.get("features"))
    if feature_rows:
        features_children = [
            html.Div("Merkmale", className="label", style={"marginBottom": "6px"}),
            html.Div(
                style={
                    "display": "grid",
                    "gridTemplateColumns": "1fr 1fr",
                    "gap": "6px 10px",
                    "fontSize": "11px",
                },
                children=[
                    html.Div(
                        [
                            html.Div(label, style={"color": "var(--muted)"}),
                            html.Div(
                                value,
                                style={
                                    "fontFamily": "ui-monospace, monospace",
                                    "color": "var(--text)",
                                },
                            ),
                        ]
                    )
                    for label, value in feature_rows
                ],
            ),
        ]
    else:
        features_children = [
            html.Div(
                "Merkmale erst nach dem Klassifizieren verfügbar.",
                style=PANEL_HINT_STYLE,
            )
        ]

    criteria = detail.get("criteria") or []
    if criteria:
        reasons_children = [
            html.Div("Begründung", className="label", style={"marginBottom": "6px"}),
            html.Div(
                style={"display": "flex", "flexDirection": "column", "gap": "7px"},
                children=[
                    html.Div(
                        style={"display": "flex", "gap": "8px", "alignItems": "flex-start"},
                        children=[
                            html.Span(
                                "✓" if criterion.get("met") else "✗",
                                style={
                                    "color": "var(--good)" if criterion.get("met") else "var(--bad)",
                                    "fontSize": "12px",
                                    "lineHeight": "1.35",
                                },
                            ),
                            html.Span(
                                criterion.get("text", ""),
                                style={
                                    "fontSize": "11px",
                                    "color": "var(--text)" if criterion.get("met") else "var(--muted)",
                                    "lineHeight": "1.4",
                                },
                            ),
                        ],
                    )
                    for criterion in criteria
                    if criterion.get("text")
                ],
            ),
        ]
    else:
        reasons_children = [
            html.Div(
                "Begründung erst nach dem Klassifizieren verfügbar.",
                style=PANEL_HINT_STYLE,
            )
        ]

    return title, subtitle, figure, features_children, reasons_children


# ============================================================================
# CALLBACKS: VOGELGENERIERUNG
# ============================================================================
@app.callback(
    Output("bird-generation-status", "children"),
    Output("bird-generation-status", "style"),
    Output("bird-stats", "children"),
    Output("bird-2d-graph", "figure"),
    Output("bird-3d-graph", "figure"),
    Input("btn-generate-birds", "n_clicks"),
    State("gen-number-of-birds", "value"),
    State("gen-time-interval", "value"),
    State("gen-date", "date"),
    State("gen-seed", "value"),
    State("gen-x-min", "value"),
    State("gen-x-max", "value"),
    State("gen-y-min", "value"),
    State("gen-y-max", "value"),
    State("gen-z-min", "value"),
    State("gen-z-max", "value"),
    State("gen-initial-spread", "value"),
    State("gen-min-speed", "value"),
    State("gen-max-speed", "value"),
    State("gen-position-noise", "value"),
    State("gen-variance-angle", "value"),
    State("gen-variance-speed", "value"),
    State("gen-variance-z", "value"),
    State("gen-variance-vertical-speed", "value"),
    State("gen-number-of-larks", "value"),
    prevent_initial_call=True,
)
def generate_birds_json(
    _n_clicks,
    number_of_birds_value,
    time_interval_value,
    simulation_date_value,
    seed_text_value,
    x_min_value,
    x_max_value,
    y_min_value,
    y_max_value,
    z_min_value,
    z_max_value,
    initial_spread_value,
    min_speed_value,
    max_speed_value,
    position_noise_value,
    variance_angle_value,
    variance_speed_value,
    variance_z_value,
    variance_vertical_speed_value,
    number_of_larks_value,
):
    try:
        parameters = parse_generation_parameters(
            number_of_birds_value=number_of_birds_value,
            time_interval_value=time_interval_value,
            simulation_date_value=simulation_date_value,
            seed_text_value=seed_text_value,
            x_min_value=x_min_value,
            x_max_value=x_max_value,
            y_min_value=y_min_value,
            y_max_value=y_max_value,
            z_min_value=z_min_value,
            z_max_value=z_max_value,
            initial_spread_value=initial_spread_value,
            min_speed_value=min_speed_value,
            max_speed_value=max_speed_value,
            position_noise_value=position_noise_value,
            variance_angle_value=variance_angle_value,
            variance_speed_value=variance_speed_value,
            variance_z_value=variance_z_value,
            variance_vertical_speed_value=variance_vertical_speed_value,
            number_of_larks_value=number_of_larks_value,
        )

        records = generate_birds(**parameters)
        output_data = {
            "plotAttachment": {
                "version": "1.0",
                "type": "triangulation_setup",
            },
            "simulated_birds": records,
        }

        BIRDS_JSON.write_text(
            json.dumps(
                output_data,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        df = pd.DataFrame(records)

        figure_2d = create_birds_2d_figure(df)

        figure_3d = create_birds_3d_figure(
            df,
            parameters["z_min"],
            parameters["z_max"],
        )

        return (
            (
                f"Status: {len(records)} Datenpunkte wurden erfolgreich "
                f"erzeugt und in „{BIRDS_JSON.name}“ gespeichert."
            ),
            SUCCESS_STATUS_STYLE,
            create_bird_stats(df),
            figure_2d,
            figure_3d,
        )

    except ValueError as error:
        return (
            f"Status: Eingabefehler – {error}",
            ERROR_STATUS_STYLE,
            no_update,
            no_update,
            no_update,
        )

    except Exception as error:
        return (
            (
                "Status: Fehler bei der Generierung – "
                f"{short_error_message(error)}"
            ),
            ERROR_STATUS_STYLE,
            no_update,
            no_update,
            no_update,
        )


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=True)
