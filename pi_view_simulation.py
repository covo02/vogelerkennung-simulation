import math
from typing import Any


def default_pi_setup() -> list[dict[str, float | str]]:
    return [
        {
            "id": "pi_1",
            "name": "Raspberry Pi Ursprung",
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "yaw_deg": 0.0,
            "pitch_deg": 45.0,
            "roll_deg": 0.0,
            "view_length": 400.0,
        },
        {
            "id": "pi_2",
            "name": "Raspberry Pi X-Achse",
            "x": 600.0,
            "y": 0.0,
            "z": 0.0,
            "yaw_deg": 180.0,
            "pitch_deg": 45.0,
            "roll_deg": 0.0,
            "view_length": 400.0,
        },
        {
            "id": "pi_3",
            "name": "Raspberry Pi Y-Achse",
            "x": 0.0,
            "y": 600.0,
            "z": 0.0,
            "yaw_deg": 90.0,
            "pitch_deg": 45.0,
            "roll_deg": 0.0,
            "view_length": 400.0,
        },
    ]


def to_float(value: Any, fallback: float) -> float:
    try:
        if value is None or value == "":
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def normalize_pi_setup(rows: list[dict[str, Any]] | None) -> list[dict[str, float | str]]:
    normalized = []
    fallback_rows = default_pi_setup()

    for index, fallback in enumerate(fallback_rows):
        row = (rows or []) and rows[index] if index < len(rows or []) else {}
        row = row or {}

        normalized.append(
            {
                "id": str(row.get("id", fallback["id"])),
                "name": str(row.get("name", fallback["name"])),
                "x": to_float(row.get("x"), float(fallback["x"])),
                "y": to_float(row.get("y"), float(fallback["y"])),
                "z": to_float(row.get("z"), float(fallback["z"])),
                "yaw_deg": to_float(
                    row.get("yaw_deg", row.get("view_horizontal_deg", row.get("view_angle_deg"))),
                    float(fallback["yaw_deg"]),
                ),
                "pitch_deg": to_float(
                    row.get("pitch_deg", row.get("view_vertical_deg")),
                    float(fallback["pitch_deg"]),
                ),
                "roll_deg": to_float(
                    row.get("roll_deg"),
                    float(fallback["roll_deg"]),
                ),
                "view_length": to_float(
                    row.get("view_length"),
                    float(fallback["view_length"]),
                ),
            }
        )

    return normalized


def direction_unit_vector(yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0) -> tuple[float, float, float]:
    """Berechnet einen normierten Richtungsvektor aus yaw, pitch und roll.

    Yaw dreht die Richtung um die Z-Achse.
    Pitch kippt die Richtung nach oben oder unten.
    Roll beeinflusst die Richtung einer Linienvisualisierung nicht und wird daher
    nur als Eingabe mitgeführt.
    """
    yaw_rad = math.radians(yaw_deg)
    pitch_rad = math.radians(pitch_deg)

    direction_x = math.cos(pitch_rad) * math.cos(yaw_rad)
    direction_y = math.cos(pitch_rad) * math.sin(yaw_rad)
    direction_z = math.sin(pitch_rad)

    magnitude = math.sqrt(direction_x ** 2 + direction_y ** 2 + direction_z ** 2)
    if magnitude <= 1e-12:
        return (1.0, 0.0, 0.0)

    return (
        direction_x / magnitude,
        direction_y / magnitude,
        direction_z / magnitude,
    )


def compute_view_vector(yaw_deg: float, pitch_deg: float, roll_deg: float, length: float) -> tuple[float, float, float]:
    unit_x, unit_y, unit_z = direction_unit_vector(yaw_deg, pitch_deg, roll_deg)
    return (unit_x * length, unit_y * length, unit_z * length)


def perpendicular_square_corners(
    end_x: float,
    end_y: float,
    end_z: float,
    direction_x: float,
    direction_y: float,
    direction_z: float,
    side_length: float,
) -> list[tuple[float, float, float]]:
    """Erzeugt die vier Ecken eines Quadrats, das senkrecht zur Richtung steht."""
    direction_unit, basis_u, basis_v = square_plane_basis(
        direction_x,
        direction_y,
        direction_z,
    )

    half_side = side_length / 2.0
    return [
        (
            end_x + basis_u[0] * half_side + basis_v[0] * half_side,
            end_y + basis_u[1] * half_side + basis_v[1] * half_side,
            end_z + basis_u[2] * half_side + basis_v[2] * half_side,
        ),
        (
            end_x - basis_u[0] * half_side + basis_v[0] * half_side,
            end_y - basis_u[1] * half_side + basis_v[1] * half_side,
            end_z - basis_u[2] * half_side + basis_v[2] * half_side,
        ),
        (
            end_x - basis_u[0] * half_side - basis_v[0] * half_side,
            end_y - basis_u[1] * half_side - basis_v[1] * half_side,
            end_z - basis_u[2] * half_side - basis_v[2] * half_side,
        ),
        (
            end_x + basis_u[0] * half_side - basis_v[0] * half_side,
            end_y + basis_u[1] * half_side - basis_v[1] * half_side,
            end_z + basis_u[2] * half_side - basis_v[2] * half_side,
        ),
    ]


def square_plane_basis(
    direction_x: float,
    direction_y: float,
    direction_z: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """Liefert Richtungs- und Basisvektoren fuer eine Ebene senkrecht zur Blickrichtung."""
    direction_magnitude = math.sqrt(direction_x ** 2 + direction_y ** 2 + direction_z ** 2)
    if direction_magnitude <= 1e-12:
        direction_unit = (0.0, 0.0, 1.0)
    else:
        direction_unit = (
            direction_x / direction_magnitude,
            direction_y / direction_magnitude,
            direction_z / direction_magnitude,
        )

    reference_axes = [
        (0.0, 0.0, 1.0),
        (0.0, 1.0, 0.0),
        (1.0, 0.0, 0.0),
    ]

    basis_u = (1.0, 0.0, 0.0)
    basis_v = (0.0, 1.0, 0.0)

    for reference_x, reference_y, reference_z in reference_axes:
        cross_x = direction_unit[1] * reference_z - direction_unit[2] * reference_y
        cross_y = direction_unit[2] * reference_x - direction_unit[0] * reference_z
        cross_z = direction_unit[0] * reference_y - direction_unit[1] * reference_x
        cross_magnitude = math.sqrt(cross_x ** 2 + cross_y ** 2 + cross_z ** 2)

        if cross_magnitude > 1e-12:
            basis_u = (
                cross_x / cross_magnitude,
                cross_y / cross_magnitude,
                cross_z / cross_magnitude,
            )
            break

    basis_v_x = direction_unit[1] * basis_u[2] - direction_unit[2] * basis_u[1]
    basis_v_y = direction_unit[2] * basis_u[0] - direction_unit[0] * basis_u[2]
    basis_v_z = direction_unit[0] * basis_u[1] - direction_unit[1] * basis_u[0]

    basis_v_magnitude = math.sqrt(basis_v_x ** 2 + basis_v_y ** 2 + basis_v_z ** 2)
    if basis_v_magnitude <= 1e-12:
        basis_v = (0.0, 1.0, 0.0)
    else:
        basis_v = (
            basis_v_x / basis_v_magnitude,
            basis_v_y / basis_v_magnitude,
            basis_v_z / basis_v_magnitude,
        )

    return direction_unit, basis_u, basis_v


def point_in_pi_view_volume(
    pi: dict[str, Any],
    point: dict[str, Any],
    plane_side_length: float,
    point_x_key: str = "enu_e",
    point_y_key: str = "enu_n",
    point_z_key: str = "enu_u",
) -> bool:
    """Prueft, ob ein Punkt im pyramidenfoermigen Sichtvolumen zwischen Pi und Quadrat liegt."""
    origin_x = to_float(pi.get("x"), 0.0)
    origin_y = to_float(pi.get("y"), 0.0)
    origin_z = to_float(pi.get("z"), 0.0)
    yaw_deg = to_float(pi.get("yaw_deg", pi.get("view_horizontal_deg")), 0.0)
    pitch_deg = to_float(pi.get("pitch_deg", pi.get("view_vertical_deg")), 0.0)
    roll_deg = to_float(pi.get("roll_deg"), 0.0)
    view_length = to_float(pi.get("view_length"), 400.0)

    if view_length <= 1e-12:
        return False

    point_x = to_float(point.get(point_x_key), 0.0)
    point_y = to_float(point.get(point_y_key), 0.0)
    point_z = to_float(point.get(point_z_key), 0.0)

    view_x, view_y, view_z = compute_view_vector(yaw_deg, pitch_deg, roll_deg, view_length)
    direction_unit, basis_u, basis_v = square_plane_basis(view_x, view_y, view_z)

    delta_x = point_x - origin_x
    delta_y = point_y - origin_y
    delta_z = point_z - origin_z

    forward_distance = (
        delta_x * direction_unit[0]
        + delta_y * direction_unit[1]
        + delta_z * direction_unit[2]
    )

    if forward_distance < 0.0 or forward_distance > view_length:
        return False

    lateral_u = (
        delta_x * basis_u[0]
        + delta_y * basis_u[1]
        + delta_z * basis_u[2]
    )
    lateral_v = (
        delta_x * basis_v[0]
        + delta_y * basis_v[1]
        + delta_z * basis_v[2]
    )

    max_offset = (plane_side_length / 2.0) * (forward_distance / view_length)
    tolerance = 1e-9
    return abs(lateral_u) <= max_offset + tolerance and abs(lateral_v) <= max_offset + tolerance


def project_point_to_pi_plane(
    pi: dict[str, Any],
    point: dict[str, Any],
    plane_side_length: float,
    point_x_key: str = "enu_e",
    point_y_key: str = "enu_n",
    point_z_key: str = "enu_u",
) -> tuple[float, float, float] | None:
    """Projiziert einen Punkt perspektivisch auf die orange Kameraebene des Pi."""
    origin_x = to_float(pi.get("x"), 0.0)
    origin_y = to_float(pi.get("y"), 0.0)
    origin_z = to_float(pi.get("z"), 0.0)
    yaw_deg = to_float(pi.get("yaw_deg", pi.get("view_horizontal_deg")), 0.0)
    pitch_deg = to_float(pi.get("pitch_deg", pi.get("view_vertical_deg")), 0.0)
    roll_deg = to_float(pi.get("roll_deg"), 0.0)
    view_length = to_float(pi.get("view_length"), 400.0)

    if view_length <= 1e-12:
        return None

    point_x = to_float(point.get(point_x_key), 0.0)
    point_y = to_float(point.get(point_y_key), 0.0)
    point_z = to_float(point.get(point_z_key), 0.0)

    view_x, view_y, view_z = compute_view_vector(yaw_deg, pitch_deg, roll_deg, view_length)
    direction_unit, basis_u, basis_v = square_plane_basis(view_x, view_y, view_z)

    delta_x = point_x - origin_x
    delta_y = point_y - origin_y
    delta_z = point_z - origin_z

    forward_distance = (
        delta_x * direction_unit[0]
        + delta_y * direction_unit[1]
        + delta_z * direction_unit[2]
    )

    if forward_distance <= 0.0 or forward_distance > view_length:
        return None

    lateral_u = (
        delta_x * basis_u[0]
        + delta_y * basis_u[1]
        + delta_z * basis_u[2]
    )
    lateral_v = (
        delta_x * basis_v[0]
        + delta_y * basis_v[1]
        + delta_z * basis_v[2]
    )

    max_offset = (plane_side_length / 2.0) * (forward_distance / view_length)
    tolerance = 1e-9
    if abs(lateral_u) > max_offset + tolerance or abs(lateral_v) > max_offset + tolerance:
        return None

    plane_center = (
        origin_x + direction_unit[0] * view_length,
        origin_y + direction_unit[1] * view_length,
        origin_z + direction_unit[2] * view_length,
    )

    projected_u = (lateral_u / max_offset) * (plane_side_length / 2.0) if max_offset > 1e-12 else 0.0
    projected_v = (lateral_v / max_offset) * (plane_side_length / 2.0) if max_offset > 1e-12 else 0.0

    projected_point = (
        plane_center[0] + basis_u[0] * projected_u + basis_v[0] * projected_v,
        plane_center[1] + basis_u[1] * projected_u + basis_v[1] * projected_v,
        plane_center[2] + basis_u[2] * projected_u + basis_v[2] * projected_v,
    )
    return projected_point


def points_in_view_volume_for_each_pi(
    pi_setup: list[dict[str, Any]],
    points: list[dict[str, Any]],
    plane_side_length: float,
    point_x_key: str = "enu_e",
    point_y_key: str = "enu_n",
    point_z_key: str = "enu_u",
) -> dict[str, list[dict[str, Any]]]:
    """Sammelt fuer jede Pi-Konfiguration alle Punkte innerhalb ihres Sichtvolumens."""
    points_by_pi: dict[str, list[dict[str, Any]]] = {}

    for pi in pi_setup:
        pi_id = str(pi.get("id", pi.get("name", "pi")))
        matching_points = []

        for point in points:
            if point_in_pi_view_volume(
                pi,
                point,
                plane_side_length,
                point_x_key=point_x_key,
                point_y_key=point_y_key,
                point_z_key=point_z_key,
            ):
                matching_points.append(point)

        points_by_pi[pi_id] = matching_points

    return points_by_pi


def points_in_view_volume_for_each_pi_with_origin(
    pi_setup: list[dict[str, Any]],
    points: list[dict[str, Any]],
    plane_side_length: float,
    point_x_key: str = "enu_e",
    point_y_key: str = "enu_n",
    point_z_key: str = "enu_u",
) -> dict[str, list[tuple[dict[str, Any], tuple[float, float, float], tuple[float, float, float]]]]:
    """Gibt pro Pi alle sichtbaren Punkte plus den Start- und Endpunkt des Vektors zurueck."""
    result: dict[str, list[tuple[dict[str, Any], tuple[float, float, float], tuple[float, float, float]]]] = {}

    for pi in pi_setup:
        pi_id = str(pi.get("id", pi.get("name", "pi")))
        origin = (
            to_float(pi.get("x"), 0.0),
            to_float(pi.get("y"), 0.0),
            to_float(pi.get("z"), 0.0),
        )
        matching = []

        for point in points:
            if point_in_pi_view_volume(
                pi,
                point,
                plane_side_length,
                point_x_key=point_x_key,
                point_y_key=point_y_key,
                point_z_key=point_z_key,
            ):
                end = (
                    to_float(point.get(point_x_key), 0.0),
                    to_float(point.get(point_y_key), 0.0),
                    to_float(point.get(point_z_key), 0.0),
                )
                matching.append((point, origin, end))

        result[pi_id] = matching

    return result
