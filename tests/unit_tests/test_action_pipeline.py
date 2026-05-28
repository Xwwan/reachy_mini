from pathlib import Path

import pytest

from action_pipeline.pipeline_utils import (
    ArmClip,
    load_arm_clip,
    make_clip_map_template,
    merge_move_with_arm_clip,
    validate_complete_mapping,
    write_json,
)


def make_clip() -> ArmClip:
    """Create a minimal validated arm clip for tests."""
    return ArmClip(
        clip_id="test_clip",
        label="Test",
        created_at="2026-01-01T00:00:00+00:00",
        sample_hz=2.0,
        motor_mode="gravity_compensation",
        duration=1.0,
        time=[0.0, 1.0],
        left_arm=[[0.0, 0.0], [1.0, 2.0]],
        right_arm=[[0.0, 0.0], [-1.0, -2.0]],
    )


def test_load_arm_clip_validates_schema(tmp_path: Path) -> None:
    clip_path = tmp_path / "clip.json"
    write_json(
        clip_path,
        {
            "schema_version": 1,
            "clip_id": "test_clip",
            "label": "Test",
            "created_at": "2026-01-01T00:00:00+00:00",
            "sample_hz": 50,
            "motor_mode": "gravity_compensation",
            "duration": 1.0,
            "time": [0.0, 1.0],
            "left_arm": [[0.0, 0.0], [1.0, 2.0]],
            "right_arm": [[0.0, 0.0], [-1.0, -2.0]],
        },
    )

    clip = load_arm_clip(clip_path)

    assert clip.clip_id == "test_clip"
    assert clip.left_arm[-1] == [1.0, 2.0]
    assert clip.right_arm[-1] == [-1.0, -2.0]


def test_load_arm_clip_rejects_length_mismatch(tmp_path: Path) -> None:
    clip_path = tmp_path / "bad_clip.json"
    write_json(
        clip_path,
        {
            "schema_version": 1,
            "clip_id": "bad_clip",
            "label": "Bad",
            "created_at": "2026-01-01T00:00:00+00:00",
            "sample_hz": 50,
            "motor_mode": "gravity_compensation",
            "duration": 1.0,
            "time": [0.0, 1.0],
            "left_arm": [[0.0, 0.0]],
            "right_arm": [[0.0, 0.0], [1.0, 1.0]],
        },
    )

    with pytest.raises(ValueError, match="lengths must match"):
        load_arm_clip(clip_path)


def test_merge_move_with_arm_clip_stretches_clip_to_source_duration() -> None:
    source = {
        "description": "demo",
        "time": [10.0, 15.0, 20.0],
        "set_target_data": [
            {"head": [[1]], "body_yaw": 0.1, "check_collision": False, "left_arm": [9, 9], "right_arm": [9, 9]},
            {"head": [[2]], "body_yaw": 0.2, "check_collision": True, "left_arm": [9, 9], "right_arm": [9, 9]},
            {"head": [[3]], "body_yaw": 0.3, "check_collision": False, "left_arm": [9, 9], "right_arm": [9, 9]},
        ],
    }

    result = merge_move_with_arm_clip(source, Path("demo.json"), make_clip())

    assert result["time"] == source["time"]
    assert result["set_target_data"][1]["head"] == [[2]]
    assert result["set_target_data"][1]["body_yaw"] == 0.2
    assert result["set_target_data"][1]["check_collision"] is True
    assert result["set_target_data"][0]["left_arm"] == [0.0, 0.0]
    assert result["set_target_data"][1]["left_arm"] == [0.5, 1.0]
    assert result["set_target_data"][1]["right_arm"] == [-0.5, -1.0]
    assert result["set_target_data"][2]["left_arm"] == [1.0, 2.0]


def test_validate_complete_mapping_requires_every_source_move() -> None:
    move_files = [Path("a.json"), Path("b.json")]
    clips = {"test_clip": make_clip()}
    mapping = {"a": "test_clip"}

    with pytest.raises(ValueError, match="missing mappings: b"):
        validate_complete_mapping(move_files, clips, mapping)


def test_make_clip_map_template_lists_source_moves(tmp_path: Path) -> None:
    write_json(tmp_path / "b.json", {"time": [0.0, 1.0], "set_target_data": [{"head": []}, {"head": []}]})
    write_json(tmp_path / "a.json", {"time": [0.0, 1.0], "set_target_data": [{"head": []}, {"head": []}]})

    template = make_clip_map_template(tmp_path)

    assert list(template["moves"]) == ["a", "b"]
    assert template["moves"]["a"] is None
