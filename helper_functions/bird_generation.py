import hashlib
import random
from datetime import datetime, timedelta
import math

def generate_birds(
    number_of_birds,
    time_interval,
    sim_date,
    x_min,
    x_max,
    y_min,
    y_max,
    z_min,
    z_max,
    min_speed,
    max_speed,
    position_noise,
    seed_value,
    initial_spread,
    variance_angle,
    variance_speed,
    variance_z,
    variance_vertical_speed,
):
    """
    Generiert simulierte Vogel-Flugbahnen.

    Die Struktur entspricht der bisherigen Streamlit-Anwendung.
    """
    seed = seed_value if seed_value is not None else datetime.now().timestamp()
    rng = random.Random(seed)

    records = []

    for bird_number in range(1, number_of_birds + 1):
        number_of_samples = rng.randint(3, 29)

        start_time = sim_date.replace(
            hour=rng.randint(0, 23),
            minute=rng.randint(0, 30),
            second=0,
        )

        x = rng.uniform(x_min, x_max) + rng.uniform(
            -initial_spread,
            initial_spread,
        )
        y = rng.uniform(y_min, y_max) + rng.uniform(
            -initial_spread,
            initial_spread,
        )
        z = rng.uniform(z_min, z_max)

        angle = rng.uniform(0, 2 * math.pi)
        speed = rng.uniform(min_speed, max_speed)
        vertical_speed = rng.uniform(-0.05, 0.05)

        for sample_index in range(number_of_samples):
            for _ in range(time_interval):
                angle += rng.gauss(0, variance_angle)

                speed += rng.gauss(0, variance_speed)
                speed = max(min_speed, min(max_speed, speed))

                x += math.cos(angle) * speed
                y += math.sin(angle) * speed

                z += vertical_speed
                z += rng.gauss(0, variance_z)
                z = max(z_min, min(z_max, z))

                vertical_speed += rng.gauss(
                    0,
                    variance_vertical_speed,
                )

            bird_id = f"bird_{bird_number:04d}"

            record = {
                "bird_id": bird_id,
                "determined_bird_id": "bird_0000",
                "timestamp": (
                    start_time
                    + timedelta(seconds=sample_index * time_interval)
                ).isoformat()
                + "Z",
                "enu_e": round(
                    x + rng.gauss(0, position_noise),
                    2,
                ),
                "enu_n": round(
                    y + rng.gauss(0, position_noise),
                    2,
                ),
                "enu_u": round(
                    z + rng.gauss(0, 0.03),
                    2,
                ),
            }

            records.append(record)

    return records

# ============================================================================
# HILFSFUNKTIONEN: GENERIERUNG
# ============================================================================
def parse_seed(seed_value):
    """
    Wandelt einen Seed in einen reproduzierbaren Integer um.

    Zahlen werden direkt verwendet.
    Text wird stabil über SHA-256 in einen Integer umgewandelt.
    """
    if seed_value is None:
        return None

    seed_text = str(seed_value).strip()

    if not seed_text:
        return None

    try:
        return int(seed_text)
    except ValueError:
        digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], byteorder="big")


# ============================================================================
# HILFSFUNKTIONEN: VALIDIERUNG
# ============================================================================
def as_float(value, label):
    """Liest eine Fließkommazahl ein."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} muss eine gültige Zahl sein.") from None

    if not math.isfinite(number):
        raise ValueError(f"{label} muss eine endliche Zahl sein.")

    return number


def as_integer(value, label):
    """Liest eine Ganzzahl ein."""
    number = as_float(value, label)

    if not number.is_integer():
        raise ValueError(f"{label} muss eine ganze Zahl sein.")

    return int(number)


def parse_generation_parameters(
    number_of_birds_value,
    time_interval_value,
    simulation_date_value,
    seed_text_value,
    x_min_value,
    x_max_value,
    y_min_value,
    y_max_value,
    z_min_value,
    z_max_value,
    initial_spread_value,
    min_speed_value,
    max_speed_value,
    position_noise_value,
    variance_angle_value,
    variance_speed_value,
    variance_z_value,
    variance_vertical_speed_value,
):
    """Liest und validiert alle Eingabewerte aus dem Formular."""
    number_of_birds = as_integer(
        number_of_birds_value,
        "Anzahl Vögel",
    )
    time_interval = as_integer(
        time_interval_value,
        "Messabstand",
    )

    try:
        simulation_date = datetime.combine(
            datetime.fromisoformat(
                str(simulation_date_value)
            ).date(),
            datetime.min.time(),
        )
    except (TypeError, ValueError):
        raise ValueError(
            "Bitte ein gültiges Simulationsdatum auswählen."
        ) from None

    x_min = as_float(x_min_value, "X Min")
    x_max = as_float(x_max_value, "X Max")
    y_min = as_float(y_min_value, "Y Min")
    y_max = as_float(y_max_value, "Y Max")
    z_min = as_float(z_min_value, "Z Min")
    z_max = as_float(z_max_value, "Z Max")

    initial_spread = as_float(
        initial_spread_value,
        "Initiale Streuung",
    )

    min_speed = as_float(
        min_speed_value,
        "Minimale Geschwindigkeit",
    )
    max_speed = as_float(
        max_speed_value,
        "Maximale Geschwindigkeit",
    )

    position_noise = as_float(
        position_noise_value,
        "Positionsrauschen",
    )

    variance_angle = as_float(
        variance_angle_value,
        "Richtungsänderung",
    )

    variance_speed = as_float(
        variance_speed_value,
        "Geschwindigkeitsänderung",
    )

    variance_z = as_float(
        variance_z_value,
        "Vertikale Bewegung",
    )

    variance_vertical_speed = as_float(
        variance_vertical_speed_value,
        "Steiggeschwindigkeit",
    )

    if not 1 <= number_of_birds <= 500:
        raise ValueError("Die Anzahl der Vögel muss zwischen 1 und 500 liegen.")

    if not 1 <= time_interval <= 60:
        raise ValueError("Der Messabstand muss zwischen 1 und 60 Sekunden liegen.")

    if x_min > x_max:
        raise ValueError("X Min darf nicht größer als X Max sein.")

    if y_min > y_max:
        raise ValueError("Y Min darf nicht größer als Y Max sein.")

    if z_min > z_max:
        raise ValueError("Z Min darf nicht größer als Z Max sein.")

    if min_speed > max_speed:
        raise ValueError(
            "Die minimale Geschwindigkeit darf nicht größer als "
            "die maximale Geschwindigkeit sein."
        )

    if not 0 <= initial_spread <= 2000:
        raise ValueError(
            "Die initiale Streuung muss zwischen 0 und 2000 liegen."
        )

    if not 0 <= position_noise <= 1:
        raise ValueError(
            "Das Positionsrauschen muss zwischen 0 und 1 liegen."
        )

    if not 0 <= variance_angle <= 0.3:
        raise ValueError(
            "Die Richtungsänderung muss zwischen 0 und 0.3 liegen."
        )

    if not 0 <= variance_speed <= 1:
        raise ValueError(
            "Die Geschwindigkeitsänderung muss zwischen 0 und 1 liegen."
        )

    if not 0 <= variance_z <= 1:
        raise ValueError(
            "Die vertikale Bewegung muss zwischen 0 und 1 liegen."
        )

    if not 0 <= variance_vertical_speed <= 0.1:
        raise ValueError(
            "Die Steiggeschwindigkeit muss zwischen 0 und 0.1 liegen."
        )

    return {
        "number_of_birds": number_of_birds,
        "time_interval": time_interval,
        "sim_date": simulation_date,
        "x_min": x_min,
        "x_max": x_max,
        "y_min": y_min,
        "y_max": y_max,
        "z_min": z_min,
        "z_max": z_max,
        "min_speed": min_speed,
        "max_speed": max_speed,
        "position_noise": position_noise,
        "seed_value": parse_seed(seed_text_value),
        "initial_spread": initial_spread,
        "variance_angle": variance_angle,
        "variance_speed": variance_speed,
        "variance_z": variance_z,
        "variance_vertical_speed": variance_vertical_speed,
    }
