
import streamlit as st
import json
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(layout="wide")
st.title("Flugtrajektorien")

# JSON laden
with open("vogel_flugbahnen.json", "r") as f:
    data = json.load(f)

df = pd.DataFrame(data)

df["timestamp"] = pd.to_datetime(df["timestamp"])
df = df.sort_values(["bird_id", "timestamp"])

fig = go.Figure()

# for bird_id, group in df.groupby("bird_id"): #<-- hier die ids des algos nutzen später

#     fig.add_trace(
#         go.Scatter3d(
#             x=group["x"],
#             y=group["y"],
#             z=group["z"],
#             mode="lines+markers",
#             marker = dict(symbol = 'x'),
#             name=bird_id,
#             text=group["timestamp"].astype(str),
#             hovertemplate=
#                 "<b>%{fullData.name}</b><br>" +
#                 "Zeit: %{text}<br>" +
#                 "X: %{x}<br>" +
#                 "Y: %{y}<br>" +
#                 "Z: %{z}<extra></extra>"
#         )
#     )

for bird_id, group in df.groupby("bird_id"):

    # Flugbahn
    fig.add_trace(
        go.Scatter3d(
            x=group["x"],
            y=group["y"],
            z=group["z"],
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
    dx = group["x"].shift(-1) - group["x"]
    dy = group["y"].shift(-1) - group["y"]
    dz = group["z"].shift(-1) - group["z"]

    # letzten Punkt entfernen (kein Nachfolger vorhanden)
    mask = dx.notna()

    fig.add_trace(
        go.Cone(
            x=group.loc[mask, "x"],
            y=group.loc[mask, "y"],
            z=group.loc[mask, "z"],
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
