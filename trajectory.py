import json
import math
from dataclasses import dataclass, field
from collections import defaultdict, Counter
from datetime import datetime
from typing import Dict, List, Tuple


INPUT_FILE = "vogel_flugbahnen.json"
OUTPUT_FILE = "vogel_flugbahnen_determined.json"

# Parameter: ggf. an deine Simulation anpassen
MAX_GAP_SECONDS = 10          # maximal erlaubte Zeitlücke innerhalb einer Trajektorie
MAX_SPEED_UNITS_PER_SEC = 25  # maximal plausible Geschwindigkeit
DISTANCE_MARGIN = 20          # zusätzlicher Toleranzpuffer


def parse_timestamp(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def euclidean_distance(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return math.sqrt(
        (a[0] - b[0]) ** 2 +
        (a[1] - b[1]) ** 2 +
        (a[2] - b[2]) ** 2
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
    point_indices: List[int] = field(default_factory=list)
    last_ts: datetime = None
    last_pos: Tuple[float, float, float] = None
    last_vel: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    def predict_position(self, ts: datetime) -> Tuple[float, float, float]:
        if self.last_ts is None or self.last_pos is None:
            return (0.0, 0.0, 0.0)

        dt = (ts - self.last_ts).total_seconds()
        return (
            self.last_pos[0] + self.last_vel[0] * dt,
            self.last_pos[1] + self.last_vel[1] * dt,
            self.last_pos[2] + self.last_vel[2] * dt,
        )

    def update(self, obs: Observation, velocity_smoothing: float = 0.7) -> None:
        new_pos = (obs.x, obs.y, obs.z)

        if self.last_ts is None:
            self.last_ts = obs.timestamp
            self.last_pos = new_pos
            self.point_indices.append(obs.idx)
            return

        dt = (obs.timestamp - self.last_ts).total_seconds()
        if dt > 0:
            measured_vel = (
                (new_pos[0] - self.last_pos[0]) / dt,
                (new_pos[1] - self.last_pos[1]) / dt,
                (new_pos[2] - self.last_pos[2]) / dt,
            )
            self.last_vel = (
                velocity_smoothing * measured_vel[0] + (1 - velocity_smoothing) * self.last_vel[0],
                velocity_smoothing * measured_vel[1] + (1 - velocity_smoothing) * self.last_vel[1],
                velocity_smoothing * measured_vel[2] + (1 - velocity_smoothing) * self.last_vel[2],
            )

        self.last_ts = obs.timestamp
        self.last_pos = new_pos
        self.point_indices.append(obs.idx)


def load_observations(records: List[dict]) -> List[Observation]:
    observations = []
    for idx, r in enumerate(records):
        observations.append(
            Observation(
                idx=idx,
                timestamp=parse_timestamp(r["timestamp"]),
                x=float(r["enu_e"]),
                y=float(r["enu_n"]),
                z=float(r["enu_u"]),
                raw=r
            )
        )
    observations.sort(key=lambda o: (o.timestamp, o.idx))
    return observations


def determine_tracks(records: List[dict]) -> List[dict]:
    observations = load_observations(records)

    observations_by_ts = defaultdict(list)
    for obs in observations:
        observations_by_ts[obs.timestamp].append(obs)

    active_tracks: Dict[int, Track] = {}
    finished_tracks: List[Track] = []
    assignment_by_obs_idx: Dict[int, int] = {}

    next_track_id = 1

    for current_ts in sorted(observations_by_ts.keys()):
        current_observations = observations_by_ts[current_ts]

        # Zu alte Tracks schließen
        stale_track_ids = []
        for track_id, track in active_tracks.items():
            dt = (current_ts - track.last_ts).total_seconds()
            if dt > MAX_GAP_SECONDS:
                stale_track_ids.append(track_id)

        for track_id in stale_track_ids:
            finished_tracks.append(active_tracks.pop(track_id))

        # Kandidatenpaare (track, observation) mit Kosten = Distanz
        candidates = []
        for track_id, track in active_tracks.items():
            dt = (current_ts - track.last_ts).total_seconds()
            if dt <= 0 or dt > MAX_GAP_SECONDS:
                continue

            predicted_pos = track.predict_position(current_ts)
            max_allowed_distance = MAX_SPEED_UNITS_PER_SEC * dt + DISTANCE_MARGIN

            for obs_idx_within_ts, obs in enumerate(current_observations):
                obs_pos = (obs.x, obs.y, obs.z)
                dist = euclidean_distance(predicted_pos, obs_pos)

                if dist <= max_allowed_distance:
                    candidates.append((dist, track_id, obs_idx_within_ts))

        # Greedy Assignment: kleinste Distanz zuerst
        assigned_tracks = set()
        assigned_observations = set()

        for dist, track_id, obs_idx_within_ts in sorted(candidates, key=lambda x: x[0]):
            if track_id in assigned_tracks or obs_idx_within_ts in assigned_observations:
                continue

            obs = current_observations[obs_idx_within_ts]
            active_tracks[track_id].update(obs)

            assignment_by_obs_idx[obs.idx] = track_id
            assigned_tracks.add(track_id)
            assigned_observations.add(obs_idx_within_ts)

        # Nicht zugeordnete Beobachtungen starten neue Tracks
        for obs_idx_within_ts, obs in enumerate(current_observations):
            if obs_idx_within_ts in assigned_observations:
                continue

            track = Track(track_id=next_track_id)
            track.update(obs)
            active_tracks[next_track_id] = track
            assignment_by_obs_idx[obs.idx] = next_track_id
            next_track_id += 1

    finished_tracks.extend(active_tracks.values())

    # Rohe Track-ID ins Ergebnis schreiben
    for obs in observations:
        track_id = assignment_by_obs_idx[obs.idx]
        obs.raw["determined_track_id"] = f"track_{track_id:04d}"

    return records


def map_tracks_to_true_ids_for_evaluation(records: List[dict]) -> Tuple[List[dict], Dict[str, str], float]:
    """
    Mappt die vom Algorithmus gefundenen determined_track_id für die Auswertung
    auf die wahrscheinlichste echte bird_id.
    """
    track_to_true_counts: Dict[str, Counter] = defaultdict(Counter)

    for r in records:
        raw_track_id = r["determined_track_id"]
        true_bird_id = r["bird_id"]
        track_to_true_counts[raw_track_id][true_bird_id] += 1

    track_to_best_true = {}
    for raw_track_id, counter in track_to_true_counts.items():
        best_true_id, _ = counter.most_common(1)[0]
        track_to_best_true[raw_track_id] = best_true_id

    correct = 0
    for r in records:
        r["determined_bird_id"] = track_to_best_true[r["determined_track_id"]]
        if r["determined_bird_id"] == r["bird_id"]:
            correct += 1

    accuracy = correct / len(records) if records else 0.0
    return records, track_to_best_true, accuracy


def print_summary(records: List[dict], track_mapping: Dict[str, str], accuracy: float) -> None:
    total_points = len(records)
    raw_tracks = sorted(set(r["determined_track_id"] for r in records))
    true_birds = sorted(set(r["bird_id"] for r in records))

    print(f"Anzahl Punkte: {total_points}")
    print(f"Anzahl echte Vögel (bird_id): {len(true_birds)}")
    print(f"Anzahl gefundene Roh-Tracks: {len(raw_tracks)}")
    print(f"Punktgenauigkeit nach Mapping: {accuracy:.2%}")
    print()

    print("Zuordnung Roh-Track -> bird_id:")
    for raw_track in sorted(track_mapping.keys()):
        print(f"  {raw_track} -> {track_mapping[raw_track]}")

    # Zusätzliche Fragmentierungsanalyse
    true_to_tracks = defaultdict(set)
    for r in records:
        true_to_tracks[r["bird_id"]].add(r["determined_track_id"])

    print()
    print("Fragmentierung pro echtem Vogel:")
    for bird_id in sorted(true_to_tracks.keys()):
        print(f"  {bird_id}: {len(true_to_tracks[bird_id])} Track(s)")


def main() -> None:
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        records = json.load(f).get("simulated_birds")


    records = determine_tracks(records)
    records, track_mapping, accuracy = map_tracks_to_true_ids_for_evaluation(records)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print_summary(records, track_mapping, accuracy)
    print()
    print(f"Ergebnis gespeichert in: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()