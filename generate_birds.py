"""
Streamlit App zur Vogel-Flugbahn-Simulation

Ermöglicht die interaktive Konfiguration aller Parameter
und generiert eine JSON-Datei mit simulierten Vogel-Daten.
"""

import streamlit as st
from datetime import datetime, timedelta
import random
import math
import json
import plotly.graph_objects as go
import plotly.express as px



def generate_birds(
    number_of_birds,
    time_interval,
    sim_date,
    x_min, x_max,
    y_min, y_max,
    z_min, z_max,
    min_speed, max_speed,
    position_noise,
    seed_value,
    initial_spread,
    variance_angle,
    variance_speed,
    variance_z,
    variance_vertical_speed
):
    """
    Generiert die Vogel-Flugbahnen basierend auf den übergebenen Parametern.
    """
    
    # Zufalls-Seed setzen
    if seed_value:
        random.seed(seed_value)
    else:
        random.seed(datetime.now().timestamp())
    
    records = []
    
    for bird_number in range(1, number_of_birds + 1):
        
        # Anzahl Messpunkte pro Vogel
        number_of_samples = random.randint(3, 29)
        
        # Zufälliger Startzeitpunkt
        start_time = sim_date.replace(
            hour=random.randint(0, 23),
            minute=random.randint(0, 30),
            second=0
        )
        
        # Initiale Position mit optionalem Offset
        x = random.uniform(x_min, x_max) + random.uniform(-initial_spread, initial_spread)
        y = random.uniform(y_min, y_max) + random.uniform(-initial_spread, initial_spread)
        z = random.uniform(z_min, z_max)
        
        # Flugparameter

        # angle ist die Richtung, in die der Vogel horizontal fliegt. (Winkel in Radiant) 0 → nach rechts | pi/2 → nach oben | pi → nach links | 3*pi/2 → nach unten 
        angle = random.uniform(0, 2 * math.pi)
        # speed ist die horizontale Geschwindigkeit pro Simulationsschritt. 
        speed = random.uniform(min_speed, max_speed)
        # vertical_speed ist die Steig- oder Sinkgeschwindigkeit

        vertical_speed = random.uniform(-0.05, 0.05)
        
        # Trajektorie erzeugen
        for sample_index in range(number_of_samples):
            
            # Bewegungsschritte zwischen zwei Messungen
            for _ in range(time_interval):
                
                # Richtungsänderung

                # bei jedem Simulationsschritt wird der Winkel ein bisschen zufällig verändert
                # mal nach links, mal nach rechts
                # je größer variance_angle, desto stärker das Zickzack
                angle += random.gauss(0, variance_angle)
                
                # Geschwindigkeitsänderung

                # pro Schritt wird speed etwas erhöht oder erniedrigt
                # ändert gesamte Schrittweite
                speed += random.gauss(0, variance_speed)
                speed = max(min_speed, min(max_speed, speed))
                
                # 2D-Bewegung
                x += math.cos(angle) * speed
                y += math.sin(angle) * speed
                
                # Vertikale Bewegung
                z += vertical_speed
                z += random.gauss(0, variance_z)
                z = max(z_min, min(z_max, z))

                
                # Steiggeschwindigkeit ändern
                #additives Höhenrauschen
                vertical_speed += random.gauss(0, variance_vertical_speed)
            
            # Messpunkt erzeugen
            bird_id = f"bird_{bird_number:04d}"
            
            # # Simulierte Vogel-ID

            determined_id = "bird_0000"            
            # Position mit Messrauschen
            record = {
                "bird_id": bird_id,
                "determined_bird_id": determined_id,
                "timestamp": (
                    start_time + timedelta(seconds=sample_index * time_interval)
                ).isoformat() + "Z",
                "enu_e": round(x + random.gauss(0, position_noise), 2),
                "enu_n": round(y + random.gauss(0, position_noise), 2),
                "enu_u": round(z + random.gauss(0, 0.03), 2)
            }
            
            records.append(record)
    
    return records


def main():
    st.set_page_config(
        page_title="Vogelgenerierung",
        layout="wide"
    )
    
    st.title("Vogel-Flugbahn Simulation")
    st.markdown("Konfigurieren Sie die Parameter und generieren Sie simulierte Vogel-Flugbahnen als JSON.")
    
    # ============================================================
    # Sidebar für Parameter-Eingaben
    # ============================================================
    
    st.sidebar.header("Allgemeine Einstellungen")
    
    number_of_birds = st.sidebar.number_input(
        "Anzahl Vögel",
        min_value=1,
        max_value=500,
        value=50,
        step=1
    )
    
    time_interval = st.sidebar.number_input(
        "Messabstand [Sekunden]",
        min_value=1,
        max_value=60,
        value=5,
        step=1
    )
    
    sim_date = st.sidebar.date_input(
        "Simulationsdatum",
        value=datetime(2026, 7, 8)
    )
    sim_date = datetime.combine(sim_date, datetime.min.time())
    
    seed_value = st.sidebar.text_input(
        "Zufalls-Seed (leer = zufällig)",
        value=""
    )
    if seed_value:
        try:
            seed_value = int(seed_value)
        except ValueError:
            seed_value = hash(seed_value)
    else:
        seed_value = None
    
    # -----------------------------------------------------------
    # Raumgrenzen
    # -----------------------------------------------------------
    
    st.sidebar.header("Raumgrenzen")
    
    col1, col2 = st.sidebar.columns(2)
    
    with col1:
        x_min = st.number_input("X Min", value=0, step=10)
        y_min = st.number_input("Y Min", value=0, step=10)
        z_min = st.number_input("Z Min", value=50, step=5)
    
    with col2:
        x_max = st.number_input("X Max", value=600, step=10)
        y_max = st.number_input("Y Max", value=600, step=10)
        z_max = st.number_input("Z Max", value=300, step=5)
    
    initial_spread = st.sidebar.slider(
        "Initiale Streuung (Offset)",
        min_value=0,
        max_value=2000,
        value=1000,
        help="Zufälliger Offset bei der Startposition, damit Vögel nicht exakt in der Mitte starten"
    )
    
    # -----------------------------------------------------------
    # Geschwindigkeit
    # -----------------------------------------------------------
    
    st.sidebar.header("Geschwindigkeit")
    
    col3, col4 = st.sidebar.columns(2)
    
    with col3:
        min_speed = st.number_input("Min Geschwindigkeit", value=4.0, step=0.5)
    
    with col4:
        max_speed = st.number_input("Max Geschwindigkeit", value=16.0, step=0.5)
    
    # -----------------------------------------------------------
    # Fehlerraten
    # -----------------------------------------------------------
    
    st.sidebar.header("Fehlerraten")
    
    
    position_noise = st.sidebar.slider(
        "Positionsrauschen",
        min_value=0.0,
        max_value=1.0,
        value=0.08,
        step=0.01,
        format="%.2f"
    )

    # -----------------------------------------------------------
    # Varianzen
    # -----------------------------------------------------------
    
    st.sidebar.header("Varianz der Flugbahnen")
    
    
    variance_angle = st.sidebar.slider(
        "Richtungsänderung",
        min_value=0.0,
        max_value=0.3,
        value=0.04,
        step=0.01,
        format="%.2f"
    )

    variance_speed = st.sidebar.slider(
        "Geschwindigkeitsänderung",
        min_value=0.0,
        max_value=1.0,
        value=0.03,
        step=0.01,
        format="%.2f"
    )

    variance_z = st.sidebar.slider(
            "Vertikale Bewegung",
            min_value=0.0,
            max_value=1.0,
            value=0.05,
            step=0.01,
            format="%.2f"
        )

    variance_vertical_speed= st.sidebar.slider(
            "Steiggeschwindigkeit ändern",
            min_value=0.0,
            max_value=0.1,
            value=0.01,
            step=0.005,
            format="%.2f"
        )

    # ============================================================
    # Hauptbereich - Button und Ergebnisse
    # ============================================================
    
    col_btn1, col_btn2 = st.columns([1, 4])
    
    with col_btn1:
        generate_button = st.button(
            "JSON generieren",
            type="primary",
            use_container_width=True
        )
    
    if generate_button:
        with st.spinner("Vogel-Flugbahnen werden generiert..."):
            # Daten generieren
            records = generate_birds(
                number_of_birds=number_of_birds,
                time_interval=time_interval,
                sim_date=sim_date,
                x_min=x_min,
                x_max=x_max,
                y_min=y_min,
                y_max=y_max,
                z_min=z_min,
                z_max=z_max,
                min_speed=min_speed,
                max_speed=max_speed,
                position_noise=position_noise,
                seed_value=seed_value,
                initial_spread=initial_spread,
                variance_angle=variance_angle,
                variance_speed=variance_speed,
                variance_z=variance_z,
                variance_vertical_speed=variance_vertical_speed
            )


            
            # -----------------------------------------------------------
            # Automatische Pi-Positionierung basierend auf Raumgrenzen
            # -----------------------------------------------------------
            
            pi_1 = {"id": "pi_1", "name": "Raspberry Pi Ursprung", "x": x_min, "y": y_min, "z": 0}
            pi_2 = {"id": "pi_2", "name": "Raspberry Pi X-Achse", "x": x_max, "y": y_min, "z": 0}
            pi_3 = {"id": "pi_3", "name": "Raspberry Pi Y-Achse", "x": x_min, "y": y_max, "z": 0}

            plot_attachment = {
                "version": "1.0",
                "type": "triangulation_setup",
                "devices": [pi_1, pi_2, pi_3]
            }

            # Zusammenführen der Daten für die JSON-Ausgabe
            output_data = {
                "plotAttachment": plot_attachment,
                "simulated_birds": records
            }

            # Als JSON speichern
            output_file = "vogel_flugbahnen.json"
            with open(output_file, "w") as f:
                json.dump(output_data, f, indent=2)
            
            # Erfolgsmeldung
            st.success(f"✅ {len(records)} Datenpunkte generiert und gespeichert!")
            
            # -----------------------------------------------------------
            # Vorschau der Daten
            # -----------------------------------------------------------
            
            # st.subheader("Vorschau (erste 10 Einträge)")
            
            # Daten als DataFrame für Tabelle
            import pandas as pd
            df = pd.DataFrame(records)
            
            # st.dataframe(
            #     df.head(10),
            #     use_container_width=True,
            #     hide_index=True
            # )
            
            # -----------------------------------------------------------
            # Statistiken
            # -----------------------------------------------------------
            
            st.subheader("Statistiken")
            
            col_stat1, col_stat2, col_stat3 = st.columns(3)
            
            with col_stat1:
                st.metric("Gesamte Datenpunkte", len(records))
            
            with col_stat2:
                st.metric("Eindeutige Vogel-IDs", df["bird_id"].nunique())
            
            with col_stat3:
                if "z" in df.columns:
                    st.metric("Durchschnittliche Höhe", f"{df['z'].mean():.1f} m")
        
            # -----------------------------------------------------------
            # Visualisierung
            # -----------------------------------------------------------
            
            st.subheader("2D-Karte der Flugbahnen")
            
            # Farbe für jeden Vogel
            unique_birds = df["bird_id"].unique()
            colors = {bird: f"hsl({i * 360 // len(unique_birds)}, 70%, 50%)" for i, bird in enumerate(unique_birds)}
            df["color"] = df["bird_id"].map(colors)
            

            
            fig = px.scatter(
                df,
                x="enu_e",
                y="enu_n",
                color="bird_id",
                hover_data=["timestamp", "enu_u"],
                title="Vogel-Flugbahnen (2D Ansicht)",
                color_discrete_sequence=px.colors.qualitative.Set3

            )
            
            fig.update_layout(
                width=800,
                height=600,
                xaxis_title="X [m]",
                yaxis_title="Y [m]"
            )
            
            st.plotly_chart(fig, use_container_width=True)

            # 3D-Plot
            st.markdown("---")
            st.subheader("3D-Plot der Flugbahnen")
            
            # Farben für jeden Vogel generieren (deterministisch basierend auf bird_id)
            unique_birds = df["bird_id"].unique()
            
            # Farbpalette definieren (bunt und unterscheidbar)
            # Wir nutzen HSL-Farben, um jeden Vogel eindeutig einzufärben
            bird_colors = {}
            for i, bird_id in enumerate(unique_birds):
                hue = (i * 137.508) % 360  # Goldener Winkel für gute Verteilung
                bird_colors[bird_id] = f"hsl({hue}, 80%, 50%)"

            fig2 = go.Figure()

            # 1. Vogel-Flugbahnen zeichnen
            for bird_id, group in df.groupby("bird_id"):
                color = bird_colors[bird_id]
                fig2.add_trace(
                    go.Scatter3d(
                        x=group["enu_e"],
                        y=group["enu_n"],
                        z=group["enu_u"],
                        mode="lines+markers",
                        marker=dict(symbol="circle", size=2, color=color),
                        line=dict(width=1, color="gray"),
                        name=bird_id,
                        text=group["timestamp"].astype(str),
                        hovertemplate="<b>%{fullData.name}</b><br>Zeit: %{text}<br>X: %{x}<br>Y: %{y}<br>Z: %{z}<extra></extra>",
                        legendgroup="birds"
                    )
                )

            # 2. Raspberry Pis als eigene Marker hinzufügen (Rot/Groß)
            pi_x = [pi_1["x"], pi_2["x"], pi_3["x"]]
            pi_y = [pi_1["y"], pi_2["y"], pi_3["y"]]
            pi_z = [pi_1["z"], pi_2["z"], pi_3["z"]]
            pi_names = [f"{p['name']} ({p['id']})" for p in [pi_1, pi_2, pi_3]]

            fig2.add_trace(
                go.Scatter3d(
                    x=pi_x,
                    y=pi_y,
                    z=pi_z,
                    mode="markers+text",
                    marker=dict(size=10, color="red", symbol="diamond"),
                    text=pi_names,
                    textposition="top center",
                    name="Raspberry Pis",
                    legendgroup="setup"
                )
            )

            fig2.update_layout(
                scene=dict(
                    xaxis_title="X [m]",
                    yaxis_title="Y [m]",
                    zaxis_title="Höhe Z [m]",
                    # Achsenbereich begrenzen, damit die Pis unten sind
                    zaxis=dict(range=[0, z_max * 1.1])
                ),
                height=1000,
                width=800,
                legend=dict(
                    itemsizing="constant"
                )
            )

            st.plotly_chart(fig2, use_container_width=True)
                
    else:
        st.info(
            "In der **Seitenleiste** die Parameter anpassen "
            "und dann auf **JSON generieren** klicken."
        )


if __name__ == "__main__":
    main()