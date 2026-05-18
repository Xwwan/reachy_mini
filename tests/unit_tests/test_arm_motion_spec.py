import math

from scripts.apply_arm_motion_spec import apply_arm_motion


def test_apply_arm_motion_preserves_source_fields_and_lengths() -> None:
    source = {
        "description": "demo",
        "time": [0.0, 0.5, 1.0],
        "set_target_data": [
            {"head": [[1]], "body_yaw": 0.1, "check_collision": False, "left_arm": [9, 9], "right_arm": [9, 9]},
            {"head": [[2]], "body_yaw": 0.2, "check_collision": True, "left_arm": [9, 9], "right_arm": [9, 9]},
            {"head": [[3]], "body_yaw": 0.3, "check_collision": False, "left_arm": [9, 9], "right_arm": [9, 9]},
        ],
    }
    spec = {
        "units": "deg",
        "keyframes": [
            {"time": 0.0, "left_arm": [0, 0], "right_arm": [0, 0]},
            {"time": 1.0, "left_arm": [20, 10], "right_arm": [-20, -10]},
        ],
    }

    result = apply_arm_motion(source, spec, interpolation="linear", max_abs_deg=60)

    assert result["description"] == "demo"
    assert result["time"] == source["time"]
    assert len(result["set_target_data"]) == len(source["set_target_data"])
    assert result["set_target_data"][1]["head"] == [[2]]
    assert result["set_target_data"][1]["body_yaw"] == 0.2
    assert result["set_target_data"][1]["check_collision"] is True
    assert result["set_target_data"][1]["left_arm"] == [math.radians(10), math.radians(5)]
    assert result["set_target_data"][1]["right_arm"] == [math.radians(-10), math.radians(-5)]


def test_apply_arm_motion_holds_final_keyframe_after_spec_end() -> None:
    source = {
        "description": "demo",
        "time": [0.0, 1.0, 2.0],
        "set_target_data": [{"head": []}, {"head": []}, {"head": []}],
    }
    spec = {
        "units": "deg",
        "keyframes": [
            {"time": 0.0, "left_arm": [5, 0], "right_arm": [-5, 0]},
            {"time": 1.0, "left_arm": [0, 0], "right_arm": [0, 0]},
        ],
    }

    result = apply_arm_motion(source, spec, interpolation="linear", max_abs_deg=60)

    assert result["set_target_data"][2]["left_arm"] == [0.0, 0.0]
    assert result["set_target_data"][2]["right_arm"] == [0.0, 0.0]
