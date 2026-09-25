import streamlit as st

pg = st.navigation([st.Page("generate_birds.py"), st.Page("plot_birds.py")])
pg.run()
