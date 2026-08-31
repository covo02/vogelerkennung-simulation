from dash import dcc, html

from helper_functions.additional_styles import FIELD_STYLE, INPUT_STYLE, LABEL_STYLE
# ============================================================================
# HILFSFUNKTIONEN: UI
# ============================================================================
def input_field(
    label,
    component_id,
    value,
    step=1,
    minimum=None,
    maximum=None,
):
    """Erstellt ein numerisches Eingabefeld."""
    properties = {
        "id": component_id,
        "type": "number",
        "value": value,
        "step": step,
        "style": INPUT_STYLE,
    }

    if minimum is not None:
        properties["min"] = minimum

    if maximum is not None:
        properties["max"] = maximum

    return html.Div(
        style=FIELD_STYLE,
        children=[
            html.Label(label, htmlFor=component_id, style=LABEL_STYLE),
            dcc.Input(**properties),
        ],
    )


def slider_field(
    label,
    component_id,
    minimum,
    maximum,
    value,
    step,
    help_text=None,
):
    """Erstellt einen Slider."""
    children = [
        html.Label(label, htmlFor=component_id, style=LABEL_STYLE),
        dcc.Slider(
            id=component_id,
            min=minimum,
            max=maximum,
            value=value,
            step=step,
            tooltip={"placement": "bottom", "always_visible": False},
        ),
    ]

    if help_text:
        children.append(
            html.Small(
                help_text,
                style={
                    "display": "block",
                    "marginTop": "5px",
                    "color": "#FFFFFF",
                },
            )
        )

    return html.Div(style=FIELD_STYLE, children=children)


def date_field(label, component_id, value):
    """Erstellt ein Datumseingabefeld."""
    return html.Div(
        style=FIELD_STYLE,
        children=[
            html.Label(label, htmlFor=component_id, style=LABEL_STYLE),
            dcc.DatePickerSingle(
                id=component_id,
                date=value,
                display_format="DD.MM.YYYY",
                first_day_of_week=1,
                clearable=False,
                style={"width": "100%"},
            ),
        ],
    )


def two_columns(left, right):
    """Stellt zwei Eingabefelder nebeneinander dar."""
    return html.Div(
        style={
            "display": "grid",
            "gridTemplateColumns": "repeat(2, minmax(0, 1fr))",
            "gap": "10px",
        },
        children=[left, right],
    )


def metric_card(label, value):
    """Erstellt eine Statistik-Kachel."""
    return html.Div(
        style={
            "flex": "1 1 180px",
            "padding": "14px",
            "borderRadius": "8px",
            "backgroundColor": "rgba(255, 255, 255, 0.05)",
            "border": "1px solid rgba(255, 255, 255, 0.08)",
        },
        children=[
            html.Div(
                label,
                style={
                    "fontSize": "13px",
                    "color": "#9ca3af",
                    "marginBottom": "5px",
                },
            ),
            html.Div(
                value,
                style={
                    "fontSize": "22px",
                    "fontWeight": "700",
                },
            ),
        ],
    )