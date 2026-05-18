"""Test preset two-arm emotion motions through the running Reachy Mini daemon.

Start the daemon first:

    reachy-mini-daemon --serialport COM3

Then run one preset:

    python examples/test_arm_action_presets.py --preset 1

Or preview all presets without moving the robot:

    python examples/test_arm_action_presets.py --all --dry-run
"""

from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from reachy_mini import ReachyMini

from tune_arm_sequence import (
    DEFAULT_HARDWARE_CONFIG,
    ArmPoseDeg,
    build_sequence,
    print_base_mode,
    print_current_arms,
    print_sequence,
    read_current_arms,
    resolve_base_pose,
    send_arm_pose,
)


@dataclass(frozen=True)
class ArmPreset:
    """One human-readable arm motion preset."""

    preset_id: str
    label: str
    main_deg: float
    swing_deg: float
    repeats: int


PRESETS: dict[str, ArmPreset] = {
    "1": ArmPreset(
        preset_id="1",
        label="small opening, two swings",
        main_deg=30.0,
        swing_deg=45.0,
        repeats=2,
    ),
    "2": ArmPreset(
        preset_id="2",
        label="wide opening, three swings",
        main_deg=-60.0,
        swing_deg=45.0,
        repeats=3,
    ),
    "3": ArmPreset(
        preset_id="3",
        label="wide opening, four swings",
        main_deg=-60.0,
        swing_deg=45.0,
        repeats=4,
    ),
    "4": ArmPreset(
        preset_id="4",
        label="wide opening, big three swings",
        main_deg=-60.0,
        swing_deg=60.0,
        repeats=3,
    ),
    "5": ArmPreset(
        preset_id="5",
        label="medium opening, soft three swings",
        main_deg=-30.0,
        swing_deg=30.0,
        repeats=3,
    ),
}


def max_home_error_deg(mini: ReachyMini) -> float:
    """Return the maximum absolute arm error from logical home."""
    left, right = read_current_arms(mini)
    return max(abs(math.degrees(value)) for value in [*left, *right])


def ensure_arms_home(
    mini: ReachyMini,
    run_args: SimpleNamespace,
    base_left: tuple[float, float],
    base_right: tuple[float, float],
    tolerance_deg: float,
    reset_duration: float,
    reset_attempts: int,
) -> bool:
    """Force arms back to logical home if the final feedback is too far away."""
    home = ArmPoseDeg((0.0, 0.0), (0.0, 0.0), "checked final home")
    for attempt in range(reset_attempts + 1):
        error_deg = max_home_error_deg(mini)
        print_current_arms(mini, f"home check {attempt}")
        if error_deg <= tolerance_deg:
            print(f"Home check passed: max error {error_deg:.2f} deg.\n")
            return True

        if attempt >= reset_attempts:
            print(f"Home check failed: max error {error_deg:.2f} deg.\n")
            return False

        print(
            f"Home error {error_deg:.2f} deg is above {tolerance_deg:.2f} deg; "
            f"forcing reset {attempt + 1}/{reset_attempts}."
        )
        send_arm_pose(mini, home, reset_duration, run_args, base_left, base_right)
        time.sleep(0.2)

    return False


def make_run_args(preset: ArmPreset, args: argparse.Namespace) -> SimpleNamespace:
    """Build the argument namespace expected by tune_arm_sequence helpers."""
    return SimpleNamespace(
        main_deg=preset.main_deg,
        right_main_deg=None,
        swing_deg=preset.swing_deg,
        right_swing_deg=None,
        repeats=preset.repeats,
        home_duration=args.home_duration,
        main_duration=args.main_duration,
        swing_duration=args.swing_duration,
        second_return_duration=args.second_return_duration,
        main_return_duration=args.main_return_duration,
        final_home_duration=args.final_home_duration,
        max_abs_deg=args.max_abs_deg,
        base_source="config",
        hardware_config=args.hardware_config,
        command_frequency=args.command_frequency,
        media_backend=args.media_backend,
        use_goto=args.use_goto,
        print_feedback=not args.no_feedback,
    )


def run_preset(preset: ArmPreset, args: argparse.Namespace) -> None:
    """Run or preview one preset."""
    run_args = make_run_args(preset, args)
    sequence = build_sequence(run_args)

    print(f"\n=== Preset {preset.preset_id}: {preset.label} ===")
    print(
        f"main left={preset.main_deg:g} deg, right={-preset.main_deg:g} deg | "
        f"swing left=+/-{preset.swing_deg:g} deg, right=-/+{preset.swing_deg:g} deg | "
        f"repeats={preset.repeats}"
    )
    print_sequence(sequence)

    if args.dry_run:
        return

    home = ArmPoseDeg((0.0, 0.0), (0.0, 0.0), "final home")
    with ReachyMini(
        connection_mode="localhost_only",
        media_backend=args.media_backend,
        automatic_body_yaw=False,
    ) as mini:
        print_current_arms(mini, "start")
        base_left, base_right = resolve_base_pose(mini, run_args)
        print_base_mode(run_args, base_left, base_right)

        try:
            for pose, duration in sequence:
                send_arm_pose(mini, pose, duration, run_args, base_left, base_right)
        except KeyboardInterrupt:
            print("\nInterrupted by user; returning arms to home.")
            raise
        finally:
            send_arm_pose(
                mini,
                home,
                run_args.final_home_duration,
                run_args,
                base_left,
                base_right,
            )
            ensure_arms_home(
                mini,
                run_args,
                base_left,
                base_right,
                tolerance_deg=args.home_tolerance_deg,
                reset_duration=args.reset_duration,
                reset_attempts=args.reset_attempts,
            )


def selected_presets(args: argparse.Namespace) -> list[ArmPreset]:
    """Return the presets selected by CLI flags."""
    if args.all:
        return [PRESETS[key] for key in sorted(PRESETS)]
    if args.preset is None:
        raise ValueError("Use --preset 1..5 or --all.")
    return [PRESETS[args.preset]]


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preset", choices=sorted(PRESETS), help="Preset ID to test.")
    group.add_argument("--all", action="store_true", help="Run all presets in order.")
    parser.add_argument("--dry-run", action="store_true", help="Print motions without moving.")
    parser.add_argument("--home-duration", type=float, default=1.2)
    parser.add_argument("--main-duration", type=float, default=0.8)
    parser.add_argument("--swing-duration", type=float, default=0.45)
    parser.add_argument("--second-return-duration", type=float, default=0.5)
    parser.add_argument("--main-return-duration", type=float, default=0.9)
    parser.add_argument("--final-home-duration", type=float, default=1.5)
    parser.add_argument("--home-tolerance-deg", type=float, default=5.0)
    parser.add_argument("--reset-duration", type=float, default=1.5)
    parser.add_argument("--reset-attempts", type=int, default=2)
    parser.add_argument("--max-abs-deg", type=float, default=100.0)
    parser.add_argument("--command-frequency", type=float, default=50.0)
    parser.add_argument("--media-backend", default="default")
    parser.add_argument("--hardware-config", type=Path, default=DEFAULT_HARDWARE_CONFIG)
    parser.add_argument("--use-goto", action="store_true")
    parser.add_argument("--no-feedback", action="store_true")
    parser.add_argument(
        "--pause-between",
        type=float,
        default=1.0,
        help="Pause between presets when using --all.",
    )
    return parser


def main() -> None:
    """Run selected arm action presets."""
    args = build_arg_parser().parse_args()
    presets = selected_presets(args)
    for index, preset in enumerate(presets):
        run_preset(preset, args)
        if index < len(presets) - 1 and not args.dry_run:
            time.sleep(args.pause_between)


if __name__ == "__main__":
    main()
