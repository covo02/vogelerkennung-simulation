import plotly.graph_objects as go
import plotly.express as px
from dash import html

from helper_functions.helper_ui import metric_card
# ============================================================================
# HILFSFUNKTIONEN: PLOTS
# ============================================================================
def status_figure(message, height=1000):
    """Leerer Plot mit Statusmeldung."""
    fig = go.Figure()

    fig.add_annotation(
        text=message,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
        font={"size": 16},
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        xaxis={"visible": False},
        yaxis={"visible": False},
    )

    return fig


def create_birds_2d_figure(df):
    """Erstellt die 2D-Ansicht der generierten Flugbahnen."""
    fig = px.scatter(
        df,
        x="enu_e",
        y="enu_n",
        color="bird_id",
        hover_data=["timestamp", "enu_u"],
        title="Vogel-Flugbahnen (2D Ansicht)",
        color_discrete_sequence=px.colors.qualitative.Set3,
    )

    fig.update_traces(marker={"size": 6})

    fig.update_layout(
        template="plotly_dark",
        height=600,
        margin={"l": 40, "r": 20, "t": 60, "b": 50},
        xaxis_title="X [m]",
        yaxis_title="Y [m]",
        legend_title_text="Vogel-ID",
    )

    return fig


def create_birds_3d_figure(df, pi_devices, z_min, z_max):
    """Erstellt die 3D-Ansicht der generierten Flugbahnen."""
    sorted_df = df.sort_values(["bird_id", "timestamp"])
    unique_birds = sorted_df["bird_id"].drop_duplicates().tolist()

    bird_colors = {}

    for index, bird_id in enumerate(unique_birds):
        hue = (index * 137.508) % 360
        bird_colors[bird_id] = f"hsl({hue:.1f}, 80%, 50%)"

    fig = go.Figure()

    for bird_id, group in sorted_df.groupby("bird_id", sort=False):
        color = bird_colors[bird_id]

        fig.add_trace(
            go.Scatter3d(
                x=group["enu_e"],
                y=group["enu_n"],
                z=group["enu_u"],
                mode="lines+markers",
                marker={
                    "symbol": "circle",
                    "size": 2,
                    "color": color,
                },
                line={
                    "width": 1,
                    "color": "gray",
                },
                name=bird_id,
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

    pi_x = [device["x"] for device in pi_devices]
    pi_y = [device["y"] for device in pi_devices]
    pi_z = [device["z"] for device in pi_devices]
    pi_names = [
        f"{device['name']} ({device['id']})"
        for device in pi_devices
    ]

    fig.add_trace(
        go.Scatter3d(
            x=pi_x,
            y=pi_y,
            z=pi_z,
            mode="markers+text",
            marker={
                "size": 10,
                "color": "red",
                "symbol": "diamond",
            },
            text=pi_names,
            textposition="top center",
            name="Raspberry Pis",
        )
    )

    z_lower = min(0, z_min)
    z_upper = max(0, z_max)
    z_padding = max((z_upper - z_lower) * 0.1, 1)
    z_upper += z_padding

    fig.update_layout(
        template="plotly_dark",
        height=1000,
        margin={"l": 0, "r": 0, "t": 40, "b": 0},
        scene={
            "xaxis_title": "X [m]",
            "yaxis_title": "Y [m]",
            "zaxis": {
                "title": "Höhe Z [m]",
                "range": [z_lower, z_upper],
            },
        },
        legend={
            "itemsizing": "constant",
        },
    )

    return fig


def create_bird_stats(df):
    """Erstellt die Statistik-Kacheln."""
    return html.Div(
        style={
            "display": "flex",
            "gap": "12px",
            "flexWrap": "wrap",
            "marginTop": "14px",
        },
        children=[
            metric_card(
                "Gesamte Datenpunkte",
                str(len(df)),
            ),
            metric_card(
                "Eindeutige Vogel-IDs",
                str(df["bird_id"].nunique()),
            ),
            metric_card(
                "Durchschnittliche Höhe",
                f"{df['enu_u'].mean():.1f} m",
            ),
        ],
    )