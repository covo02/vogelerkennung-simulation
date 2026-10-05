import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple


INPUT_FILE = "vogel_position_erkannt.json"
OUTPUT_FILE = "vogel_flugbahnen_determined.json"

# Parameter: an reale Messdaten und Koordinateneinheit anpassen
MAX_GAP_SECONDS = 10          # Maximale Zeitlücke innerhalb eines Tracks
MAX_SPEED_UNITS_PER_SEC = 25  # Maximal plausible Geschwindigkeit
DISTANCE_MARGIN = 20          # Zusätzlicher Toleranzpuffer


def parse_timestamp(timestamp: str) -> datetime:
    """Wandelt einen ISO-8601-Zeitstempel in ein datetime-Objekt um."""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def euclidean_distance(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float]
) -> float:
    """Berechnet die euklidische Distanz zwischen zwei 3D-Punkten."""
    return math.sqrt(
        (a[0] - b[0]) ** 2
        + (a[1] - b[1]) ** 2
        + (a[2] - b[2]) ** 2
    )


@dataclass
class Observation:
    idx: int
    timestamp: datetime
    x: float
    y: float
    z: float
    raw: dict


@dataclass
class Track:
    track_id: int
    last_ts: datetime = None
    last_pos: Tuple[float, float, float] = None
    last_vel: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    def predict_position(self, timestamp: datetime) -> Tuple[float, float, float]:
        """Sagt die aktuelle Position anhand der zuletzt geschätzten Geschwindigkeit voraus."""
        if self.last_ts is None or self.last_pos is None:
            return 0.0, 0.0, 0.0

        dt = (timestamp - self.last_ts).total_seconds()

        return (
            self.last_pos[0] + self.last_vel[0] * dt,
            self.last_pos[1] + self.last_vel[1] * dt,
            self.last_pos[2] + self.last_vel[2] * dt,
        )

    def update(self, observation: Observation, velocity_smoothing: float = 0.7) -> None:
        """Aktualisiert den Track mit einer neuen Beobachtung."""
        new_pos = (observation.x, observation.y, observation.z)

        if self.last_ts is None:
            self.last_ts = observation.timestamp
            self.last_pos = new_pos
            return

        dt = (observation.timestamp - self.last_ts).total_seconds()

        if dt > 0:
            measured_vel = (
                (new_pos[0] - self.last_pos[0]) / dt,
                (new_pos[1] - self.last_pos[1]) / dt,
                (new_pos[2] - self.last_pos[2]) / dt,
            )

            self.last_vel = (
                velocity_smoothing * measured_vel[0]
                + (1 - velocity_smoothing) * self.last_vel[0],

                velocity_smoothing * measured_vel[1]
                + (1 - velocity_smoothing) * self.last_vel[1],

                velocity_smoothing * measured_vel[2]
                + (1 - velocity_smoothing) * self.last_vel[2],
            )

        self.last_ts = observation.timestamp
        self.last_pos = new_pos


def load_input_records(path: str = INPUT_FILE) -> List[dict]:
    """Lädt die 3D-Punkte aus der erkannten Positionsdatei."""
    with open(path, "r", encoding="utf-8") as file:
        payload = json.load(file)

    overlaps = payload.get("overlaps", [])
    if isinstance(overlaps, list) and overlaps:
        records: List[dict] = []
        for overlap in overlaps:
            point = overlap.get("point", {})
            if not isinstance(point, dict):
                continue

            timestamp = overlap.get("timestamp")
            if not timestamp:
                continue

            records.append(
                {
                    "timestamp": timestamp,
                    "enu_e": float(point.get("x", 0.0)),
                    "enu_n": float(point.get("y", 0.0)),
                    "enu_u": float(point.get("z", 0.0)),
                }
            )

        if records:
            return records

    legacy_records = payload.get("simulated_birds", [])
    if isinstance(legacy_records, list) and legacy_records:
        return legacy_records

    raise ValueError(
        "Keine 3D-Punkte in der erkannten Positionsdatei gefunden. "
        "Erwartet ein Feld 'overlaps' mit 'point'."
    )


def load_observations(records: List[dict]) -> List[Observation]:
    """Lädt und zeitlich sortiert Beobachtungen."""
    observations = []

    for idx, record in enumerate(records):
        observations.append(
            Observation(
                idx=idx,
                timestamp=parse_timestamp(record["timestamp"]),
                x=float(record["enu_e"]),
                y=float(record["enu_n"]),
                z=float(record["enu_u"]),
                raw=record,
            )
        )

    observations.sort(key=lambda observation: (observation.timestamp, observation.idx))
    return observations


def determine_tracks(records: List[dict]) -> List[dict]:
    """
    Ermittelt Trajektorien aus Zeitstempeln und ENU-Koordinaten.

    Die Funktion benötigt keine bird_id.
    Jede Beobachtung erhält eine determined_track_id.
    """
    observations = load_observations(records)

    observations_by_timestamp = defaultdict(list)
    for observation in observations:
        observations_by_timestamp[observation.timestamp].append(observation)

    active_tracks: Dict[int, Track] = {}
    assignment_by_observation_idx: Dict[int, int] = {}

    next_track_id = 1

    for current_timestamp in sorted(observations_by_timestamp):
        current_observations = observations_by_timestamp[current_timestamp]

        # Tracks schließen, deren letzte Beobachtung zu lange zurückliegt.
        stale_track_ids = [
            track_id
            for track_id, track in active_tracks.items()
            if (current_timestamp - track.last_ts).total_seconds() > MAX_GAP_SECONDS
        ]

        for track_id in stale_track_ids:
            del active_tracks[track_id]

        # Mögliche Track-Beobachtungs-Zuordnungen bestimmen.
        candidates = []

        for track_id, track in active_tracks.items():
            dt = (current_timestamp - track.last_ts).total_seconds()

            if dt <= 0 or dt > MAX_GAP_SECONDS:
                continue

            predicted_pos = track.predict_position(current_timestamp)
            max_allowed_distance = MAX_SPEED_UNITS_PER_SEC * dt + DISTANCE_MARGIN

            for observation_index, observation in enumerate(current_observations):
                observation_pos = (observation.x, observation.y, observation.z)
                distance = euclidean_distance(predicted_pos, observation_pos)

                if distance <= max_allowed_distance:
                    candidates.append((distance, track_id, observation_index))

        # Greedy-Zuordnung: kürzeste Distanz wird zuerst vergeben.
        assigned_tracks = set()
        assigned_observations = set()

        for _, track_id, observation_index in sorted(candidates, key=lambda item: item[0]):
            if track_id in assigned_tracks or observation_index in assigned_observations:
                continue

            observation = current_observations[observation_index]
            active_tracks[track_id].update(observation)

            assignment_by_observation_idx[observation.idx] = track_id
            assigned_tracks.add(track_id)
            assigned_observations.add(observation_index)

        # Nicht zugeordnete Beobachtungen starten jeweils einen neuen Track.
        for observation_index, observation in enumerate(current_observations):
            if observation_index in assigned_observations:
                continue

            new_track = Track(track_id=next_track_id)
            new_track.update(observation)

            active_tracks[next_track_id] = new_track
            assignment_by_observation_idx[observation.idx] = next_track_id

            next_track_id += 1

    # Track-ID in die ursprünglichen Datensätze schreiben.
    for observation in observations:
        track_id = assignment_by_observation_idx[observation.idx]
        observation.raw["determined_track_id"] = f"track_{track_id:04d}"

    return records


def main() -> None:
    records = load_input_records(INPUT_FILE)

    if not records:
        raise ValueError(
            "Keine Beobachtungen gefunden. "
            "Die Eingabedatei muss ein Feld 'overlaps' mit 3D-Punkten enthalten."
        )

    determined_records = determine_tracks(records)

    output = {
        "observations": determined_records
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(determined_records, file, ensure_ascii=False, indent=2)

    track_ids = {
        record["determined_track_id"]
        for record in determined_records
    }

    print(f"Verarbeitete Beobachtungen: {len(determined_records)}")
    print(f"Ermittelte Trajektorien: {len(track_ids)}")
    print(f"Ergebnis gespeichert in: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()