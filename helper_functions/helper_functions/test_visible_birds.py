from webinterface import collect_visible_bird_positions
from helper_functions.pi_view_simulation import points_in_view_volume_for_each_pi_with_origin


def test_collect_visible_bird_positions_groups_points_by_bird():
    records = [
        {"bird_id": "bird_0001", "enu_e": 10, "enu_n": 20, "enu_u": 30},
        {"bird_id": "bird_0001", "enu_e": 11, "enu_n": 21, "enu_u": 31},
        {"bird_id": "bird_0002", "enu_e": 50, "enu_n": 60, "enu_u": 70},
    ]

    grouped = collect_visible_bird_positions(records)

    assert list(grouped.keys()) == ["bird_0001", "bird_0002"]
    assert grouped["bird_0001"] == [(10.0, 20.0, 30.0), (11.0, 21.0, 31.0)]
    assert grouped["bird_0002"] == [(50.0, 60.0, 70.0)]


def test_points_in_view_volume_for_each_pi_with_origin_keeps_origin_and_end():
    pi_setup = [{
        "id": "pi_1",
        "name": "Raspberry Pi Ursprung",
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "yaw_deg": 0.0,
        "pitch_deg": 45.0,
        "roll_deg": 0.0,
        "view_length": 400.0,
    }]
    records = [{"bird_id": "bird_0001", "enu_e": 100.0, "enu_n": 0.0, "enu_u": 100.0}]

    matching = points_in_view_volume_for_each_pi_with_origin(pi_setup, records, 600.0)

    assert "pi_1" in matching
    assert matching["pi_1"][0][1] == (0.0, 0.0, 0.0)
    assert matching["pi_1"][0][2] == (100.0, 0.0, 100.0)
