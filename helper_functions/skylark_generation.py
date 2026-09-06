from datetime import timedelta
import hashlib
import math
import random


def generate_skylarks(
    number_of_larks,
    time_interval,
    sim_date,
    x_min,
    x_max,
    y_min,
    y_max,
    z_min,
    z_max,
    position_noise,
    seed_value,
    min_flight_duration=180,
    max_flight_duration=480,
    min_song_height=50.0,
    max_song_height=120.0,
):
    """
    Generiert Feldlerchen-Singflüge um ein Revier-/Nestzentrum.

    Ablauf:
    - Steigflug mit größer werdenden Kreisen
    - Kreis-/Singflug in größerer Höhe
    - spiralförmiger Sinkflug zurück zum Ausgangspunkt
    """
    if number_of_larks < 0:
        raise ValueError("Die Anzahl der Feldlerchen darf nicht negativ sein.")

    if number_of_larks == 0:
        return []

    if time_interval <= 0:
        raise ValueError("Der Messabstand muss größer als 0 sein.")

    if x_min > x_max or y_min > y_max:
        raise ValueError("Ungültige horizontale Grenzen.")

    if z_max <= z_min:
        raise ValueError(
            "Für Feldlerchen muss Z Max größer als Z Min sein."
        )

    if position_noise < 0:
        raise ValueError("Das Positionsrauschen darf nicht negativ sein.")

    if not 0 < min_flight_duration <= max_flight_duration:
        raise ValueError("Ungültige Flugdauer für Feldlerchen.")

    if not 0 < min_song_height <= max_song_height:
        raise ValueError("Ungültige Flughöhe für Feldlerchen.")

    # Separater Seed: Normale Vögel bleiben bei gleichem Seed unverändert.
    if seed_value is None:
        rng = random.Random()
    else:
        lark_seed = int.from_bytes(
            hashlib.sha256(
                f"{seed_value}:skylarks".encode("utf-8")
            ).digest()[:8],
            byteorder="big",
        )
        rng = random.Random(lark_seed)

    def clamp(value, lower, upper):
        return max(lower, min(upper, value))

    def ease(progress):
        """Sanfter Übergang von 0 nach 1."""
        return 0.5 - 0.5 * math.cos(math.pi * progress)

    def random_center(lower, upper, margin):
        if upper - lower <= 2 * margin:
            return (lower + upper) / 2

        return rng.uniform(lower + margin, upper - margin)

    records = []

    horizontal_radius_limit = max(
        0.0,
        min(
            70.0,
            (x_max - x_min) / 2 - 3 * position_noise,
            (y_max - y_min) / 2 - 3 * position_noise,
        ),
    )

    for lark_number in range(1, number_of_larks + 1):
        nest_margin = horizontal_radius_limit + 3 * position_noise

        nest_x = random_center(x_min, x_max, nest_margin)
        nest_y = random_center(y_min, y_max, nest_margin)

        orbit_radius = rng.uniform(
            horizontal_radius_limit * 0.55,
            horizontal_radius_limit * 0.85,
        )

        z_range = z_max - z_min
        takeoff_z = z_min + rng.uniform(
            0.0,
            min(5.0, z_range * 0.1),
        )

        available_height = z_max - takeoff_z

        if available_height * 0.95 >= min_song_height:
            peak_gain = rng.uniform(
                min_song_height,
                min(max_song_height, available_height * 0.95),
            )
        else:
            # Falls der simulierte Luftraum kleiner ist als 50 m.
            peak_gain = rng.uniform(
                available_height * 0.6,
                available_height * 0.9,
            )

        peak_z = takeoff_z + peak_gain
        vertical_wobble = min(4.0, peak_gain * 0.04)

        duration = rng.uniform(
            min_flight_duration,
            max_flight_duration,
        )
        number_of_samples = max(
            3,
            int(round(duration / time_interval)) + 1,
        )

        climb_end = rng.uniform(0.26, 0.36)
        descent_start = rng.uniform(0.62, 0.74)

        turns = rng.uniform(2.5, 5.0)
        initial_angle = rng.uniform(0, 2 * math.pi)
        direction = rng.choice((-1, 1))
        wave_phase = rng.uniform(0, 2 * math.pi)

        start_time = sim_date.replace(
            hour=rng.randint(5, 19),
            minute=rng.randint(0, 59),
            second=0,
            microsecond=0,
        )

        bird_id = f"lark_{lark_number:04d}"

        for sample_index in range(number_of_samples):
            phase = sample_index / (number_of_samples - 1)

            if phase <= climb_end:
                local_phase = phase / climb_end
                climb_progress = ease(local_phase)

                altitude = takeoff_z + peak_gain * climb_progress
                radius = orbit_radius * climb_progress

            elif phase <= descent_start:
                local_phase = (
                    (phase - climb_end)
                    / (descent_start - climb_end)
                )

                altitude = peak_z + vertical_wobble * (
                    math.sin(math.pi * local_phase)
                    * math.sin(
                        4 * math.pi * local_phase + wave_phase
                    )
                )
                radius = orbit_radius

            else:
                local_phase = (
                    (phase - descent_start)
                    / (1 - descent_start)
                )
                descent_progress = 1 - ease(local_phase)

                altitude = takeoff_z + peak_gain * descent_progress
                altitude += 0.75 * vertical_wobble * (
                    math.sin(math.pi * local_phase)
                    * math.sin(
                        6 * math.pi * local_phase + wave_phase
                    )
                )
                radius = orbit_radius * descent_progress

            angle = initial_angle + direction * (
                2 * math.pi * turns * phase
                + 0.2 * math.sin(2 * math.pi * phase)
            )

            x = nest_x + radius * math.cos(angle)
            y = nest_y + radius * math.sin(angle)

            records.append(
                {
                    "bird_id": bird_id,
                    "determined_bird_id": "bird_0000",
                    "timestamp": (
                        start_time
                        + timedelta(
                            seconds=sample_index * time_interval
                        )
                    ).isoformat()
                    + "Z",
                    "enu_e": round(
                        clamp(
                            x + rng.gauss(0, position_noise),
                            x_min,
                            x_max,
                        ),
                        2,
                    ),
                    "enu_n": round(
                        clamp(
                            y + rng.gauss(0, position_noise),
                            y_min,
                            y_max,
                        ),
                        2,
                    ),
                    "enu_u": round(
                        clamp(
                            altitude + rng.gauss(0, 0.03),
                            z_min,
                            z_max,
                        ),
                        2,
                    ),
                }
            )

    return records