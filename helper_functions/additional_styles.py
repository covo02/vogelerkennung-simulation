# ============================================================================
# STYLES
# ============================================================================
FIELD_STYLE = {
    "marginBottom": "14px",
}

LABEL_STYLE = {
    "color": "#ffffff",  # Helle Schrift für dunklen Hintergrund
    "marginBottom": "5px",
    "fontWeight": "bold",
}

INPUT_STYLE = {
    "color": "#1f2937",  # Dunkle Schrift für weißen Hintergrund
    "backgroundColor": "#ffffff",
    "border": "1px solid #ccc",
    "borderRadius": "4px",
    "padding": "5px",
}
STATUS_STYLE = {
    "marginTop": "12px",
    "padding": "10px 12px",
    "borderRadius": "6px",
    "backgroundColor": "rgba(255, 255, 255, 0.06)",
    "color": "#d1d5db",
}

SUCCESS_STATUS_STYLE = {
    **STATUS_STYLE,
    "backgroundColor": "rgba(34, 197, 94, 0.15)",
    "border": "1px solid rgba(34, 197, 94, 0.45)",
    "color": "#bbf7d0",
}

ERROR_STATUS_STYLE = {
    **STATUS_STYLE,
    "backgroundColor": "rgba(239, 68, 68, 0.15)",
    "border": "1px solid rgba(239, 68, 68, 0.45)",
    "color": "#fecaca",
}