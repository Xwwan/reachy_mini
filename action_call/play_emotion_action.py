"""Play one generated dual-arm emotion action through the Reachy Mini daemon."""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path


ACTION_CALL_DIR = Path(__file__).resolve().parent
REPO_ROOT = ACTION_CALL_DIR.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


LIBRARY_DIR = ACTION_CALL_DIR / "library"
ARM_HOME = [0.0, 0.0]

EMOTIONS = {
    "cheerful": ("cheerful1", "快乐"),
    "happy": ("cheerful1", "快乐"),
    "joy": ("cheerful1", "快乐"),
    "sad": ("sad1", "悲伤"),
    "fear": ("fear1", "恐惧"),
    "scared": ("fear1", "恐惧"),
    "furious": ("furious1", "愤怒"),
    "angry": ("furious1", "愤怒"),
    "surprised": ("surprised1", "惊讶"),
    "surprise": ("surprised1", "惊讶"),
}

DISPLAY_ORDER = ("cheerful", "sad", "fear", "furious", "surprised")


def format_arm_deg(values: list[float]) -> str:
    """Format a two-joint arm target in degrees."""
    return f"[{math.degrees(values[0]):.2f}, {math.degrees(values[1]):.2f}] deg"


def max_home_error_deg(left_arm: list[float], right_arm: list[float]) -> float:
    """Return the maximum absolute arm-home error in degrees."""
    return max(abs(math.degrees(value)) for value in [*left_arm, *right_arm])


def ensure_arms_home(
    reachy: object,
    tolerance_deg: float,
    reset_duration: float,
    reset_attempts: int,
) -> bool:
    """Check final arm feedback and force home if needed."""
    for attempt in range(reset_attempts + 1):
        left_arm = reachy.get_present_left_arm_joint_positions()
        right_arm = reachy.get_present_right_arm_joint_positions()
        error_deg = max_home_error_deg(left_arm, right_arm)
        print(
            "Final arm feedback: "
            f"left={format_arm_deg(left_arm)}, "
            f"right={format_arm_deg(right_arm)}, "
            f"max_error={error_deg:.2f} deg"
        )
        if error_deg <= tolerance_deg:
            print("Final home check passed.\n")
            return True

        if attempt >= reset_attempts:
            print("Final home check still failed after reset attempts.\n")
            return False

        print(
            f"Arms are not home within {tolerance_deg:.1f} deg; "
            f"forcing reset attempt {attempt + 1}/{reset_attempts}."
        )
        reachy.goto_target(
            left_arm=ARM_HOME,
            right_arm=ARM_HOME,
            duration=reset_duration,
            body_yaw=None,
        )
        time.sleep(0.2)

    return False


def print_available_emotions(library_dir: Path) -> None:
    """Print the supported emotion aliases."""
    print(f"Library: {library_dir}")
    print("Available emotions:")
    for alias in DISPLAY_ORDER:
        move_name, label_zh = EMOTIONS[alias]
        status = "ready" if (library_dir / f"{move_name}.json").exists() else "missing"
        print(f"  {alias:9} {label_zh:4} -> {move_name:10} [{status}]")
    print("\nUseful aliases: happy=cheerful, angry=furious, scared=fear.")


def play_emotion(args: argparse.Namespace) -> None:
    """Connect to the daemon and play one emotion."""
    from reachy_mini import ReachyMini
    from reachy_mini.motion.recorded_move import RecordedMoves

    move_name, label_zh = EMOTIONS[args.emotion]
    move_path = args.library_dir / f"{move_name}.json"
    if not move_path.exists():
        raise FileNotFoundError(
            f"Missing {move_path}. Run python .\\action_call\\build_action_library.py first."
        )

    recorded_moves = RecordedMoves(str(args.library_dir))
    move = recorded_moves.get(move_name)

    print(f"Connecting to Reachy Mini daemon at localhost...")
    with ReachyMini(
        connection_mode=args.connection_mode,
        media_backend=args.media_backend,
        automatic_body_yaw=False,
    ) as reachy:
        for index in range(args.repeat):
            print(
                f"Playing {args.emotion} / {label_zh} "
                f"({move_name}), repeat {index + 1}/{args.repeat}."
            )
            reachy.play_move(
                move,
                initial_goto_duration=args.initial_goto_duration,
                sound=args.sound,
            )
            if args.final_home_check:
                ensure_arms_home(
                    reachy,
                    tolerance_deg=args.home_tolerance_deg,
                    reset_duration=args.reset_duration,
                    reset_attempts=args.reset_attempts,
                )
            if index < args.repeat - 1:
                time.sleep(args.pause_between)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--emotion", choices=sorted(EMOTIONS), help="Emotion name or alias to play.")
    group.add_argument("--list", action="store_true", help="List generated emotions and exit.")
    parser.add_argument("--library-dir", type=Path, default=LIBRARY_DIR)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--pause-between", type=float, default=1.0)
    parser.add_argument("--initial-goto-duration", type=float, default=1.0)
    parser.add_argument("--connection-mode", default="localhost_only")
    parser.add_argument("--media-backend", default="default")
    parser.add_argument(
        "--no-sound",
        dest="sound",
        action="store_false",
        help="Play the motion only and skip the WAV sound.",
    )
    parser.add_argument(
        "--no-final-home-check",
        dest="final_home_check",
        action="store_false",
        help="Do not check and force arm home after playback.",
    )
    parser.add_argument("--home-tolerance-deg", type=float, default=5.0)
    parser.add_argument("--reset-duration", type=float, default=1.5)
    parser.add_argument("--reset-attempts", type=int, default=2)
    parser.set_defaults(final_home_check=True, sound=True)
    return parser


def main() -> None:
    """Run the CLI."""
    args = build_arg_parser().parse_args()
    args.library_dir = args.library_dir.resolve()
    if args.repeat < 1:
        raise ValueError("--repeat must be >= 1")

    if args.list:
        print_available_emotions(args.library_dir)
        return

    play_emotion(args)


if __name__ == "__main__":
    main()
