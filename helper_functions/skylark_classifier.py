"""
feldlerche_classifier.py (v3)

FeatureExtractor + Classifier zur Erkennung des artspezifischen Singflug-
Musters der Feldlerche (Alauda arvensis) in simulierten (oder spaeter
echten) Positionsdaten.

Gehoert in helper_functions/ - gleiche Ebene wie bird_generation.py,
skylark_generation.py, pi_view_simulation.py, detected_positions.py.

Kalibriert gegen skylark_generation.py::generate_skylarks(): spiral-
foermiger Steigflug -> Kreisflug/Singflug in Revierhoehe -> spiral-
foermiger Sinkflug zurueck zum Nestbereich. Zusaetzlich extern validiert
gegen veroeffentlichte Feldbeobachtungen (Singflughoehe 50-100m, Drei-
Phasen-Struktur Steigen/Kreisen/Sturzflug, Revierbindung).

Steigflug UND Sturzflug sind ein hartes Gate (Pflichtkriterium) fuer eine
vollstaendige Klassifikation. Fehlt der Hoehenbeweis (z.B. durch
eingeschraenkte Kamerasicht), greift ein strengerer Ersatzpfad basierend
auf kreisender Bewegung mit Revierbindung.

Diese Datei arbeitet ausschliesslich mit Positions- und Zeitdaten
(enu_e, enu_n, enu_u, timestamp) - keine Abhaengigkeit von
skylark_generation.py oder bird_generation.py.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


# ============================================================================
# SCHWELLENWERTE
# ============================================================================
DEFAULT_THRESHOLDS: Dict[str, float] = {
    "min_altitude_gain": 15.0,
    "min_climb_rate": 1.0,
    "min_descent_rate": 1.0,
    "min_hover_fraction": 0.15,
    "max_return_ratio": 0.35,
    "max_territory_radius": 100.0,
    "min_score": 0.6,
    "min_score_isolated": 0.75,
    "min_orbit_radius": 3.0,
    "min_horizontal_path_for_orbit": 10.0,
}


def _parse_timestamp(timestamp: str) -> datetime:
    """Wandelt einen ISO-8601-Zeitstempel in ein datetime-Objekt um."""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


# ============================================================================
# FEATURE EXTRACTOR
# ============================================================================
@dataclass
class FlightFeatures:
    """Aus einer Flugbahn (Track) extrahierte Merkmale des Flugmusters."""
    track_id: str
    sample_count: int
    duration_s: float
    altitude_min: float
    altitude_max: float
    altitude_gain: float
    max_climb_rate: float
    max_descent_rate: float
    hover_duration_s: float
    hover_fraction: float
    horizontal_path_length: float
    net_horizontal_displacement: float
    max_radius_from_centroid: float
    centroid_e: float = 0.0
    centroid_n: float = 0.0


def extract_features(records: List[Dict[str, Any]], track_id: str = "") -> Optional[FlightFeatures]:
    """
    Berechnet Flugmuster-Merkmale aus einer Liste von Positions-Records
    ({"timestamp", "enu_e", "enu_n", "enu_u"}). Records muessen nicht
    vorsortiert sein. Gibt None zurueck bei < 4 Samples.
    """
    if len(records) < 4:
        return None

    sorted_records = sorted(records, key=lambda r: r["timestamp"])
    timestamps = [_parse_timestamp(r["timestamp"]) for r in sorted_records]
    xs = [float(r["enu_e"]) for r in sorted_records]
    ys = [float(r["enu_n"]) for r in sorted_records]
    zs = [float(r["enu_u"]) for r in sorted_records]

    duration_s = (timestamps[-1] - timestamps[0]).total_seconds()
    if duration_s <= 0:
        return None

    vertical_rates = []
    for i in range(1, len(sorted_records)):
        dt = (timestamps[i] - timestamps[i - 1]).total_seconds()
        if dt <= 0:
            continue
        vertical_rates.append((zs[i] - zs[i - 1]) / dt)

    altitude_min = min(zs)
    altitude_max = max(zs)
    altitude_gain = altitude_max - altitude_min
    max_climb_rate = max(vertical_rates) if vertical_rates else 0.0
    max_descent_rate = -min(vertical_rates) if vertical_rates else 0.0

    plateau_altitude_threshold = altitude_max - 0.15 * max(altitude_gain, 1e-6)
    plateau_indices = [i for i, z in enumerate(zs) if z >= plateau_altitude_threshold]

    hover_duration_s = 0.0
    for i in plateau_indices:
        if i == 0:
            continue
        dt = (timestamps[i] - timestamps[i - 1]).total_seconds()
        if dt <= 0:
            continue
        hover_duration_s += dt

    hover_fraction = hover_duration_s / duration_s if duration_s > 0 else 0.0

    horizontal_path_length = sum(
        math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1])
        for i in range(1, len(sorted_records))
    )
    net_horizontal_displacement = math.hypot(xs[-1] - xs[0], ys[-1] - ys[0])

    centroid_x = sum(xs) / len(xs)
    centroid_y = sum(ys) / len(ys)
    max_radius_from_centroid = max(
        math.hypot(x - centroid_x, y - centroid_y) for x, y in zip(xs, ys)
    )

    return FlightFeatures(
        track_id=track_id,
        sample_count=len(sorted_records),
        duration_s=duration_s,
        altitude_min=altitude_min,
        altitude_max=altitude_max,
        altitude_gain=altitude_gain,
        max_climb_rate=max_climb_rate,
        max_descent_rate=max_descent_rate,
        hover_duration_s=hover_duration_s,
        hover_fraction=hover_fraction,
        horizontal_path_length=horizontal_path_length,
        net_horizontal_displacement=net_horizontal_displacement,
        max_radius_from_centroid=max_radius_from_centroid,
        centroid_e=centroid_x,
        centroid_n=centroid_y,
    )


# ============================================================================
# CLASSIFIER
# ============================================================================
@dataclass
class ClassificationResult:
    """Ergebnis der Feldlerchen-Klassifikation fuer einen einzelnen Track."""
    track_id: str
    is_feldlerche: bool
    score: float
    reasons: List[str]
    classification_path: str  # "vollstaendig" | "isolierter_singflug" | "unvollstaendig"
    features: FlightFeatures
    # Strukturierte Fassung von reasons: je Kriterium Text, ob es erfuellt
    # wurde und mit welchem Gewicht es in den Score eingeht. Der Fliesstext
    # in reasons bleibt unveraendert erhalten.
    criteria: List[Dict[str, Any]] = field(default_factory=list)


def classify_features(
    features: FlightFeatures,
    thresholds: Dict[str, float] = DEFAULT_THRESHOLDS,
) -> ClassificationResult:
    """
    Zweistufige Klassifikation: Pfad "vollstaendig" erfordert Steigflug UND
    Sturzflug als hartes Gate. Pfad "isolierter Singflug" greift, wenn kein
    Hoehenwechsel beobachtbar ist, und nutzt eine strengere Schwelle.
    """
    reasons: List[str] = []
    criteria: List[Dict[str, Any]] = []

    has_climb = features.max_climb_rate >= thresholds["min_climb_rate"]
    has_descent = features.max_descent_rate >= thresholds["min_descent_rate"]
    has_altitude_gain = features.altitude_gain >= thresholds["min_altitude_gain"]

    return_ratio = (
        features.net_horizontal_displacement / features.horizontal_path_length
        if features.horizontal_path_length > 1e-6 else 1.0
    )
    in_territory = features.max_radius_from_centroid <= thresholds["max_territory_radius"]

    if has_climb and has_descent and has_altitude_gain:
        gate_reason = (
            f"Steigrate {features.max_climb_rate:.2f} m/s und Sinkrate "
            f"{features.max_descent_rate:.2f} m/s bestaetigt (Pflichtkriterium erfuellt)"
        )
        reasons.append(gate_reason)
        criteria.append({"text": gate_reason, "met": True, "weight": None})

        score = 0.0
        weight_total = 0.0

        def add_criterion(condition: bool, weight: float, reason_true: str, reason_false: str) -> None:
            nonlocal score, weight_total
            weight_total += weight
            if condition:
                score += weight
                reasons.append(reason_true)
                criteria.append({"text": reason_true, "met": True, "weight": weight})
            else:
                reasons.append(reason_false)
                criteria.append({"text": reason_false, "met": False, "weight": weight})

        add_criterion(True, 0.25, f"Hoehengewinn {features.altitude_gain:.1f}m bestaetigt", "")
        add_criterion(
            features.hover_fraction >= thresholds["min_hover_fraction"], 0.20,
            f"Singflug-Plateauanteil {features.hover_fraction:.0%} ausreichend",
            f"Singflug-Plateauanteil {features.hover_fraction:.0%} zu gering",
        )
        add_criterion(
            return_ratio <= thresholds["max_return_ratio"], 0.30,
            f"Rueckkehr-Verhaeltnis {return_ratio:.3f} spricht fuer Revierbindung",
            f"Rueckkehr-Verhaeltnis {return_ratio:.3f} spricht fuer Streckenflug",
        )
        add_criterion(
            in_territory, 0.25,
            f"Aktionsradius {features.max_radius_from_centroid:.1f}m im Revier-Bereich",
            f"Aktionsradius {features.max_radius_from_centroid:.1f}m zu gross fuer Revierbindung",
        )

        normalized_score = score / weight_total if weight_total > 0 else 0.0
        is_feldlerche = normalized_score >= thresholds["min_score"]

        return ClassificationResult(
            track_id=features.track_id,
            is_feldlerche=is_feldlerche,
            score=round(normalized_score, 3),
            reasons=reasons,
            classification_path="vollstaendig",
            features=features,
            criteria=criteria,
        )

    gate_reason = (
        "Kein vollstaendiger Steig-/Sturzflug beobachtet "
        "(vermutlich nur Plateauphase trianguliert) - pruefe isolierten Singflug"
    )
    reasons.append(gate_reason)
    criteria.append({"text": gate_reason, "met": False, "weight": None})

    has_real_orbit = (
        features.max_radius_from_centroid >= thresholds["min_orbit_radius"]
        and features.horizontal_path_length >= thresholds["min_horizontal_path_for_orbit"]
    )

    if not has_real_orbit:
        orbit_reason = "Keine kreisende Bewegung erkennbar (zu klein, vermutlich nur Rauschen)"
        reasons.append(orbit_reason)
        criteria.append({"text": orbit_reason, "met": False, "weight": None})
        return ClassificationResult(
            track_id=features.track_id,
            is_feldlerche=False,
            score=0.0,
            reasons=reasons,
            classification_path="unvollstaendig",
            features=features,
            criteria=criteria,
        )

    score = 0.0
    weight_total = 0.0

    def add_isolated(condition: bool, weight: float, reason_true: str, reason_false: str) -> None:
        nonlocal score, weight_total
        weight_total += weight
        if condition:
            score += weight
            reasons.append(reason_true)
            criteria.append({"text": reason_true, "met": True, "weight": weight})
        else:
            reasons.append(reason_false)
            criteria.append({"text": reason_false, "met": False, "weight": weight})

    add_isolated(True, 0.35, f"Kreisende Bewegung mit Radius {features.max_radius_from_centroid:.1f}m erkannt", "")
    add_isolated(
        return_ratio <= thresholds["max_return_ratio"], 0.40,
        f"Rueckkehr-Verhaeltnis {return_ratio:.3f} spricht fuer Revierbindung",
        f"Rueckkehr-Verhaeltnis {return_ratio:.3f} spricht fuer Streckenflug",
    )
    add_isolated(
        in_territory, 0.25,
        f"Aktionsradius {features.max_radius_from_centroid:.1f}m im Revier-Bereich",
        f"Aktionsradius {features.max_radius_from_centroid:.1f}m zu gross fuer Revierbindung",
    )

    normalized_score = score / weight_total if weight_total > 0 else 0.0
    is_feldlerche = normalized_score >= thresholds["min_score_isolated"]

    return ClassificationResult(
        track_id=features.track_id,
        is_feldlerche=is_feldlerche,
        score=round(normalized_score, 3),
        reasons=reasons,
        classification_path="isolierter_singflug",
        features=features,
        criteria=criteria,
    )


def classify_tracks(
    records: List[Dict[str, Any]],
    group_column: str = "determined_track_id",
    thresholds: Dict[str, float] = DEFAULT_THRESHOLDS,
) -> List[ClassificationResult]:
    """
    Gruppiert Records nach group_column und klassifiziert jeden Track
    einzeln. group_column: z.B. "determined_track_id" (aus trajectory.py)
    oder "bird_id" (direkte Rohdaten).
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        track_id = str(record.get(group_column, "unbekannt"))
        grouped.setdefault(track_id, []).append(record)

    results = []
    for track_id, track_records in grouped.items():
        features = extract_features(track_records, track_id=track_id)
        if features is None:
            continue
        results.append(classify_features(features, thresholds))

    results.sort(key=lambda r: r.score, reverse=True)
    return results


# ============================================================================
# OPTIONALE VALIDIERUNG (nur in der Simulation sinnvoll)
# ============================================================================
def evaluate_against_ground_truth(
    records: List[Dict[str, Any]],
    results: List[ClassificationResult],
    group_column: str = "determined_track_id",
    id_column: str = "bird_id",
    lark_id_prefix: str = "lark_",
) -> Optional[Dict[str, Any]]:
    """
    Vergleicht Klassifikationsergebnisse mit der Simulations-Ground-Truth
    (skylark_generation.py vergibt IDs "lark_0001", bird_generation.py
    "bird_0001"). Liefert None bei echten Messdaten ohne dieses Praefix.
    """
    truth_by_track: Dict[str, bool] = {}
    for record in records:
        raw_id = record.get(id_column)
        if not raw_id:
            continue
        track_id = str(record.get(group_column, "unbekannt"))
        truth_by_track.setdefault(track_id, str(raw_id).startswith(lark_id_prefix))

    if not truth_by_track:
        return None

    true_positive = false_positive = true_negative = false_negative = 0

    for result in results:
        truth = truth_by_track.get(result.track_id)
        if truth is None:
            continue
        if result.is_feldlerche and truth:
            true_positive += 1
        elif result.is_feldlerche and not truth:
            false_positive += 1
        elif not result.is_feldlerche and truth:
            false_negative += 1
        else:
            true_negative += 1

    total = true_positive + false_positive + true_negative + false_negative
    if total == 0:
        return None

    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) > 0 else None
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) > 0 else None
    accuracy = (true_positive + true_negative) / total

    return {
        "total_tracks_evaluated": total,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "false_negative": false_negative,
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
        "accuracy": round(accuracy, 3),
    }
