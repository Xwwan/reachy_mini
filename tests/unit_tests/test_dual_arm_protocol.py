import json

import numpy as np

from reachy_mini.io.protocol import (
    GotoTargetCmd,
    JointPositionsMsg,
    SetArmsCmd,
    SetFullTargetCmd,
    command_adapter,
)
from reachy_mini.motion.recorded_move import RecordedMoves


def test_protocol_parses_dual_arm_commands() -> None:
    set_arms = command_adapter.validate_python(
        {"type": "set_arms", "left_arm": [0.1, 0.2], "right_arm": [-0.1, -0.2]}
    )
    assert isinstance(set_arms, SetArmsCmd)
    assert set_arms.left_arm == [0.1, 0.2]
    assert set_arms.right_arm == [-0.1, -0.2]

    full_target = command_adapter.validate_python(
        {
            "type": "set_full_target",
            "head": np.eye(4).flatten().tolist(),
            "left_arm": [0.3, 0.4],
            "right_arm": [-0.3, -0.4],
            "body_yaw": 0.5,
        }
    )
    assert isinstance(full_target, SetFullTargetCmd)
    assert full_target.left_arm == [0.3, 0.4]
    assert full_target.right_arm == [-0.3, -0.4]

    goto = command_adapter.validate_python(
        {
            "type": "goto_target",
            "left_arm": [0.5, 0.6],
            "right_arm": [-0.5, -0.6],
            "duration": 1.0,
        }
    )
    assert isinstance(goto, GotoTargetCmd)
    assert goto.left_arm == [0.5, 0.6]
    assert goto.right_arm == [-0.5, -0.6]


def test_joint_positions_message_uses_dual_arm_fields() -> None:
    msg = JointPositionsMsg(
        head_joint_positions=[0.0] * 7,
        left_arm_joint_positions=[0.1, 0.2],
        right_arm_joint_positions=[-0.1, -0.2],
    )

    dumped = msg.model_dump()
    assert dumped["left_arm_joint_positions"] == [0.1, 0.2]
    assert dumped["right_arm_joint_positions"] == [-0.1, -0.2]


def test_recorded_moves_load_local_dual_arm_dataset(tmp_path) -> None:
    dataset_dir = tmp_path / "arm_emotions_library"
    dataset_dir.mkdir()

    move = {
        "description": "unit test arm move",
        "time": [0.0, 1.0],
        "set_target_data": [
            {
                "head": np.eye(4).tolist(),
                "body_yaw": 0.0,
                "left_arm": [0.0, 0.0],
                "right_arm": [0.0, 0.0],
            },
            {
                "head": np.eye(4).tolist(),
                "body_yaw": 0.5,
                "left_arm": [1.0, -1.0],
                "right_arm": [2.0, -2.0],
            },
        ],
    }
    (dataset_dir / "amazed1.json").write_text(json.dumps(move), encoding="utf-8")

    library = RecordedMoves(str(dataset_dir))
    evaluated = library.get("amazed1").evaluate(0.5)

    np.testing.assert_allclose(evaluated.left_arm, np.array([0.5, -0.5]))
    np.testing.assert_allclose(evaluated.right_arm, np.array([1.0, -1.0]))
    assert evaluated.body_yaw == 0.25
