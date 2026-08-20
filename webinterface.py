import json
import subprocess
import sys
from pathlib import Path

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, dcc, html

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


def create_trajectory_figure() -> go.Figure:
    """Lädt die JSON-Datei und erstellt den 3D-Trajektorienplot."""
    if not TRAJECTORY_JSON.is_file():
        raise FileNotFoundError(
            f"Die Ausgabedatei wurde nicht gefunden: {TRAJECTORY_JSON}"
        )

    with TRAJECTORY_JSON.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list) or not data:
        raise ValueError("Die JSON-Datei enthält keine Trajektoriendaten.")

    df = pd.DataFrame(data)

    required_columns = {"bird_id", "timestamp", "enu_e", "enu_n", "enu_u"}
    missing_columns = required_columns - set(df.columns)

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Folgende Spalten fehlen in der JSON-Datei: {missing}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    for coordinate in ("enu_e", "enu_n", "enu_u"):
        df[coordinate] = pd.to_numeric(df[coordinate], errors="coerce")

    df = df.dropna(subset=["bird_id", "timestamp", "enu_e", "enu_n", "enu_u"])
    df = df.sort_values(["bird_id", "timestamp"])

    if df.empty:
        raise ValueError("Nach der Datenbereinigung sind keine Punkte vorhanden.")

    fig = go.Figure()

    for bird_id, group in df.groupby("bird_id", sort=False):
        # Flugbahn
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

        # Richtungsvektoren berechnen
        dx = group["enu_e"].shift(-1) - group["enu_e"]
        dy = group["enu_n"].shift(-1) - group["enu_n"]
        dz = group["enu_u"].shift(-1) - group["enu_u"]

        # Letzten Punkt entfernen, da kein Nachfolger vorhanden ist
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

    fig.update_layout(
        template="plotly_dark",
        scene=dict(
            xaxis_title="X",
            yaxis_title="Y",
            zaxis_title="Z",
            aspectmode="manual",
            aspectratio=dict(
                x=1,
                y=1,
                z=1
            ),
        ),
        height=1000,
        legend=dict(
            itemsizing="constant"
        )
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
                                            className="cardTitle",
                                            children=[html.H2("3D-Flugtrajektorien")],
                                        ),

                                        dcc.Loading(
                                            type="default",
                                            children=dcc.Graph(
                                                id="main-graph",
                                                figure=status_figure(
                                                    "Klicke auf „Trajektorie berechnen“, "
                                                    "um den 3D-Plot zu laden."
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
    Input("tabs", "value"),
)
def render_tab(tab):
    if tab == "tab-1":
        return {"display": "block"}, {"display": "none"}

    return {"display": "none"}, {"display": "block"}


@app.callback(
    Output("main-graph", "figure"),
    Output("action-output", "children"),
    Input("btn-run-trajectory", "n_clicks"),
    prevent_initial_call=True,
)
def run_trajectory(_n_clicks):
    if not TRAJECTORY_SCRIPT.is_file():
        return (
            status_figure("trajectory.py wurde nicht gefunden."),
            f"Status: Datei nicht gefunden: {TRAJECTORY_SCRIPT}",
        )

    try:
        # Führt trajectory.py mit demselben Python-Interpreter aus,
        # mit dem auch Dash gestartet wurde.
        subprocess.run(
            [sys.executable, str(TRAJECTORY_SCRIPT)],
            cwd=str(BASE_DIR),
            check=True,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_SECONDS,
        )

        figure = create_trajectory_figure()

        return (
            figure,
            "Status: trajectory.py wurde erfolgreich ausgeführt. 3D-Plot geladen.",
        )

    except subprocess.TimeoutExpired:
        return (
            status_figure("Die Berechnung hat das Zeitlimit überschritten."),
            f"Status: Abbruch nach {SCRIPT_TIMEOUT_SECONDS} Sekunden.",
        )

    except subprocess.CalledProcessError as error:
        details = short_error_message(error.stderr or error.stdout)

        return (
            status_figure("Fehler beim Ausführen von trajectory.py."),
            f"Status: trajectory.py ist fehlgeschlagen: {details}",
        )

    except Exception as error:
        return (
            status_figure("Die Trajektoriendaten konnten nicht geladen werden."),
            f"Status: Fehler beim Laden der Daten: {short_error_message(error)}",
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