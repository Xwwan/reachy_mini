"""Create quick playback-test moves from recorded arm clips."""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ACTION_PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = ACTION_PIPELINE_DIR.parent
SRC_DIR = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from action_pipeline.pipeline_utils import (  # noqa: E402
    load_arm_clip,
    load_json,
    merge_move_with_arm_clip,
    write_json,
)

DEFAULT_SOURCE_MOVE = REPO_ROOT / ".run" / "arm_emotions_library" / "amazed1.json"
DEFAULT_CLIP_DIR = ACTION_PIPELINE_DIR / "arm_clips"
DEFAULT_OUTPUT_DIR = ACTION_PIPELINE_DIR / "library" / "quick_tests"

STATIC_HEAD = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]


def make_static_head_move(clip: object) -> dict[str, object]:
    """Create a move with fixed head/body and the clip's original arm samples."""
    return {
        "description": f"Static head/body with arm clip {clip.clip_id}",
        "time": list(clip.time),
        "set_target_data": [
            {
                "head": copy.deepcopy(STATIC_HEAD),
                "body_yaw": 0.0,
                "check_collision": False,
                "left_arm": list(left_arm),
                "right_arm": list(right_arm),
            }
            for left_arm, right_arm in zip(clip.left_arm, clip.right_arm)
        ],
    }


def main() -> None:
    """Create two quick-test moves."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-move", type=Path, default=DEFAULT_SOURCE_MOVE)
    parser.add_argument("--clip-dir", type=Path, default=DEFAULT_CLIP_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--head-clip-id", default="test_arm_001")
    parser.add_argument("--static-clip-id", default="test_arm_002")
    parser.add_argument("--head-move-name", default="quick_test_head_plus_test_arm_001")
    parser.add_argument("--static-move-name", default="quick_test_static_head_test_arm_002")
    parser.add_argument("--interpolation", choices=["linear", "minjerk"], default="linear")
    args = parser.parse_args()

    source_move_path = args.source_move.resolve()
    clip_dir = args.clip_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    source_move = load_json(source_move_path)
    head_clip = load_arm_clip(clip_dir / f"{args.head_clip_id}.json")
    static_clip = load_arm_clip(clip_dir / f"{args.static_clip_id}.json")

    head_plus_arm = merge_move_with_arm_clip(
        source_move,
        source_move_path,
        head_clip,
        interpolation=args.interpolation,
    )
    head_plus_arm["description"] = (
        f"Quick test: source head/body {source_move_path.stem} + arm clip {head_clip.clip_id}"
    )

    static_head = make_static_head_move(static_clip)

    head_output = output_dir / f"{args.head_move_name}.json"
    static_output = output_dir / f"{args.static_move_name}.json"
    write_json(head_output, head_plus_arm)
    write_json(static_output, static_head)

    print(f"Wrote {head_output}")
    print(f"Wrote {static_output}")


if __name__ == "__main__":
    main()
