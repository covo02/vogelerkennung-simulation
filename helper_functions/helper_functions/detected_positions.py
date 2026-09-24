import json
from pathlib import Path

import plotly.graph_objects as go

from helper_functions.pi_view_simulation import square_plane_basis, to_float


VECTOR_OVERLAP_TOLERANCE = 5.0


def _load_json_payload(json_path: Path) -> dict | list | None:
    if not json_path.is_file():
        return None

    with json_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_detected_positions_payload(detected_positions_json: Path) -> dict | None:
    payload = _load_json_payload(detected_positions_json)
    return payload if isinstance(payload, dict) else None


def save_detected_positions_payload(detected_positions_json: Path, payload: dict) -> None:
    with detected_positions_json.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def build_detected_positions_payload(
    bird_images_json: Path,
    overlap_tolerance: float = VECTOR_OVERLAP_TOLERANCE,
) -> dict | None:
    # Die Vektorbestimmung basiert ausschliesslich auf vogel_bilder.json.
    payload = _load_json_payload(bird_images_json)
    if not isinstance(payload, dict):
        return None

    pi_images = payload.get("pi_images")
    if not isinstance(pi_images, list) or not pi_images:
        return None

    detected_vectors: list[dict] = []

    for pi_entry in pi_images:
        if not isinstance(pi_entry, dict):
            continue

        pi_id = str(pi_entry.get("pi_id", "pi"))
        pi_position = pi_entry.get("pi_position", {})
        view_direction = pi_entry.get("view_direction_vector", {})

        origin_x = to_float(pi_position.get("x"), 0.0)
        origin_y = to_float(pi_position.get("y"), 0.0)
        origin_z = to_float(pi_position.get("z"), 0.0)
        view_x = to_float(view_direction.get("x"), 0.0)
        view_y = to_float(view_direction.get("y"), 0.0)
        view_z = to_float(view_direction.get("z"), 0.0)

        _direction_unit, basis_u, basis_v = square_plane_basis(view_x, view_y, view_z)

        plane_center_x = origin_x + view_x
        plane_center_y = origin_y + view_y
        plane_center_z = origin_z + view_z

        projected_points = pi_entry.get("projected_points")
        if not isinstance(projected_points, list):
            continue

        for point in projected_points:
            if not isinstance(point, dict):
                continue

            image_x = to_float(point.get("image_x"), 0.0)
            image_y = to_float(point.get("image_y"), 0.0)

            target_x = plane_center_x + basis_u[0] * image_x + basis_v[0] * image_y
            target_y = plane_center_y + basis_u[1] * image_x + basis_v[1] * image_y
            target_z = plane_center_z + basis_u[2] * image_x + basis_v[2] * image_y

            detected_vectors.append(
                {
                    "pi_id": pi_id,
                    "timestamp": str(point.get("timestamp", "")),
                    "origin": {
                        "x": round(origin_x, 6),
                        "y": round(origin_y, 6),
                        "z": round(origin_z, 6),
                    },
                    "vector": {
                        "x": round(target_x - origin_x, 6),
                        "y": round(target_y - origin_y, 6),
                        "z": round(target_z - origin_z, 6),
                    },
                }
            )

    overlaps = find_vector_overlaps_by_timestamp(detected_vectors, overlap_tolerance)

    return {
        "meta": {
            "source_file": bird_images_json.name,
            "vector_count": len(detected_vectors),
            "overlap_count": len(overlaps),
            "overlap_tolerance": overlap_tolerance,
        },
        "detected_vectors": detected_vectors,
        "overlaps": overlaps,
    }


def solve_linear_3x3(matrix: list[list[float]], rhs: list[float]) -> tuple[float, float, float] | None:
    augmented = [row[:] + [rhs_value] for row, rhs_value in zip(matrix, rhs)]

    for pivot_index in range(3):
        pivot_row = max(range(pivot_index, 3), key=lambda row_index: abs(augmented[row_index][pivot_index]))
        pivot_value = augmented[pivot_row][pivot_index]
        if abs(pivot_value) <= 1e-12:
            return None

        if pivot_row != pivot_index:
            augmented[pivot_index], augmented[pivot_row] = augmented[pivot_row], augmented[pivot_index]

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


def point_line_distance(
    point: tuple[float, float, float],
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
) -> float:
    rel_x = point[0] - origin[0]
    rel_y = point[1] - origin[1]
    rel_z = point[2] - origin[2]

    cross_x = rel_y * direction[2] - rel_z * direction[1]
    cross_y = rel_z * direction[0] - rel_x * direction[2]
    cross_z = rel_x * direction[1] - rel_y * direction[0]

    return (cross_x * cross_x + cross_y * cross_y + cross_z * cross_z) ** 0.5


def estimate_overlap_for_vectors(vectors: list[dict], tolerance: float) -> dict | None:
    lines: list[tuple[str, tuple[float, float, float], tuple[float, float, float]]] = []

    for vector in vectors:
        if not isinstance(vector, dict):
            continue

        pi_id = str(vector.get("pi_id", "pi"))
        origin_raw = vector.get("origin", {})
        direction_raw = vector.get("vector", {})

        origin = (
            to_float(origin_raw.get("x"), 0.0),
            to_float(origin_raw.get("y"), 0.0),
            to_float(origin_raw.get("z"), 0.0),
        )
        direction = (
            to_float(direction_raw.get("x"), 0.0),
            to_float(direction_raw.get("y"), 0.0),
            to_float(direction_raw.get("z"), 0.0),
        )

        magnitude = (direction[0] ** 2 + direction[1] ** 2 + direction[2] ** 2) ** 0.5
        if magnitude <= 1e-12:
            continue

        unit_direction = (
            direction[0] / magnitude,
            direction[1] / magnitude,
            direction[2] / magnitude,
        )
        lines.append((pi_id, origin, unit_direction))

    if len(lines) < 2:
        return None

    matrix = [
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
    ]
    rhs = [0.0, 0.0, 0.0]

    for _pi_id, origin, direction in lines:
        projection = [
            [direction[0] * direction[0], direction[0] * direction[1], direction[0] * direction[2]],
            [direction[1] * direction[0], direction[1] * direction[1], direction[1] * direction[2]],
            [direction[2] * direction[0], direction[2] * direction[1], direction[2] * direction[2]],
        ]

        for row_index in range(3):
            for col_index in range(3):
                value = (1.0 if row_index == col_index else 0.0) - projection[row_index][col_index]
                matrix[row_index][col_index] += value

            rhs[row_index] += (
                ((1.0 if row_index == 0 else 0.0) - projection[row_index][0]) * origin[0]
                + ((1.0 if row_index == 1 else 0.0) - projection[row_index][1]) * origin[1]
                + ((1.0 if row_index == 2 else 0.0) - projection[row_index][2]) * origin[2]
            )

    overlap_point = solve_linear_3x3(matrix, rhs)
    if overlap_point is None:
        return None

    residuals = []
    max_distance = 0.0

    for pi_id, origin, direction in lines:
        distance = point_line_distance(overlap_point, origin, direction)
        max_distance = max(max_distance, distance)
        residuals.append(
            {
                "pi_id": pi_id,
                "distance": round(distance, 6),
            }
        )

    if max_distance > tolerance:
        return None

    return {
        "timestamp": str(vectors[0].get("timestamp", "")),
        "point": {
            "x": round(overlap_point[0], 6),
            "y": round(overlap_point[1], 6),
            "z": round(overlap_point[2], 6),
        },
        "pis": sorted({pi_id for pi_id, _origin, _direction in lines}),
        "max_distance": round(max_distance, 6),
        "residuals": residuals,
    }


def find_vector_overlaps_by_timestamp(detected_vectors: list[dict], tolerance: float) -> list[dict]:
    vectors_by_timestamp: dict[str, list[dict]] = {}

    for vector in detected_vectors:
        if not isinstance(vector, dict):
            continue
        timestamp = str(vector.get("timestamp", "")).strip()
        if not timestamp:
            continue
        vectors_by_timestamp.setdefault(timestamp, []).append(vector)

    overlaps = []
    for timestamp in sorted(vectors_by_timestamp):
        overlap = estimate_overlap_for_vectors(vectors_by_timestamp[timestamp], tolerance)
        if overlap is not None:
            overlaps.append(overlap)

    return overlaps


def add_detected_vectors_to_figure(fig: go.Figure, payload: dict | None) -> int:
    if not isinstance(payload, dict):
        return 0

    vectors = payload.get("detected_vectors")
    if not isinstance(vectors, list) or not vectors:
        return 0

    vectors_by_pi: dict[str, list[dict]] = {}
    for vector in vectors:
        if not isinstance(vector, dict):
            continue
        pi_id = str(vector.get("pi_id", "pi"))
        vectors_by_pi.setdefault(pi_id, []).append(vector)

    for pi_id, pi_vectors in vectors_by_pi.items():
        line_x: list[float | None] = []
        line_y: list[float | None] = []
        line_z: list[float | None] = []
        origin_x = 0.0
        origin_y = 0.0
        origin_z = 0.0

        for vector in pi_vectors:
            origin = vector.get("origin", {})
            vector_values = vector.get("vector", {})

            origin_x = to_float(origin.get("x"), 0.0)
            origin_y = to_float(origin.get("y"), 0.0)
            origin_z = to_float(origin.get("z"), 0.0)
            vector_x = to_float(vector_values.get("x"), 0.0)
            vector_y = to_float(vector_values.get("y"), 0.0)
            vector_z = to_float(vector_values.get("z"), 0.0)
            end_x = origin_x + vector_x
            end_y = origin_y + vector_y
            end_z = origin_z + vector_z

            line_x.extend([origin_x, end_x, None])
            line_y.extend([origin_y, end_y, None])
            line_z.extend([origin_z, end_z, None])

        fig.add_trace(
            go.Scatter3d(
                x=line_x,
                y=line_y,
                z=line_z,
                mode="lines",
                line=dict(width=2, color="#34d399"),
                name=f"{pi_id} Vektoren",
                showlegend=True,
                hovertemplate=(
                    f"<b>{pi_id}</b><br>"
                    "X: %{x}<br>"
                    "Y: %{y}<br>"
                    "Z: %{z}<extra></extra>"
                ),
            )
        )

        fig.add_trace(
            go.Scatter3d(
                x=[origin_x],
                y=[origin_y],
                z=[origin_z],
                mode="markers",
                marker=dict(size=6, color="#ef4444", symbol="diamond"),
                name=f"{pi_id} Ursprung",
                showlegend=True,
                hovertemplate=(
                    f"<b>{pi_id}</b><br>"
                    "Pi-Position<br>"
                    "X: %{x}<br>"
                    "Y: %{y}<br>"
                    "Z: %{z}<extra></extra>"
                ),
            )
        )

    overlaps = payload.get("overlaps")
    if isinstance(overlaps, list) and overlaps:
        overlap_x: list[float] = []
        overlap_y: list[float] = []
        overlap_z: list[float] = []
        overlap_text: list[str] = []

        for overlap in overlaps:
            if not isinstance(overlap, dict):
                continue
            point = overlap.get("point", {})
            if not isinstance(point, dict):
                continue

            timestamp = str(overlap.get("timestamp", ""))
            max_distance = to_float(overlap.get("max_distance"), 0.0)

            overlap_x.append(to_float(point.get("x"), 0.0))
            overlap_y.append(to_float(point.get("y"), 0.0))
            overlap_z.append(to_float(point.get("z"), 0.0))
            overlap_text.append(f"Zeit: {timestamp}<br>Abstand: {max_distance:.3f}")

        if overlap_x:
            fig.add_trace(
                go.Scatter3d(
                    x=overlap_x,
                    y=overlap_y,
                    z=overlap_z,
                    mode="markers",
                    marker=dict(size=7, color="#facc15", symbol="circle"),
                    name="Ueberlappungspunkt",
                    showlegend=True,
                    text=overlap_text,
                    hovertemplate="%{text}<br>X: %{x}<br>Y: %{y}<br>Z: %{z}<extra></extra>",
                )
            )

    return len(vectors)
