import streamlit as st

pg = st.navigation([st.Page("generate_birds.py"), st.Page("debug_algorithm.py")])
pg.run()
