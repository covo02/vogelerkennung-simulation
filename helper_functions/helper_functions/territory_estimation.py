"""
territory_estimation.py

Schaetzt aus klassifizierten Feldlerchen-Trajektorien das Revierzentrum
(den wahrscheinlichen Nestbereich) und bewertet diese Schaetzung gegen die
Ground Truth aus skylark_generation.py.

WICHTIGE FACHLICHE EINORDNUNG
-----------------------------
Der Singflug markiert das REVIER des Maennchens, nicht punktgenau das Nest.
Das Nest liegt innerhalb des Reviers (Reviergroesse in der Literatur grob
0,25-1 ha, also ein Radius von etwa 30-60 m), aber nicht zwingend in dessen
Mittelpunkt. Dieses Modul schaetzt deshalb bewusst ein "Revierzentrum" bzw.
einen "wahrscheinlichen Nestbereich" - keinen exakten Nestort.

In der Simulation ist das Revierzentrum identisch mit dem Punkt, um den
generate_skylarks() den Singflug konstruiert (nest_e / nest_n). Der
gemessene Lokalisierungsfehler ist damit ein reiner Verfahrensfehler und
enthaelt nicht die biologische Unschaerfe zwischen Revierzentrum und Nest.

SCHAETZVERFAHREN
----------------
"centroid"    Schwerpunkt aller Horizontalpositionen des Tracks.
              Robust gegen Ausreisser und gegen abgeschnittene Tracks,
              aber systematisch verzerrt, wenn der Track die Kreisphase
              nicht vollstaendig abdeckt.

"start_end"   Mittel aus erstem und letztem Punkt des Tracks. Der Steigflug
              beginnt und der Sturzflug endet am Revierzentrum (siehe
              skylark_generation.py: Bahnradius geht in beiden Phasen gegen
              null), deshalb ist dieses Verfahren bei vollstaendigen Tracks
              am genauesten - und bei fragmentierten Tracks das schlechteste.

"circle_fit"  Algebraischer Kreisfit (Kasa) auf die Plateauphase des
              Singflugs. Nutzt die geometrische Information der Kreisbahn
              und kommt auch ohne beobachteten Start-/Endpunkt aus, braucht
              dafuer aber genuegend Punkte auf einem hinreichend grossen
              Kreisbogen.

Das Modul arbeitet ausschliesslich mit Positions- und Zeitdaten
(timestamp, enu_e, enu_n, enu_u) und kommt ohne Fremdbibliotheken aus.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ============================================================================
# KONFIGURATION
# ============================================================================
DEFAULT_METHOD = "start_end"

AVAILABLE_METHODS = ("centroid", "start_end", "circle_fit")

METHOD_LABELS = {
    "centroid": "Zentroid",
    "start_end": "Start-/Endpunkt-Mittel",
    "circle_fit": "Kreisfit (Plateauphase)",
}

# Anteil des Hoehengewinns, ab dem ein Punkt zur Plateauphase (Singflug)
# gezaehlt wird. Bewusst identisch zur Plateau-Definition in
# skylark_classifier.extract_features().
PLATEAU_HEIGHT_FRACTION = 0.15

# Perzentil der Bahnradien, das als Revierradius ausgegeben wird.
RADIUS_PERCENTILE = 90.0

# Mindestanzahl Punkte fuer eine sinnvolle Schaetzung.
MIN_SAMPLES = 4

# Mindestanzahl Plateaupunkte fuer den Kreisfit.
MIN_PLATEAU_SAMPLES_FOR_CIRCLE_FIT = 6


# ============================================================================
# ERGEBNISTYP
# ============================================================================
@dataclass
class TerritoryEstimate:
    """Geschaetztes Revierzentrum eines einzelnen Tracks."""
    track_id: str
    center_e: float
    center_n: float
    radius: float
    method: str
    sample_count: int
    # Ground Truth, nur in der Simulation vorhanden.
    true_nest_e: Optional[float] = None
    true_nest_n: Optional[float] = None
    error_m: Optional[float] = None
    # Hinweis, falls auf ein anderes Verfahren zurueckgefallen wurde.
    note: str = ""

    @property
    def method_label(self) -> str:
        return METHOD_LABELS.get(self.method, self.method)


# ============================================================================
# HILFSFUNKTIONEN
# ============================================================================
def _parse_timestamp(timestamp: str) -> datetime:
    """Wandelt einen ISO-8601-Zeitstempel in ein datetime-Objekt um."""
    return datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))


def _sorted_points(records: Sequence[Dict[str, Any]]) -> List[Tuple[float, float, float]]:
    """Liefert die Punkte eines Tracks zeitlich sortiert als (e, n, u)."""
    try:
        ordered = sorted(records, key=lambda record: _parse_timestamp(record["timestamp"]))
    except (KeyError, TypeError, ValueError):
        # Ohne verwertbare Zeitstempel bleibt die Eingabereihenfolge.
        ordered = list(records)

    points = []
    for record in ordered:
        try:
            points.append(
                (
                    float(record["enu_e"]),
                    float(record["enu_n"]),
                    float(record["enu_u"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return points


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Perzentil mit linearer Interpolation (ohne numpy)."""
    if not values:
        return 0.0

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    position = (percentile / 100.0) * (len(ordered) - 1)
    lower_index = int(math.floor(position))
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = position - lower_index

    return ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight


def _solve_3x3(matrix: List[List[float]], rhs: List[float]) -> Optional[Tuple[float, float, float]]:
    """Loest ein 3x3-Gleichungssystem per Gauss-Elimination mit Pivotisierung."""
    augmented = [row[:] + [value] for row, value in zip(matrix, rhs)]

    for pivot_index in range(3):
        pivot_row = max(
            range(pivot_index, 3),
            key=lambda row_index: abs(augmented[row_index][pivot_index]),
        )

        if abs(augmented[pivot_row][pivot_index]) <= 1e-12:
            return None

        if pivot_row != pivot_index:
            augmented[pivot_index], augmented[pivot_row] = (
                augmented[pivot_row],
                augmented[pivot_index],
            )

        pivot_value = augmented[pivot_index][pivot_index]
        for column_index in range(pivot_index, 4):
            augmented[pivot_index][column_index] /= pivot_value

        for row_index in range(3):
            if row_index == pivot_index:
                continue
            factor = augmented[row_index][pivot_index]
            for column_index in range(pivot_index, 4):
                augmented[row_index][column_index] -= factor * augmented[pivot_index][column_index]

    return (augmented[0][3], augmented[1][3], augmented[2][3])


def plateau_points(points: Sequence[Tuple[float, float, float]]) -> List[Tuple[float, float, float]]:
    """
    Waehlt die Punkte der Plateau-/Singflugphase aus: alle Punkte, deren
    Hoehe im obersten PLATEAU_HEIGHT_FRACTION-Anteil des Hoehengewinns liegt.
    """
    if not points:
        return []

    altitudes = [point[2] for point in points]
    altitude_max = max(altitudes)
    altitude_gain = altitude_max - min(altitudes)

    threshold = altitude_max - PLATEAU_HEIGHT_FRACTION * max(altitude_gain, 1e-6)

    return [point for point in points if point[2] >= threshold]


# ============================================================================
# SCHAETZVERFAHREN
# ============================================================================
def estimate_centroid(points: Sequence[Tuple[float, float, float]]) -> Tuple[float, float]:
    """Schwerpunkt aller Horizontalpositionen."""
    count = len(points)
    return (
        sum(point[0] for point in points) / count,
        sum(point[1] for point in points) / count,
    )


def estimate_start_end(points: Sequence[Tuple[float, float, float]]) -> Tuple[float, float]:
    """
    Mittel aus erstem und letztem Punkt. Steigflug startet und Sturzflug
    endet am Revierzentrum, der Bahnradius geht dort gegen null.
    """
    first = points[0]
    last = points[-1]
    return ((first[0] + last[0]) / 2.0, (first[1] + last[1]) / 2.0)


def estimate_circle_fit(
    points: Sequence[Tuple[float, float, float]],
) -> Optional[Tuple[float, float]]:
    """
    Algebraischer Kreisfit nach Kasa auf die Plateauphase.

    Minimiert sum (x^2 + y^2 - 2*a*x - 2*b*y - c)^2 ueber a, b, c.
    Der Kreismittelpunkt ist (a, b). Gibt None zurueck, wenn zu wenige
    Punkte vorliegen oder das Gleichungssystem entartet ist (z. B. wenn
    alle Punkte nahezu auf einer Geraden liegen).
    """
    candidates = plateau_points(points)
    if len(candidates) < MIN_PLATEAU_SAMPLES_FOR_CIRCLE_FIT:
        return None

    sum_x = sum_y = sum_xx = sum_yy = sum_xy = 0.0
    sum_z = sum_xz = sum_yz = 0.0
    count = float(len(candidates))

    for x, y, _u in candidates:
        z = x * x + y * y
        sum_x += x
        sum_y += y
        sum_xx += x * x
        sum_yy += y * y
        sum_xy += x * y
        sum_z += z
        sum_xz += x * z
        sum_yz += y * z

    matrix = [
        [2 * sum_xx, 2 * sum_xy, sum_x],
        [2 * sum_xy, 2 * sum_yy, sum_y],
        [2 * sum_x, 2 * sum_y, count],
    ]
    rhs = [sum_xz, sum_yz, sum_z]

    solution = _solve_3x3(matrix, rhs)
    if solution is None:
        return None

    center_x, center_y, _c = solution

    if not (math.isfinite(center_x) and math.isfinite(center_y)):
        return None

    return (center_x, center_y)


def territory_radius(
    points: Sequence[Tuple[float, float, float]],
    center_e: float,
    center_n: float,
) -> float:
    """
    Revierradius als RADIUS_PERCENTILE-Perzentil der horizontalen Abstaende
    zum geschaetzten Zentrum. Bevorzugt werden die Punkte der Plateauphase,
    weil dort der eigentliche Kreisflug stattfindet - Steig- und Sturzflug
    wuerden den Radius sonst nach unten ziehen.
    """
    candidates = plateau_points(points) or list(points)

    distances = [
        math.hypot(point[0] - center_e, point[1] - center_n)
        for point in candidates
    ]

    return _percentile(distances, RADIUS_PERCENTILE)


# ============================================================================
# SCHAETZUNG JE TRACK
# ============================================================================
def true_nest_position(
    records: Sequence[Dict[str, Any]],
) -> Optional[Tuple[float, float]]:
    """
    Liest die Ground Truth des Revierzentrums aus den Records
    (nest_e / nest_n, gesetzt von skylark_generation.py). Gibt None
    zurueck, wenn die Felder fehlen - etwa bei echten Messdaten.
    """
    for record in records:
        if "nest_e" in record and "nest_n" in record:
            try:
                return (float(record["nest_e"]), float(record["nest_n"]))
            except (TypeError, ValueError):
                continue

    return None


def estimate_territory(
    records: Sequence[Dict[str, Any]],
    track_id: str = "",
    method: str = DEFAULT_METHOD,
) -> Optional[TerritoryEstimate]:
    """
    Schaetzt das Revierzentrum eines einzelnen Tracks.

    Gibt None zurueck, wenn weniger als MIN_SAMPLES verwertbare Punkte
    vorliegen. Faellt der Kreisfit aus (zu wenige Plateaupunkte oder
    entartete Geometrie), wird auf den Zentroid zurueckgegriffen und das
    Feld note entsprechend gesetzt.
    """
    if method not in AVAILABLE_METHODS:
        method = DEFAULT_METHOD

    points = _sorted_points(records)
    if len(points) < MIN_SAMPLES:
        return None

    note = ""
    used_method = method

    if method == "centroid":
        center = estimate_centroid(points)
    elif method == "start_end":
        center = estimate_start_end(points)
    else:
        center = estimate_circle_fit(points)
        if center is None:
            center = estimate_centroid(points)
            used_method = "centroid"
            note = "Kreisfit nicht moeglich, Zentroid verwendet"

    center_e, center_n = center
    radius = territory_radius(points, center_e, center_n)

    estimate = TerritoryEstimate(
        track_id=track_id,
        center_e=round(center_e, 2),
        center_n=round(center_n, 2),
        radius=round(radius, 2),
        method=used_method,
        sample_count=len(points),
        note=note,
    )

    true_nest = true_nest_position(records)
    if true_nest is not None:
        estimate.true_nest_e = round(true_nest[0], 2)
        estimate.true_nest_n = round(true_nest[1], 2)
        estimate.error_m = round(
            math.hypot(center_e - true_nest[0], center_n - true_nest[1]),
            2,
        )

    return estimate


def estimate_territories(
    records: Sequence[Dict[str, Any]],
    track_ids: Optional[Sequence[str]] = None,
    group_column: str = "determined_track_id",
    method: str = DEFAULT_METHOD,
) -> List[TerritoryEstimate]:
    """
    Schaetzt Revierzentren fuer die angegebenen Tracks.

    track_ids: Liste der zu beruecksichtigenden Track-IDs, ueblicherweise
    die als Feldlerche klassifizierten. None bedeutet alle Tracks.
    """
    wanted = {str(track_id) for track_id in track_ids} if track_ids is not None else None

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        track_id = str(record.get(group_column, "unbekannt"))
        if wanted is not None and track_id not in wanted:
            continue
        grouped.setdefault(track_id, []).append(record)

    estimates = []
    for track_id, track_records in grouped.items():
        estimate = estimate_territory(track_records, track_id=track_id, method=method)
        if estimate is not None:
            estimates.append(estimate)

    estimates.sort(key=lambda item: item.track_id)
    return estimates


# ============================================================================
# BEWERTUNG GEGEN GROUND TRUTH
# ============================================================================
def evaluate_estimates(estimates: Sequence[TerritoryEstimate]) -> Optional[Dict[str, Any]]:
    """
    Fasst die Lokalisierungsfehler zusammen. Gibt None zurueck, wenn zu
    keinem Track eine Ground Truth vorliegt (echte Messdaten).
    """
    errors = [
        estimate.error_m
        for estimate in estimates
        if estimate.error_m is not None
    ]

    if not errors:
        return None

    return {
        "count": len(errors),
        "median_error": round(_percentile(errors, 50.0), 2),
        "p90_error": round(_percentile(errors, 90.0), 2),
        "max_error": round(max(errors), 2),
        "mean_error": round(sum(errors) / len(errors), 2),
    }


def compare_methods(
    records: Sequence[Dict[str, Any]],
    track_ids: Optional[Sequence[str]] = None,
    group_column: str = "determined_track_id",
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Wendet alle Schaetzverfahren auf dieselben Tracks an und liefert je
    Verfahren die Fehlerstatistik. Nuetzlich, um die Verfahren gegen-
    einander zu bewerten statt nur eins zu behaupten.
    """
    return {
        method: evaluate_estimates(
            estimate_territories(
                records,
                track_ids=track_ids,
                group_column=group_column,
                method=method,
            )
        )
        for method in AVAILABLE_METHODS
    }
