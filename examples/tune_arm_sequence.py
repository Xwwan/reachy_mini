"""Tune a symmetric two-arm sequence through the Reachy Mini daemon.

Start the daemon first:

    reachy-mini-daemon --serialport COM3

Then tune the arms through arm-only SDK commands:

    python examples/tune_arm_sequence.py --main-deg 3 --swing-deg 2

Default behavior:

* The home/base pose is the calibrated zero from hardware_config.yaml.
* In SDK/daemon space that home target is left_arm=[0, 0], right_arm=[0, 0].
* YAML offset values are Dynamixel homing offsets, not arm targets sent directly.
* The script sends only set_arms commands, so it should not move the head motors.

Use --base-source current to use the current arm pose as home instead.

The sequence is:

1. move both arms to home
2. move IDs 17 and 19 by the main symmetric offset from home
3. oscillate IDs 18 and 20 while IDs 17 and 19 stay fixed
4. return IDs 18 and 20 to home
5. return IDs 17 and 19 to home
"""

from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from reachy_mini import ReachyMini


DEFAULT_HARDWARE_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "reachy_mini"
    / "assets"
    / "config"
    / "hardware_config.yaml"
)


@dataclass(frozen=True)
class ArmPoseDeg:
    """A symmetric logical arm pose in degrees."""

    left: tuple[float, float]
    right: tuple[float, float]
    label: str


ArmPoseRad = tuple[tuple[float, float], tuple[float, float]]


def assert_pose_within_limit(pose: ArmPoseDeg, max_abs_deg: float) -> None:
    """Refuse obviously unsafe arm offsets."""
    values = [*pose.left, *pose.right]
    biggest = max(abs(v) for v in values)
    if biggest > max_abs_deg:
        raise ValueError(
            f"{pose.label} contains {biggest:.1f} deg, above --max-abs-deg={max_abs_deg:.1f}"
        )


def build_sequence(args: argparse.Namespace) -> list[tuple[ArmPoseDeg, float]]:
    """Build the ordered arm sequence in logical degrees."""
    right_main = -args.main_deg if args.right_main_deg is None else args.right_main_deg
    right_swing = -args.swing_deg if args.right_swing_deg is None else args.right_swing_deg

    home = ArmPoseDeg((0.0, 0.0), (0.0, 0.0), "home")
    main = ArmPoseDeg((args.main_deg, 0.0), (right_main, 0.0), "main joints")
    second_zero = ArmPoseDeg((args.main_deg, 0.0), (right_main, 0.0), "second joints zero")

    sequence: list[tuple[ArmPoseDeg, float]] = [
        (home, args.home_duration),
        (main, args.main_duration),
    ]

    for idx in range(args.repeats):
        sequence.append(
            (
                ArmPoseDeg(
                    (args.main_deg, args.swing_deg),
                    (right_main, right_swing),
                    f"swing {idx + 1}.a",
                ),
                args.swing_duration,
            )
        )
        sequence.append(
            (
                ArmPoseDeg(
                    (args.main_deg, -args.swing_deg),
                    (right_main, -right_swing),
                    f"swing {idx + 1}.b",
                ),
                args.swing_duration,
            )
        )

    sequence.extend(
        [
            (second_zero, args.second_return_duration),
            (home, args.main_return_duration),
        ]
    )

    for pose, _ in sequence:
        assert_pose_within_limit(pose, args.max_abs_deg)

    return sequence


def print_sequence(sequence: list[tuple[ArmPoseDeg, float]]) -> None:
    """Print the offset sequence without touching the robot."""
    for pose, duration in sequence:
        print(
            f"{pose.label:>18} | duration={duration:.2f}s | "
            f"left_offset={list(pose.left)} deg | right_offset={list(pose.right)} deg"
        )


def run_sequence(sequence: list[tuple[ArmPoseDeg, float]], args: argparse.Namespace) -> None:
    """Send the sequence through the Reachy Mini SDK."""
    home = ArmPoseDeg((0.0, 0.0), (0.0, 0.0), "final home")
    with ReachyMini(
        connection_mode="localhost_only",
        media_backend=args.media_backend,
        automatic_body_yaw=False,
    ) as mini:
        print_current_arms(mini, "start")
        base_left, base_right = resolve_base_pose(mini, args)
        print_base_mode(args, base_left, base_right)

        try:
            for pose, duration in sequence:
                send_arm_pose(mini, pose, duration, args, base_left, base_right)
        except KeyboardInterrupt:
            print("\nInterrupted by user; returning arms to home.")
        finally:
            send_arm_pose(mini, home, args.final_home_duration, args, base_left, base_right)
            print_current_arms(mini, "end")


def send_arm_pose(
    mini: ReachyMini,
    pose: ArmPoseDeg,
    duration: float,
    args: argparse.Namespace,
    base_left: tuple[float, float],
    base_right: tuple[float, float],
) -> None:
    """Send one arm-only pose with local interpolation."""
    target_left = add_offset_deg(base_left, pose.left)
    target_right = add_offset_deg(base_right, pose.right)
    print_target(pose, duration, target_left, target_right)

    if args.use_goto:
        mini.goto_target(
            left_arm=list(target_left),
            right_arm=list(target_right),
            duration=duration,
            body_yaw=None,
        )
    else:
        arm_only_goto(mini, target_left, target_right, duration, args.command_frequency)

    if args.print_feedback:
        print_current_arms(mini, f"after {pose.label}")


def arm_only_goto(
    mini: ReachyMini,
    target_left: tuple[float, float],
    target_right: tuple[float, float],
    duration: float,
    frequency: float,
) -> None:
    """Interpolate arms locally using only set_arms commands."""
    start_left, start_right = read_current_arms(mini)
    if duration <= 0:
        mini.set_target_arm_joint_positions(left_arm=list(target_left), right_arm=list(target_right))
        return

    period = 1.0 / frequency
    start = time.perf_counter()
    while True:
        elapsed = time.perf_counter() - start
        alpha = min(elapsed / duration, 1.0)
        left = interpolate_pair(start_left, target_left, alpha)
        right = interpolate_pair(start_right, target_right, alpha)
        mini.set_target_arm_joint_positions(left_arm=list(left), right_arm=list(right))
        if alpha >= 1.0:
            break
        time.sleep(period)


def interpolate_pair(
    start: tuple[float, float],
    target: tuple[float, float],
    alpha: float,
) -> tuple[float, float]:
    """Linearly interpolate two arm joints."""
    return (
        start[0] + (target[0] - start[0]) * alpha,
        start[1] + (target[1] - start[1]) * alpha,
    )


def print_target(
    pose: ArmPoseDeg,
    duration: float,
    target_left: tuple[float, float],
    target_right: tuple[float, float],
) -> None:
    """Print one command target."""
    left_target_deg = [math.degrees(v) for v in target_left]
    right_target_deg = [math.degrees(v) for v in target_right]
    print(
        f"{pose.label}: offset left={list(pose.left)} deg, "
        f"offset right={list(pose.right)} deg, duration={duration:.2f}s | "
        f"target left=[{left_target_deg[0]:.2f}, {left_target_deg[1]:.2f}] deg, "
        f"target right=[{right_target_deg[0]:.2f}, {right_target_deg[1]:.2f}] deg"
    )


def print_current_arms(mini: ReachyMini, label: str) -> None:
    """Print current arm joint positions in degrees."""
    left_rad, right_rad = read_current_arms(mini)
    left = [math.degrees(v) for v in left_rad]
    right = [math.degrees(v) for v in right_rad]
    print(
        f"{label} feedback: "
        f"left=[{left[0]:.2f}, {left[1]:.2f}] deg, "
        f"right=[{right[0]:.2f}, {right[1]:.2f}] deg"
    )


def read_current_arms(mini: ReachyMini) -> ArmPoseRad:
    """Return current left and right arm joint positions in radians."""
    left = mini.get_present_left_arm_joint_positions()
    right = mini.get_present_right_arm_joint_positions()
    return (float(left[0]), float(left[1])), (float(right[0]), float(right[1]))


def resolve_base_pose(mini: ReachyMini, args: argparse.Namespace) -> ArmPoseRad:
    """Return the pose used as home/base for this run."""
    if args.base_source == "current":
        return read_current_arms(mini)
    if args.base_source == "config":
        load_arm_offsets(args.hardware_config)
        return (0.0, 0.0), (0.0, 0.0)
    raise ValueError(f"Unknown base source: {args.base_source}")


def print_base_mode(
    args: argparse.Namespace,
    base_left: tuple[float, float],
    base_right: tuple[float, float],
) -> None:
    """Print the selected base mode."""
    if args.base_source == "current":
        print(
            "Mode: current-pose home. "
            f"base left={format_deg_pair(base_left)}, right={format_deg_pair(base_right)}."
        )
    else:
        offsets = load_arm_offsets(args.hardware_config)
        print(
            "Mode: calibrated config home. "
            f"YAML offsets={offsets}; SDK home target is left=[0, 0], right=[0, 0]."
        )


def format_deg_pair(values: tuple[float, float]) -> str:
    """Format a two-joint radian pair in degrees."""
    return f"[{math.degrees(values[0]):.2f}, {math.degrees(values[1]):.2f}] deg"


def load_arm_offsets(config_path: Path) -> dict[str, int]:
    """Read arm homing offsets from hardware_config.yaml for visibility."""
    with config_path.open("r", encoding="utf-8") as fp:
        config = yaml.safe_load(fp)

    offsets: dict[str, int] = {}
    for motor_entry in config.get("motors", []):
        if not isinstance(motor_entry, dict):
            continue
        for name, params in motor_entry.items():
            if name in {"left_arm_1", "left_arm_2", "right_arm_1", "right_arm_2"}:
                offsets[name] = int(params["offset"])

    expected = {"left_arm_1", "left_arm_2", "right_arm_1", "right_arm_2"}
    missing = sorted(expected.difference(offsets))
    if missing:
        raise ValueError(f"Missing arm offsets in {config_path}: {missing}")
    return offsets


def add_offset_deg(base_rad: tuple[float, float], offset_deg: tuple[float, float]) -> tuple[float, float]:
    """Add a degree offset to a radian base pose."""
    return (
        base_rad[0] + math.radians(offset_deg[0]),
        base_rad[1] + math.radians(offset_deg[1]),
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--main-deg",
        type=float,
        default=8.0,
        help="Left ID17 main offset in degrees. Right ID19 defaults to the opposite.",
    )
    parser.add_argument(
        "--right-main-deg",
        type=float,
        default=None,
        help="Right ID19 main offset in degrees. Defaults to -main-deg.",
    )
    parser.add_argument(
        "--swing-deg",
        type=float,
        default=5.0,
        help="Left ID18 swing amplitude in degrees. Right ID20 defaults to the opposite.",
    )
    parser.add_argument(
        "--right-swing-deg",
        type=float,
        default=None,
        help="Right ID20 swing amplitude in degrees. Defaults to -swing-deg.",
    )
    parser.add_argument("--repeats", type=int, default=2, help="Number of 18/20 swing cycles.")
    parser.add_argument("--home-duration", type=float, default=0.8)
    parser.add_argument("--main-duration", type=float, default=0.6)
    parser.add_argument("--swing-duration", type=float, default=0.35)
    parser.add_argument("--second-return-duration", type=float, default=0.4)
    parser.add_argument("--main-return-duration", type=float, default=0.7)
    parser.add_argument(
        "--final-home-duration",
        type=float,
        default=0.8,
        help="Extra final return-to-home duration, also used after Ctrl+C.",
    )
    parser.add_argument(
        "--max-abs-deg",
        type=float,
        default=60.0,
        help="Safety cap applied to every requested offset.",
    )
    parser.add_argument(
        "--base-source",
        choices=["config", "current"],
        default="config",
        help=(
            "Home/base pose. 'config' uses calibrated SDK zero [0,0] from "
            "hardware_config.yaml; 'current' uses the current arm pose."
        ),
    )
    parser.add_argument(
        "--hardware-config",
        type=Path,
        default=DEFAULT_HARDWARE_CONFIG,
        help="Path to hardware_config.yaml used when --base-source=config.",
    )
    parser.add_argument(
        "--command-frequency",
        type=float,
        default=50.0,
        help="Arm-only interpolation command frequency in Hz.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the sequence without connecting to the daemon.",
    )
    parser.add_argument(
        "--media-backend",
        default="default",
        help="SDK media backend. Keep default to preserve daemon media ownership.",
    )
    parser.add_argument(
        "--use-goto",
        action="store_true",
        help="Use daemon goto_target interpolation. Default is local arm-only set_arms interpolation.",
    )
    parser.add_argument(
        "--no-feedback",
        dest="print_feedback",
        action="store_false",
        help="Do not print current arm joint feedback after each step.",
    )
    parser.set_defaults(print_feedback=True)
    return parser


def main() -> None:
    """Run or preview the tuning sequence."""
    args = build_arg_parser().parse_args()
    if args.repeats < 0:
        raise ValueError("--repeats must be >= 0")
    if args.command_frequency <= 0:
        raise ValueError("--command-frequency must be > 0")

    sequence = build_sequence(args)
    print_sequence(sequence)
    if args.dry_run:
        if args.base_source == "config":
            print(f"Config arm offsets: {load_arm_offsets(args.hardware_config)}")
            print("Dry-run home target for config mode: left=[0, 0], right=[0, 0].")
        return
    run_sequence(sequence, args)


if __name__ == "__main__":
    main()
