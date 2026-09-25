"""
geo_export.py

Umrechnung zwischen dem lokalen ENU-System der Simulation und WGS84
(Breiten-/Längengrad) sowie Export der geschätzten Reviere als GeoJSON.

WARUM DAS NÖTIG IST
-------------------
ENU (East-North-Up) ist ein lokales Tangentialsystem: Meter relativ zu
einem Ursprung. Ohne die Angabe, wo dieser Ursprung auf der Erde liegt,
haben die Koordinaten der Simulation keinen Ortsbezug. Für den Export
wird deshalb genau ein Referenzpunkt gebraucht — die Position von pi_1,
das im Standardaufbau bei (0, 0, 0) steht.

Die Kapitel-Dokumentation zur Überführung ins Livesystem nennt als
Schritt 3 "Geografische Positionsdaten werden in ENU-Koordinaten
umgerechnet". Dieses Modul liefert beide Richtungen: wgs84_to_enu() für
den Weg ins Livesystem, enu_to_wgs84() für den Kartenexport zurück.
Beide sind zueinander invers und lassen sich damit gegeneinander testen.

WICHTIGER HINWEIS ZUR DATENHERKUNFT
-----------------------------------
Die exportierten Reviere stammen aus einer Simulation und liegen auf
einem frei gewählten Referenzpunkt. Sie sind keine Kartierung realer
Brutvögel. Jedes Feature und die Datei selbst tragen deshalb das Feld
"datenquelle": "simulation" — eine GeoJSON-Datei wandert erfahrungsgemäß
schnell weiter, und ohne diese Kennzeichnung sähe sie in QGIS aus wie
eine echte Erfassung.

GENAUIGKEIT
-----------
Verwendet wird eine lokale Tangentialebenen-Näherung erster Ordnung um
den Referenzpunkt (Krümmungsradien des WGS84-Ellipsoids an lat0). Für
die Ausdehnung dieser Simulation (einige hundert Meter) liegt der Fehler
im Millimeterbereich, bis wenige Kilometer im Zentimeterbereich. Für
größere Gebiete wäre eine vollständige ECEF-Transformation nötig.
"""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ============================================================================
# WGS84-ELLIPSOID
# ============================================================================
WGS84_SEMI_MAJOR_AXIS = 6378137.0
WGS84_FLATTENING = 1.0 / 298.257223563
WGS84_ECCENTRICITY_SQUARED = WGS84_FLATTENING * (2.0 - WGS84_FLATTENING)

# Stützpunkte für die Kreispolygone der Revierradien.
CIRCLE_STEPS = 64


def _curvature_radii(latitude_deg: float) -> Tuple[float, float]:
    """
    Querkrümmungs- und Meridiankrümmungsradius des WGS84-Ellipsoids
    auf der angegebenen geografischen Breite.
    """
    latitude_rad = math.radians(latitude_deg)
    sin_latitude = math.sin(latitude_rad)
    denominator = 1.0 - WGS84_ECCENTRICITY_SQUARED * sin_latitude * sin_latitude

    prime_vertical = WGS84_SEMI_MAJOR_AXIS / math.sqrt(denominator)
    meridian = (
        WGS84_SEMI_MAJOR_AXIS
        * (1.0 - WGS84_ECCENTRICITY_SQUARED)
        / (denominator ** 1.5)
    )

    return prime_vertical, meridian


def enu_to_wgs84(
    east: float,
    north: float,
    reference_latitude: float,
    reference_longitude: float,
) -> Tuple[float, float]:
    """
    Rechnet eine lokale ENU-Position (Meter) in Breiten- und Längengrad um.

    Rückgabe: (Breitengrad, Längengrad) in Dezimalgrad.
    """
    prime_vertical, meridian = _curvature_radii(reference_latitude)
    reference_latitude_rad = math.radians(reference_latitude)

    latitude = reference_latitude + math.degrees(north / meridian)
    longitude = reference_longitude + math.degrees(
        east / (prime_vertical * math.cos(reference_latitude_rad))
    )

    return (latitude, longitude)


def wgs84_to_enu(
    latitude: float,
    longitude: float,
    reference_latitude: float,
    reference_longitude: float,
) -> Tuple[float, float]:
    """
    Rechnet Breiten- und Längengrad in eine lokale ENU-Position (Meter) um.

    Das ist die Umkehrung von enu_to_wgs84() und die Richtung, die ein
    Livesystem braucht, um GPS-Positionen in das Koordinatensystem der
    Trajektorienberechnung zu überführen.

    Rückgabe: (east, north) in Metern.
    """
    prime_vertical, meridian = _curvature_radii(reference_latitude)
    reference_latitude_rad = math.radians(reference_latitude)

    north = math.radians(latitude - reference_latitude) * meridian
    east = (
        math.radians(longitude - reference_longitude)
        * prime_vertical
        * math.cos(reference_latitude_rad)
    )

    return (east, north)


def _position(
    east: float,
    north: float,
    reference_latitude: float,
    reference_longitude: float,
) -> List[float]:
    """
    Eine GeoJSON-Position. Achtung: GeoJSON erwartet [Länge, Breite],
    also die umgekehrte Reihenfolge zur üblichen Schreibweise.
    """
    latitude, longitude = enu_to_wgs84(
        east, north, reference_latitude, reference_longitude
    )
    return [round(longitude, 8), round(latitude, 8)]


def circle_ring(
    center_east: float,
    center_north: float,
    radius: float,
    reference_latitude: float,
    reference_longitude: float,
    steps: int = CIRCLE_STEPS,
) -> List[List[float]]:
    """
    Nähert einen Kreis in der ENU-Ebene durch ein Polygon an und gibt den
    geschlossenen Ring in GeoJSON-Positionen zurück.
    """
    ring = []

    for step in range(steps):
        angle = 2.0 * math.pi * step / steps
        ring.append(
            _position(
                center_east + radius * math.cos(angle),
                center_north + radius * math.sin(angle),
                reference_latitude,
                reference_longitude,
            )
        )

    # GeoJSON verlangt einen geschlossenen Ring.
    ring.append(ring[0])
    return ring


# ============================================================================
# GEOJSON-EXPORT
# ============================================================================
def territories_to_geojson(
    territories: Sequence[Dict[str, Any]],
    reference_latitude: float,
    reference_longitude: float,
    pi_setup: Optional[Sequence[Dict[str, Any]]] = None,
    include_true_nests: bool = True,
) -> Dict[str, Any]:
    """
    Baut eine GeoJSON-FeatureCollection aus den geschätzten Revieren.

    Enthalten sind je Revier ein Punkt für das geschätzte Zentrum und ein
    Polygon für den Revierkreis, optional die Ground-Truth-Nester und die
    Standorte der Pi-Stationen.

    territories: serialisierte TerritoryEstimate-Dicts aus dem Store.
    """
    features: List[Dict[str, Any]] = []

    for territory in territories:
        track_id = str(territory.get("track_id", ""))
        center_east = float(territory.get("center_e", 0.0))
        center_north = float(territory.get("center_n", 0.0))
        radius = float(territory.get("radius", 0.0))

        center_latitude, center_longitude = enu_to_wgs84(
            center_east, center_north, reference_latitude, reference_longitude
        )

        common_properties = {
            "track_id": track_id,
            "radius_m": round(radius, 2),
            "verfahren": territory.get("method_label", territory.get("method", "")),
            "punkte": territory.get("sample_count"),
            "abweichung_m": territory.get("error_m"),
            "koordinaten": format_coordinates(center_latitude, center_longitude),
            "google_maps_url": maps_url(center_latitude, center_longitude),
            "navigation_url": maps_directions_url(center_latitude, center_longitude),
            "datenquelle": "simulation",
        }

        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": _position(
                        center_east, center_north, reference_latitude, reference_longitude
                    ),
                },
                "properties": dict(
                    common_properties,
                    name=f"Revierzentrum {track_id}",
                    typ="revierzentrum_geschaetzt",
                ),
            }
        )

        if radius > 0:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            circle_ring(
                                center_east,
                                center_north,
                                radius,
                                reference_latitude,
                                reference_longitude,
                            )
                        ],
                    },
                    "properties": dict(
                        common_properties,
                        name=f"Revier {track_id}",
                        typ="revierflaeche",
                    ),
                }
            )

        true_east = territory.get("true_nest_e")
        true_north = territory.get("true_nest_n")

        if include_true_nests and true_east is not None and true_north is not None:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": _position(
                            float(true_east),
                            float(true_north),
                            reference_latitude,
                            reference_longitude,
                        ),
                    },
                    "properties": {
                        "track_id": track_id,
                        "name": f"Nest (Ground Truth) {track_id}",
                        "typ": "nest_ground_truth",
                        "abweichung_m": territory.get("error_m"),
                        "datenquelle": "simulation",
                    },
                }
            )

    for pi in pi_setup or []:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": _position(
                        float(pi.get("x", 0.0)),
                        float(pi.get("y", 0.0)),
                        reference_latitude,
                        reference_longitude,
                    ),
                },
                "properties": {
                    "name": str(pi.get("name", pi.get("id", "Pi"))),
                    "typ": "kamerastation",
                    "yaw_deg": pi.get("yaw_deg"),
                    "pitch_deg": pi.get("pitch_deg"),
                    "datenquelle": "simulation",
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "name": "Feldlerchen-Reviere (Simulation)",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
        },
        "metadata": {
            "hinweis": (
                "Simulierte Daten. Die Reviere stammen aus einer Simulation und "
                "wurden auf einen frei gewaehlten Referenzpunkt gelegt. Dies ist "
                "KEINE Kartierung realer Brutvoegel."
            ),
            "datenquelle": "simulation",
            "enu_referenzpunkt": {
                "latitude": reference_latitude,
                "longitude": reference_longitude,
                "bedeutung": "Position von pi_1, entspricht ENU (0, 0, 0)",
            },
            "reviere": len(territories),
        },
        "features": features,
    }


# ============================================================================
# KARTENLINKS
# ============================================================================
def maps_url(latitude: float, longitude: float) -> str:
    """Google-Maps-Link, der einen Pin auf die Position setzt."""
    return f"https://www.google.com/maps?q={latitude:.6f},{longitude:.6f}"


def maps_satellite_url(latitude: float, longitude: float, zoom: int = 19) -> str:
    """Google-Maps-Link in Satellitenansicht."""
    return (
        f"https://www.google.com/maps/@{latitude:.6f},{longitude:.6f},"
        f"{zoom}z/data=!3m1!1e3"
    )


def maps_directions_url(latitude: float, longitude: float) -> str:
    """Google-Maps-Link, der die Navigation zur Position startet."""
    return (
        "https://www.google.com/maps/dir/?api=1&destination="
        f"{latitude:.6f},{longitude:.6f}"
    )


def format_coordinates(latitude: float, longitude: float) -> str:
    """Koordinaten in der Form, die Google Maps direkt akzeptiert."""
    return f"{latitude:.6f}, {longitude:.6f}"


# ============================================================================
# PI-AUFBAU AUS GPS-KOORDINATEN
# ============================================================================
def derive_pi_positions(
    pi_rows: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Rechnet die GPS-Koordinaten der Pi-Stationen in lokale ENU-Meter um.

    Die erste Zeile der Tabelle definiert den Ursprung: ihr Breiten- und
    Längengrad wird zu ENU (0, 0), alle weiteren Stationen werden relativ
    dazu in Metern ausgedrückt. Das ist der Weg, den ein Livesystem geht,
    wenn die Standorte im Feld per GPS aufgenommen wurden.

    Wichtig zur Genauigkeit: handelsübliches GPS streut um 2-5 m. Für die
    Triangulation zählt die relative Geometrie der Stationen zueinander,
    dort wirkt sich dieser Fehler voll aus. Wo die Abstände direkt
    gemessen werden können (Maßband, Laserentfernungsmesser, RTK-GPS),
    sind die eingetragenen Meter genauer als der Umweg über GPS.

    Rückgabe: (neue Zeilen mit gefüllten x/y, Fehlermeldung oder None).
    """
    if not pi_rows:
        return [], "Keine Pi-Stationen vorhanden."

    reference = pi_rows[0]

    try:
        reference_latitude = float(reference.get("lat"))
        reference_longitude = float(reference.get("lon"))
    except (TypeError, ValueError):
        return list(pi_rows), (
            "Die erste Station braucht Breiten- und Längengrad, "
            "sie bildet den Ursprung."
        )

    updated: List[Dict[str, Any]] = []

    for index, row in enumerate(pi_rows):
        new_row = dict(row)

        try:
            latitude = float(row.get("lat"))
            longitude = float(row.get("lon"))
        except (TypeError, ValueError):
            updated.append(new_row)
            continue

        if index == 0:
            new_row["x"] = 0.0
            new_row["y"] = 0.0
        else:
            east, north = wgs84_to_enu(
                latitude, longitude, reference_latitude, reference_longitude
            )
            new_row["x"] = round(east, 2)
            new_row["y"] = round(north, 2)

        updated.append(new_row)

    return updated, None


def reference_from_pi_rows(
    pi_rows: Sequence[Dict[str, Any]],
) -> Optional[Tuple[float, float]]:
    """
    Liest den Georeferenzpunkt aus der Pi-Tabelle: Breiten- und
    Längengrad der ersten Station, die ENU (0, 0) definiert.
    """
    if not pi_rows:
        return None

    try:
        return (float(pi_rows[0].get("lat")), float(pi_rows[0].get("lon")))
    except (TypeError, ValueError):
        return None


def round_trip_error(
    east: float,
    north: float,
    reference_latitude: float,
    reference_longitude: float,
) -> float:
    """
    Hilfsfunktion für Tests: rechnet eine ENU-Position nach WGS84 und
    wieder zurück und gibt den verbleibenden Abstand in Metern aus.
    """
    latitude, longitude = enu_to_wgs84(
        east, north, reference_latitude, reference_longitude
    )
    east_back, north_back = wgs84_to_enu(
        latitude, longitude, reference_latitude, reference_longitude
    )

    return math.hypot(east_back - east, north_back - north)
