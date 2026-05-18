"""Play recorded moves on the local two-arm rmmc fork.

This example bypasses the SDK/daemon and talks directly to the local rmmc fork,
which exposes two arms:

    body_yaw, stewart x6, left_arm x2, right_arm x2

The script can read either the official recorded-move schema with ``antennas`` or
the converted schema with ``left_arm``/``right_arm``. For official files, antenna
values are mapped as:

    antennas[0] -> right_arm[0]
    antennas[1] -> left_arm[0]

The second joint of each arm is held at a configurable constant unless the input
file already contains explicit arm data.
"""

from __future__ import annotations

import argparse
import bisect
import math
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from huggingface_hub import snapshot_download

from reachy_mini.kinematics import AnalyticalKinematics
from reachy_mini.utils.interpolation import linear_pose_interpolation

DEFAULT_DATASET = "pollen-robotics/reachy-mini-emotions-library"


@dataclass(frozen=True)
class ArmMapping:
    """Mapping from official antenna data to local two-DOF arm targets."""

    arm_scale: float
    left_second_joint: float
    right_second_joint: float
    left_offset: float
    right_offset: float
    swap_arms: bool


@dataclass(frozen=True)
class FrameTarget:
    """One evaluated motor target frame."""

    head: npt.NDArray[np.float64]
    left_arm: list[float]
    right_arm: list[float]
    body_yaw: float


@dataclass(frozen=True)
class PlaybackTarget:
    """A complete 11-DOF motor target."""

    body_yaw: float
    stewart: list[float]
    left_arm: list[float]
    right_arm: list[float]


class ArmRecordedMove:
    """Recorded move evaluator supporting official and arm schemas."""

    def __init__(self, path: Path) -> None:
        """Load a move JSON file."""
        import json

        with path.open("r", encoding="utf-8") as fp:
            move: dict[str, Any] = json.load(fp)

        self.path = path
        self.name = path.stem
        self.description: str = move["description"]
        self.timestamps: list[float] = [float(t) for t in move["time"]]
        self.trajectory: list[dict[str, Any]] = move["set_target_data"]
        self.sound_path = path.with_suffix(".wav") if path.with_suffix(".wav").exists() else None

    @property
    def duration(self) -> float:
        """Return move duration in seconds."""
        return self.timestamps[-1] - self.timestamps[0]

    @property
    def frame_count(self) -> int:
        """Return the number of recorded frames."""
        return len(self.trajectory)

    def evaluate(self, t: float, mapping: ArmMapping) -> FrameTarget:
        """Evaluate head pose, body yaw, and arm joints at time ``t``."""
        t_recording = min(
            max(t + self.timestamps[0], self.timestamps[0]),
            self.timestamps[-1] - 1e-6,
        )
        index = bisect.bisect_right(self.timestamps, t_recording)
        idx_prev = index - 1 if index > 0 else 0
        idx_next = index if index < len(self.timestamps) else idx_prev

        t_prev = self.timestamps[idx_prev]
        t_next = self.timestamps[idx_next]
        alpha = 0.0 if t_next == t_prev else (t_recording - t_prev) / (t_next - t_prev)

        frame_prev = self.trajectory[idx_prev]
        frame_next = self.trajectory[idx_next]

        head_prev = np.array(frame_prev["head"], dtype=np.float64)
        head_next = np.array(frame_next["head"], dtype=np.float64)
        body_yaw = _lerp(
            float(frame_prev.get("body_yaw", 0.0)),
            float(frame_next.get("body_yaw", 0.0)),
            alpha,
        )

        if "left_arm" in frame_prev and "right_arm" in frame_prev:
            left_arm = _lerp_list(frame_prev["left_arm"], frame_next["left_arm"], alpha)
            right_arm = _lerp_list(frame_prev["right_arm"], frame_next["right_arm"], alpha)
        else:
            antennas = _lerp_list(frame_prev["antennas"], frame_next["antennas"], alpha)
            left_arm, right_arm = _antennas_to_arms(antennas, mapping)

        return FrameTarget(
            head=linear_pose_interpolation(head_prev, head_next, alpha),
            left_arm=left_arm,
            right_arm=right_arm,
            body_yaw=body_yaw,
        )


def _lerp(v0: float, v1: float, alpha: float) -> float:
    return v0 + alpha * (v1 - v0)


def _lerp_list(values0: list[float], values1: list[float], alpha: float) -> list[float]:
    return [float(_lerp(v0, v1, alpha)) for v0, v1 in zip(values0, values1)]


def _antennas_to_arms(
    antennas: list[float],
    mapping: ArmMapping,
) -> tuple[list[float], list[float]]:
    if len(antennas) != 2:
        raise ValueError(f"expected two antenna joints, got {len(antennas)}")

    # Official convention is [right_antenna, left_antenna].
    right_first = antennas[0] * mapping.arm_scale + mapping.right_offset
    left_first = antennas[1] * mapping.arm_scale + mapping.left_offset
    if mapping.swap_arms:
        left_first, right_first = right_first, left_first

    return (
        [float(left_first), float(mapping.left_second_joint)],
        [float(right_first), float(mapping.right_second_joint)],
    )


def resolve_dataset(dataset: str) -> Path:
    """Return a local path for either a filesystem dataset or a HF dataset."""
    path = Path(dataset)
    if path.exists():
        return path
    return Path(snapshot_download(dataset, repo_type="dataset"))


def list_move_files(dataset_path: Path) -> list[Path]:
    """Find move JSON files in a local dataset folder."""
    if dataset_path.is_file():
        return [dataset_path]

    move_files = sorted(dataset_path.glob("*.json"))
    data_dir = dataset_path / "data"
    if data_dir.is_dir():
        move_files.extend(sorted(data_dir.glob("*.json")))
    return move_files


def find_move_file(dataset_path: Path, move_name: str) -> Path:
    """Find a move by stem name."""
    for path in list_move_files(dataset_path):
        if path.stem == move_name:
            return path
    available = ", ".join(path.stem for path in list_move_files(dataset_path)[:12])
    raise SystemExit(f"Move {move_name!r} not found. First available moves: {available}")


def make_playback_target(
    move: ArmRecordedMove,
    t: float,
    mapping: ArmMapping,
    kinematics: AnalyticalKinematics,
    current: Any,
    use_head: bool,
    use_arms: bool,
) -> PlaybackTarget:
    """Build an 11-DOF playback target for the local rmmc fork."""
    frame = move.evaluate(t, mapping)

    if use_head:
        head_joints = kinematics.ik(frame.head, body_yaw=frame.body_yaw)
        body_yaw = float(head_joints[0])
        stewart = [float(v) for v in head_joints[1:]]
    else:
        body_yaw = float(current.body_yaw)
        stewart = [float(v) for v in current.stewart]

    if use_arms:
        left_arm = frame.left_arm
        right_arm = frame.right_arm
    else:
        left_arm = [float(v) for v in current.left_arm]
        right_arm = [float(v) for v in current.right_arm]

    return PlaybackTarget(body_yaw, stewart, left_arm, right_arm)


def interpolate_target(
    start: PlaybackTarget,
    end: PlaybackTarget,
    alpha: float,
) -> PlaybackTarget:
    """Linearly interpolate a full motor target."""
    return PlaybackTarget(
        body_yaw=_lerp(start.body_yaw, end.body_yaw, alpha),
        stewart=_lerp_list(start.stewart, end.stewart, alpha),
        left_arm=_lerp_list(start.left_arm, end.left_arm, alpha),
        right_arm=_lerp_list(start.right_arm, end.right_arm, alpha),
    )


def target_from_current(current: Any) -> PlaybackTarget:
    """Convert rmmc FullBodyPosition-like data into a PlaybackTarget."""
    return PlaybackTarget(
        body_yaw=float(current.body_yaw),
        stewart=[float(v) for v in current.stewart],
        left_arm=[float(v) for v in current.left_arm],
        right_arm=[float(v) for v in current.right_arm],
    )


def print_dry_run(move: ArmRecordedMove, mapping: ArmMapping) -> None:
    """Print a compact preview without touching the robot."""
    kinematics = AnalyticalKinematics()
    print(f"Move: {move.name}")
    print(f"Description: {move.description}")
    print(f"Frames: {move.frame_count}, duration: {move.duration:.3f}s")
    for label, t in [
        ("start", 0.0),
        ("middle", move.duration / 2.0),
        ("end", max(move.duration - 1e-3, 0.0)),
    ]:
        frame = move.evaluate(t, mapping)
        head_joints = kinematics.ik(frame.head, body_yaw=frame.body_yaw)
        print(
            f"{label:>6}: body={head_joints[0]: .3f} rad, "
            f"left_arm={_format_angles(frame.left_arm)}, "
            f"right_arm={_format_angles(frame.right_arm)}"
        )


def _format_angles(values: list[float]) -> str:
    return "[" + ", ".join(f"{value:.3f} rad/{math.degrees(value):.1f} deg" for value in values) + "]"


def play_on_robot(
    move: ArmRecordedMove,
    serialport: str,
    mapping: ArmMapping,
    play_frequency: float,
    initial_goto_duration: float,
    use_head: bool,
    use_arms: bool,
    set_position_mode: bool,
    disable_torque_on_exit: bool,
) -> None:
    """Connect to the local rmmc fork and play the selected move."""
    from reachy_mini_motor_controller import FullBodyPosition, ReachyMiniPyControlLoop

    period = timedelta(seconds=1.0 / play_frequency)
    loop = ReachyMiniPyControlLoop(
        serialport,
        period,
        allowed_retries=5,
        stats_pub_period=timedelta(seconds=1),
        voltage_rampup_timeout=timedelta(seconds=30),
    )

    try:
        names = loop.get_motor_name_id()
        required = {"left_arm_1", "left_arm_2", "right_arm_1", "right_arm_2"}
        missing = sorted(required.difference(names))
        if missing:
            raise RuntimeError(
                "This script needs the modified local rmmc package. "
                f"Missing motor names: {missing}"
            )

        if set_position_mode:
            loop.disable_torque()
            time.sleep(0.2)
            if use_head:
                loop.set_body_rotation_operating_mode(3)
                loop.set_stewart_platform_operating_mode(3)
            if use_arms:
                loop.set_left_arm_operating_mode(3)
                loop.set_right_arm_operating_mode(3)

        if use_head:
            loop.enable_body_rotation(True)
            loop.enable_stewart_platform(True)
        if use_arms:
            loop.enable_left_arm(True)
            loop.enable_right_arm(True)

        kinematics = AnalyticalKinematics()
        current = loop.get_last_position()
        current_target = target_from_current(current)
        first_target = make_playback_target(
            move,
            0.0,
            mapping,
            kinematics,
            current=current,
            use_head=use_head,
            use_arms=use_arms,
        )

        def send(target: PlaybackTarget) -> None:
            loop.set_all_goal_positions(
                FullBodyPosition(
                    target.body_yaw,
                    target.stewart,
                    target.left_arm,
                    target.right_arm,
                )
            )

        if initial_goto_duration > 0.0:
            goto_start = time.perf_counter()
            while True:
                elapsed = time.perf_counter() - goto_start
                if elapsed >= initial_goto_duration:
                    break
                alpha = elapsed / initial_goto_duration
                send(interpolate_target(current_target, first_target, alpha))
                time.sleep(period.total_seconds())

        start = time.perf_counter()
        while True:
            elapsed = time.perf_counter() - start
            if elapsed >= move.duration:
                break
            target = make_playback_target(
                move,
                elapsed,
                mapping,
                kinematics,
                current=current,
                use_head=use_head,
                use_arms=use_arms,
            )
            send(target)
            time.sleep(period.total_seconds())
    finally:
        if disable_torque_on_exit:
            loop.disable_torque()
        loop.close()


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List available moves and exit.",
    )
    parser.add_argument(
        "-m",
        "--move",
        help="Move name to play, for example cheerful1 or sad1.",
    )
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        help="HuggingFace dataset name, converted dataset folder, or one JSON file.",
    )
    parser.add_argument("--serialport", default="COM3", help="Robot serial port.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview targets without opening the serial port.",
    )
    parser.add_argument(
        "--play-frequency",
        type=float,
        default=50.0,
        help="Motor target update frequency in Hz.",
    )
    parser.add_argument(
        "--initial-goto-duration",
        type=float,
        default=1.0,
        help="Seconds to move from current pose to the first recorded target.",
    )
    parser.add_argument(
        "--arm-scale",
        type=float,
        default=1.0,
        help="Scale original antenna values when reading official datasets.",
    )
    parser.add_argument(
        "--left-second-joint",
        type=float,
        default=0.0,
        help="Constant radian target for left_arm[1] when reading official datasets.",
    )
    parser.add_argument(
        "--right-second-joint",
        type=float,
        default=0.0,
        help="Constant radian target for right_arm[1] when reading official datasets.",
    )
    parser.add_argument(
        "--left-offset",
        type=float,
        default=0.0,
        help="Radian offset added to left_arm[0] when reading official datasets.",
    )
    parser.add_argument(
        "--right-offset",
        type=float,
        default=0.0,
        help="Radian offset added to right_arm[0] when reading official datasets.",
    )
    parser.add_argument(
        "--swap-arms",
        action="store_true",
        help="Swap original left/right antenna sources before writing arm targets.",
    )
    parser.add_argument("--no-head", dest="use_head", action="store_false", default=True)
    parser.add_argument("--no-arms", dest="use_arms", action="store_false", default=True)
    parser.add_argument(
        "--set-position-mode",
        action="store_true",
        help="Set selected motors to position mode before playing.",
    )
    parser.add_argument(
        "--disable-torque-on-exit",
        action="store_true",
        help="Disable torque after playback finishes.",
    )
    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_arg_parser()
    args = parser.parse_args()

    dataset_path = resolve_dataset(args.dataset)
    move_files = list_move_files(dataset_path)
    if not move_files:
        raise SystemExit(f"No JSON move files found in {dataset_path}")

    if args.list:
        for path in move_files:
            print(path.stem)
        return

    if not args.move:
        raise SystemExit("Please pass --move NAME, or use --list to inspect moves.")

    mapping = ArmMapping(
        arm_scale=args.arm_scale,
        left_second_joint=args.left_second_joint,
        right_second_joint=args.right_second_joint,
        left_offset=args.left_offset,
        right_offset=args.right_offset,
        swap_arms=args.swap_arms,
    )
    move = ArmRecordedMove(find_move_file(dataset_path, args.move))

    if args.dry_run:
        print_dry_run(move, mapping)
        return

    play_on_robot(
        move,
        serialport=args.serialport,
        mapping=mapping,
        play_frequency=args.play_frequency,
        initial_goto_duration=args.initial_goto_duration,
        use_head=args.use_head,
        use_arms=args.use_arms,
        set_position_mode=args.set_position_mode,
        disable_torque_on_exit=args.disable_torque_on_exit,
    )


if __name__ == "__main__":
    main()
