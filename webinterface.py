import json
import subprocess
import sys
from pathlib import Path

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, ctx, dash_table, dcc, html
from pi_view_simulation import (
    compute_view_vector,
    default_pi_setup,
    normalize_pi_setup,
    perpendicular_square_corners,
    points_in_view_volume_for_each_pi,
    to_float,
)

# ============================================================================
# KONFIGURATION
# ============================================================================
APP_PORT = 8060

BASE_DIR = Path(__file__).resolve().parent
TRAJECTORY_SCRIPT = BASE_DIR / "trajectory.py"
TRAJECTORY_JSON = (
    BASE_DIR / "vogel_flugbahnen_determined.json"
)

SCRIPT_TIMEOUT_SECONDS = 300
CAMERA_PLANE_SIDE_LENGTH = 600.0

app = dash.Dash(__name__)
server = app.server


# ============================================================================
# HILFSFUNKTIONEN
# ============================================================================
def status_figure(message: str) -> go.Figure:
    """Leerer Plot mit Statusmeldung."""
    fig = go.Figure()

    fig.add_annotation(
        text=message,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
        font=dict(size=16),
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=1000,
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )

    return fig


def load_trajectory_dataframe() -> pd.DataFrame | None:
    if not TRAJECTORY_JSON.is_file():
        return None

    payload = load_trajectory_payload()

    if isinstance(payload, dict):
        data = payload.get("simulated_birds")
    else:
        data = payload

    if not isinstance(data, list) or not data:
        return None

    df = pd.DataFrame(data)

    required_columns = {"bird_id", "timestamp", "enu_e", "enu_n", "enu_u"}
    missing_columns = required_columns - set(df.columns)
    if missing_columns:
        return None

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for coordinate in ("enu_e", "enu_n", "enu_u"):
        df[coordinate] = pd.to_numeric(df[coordinate], errors="coerce")

    df = df.dropna(subset=["bird_id", "timestamp", "enu_e", "enu_n", "enu_u"])
    df = df.sort_values(["bird_id", "timestamp"])

    return df if not df.empty else None


def load_trajectory_payload() -> dict | list | None:
    if not TRAJECTORY_JSON.is_file():
        return None

    with TRAJECTORY_JSON.open("r", encoding="utf-8") as file:
        return json.load(file)


def extract_trajectory_records(payload: dict | list | None) -> list[dict]:
    if isinstance(payload, dict):
        records = payload.get("simulated_birds")
    else:
        records = payload

    return records if isinstance(records, list) else []


def save_trajectory_payload(payload: dict | list | None, records: list[dict]) -> None:
    if isinstance(payload, dict):
        payload_to_save = dict(payload)
        payload_to_save["simulated_birds"] = records
    else:
        payload_to_save = records

    with TRAJECTORY_JSON.open("w", encoding="utf-8") as file:
        json.dump(payload_to_save, file, ensure_ascii=False, indent=2)


def build_figure(
    trajectory_df: pd.DataFrame | None,
    pi_setup: list[dict],
    message: str | None = None,
    aspect_mode: str = "data",
) -> go.Figure:
    fig = go.Figure()

    if trajectory_df is not None:
        for bird_id, group in trajectory_df.groupby("bird_id", sort=False):
            fig.add_trace(
                go.Scatter3d(
                    x=group["enu_e"],
                    y=group["enu_n"],
                    z=group["enu_u"],
                    mode="lines+markers",
                    marker=dict(symbol="circle", size=2),
                    name=str(bird_id),
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
                        name=f"{bird_id} Richtung",
                    )
                )

    if pi_setup:
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


# ============================================================================
# LAYOUT
# ============================================================================
app.layout = html.Div(
    className="container",
    children=[
        # Download-Komponente
        dcc.Download(id="download-json"),

        html.Div(
            className="header",
            children=[
                html.Div(
                    className="title",
                    children=[
                        html.H1("Flugtrajektorien", className="h1"),
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
                            label="Übersicht",
                            value="tab-1",
                            className="custom-tab",
                            selected_className="custom-tab--selected",
                        ),
                        dcc.Tab(
                            label="Daten",
                            value="tab-2",
                            className="custom-tab",
                            selected_className="custom-tab--selected",
                        ),
                        dcc.Tab(
                            label="Kameras",
                            value="tab-3",
                            className="custom-tab",
                            selected_className="custom-tab--selected",
                        ),
                    ],
                ),

                html.Div(
                    style={"padding": "12px"},
                    children=[
                        html.Div(
                            id="tab-1-content",
                            children=[
                                html.Div(
                                    className="card",
                                    children=[
                                        html.Div(
                                            className="cardTitle",
                                            children=[
                                                html.H2("Trajektorie berechnen"),
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
                                                    "Trajektorie berechnen",
                                                    id="btn-run-trajectory",
                                                    n_clicks=0,
                                                    className="btn",
                                                ),
                                                html.Button(
                                                    "Punkte in Pyramiden filtern",
                                                    id="btn-filter-pyramid-points",
                                                    n_clicks=0,
                                                    className="btn",
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
                                            ],
                                        ),

                                        html.Div(className="divider"),

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
                                                    "JSON herunterladen",
                                                    id="btn-download-json",
                                                    n_clicks=0,
                                                    className="btn",
                                                ),
                                                html.Div(
                                                    "",
                                                    id="download-status",
                                                    className="mono",
                                                    style={
                                                        "marginLeft": "10px",
                                                        "color": "var(--muted)",
                                                    },
                                                ),
                                            ]
                                        )
                                    ],
                                ),
                            ],
                        ),
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

                                        dash_table.DataTable(
                                            id="pi-setup-table",
                                            data=default_pi_setup(),
                                            columns=[
                                                {"name": "Name", "id": "name", "editable": False},
                                                {"name": "X", "id": "x", "type": "numeric"},
                                                {"name": "Y", "id": "y", "type": "numeric"},
                                                {"name": "Z", "id": "z", "type": "numeric"},
                                                {"name": "Yaw um Z [°]", "id": "yaw_deg", "type": "numeric"},
                                                {"name": "Pitch hoch/runter [°]", "id": "pitch_deg", "type": "numeric"},
                                                {"name": "Roll [°]", "id": "roll_deg", "type": "numeric"},
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
    Input("tabs", "value"),
)
def render_tab(tab):
    if tab == "tab-1":
        return {"display": "block"}, {"display": "none"}, {"display": "none"}

    if tab == "tab-2":
        return {"display": "none"}, {"display": "block"}, {"display": "none"}

    return {"display": "none"}, {"display": "none"}, {"display": "block"}


@app.callback(
    Output("main-graph", "figure"),
    Output("action-output", "children"),
    Input("btn-run-trajectory", "n_clicks"),
    Input("btn-filter-pyramid-points", "n_clicks"),
    Input("pi-setup-table", "data"),
    Input("aspect-mode-toggle", "value"),
)
def run_trajectory(n_clicks, filter_clicks, pi_setup_rows, aspect_mode):
    pi_setup = normalize_pi_setup(pi_setup_rows)
    view_length = 400.0

    for index, pi in enumerate(pi_setup):
        pi["view_length"] = view_length

    triggered = ctx.triggered_id

    status_message = "Status: bereit"

    if triggered == "btn-run-trajectory" and (n_clicks or 0) > 0:
        if not TRAJECTORY_SCRIPT.is_file():
            return (
                build_figure(
                    load_trajectory_dataframe(),
                    pi_setup,
                    "trajectory.py wurde nicht gefunden.",
                    aspect_mode=aspect_mode or "data",
                ),
                f"Status: Datei nicht gefunden: {TRAJECTORY_SCRIPT}",
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
            status_message = "Status: trajectory.py wurde erfolgreich ausgeführt. 3D-Plot geladen."
        except subprocess.TimeoutExpired:
            return (
                build_figure(
                    load_trajectory_dataframe(),
                    pi_setup,
                    "Die Berechnung hat das Zeitlimit überschritten.",
                    aspect_mode=aspect_mode or "data",
                ),
                f"Status: Abbruch nach {SCRIPT_TIMEOUT_SECONDS} Sekunden.",
            )
        except subprocess.CalledProcessError as error:
            details = short_error_message(error.stderr or error.stdout)
            return (
                build_figure(
                    load_trajectory_dataframe(),
                    pi_setup,
                    "Fehler beim Ausführen von trajectory.py.",
                    aspect_mode=aspect_mode or "data",
                ),
                f"Status: trajectory.py ist fehlgeschlagen: {details}",
            )
        except Exception as error:
            return (
                build_figure(
                    load_trajectory_dataframe(),
                    pi_setup,
                    "Die Trajektoriendaten konnten nicht geladen werden.",
                    aspect_mode=aspect_mode or "data",
                ),
                f"Status: Fehler beim Laden der Daten: {short_error_message(error)}",
            )
    elif triggered == "btn-filter-pyramid-points" and (filter_clicks or 0) > 0:
        payload = load_trajectory_payload()
        records = extract_trajectory_records(payload)

        if not records:
            return (
                build_figure(
                    load_trajectory_dataframe(),
                    pi_setup,
                    "Keine Trajektoriendaten zum Filtern gefunden.",
                    aspect_mode=aspect_mode or "data",
                ),
                "Status: Keine Trajektoriendaten zum Filtern gefunden.",
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
        filtered_records = [record for record in records if id(record) in matching_point_ids]

        save_trajectory_payload(payload, filtered_records)

        deleted_points = len(records) - len(filtered_records)
        status_message = (
            f"Status: {len(filtered_records)} Punkte innerhalb der Pyramiden behalten, "
            f"{deleted_points} Punkte entfernt."
        )
    elif triggered == "pi-setup-table":
        status_message = "Status: Pi-Setup aktualisiert."

    trajectory_df = load_trajectory_dataframe()
    return (
        build_figure(
            trajectory_df,
            pi_setup,
            status_message,
            aspect_mode=aspect_mode or "data",
        ),
        status_message,
    )


@app.callback(
    Output("download-json", "data"),
    Output("download-status", "children"),
    Input("btn-download-json", "n_clicks"),
    prevent_initial_call=True,
)
def download_json(n_clicks):
    """Prüft die Datei und startet den Download oder zeigt einen Fehler an."""
    if TRAJECTORY_JSON.is_file():
        return dcc.send_file(
            str(TRAJECTORY_JSON),
            filename=TRAJECTORY_JSON.name
        ), "Status: Datei gefunden, Download gestartet."
    
    return None, "Status: Datei nicht verfügbar. Bitte zuerst berechnen."


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=True)