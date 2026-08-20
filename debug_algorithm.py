
import streamlit as st
import json
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(layout="wide")
st.title("Debug Algorithmus")

# JSON laden, falls vorhanden

try:
        
    with open("vogel_flugbahnen_determined.json", "r") as f:

        data = json.load(f)

    df = pd.DataFrame(data)

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(["bird_id", "timestamp"])

    fig = go.Figure()


    for bird_id, group in df.groupby("bird_id"):

        # Flugbahn
        fig.add_trace(
            go.Scatter3d(
                x=group["enu_e"],
                y=group["enu_n"],
                z=group["enu_u"],
                mode="lines+markers",
                marker=dict(symbol="circle",size=2),
                name=bird_id,
                text=group["timestamp"].astype(str),
                hovertemplate=
                    "<b>%{fullData.name}</b><br>" +
                    "Zeit: %{text}<br>" +
                    "X: %{x}<br>" +
                    "Y: %{y}<br>" +
                    "Z: %{z}<extra></extra>"
            )
        )

        # Richtungsvektoren berechnen
        dx = group["enu_e"].shift(-1) - group["enu_e"]
        dy = group["enu_n"].shift(-1) - group["enu_n"]
        dz = group["enu_u"].shift(-1) - group["enu_u"]

        # letzten Punkt entfernen (kein Nachfolger vorhanden)
        mask = dx.notna()

        fig.add_trace(
            go.Cone(
                x=group.loc[mask, "enu_e"],
                y=group.loc[mask, "enu_n"],
                z=group.loc[mask, "enu_u"],
                u=dx[mask],
                v=dy[mask],
                w=dz[mask],
                sizemode="absolute",
                sizeref=20,  # Pfeilgröße anpassen
                anchor="tail",
                showscale=False,
                name=f"{bird_id} Richtung"
            )
        )

    fig.update_layout(
        scene=dict(
            xaxis_title="X",
            yaxis_title="Y",
            zaxis_title="Z"
        ),
        height=1000,
        width = 600,
        legend=dict(
            itemsizing="constant"
        )
    )

    st.plotly_chart(fig, width="stretch")


    df["korrekt"] = df["bird_id"] == df["determined_bird_id"]


    counts = df["korrekt"].value_counts()

    richtig = counts.get(True, 0)
    falsch = counts.get(False, 0)

    plot_df = pd.DataFrame({
        "Ergebnis": ["Richtig", "Falsch"],
        "Anzahl": [richtig, falsch]
    })

    fig2 = px.bar(
        plot_df,
        x="Ergebnis",
        y="Anzahl",
        color="Ergebnis",
        text="Anzahl",
        title="Vergleich tatsächlicher vs. vorhergesagter Werte"
    )

    fig2.update_traces(textposition="outside")
    st.plotly_chart(fig2, width="stretch")
except FileNotFoundError:
    st.write("Bitte führe den Algorithmus durch, der die Trajektorien bestimmt,")