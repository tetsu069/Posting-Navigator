from shapely.geometry import LineString
from posting_navigator.routing import build_navigation_legs


def step(seq, coords, length, name=""):
    return {"seq": seq, "geometry": LineString(coords), "length_m": length, "name": name,
            "transfer": False, "duplicated": False}


def test_micro_leg_is_not_a_standalone_instruction():
    steps = [
        step(1, [(139.0,35.0),(139.00025,35.0)], 23, "A"),
        step(2, [(139.00025,35.0),(139.00025,35.00001)], 1, ""),
        step(3, [(139.00025,35.00001),(139.00025,35.00025)], 26, "B"),
    ]
    legs = build_navigation_legs(steps)
    assert all(l["length_m"] >= 7 for l in legs)
    assert len(legs) <= 2


def test_no_one_meter_turnaround_noise():
    steps = [
        step(1, [(139.0,35.0),(139.0002,35.0)], 18),
        step(2, [(139.0002,35.0),(139.0002,35.00001)], 1),
        step(3, [(139.0002,35.00001),(139.0002,35.0002)], 20),
    ]
    legs = build_navigation_legs(steps)
    assert not any(l["length_m"] <= 2 and l["turn"] == "折り返し" for l in legs)
