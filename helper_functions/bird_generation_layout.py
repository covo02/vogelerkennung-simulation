from dash import dcc, html
from helper_functions.additional_styles import FIELD_STYLE, INPUT_STYLE, LABEL_STYLE, STATUS_STYLE
from helper_functions.generate_plots import status_figure
from helper_functions.helper_ui import date_field, input_field, slider_field, two_columns
# ============================================================================
# LAYOUT: VOGELGENERIERUNG
# ============================================================================
def bird_generator_tab():
    """Layout des neuen Dash-Tabs zur Vogelgenerierung."""
    return html.Div(
        id="tab-generate-content",
        style={"display": "none"},
        children=[
            html.Div(
                style={
                    "display": "flex",
                    "gap": "16px",
                    "alignItems": "flex-start",
                    "flexWrap": "wrap",
                },
                children=[
                    html.Div(
                        className="card",
                        style={
                            "flex": "1 1 320px",
                            "maxWidth": "390px",
                        },
                        children=[
                            html.Div(
                                className="cardTitle",
                                children=[
                                    html.H2("Parameter"),
                                    html.Span(
                                        "Simulation",
                                        className="badge",
                                    ),
                                ],
                            ),
                            html.H3("Allgemeine Einstellungen"),
                            input_field(
                                "Anzahl Vögel",
                                "gen-number-of-birds",
                                50,
                                step=1,
                                minimum=1,
                                maximum=500,
                            ),
                            input_field(
                                "Anzahl Feldlerchen",
                                "gen-number-of-larks",
                                1,
                                step=1,
                                minimum=0,
                                maximum=500,
                            ),
                            input_field(
                                "Messabstand [Sekunden]",
                                "gen-time-interval",
                                5,
                                step=1,
                                minimum=1,
                                maximum=60,
                            ),
                            date_field(
                                "Simulationsdatum",
                                "gen-date",
                                "2026-07-08",
                            ),
                            html.Div(
                                style=FIELD_STYLE,
                                children=[
                                    html.Label(
                                        "Zufalls-Seed (leer = zufällig)",
                                        htmlFor="gen-seed",
                                        style=LABEL_STYLE,
                                    ),
                                    dcc.Input(
                                        id="gen-seed",
                                        type="text",
                                        value="",
                                        style=INPUT_STYLE,
                                    ),
                                ],
                            ),
                            html.Div(className="divider"),
                            html.H3("Raumgrenzen"),
                            two_columns(
                                input_field(
                                    "X Min",
                                    "gen-x-min",
                                    0,
                                    step=10,
                                ),
                                input_field(
                                    "X Max",
                                    "gen-x-max",
                                    600,
                                    step=10,
                                ),
                            ),
                            two_columns(
                                input_field(
                                    "Y Min",
                                    "gen-y-min",
                                    0,
                                    step=10,
                                ),
                                input_field(
                                    "Y Max",
                                    "gen-y-max",
                                    600,
                                    step=10,
                                ),
                            ),
                            two_columns(
                                input_field(
                                    "Z Min",
                                    "gen-z-min",
                                    50,
                                    step=5,
                                ),
                                input_field(
                                    "Z Max",
                                    "gen-z-max",
                                    300,
                                    step=5,
                                ),
                            ),
                            slider_field(
                                "Initiale Streuung (Offset)",
                                "gen-initial-spread",
                                0,
                                2000,
                                1000,
                                1,
                                (
                                    "Zufälliger Offset bei der Startposition, "
                                    "damit Vögel nicht exakt in der Mitte starten."
                                ),
                            ),
                            html.Div(className="divider"),
                            html.H3("Geschwindigkeit"),
                            two_columns(
                                input_field(
                                    "Min Geschwindigkeit",
                                    "gen-min-speed",
                                    4.0,
                                    step=0.5,
                                ),
                                input_field(
                                    "Max Geschwindigkeit",
                                    "gen-max-speed",
                                    16.0,
                                    step=0.5,
                                ),
                            ),
                            html.Div(className="divider"),
                            html.H3("Fehlerraten"),
                            slider_field(
                                "Positionsrauschen",
                                "gen-position-noise",
                                0,
                                1,
                                0.08,
                                0.01,
                            ),
                            html.Div(className="divider"),
                            html.H3("Varianz der Flugbahnen"),
                            slider_field(
                                "Richtungsänderung",
                                "gen-variance-angle",
                                0,
                                0.3,
                                0.04,
                                0.01,
                            ),
                            slider_field(
                                "Geschwindigkeitsänderung",
                                "gen-variance-speed",
                                0,
                                1,
                                0.03,
                                0.01,
                            ),
                            slider_field(
                                "Vertikale Bewegung",
                                "gen-variance-z",
                                0,
                                1,
                                0.05,
                                0.01,
                            ),
                            slider_field(
                                "Steiggeschwindigkeit ändern",
                                "gen-variance-vertical-speed",
                                0,
                                0.1,
                                0.01,
                                0.005,
                            ),
                            html.Button(
                                "JSON generieren",
                                id="btn-generate-birds",
                                n_clicks=0,
                                className="btn",
                                style={
                                    "width": "100%",
                                    "marginTop": "10px",
                                },
                            ),
                        ],
                    ),
                    html.Div(
                        style={
                            "flex": "3 1 700px",
                            "minWidth": "0",
                        },
                        children=[
                            html.Div(
                                className="card",
                                children=[
                                    html.Div(
                                        className="cardTitle",
                                        children=[
                                            html.H2("Generierte Daten"),
                                            html.Span(
                                                "vogel_flugbahnen.json",
                                                className="badge",
                                            ),
                                        ],
                                    ),
                                    html.Div(
                                        id="bird-generation-status",
                                        children=(
                                            "Status: Parameter einstellen und "
                                            "auf „JSON generieren“ klicken."
                                        ),
                                        style=STATUS_STYLE,
                                    ),
                                    html.Div(
                                        id="bird-stats",
                                        children=html.P(
                                            (
                                                "Nach der Generierung werden "
                                                "hier die Statistiken angezeigt."
                                            ),
                                            className="sub",
                                        ),
                                    ),
                                    html.Div(className="divider"),
                                    html.Div(
                                        className="row",
                                        children=[
                                            html.Button(
                                                "JSON herunterladen",
                                                id="btn-download-generated-json",
                                                n_clicks=0,
                                                className="btn",
                                            ),
                                            html.Div(
                                                "",
                                                id="generated-download-status",
                                                className="mono",
                                                style={
                                                    "marginLeft": "10px",
                                                    "color": "#9ca3af",
                                                },
                                            ),
                                        ],
                                    ),
                                    html.P(
                                        (
                                            "Anschließend kann im Tab "
                                            "„Übersicht“ die vorhandene "
                                            "trajectory.py ausgeführt werden."
                                        ),
                                        className="sub",
                                        style={"marginTop": "14px"},
                                    ),
                                ],
                            ),
                            html.Div(
                                className="card",
                                style={"marginTop": "16px"},
                                children=[
                                    html.Div(
                                        className="cardTitle",
                                        children=[
                                            html.H2(
                                                "2D-Karte der Flugbahnen"
                                            ),
                                        ],
                                    ),
                                    dcc.Loading(
                                        type="default",
                                        children=dcc.Graph(
                                            id="bird-2d-graph",
                                            figure=status_figure(
                                                (
                                                    "Nach der Generierung "
                                                    "erscheint hier die "
                                                    "2D-Karte."
                                                ),
                                                height=600,
                                            ),
                                            config={"responsive": True},
                                            style={"height": "600px"},
                                        ),
                                    ),
                                ],
                            ),
                            html.Div(
                                className="card",
                                style={"marginTop": "16px"},
                                children=[
                                    html.Div(
                                        className="cardTitle",
                                        children=[
                                            html.H2(
                                                "3D-Plot der Flugbahnen"
                                            ),
                                        ],
                                    ),
                                    dcc.Loading(
                                        type="default",
                                        children=dcc.Graph(
                                            id="bird-3d-graph",
                                            figure=status_figure(
                                                (
                                                    "Nach der Generierung "
                                                    "erscheint hier der "
                                                    "3D-Plot."
                                                ),
                                                height=1000,
                                            ),
                                            config={"responsive": True},
                                            style={"height": "1000px"},
                                        ),
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )




