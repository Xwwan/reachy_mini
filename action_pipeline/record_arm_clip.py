"""Record a manual two-arm clip by sampling the physical robot on Raspberry Pi/Linux."""

from __future__ import annotations

import argparse
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ACTION_PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = ACTION_PIPELINE_DIR.parent
SRC_DIR = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from action_pipeline.pipeline_utils import arm_clip_to_json, write_json  # noqa: E402

DEFAULT_OUTPUT_DIR = ACTION_PIPELINE_DIR / "arm_clips"
ARM_MOTOR_IDS = ["left_arm_1", "left_arm_2", "right_arm_1", "right_arm_2"]
VALID_CLIP_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_clip_id(value: str) -> str:
    """Validate a filesystem-safe clip id."""
    if not VALID_CLIP_ID.match(value):
        raise argparse.ArgumentTypeError(
            "clip id must contain only letters, numbers, underscores, and hyphens"
        )
    return value


def configure_motor_mode(reachy: object, motor_mode: str) -> None:
    """Switch the robot into the requested hand-teaching motor mode."""
    if motor_mode == "disabled":
        reachy.disable_motors(ids=ARM_MOTOR_IDS)
        return
    raise ValueError(f"Unsupported motor mode: {motor_mode}")


def restore_motor_mode(reachy: object, motor_mode: str) -> None:
    """Return the robot to position control after recording."""
    if motor_mode == "disabled":
        return


def wait_for_enter(stop_event: threading.Event) -> None:
    """Block until the user presses Enter, then signal the sampler."""
    input()
    stop_event.set()


def sample_arm_clip(
    reachy: object,
    sample_hz: float,
) -> tuple[list[float], list[list[float]], list[list[float]]]:
    """Sample current left/right arm positions until the user presses Enter."""
    period = 1.0 / sample_hz
    stop_event = threading.Event()
    stop_thread = threading.Thread(target=wait_for_enter, args=(stop_event,), daemon=True)
    stop_thread.start()

    time_values: list[float] = []
    left_arm_values: list[list[float]] = []
    right_arm_values: list[list[float]] = []

    start_time = time.monotonic()
    next_sample_time = start_time
    while not stop_event.is_set():
        now = time.monotonic()
        if now < next_sample_time:
            time.sleep(min(0.005, next_sample_time - now))
            continue

        left_arm = [float(value) for value in reachy.get_present_left_arm_joint_positions()]
        right_arm = [float(value) for value in reachy.get_present_right_arm_joint_positions()]
        time_values.append(now - start_time)
        left_arm_values.append(left_arm)
        right_arm_values.append(right_arm)
        next_sample_time += period

    if len(time_values) < 2:
        raise RuntimeError("Recording is too short; at least two samples are required.")

    first_time = time_values[0]
    normalized_time = [timestamp - first_time for timestamp in time_values]
    return normalized_time, left_arm_values, right_arm_values


def record_clip(args: argparse.Namespace) -> Path:
    """Record one arm clip and write it to disk."""
    from reachy_mini import ReachyMini

    output_path = args.output_dir / f"{args.clip_id}.json"
    if output_path.exists() and not args.overwrite:
        raise FileExistsError(f"{output_path} already exists. Pass --overwrite to replace it.")

    print("Connect to the already-running Reachy Mini daemon on this Raspberry Pi.")

    with ReachyMini(
        connection_mode=args.connection_mode,
        media_backend=args.media_backend,
        automatic_body_yaw=False,
    ) as reachy:
        print(f"Switching arms to {args.motor_mode} mode.")
        configure_motor_mode(reachy, args.motor_mode)
        try:
            print("Place the arms in the initial pose, then press Enter.")
            input()
            print("Press Enter to START recording.")
            input()
            print("Recording. Move the arms by hand, then press Enter to STOP.")
            time_values, left_arm, right_arm = sample_arm_clip(reachy, args.sample_hz)
        finally:
            print("Leaving arms in disabled mode for hand recording.")
            restore_motor_mode(reachy, args.motor_mode)

    clip = arm_clip_to_json(
        clip_id=args.clip_id,
        label=args.label,
        created_at=datetime.now(timezone.utc).isoformat(),
        sample_hz=args.sample_hz,
        motor_mode=args.motor_mode,
        time_values=time_values,
        left_arm=left_arm,
        right_arm=right_arm,
    )
    write_json(output_path, clip)
    print(
        f"Wrote {output_path} with {len(time_values)} samples "
        f"over {time_values[-1]:.3f}s."
    )
    return output_path


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clip-id", required=True, type=validate_clip_id)
    parser.add_argument("--label", default="")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--sample-hz", type=float, default=50.0)
    parser.add_argument(
        "--motor-mode",
        choices=["disabled"],
        default="disabled",
    )
    parser.add_argument("--connection-mode", default="localhost_only")
    parser.add_argument("--media-backend", default="no_media")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> None:
    """Run the arm clip recorder."""
    args = build_arg_parser().parse_args()
    if args.sample_hz <= 0.0:
        raise ValueError("--sample-hz must be positive")
    args.output_dir = args.output_dir.resolve()
    record_clip(args)


if __name__ == "__main__":
    main()
