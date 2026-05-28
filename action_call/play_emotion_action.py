"""Play one of the generated 82 actions through the Reachy Mini daemon."""

from __future__ import annotations

import argparse
import json
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
CONFIG_PATH = ACTION_CALL_DIR / "config.json"
ARM_HOME = [0.0, 0.0]
HEAD_HOME = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]
BODY_HOME = 0.0


def configure_output_encoding() -> None:
    """Make emoji output safe in terminals with non-UTF-8 defaults."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def load_signal_map(config_path: Path) -> dict[str, str]:
    """Load signal-to-move mapping."""
    if not config_path.exists():
        raise FileNotFoundError(f"Missing config file: {config_path}")
    with config_path.open("r", encoding="utf-8") as file:
        config = json.load(file)
    if config.get("schema_version") != 1:
        raise ValueError(f"{config_path} must contain schema_version=1")
    signal_map = config.get("signal_map")
    if not isinstance(signal_map, dict):
        raise ValueError(f"{config_path} must contain a signal_map object")

    resolved: dict[str, str] = {}
    for signal, move_name in signal_map.items():
        if not isinstance(signal, str) or not signal:
            raise ValueError("signal_map keys must be non-empty strings")
        if not isinstance(move_name, str) or not move_name:
            raise ValueError(f"signal_map[{signal!r}] must be a non-empty move name")
        resolved[signal] = move_name
    return resolved


def resolve_signal(signal: str, signal_map: dict[str, str]) -> str:
    """Resolve a CLI signal to a generated move name."""
    if signal in signal_map:
        return signal_map[signal]
    normalized = signal.strip().lower()
    if normalized in signal_map:
        return signal_map[normalized]
    available = " ".join(sorted(signal_map)[:80])
    raise ValueError(f"Unknown signal {signal!r}. First available keys: {available}")


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
    """Check final arm feedback and force arm home if needed."""
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
        print(f"Resetting arms home attempt {attempt + 1}/{reset_attempts}.")
        reachy.goto_target(
            left_arm=ARM_HOME,
            right_arm=ARM_HOME,
            duration=reset_duration,
            body_yaw=None,
        )
        time.sleep(0.2)
    return False


def reset_full_body_home(reachy: object, duration: float) -> None:
    """Return head, body yaw, and both arms to the calibrated initial position."""
    print("Returning head/body/arms to initial position.")
    reachy.goto_target(
        head=HEAD_HOME,
        left_arm=ARM_HOME,
        right_arm=ARM_HOME,
        body_yaw=BODY_HOME,
        duration=duration,
    )
    time.sleep(0.2)


def print_available(library_dir: Path, signal_map: dict[str, str]) -> None:
    """Print configured signals and generated file status."""
    print(f"Library: {library_dir}")
    by_move: dict[str, list[str]] = {}
    for signal, move_name in signal_map.items():
        by_move.setdefault(move_name, []).append(signal)
    for move_name in sorted(by_move):
        status = "ready" if (library_dir / f"{move_name}.json").exists() else "missing"
        keys = " ".join(sorted(by_move[move_name]))
        print(f"{move_name:24} [{status}] {keys}")
    print(f"\nSignals: {len(signal_map)}")
    print(f"Moves in config: {len(by_move)}")


def play_signal_on_reachy(
    reachy: object,
    signal: str,
    *,
    config_path: Path = CONFIG_PATH,
    library_dir: Path = LIBRARY_DIR,
    repeat: int = 1,
    pause_between: float = 1.0,
    initial_goto_duration: float = 1.0,
    sound: bool = False,
    final_home_check: bool = True,
    home_tolerance_deg: float = 5.0,
    reset_duration: float = 1.5,
    reset_attempts: int = 2,
) -> str:
    """Play a configured signal using an already-open Reachy Mini connection."""
    from reachy_mini.motion.recorded_move import RecordedMoves

    if repeat < 1:
        raise ValueError("repeat must be >= 1")

    config_path = config_path.resolve()
    library_dir = library_dir.resolve()
    signal_map = load_signal_map(config_path)
    move_name = resolve_signal(signal, signal_map)
    move_path = library_dir / f"{move_name}.json"
    if not move_path.exists():
        raise FileNotFoundError(
            f"Missing {move_path}. Build the library with action_call/build_action_library.py."
        )

    recorded_moves = RecordedMoves(str(library_dir))
    move = recorded_moves.get(move_name)

    print("Enabling motors for playback.")
    reachy.enable_motors()
    time.sleep(0.2)

    for index in range(repeat):
        print(f"Playing {signal!r} -> {move_name}, repeat {index + 1}/{repeat}.")
        reachy.play_move(
            move,
            initial_goto_duration=initial_goto_duration,
            sound=sound,
        )
        if final_home_check:
            reset_full_body_home(reachy, duration=reset_duration)
            ensure_arms_home(
                reachy,
                tolerance_deg=home_tolerance_deg,
                reset_duration=reset_duration,
                reset_attempts=reset_attempts,
            )
        if index < repeat - 1:
            time.sleep(pause_between)
    return move_name


def play_signal(args: argparse.Namespace) -> None:
    """Connect to the daemon and play one configured signal."""
    from reachy_mini import ReachyMini

    print("Connecting to Reachy Mini daemon at localhost...")
    with ReachyMini(
        connection_mode=args.connection_mode,
        media_backend=args.media_backend,
        automatic_body_yaw=False,
    ) as reachy:
        play_signal_on_reachy(
            reachy,
            args.signal,
            config_path=args.config,
            library_dir=args.library_dir,
            repeat=args.repeat,
            pause_between=args.pause_between,
            initial_goto_duration=args.initial_goto_duration,
            sound=args.sound,
            final_home_check=args.final_home_check,
            home_tolerance_deg=args.home_tolerance_deg,
            reset_duration=args.reset_duration,
            reset_attempts=args.reset_attempts,
        )


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--signal", "--emoji", dest="signal", help="Signal key or emoji to play.")
    group.add_argument("--list", action="store_true", help="List configured signals and exit.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--library-dir", type=Path, default=LIBRARY_DIR)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--pause-between", type=float, default=1.0)
    parser.add_argument("--initial-goto-duration", type=float, default=1.0)
    parser.add_argument("--connection-mode", default="localhost_only")
    parser.add_argument("--media-backend", default="no_media")
    parser.add_argument("--sound", dest="sound", action="store_true")
    parser.add_argument("--no-sound", dest="sound", action="store_false", help=argparse.SUPPRESS)
    parser.add_argument(
        "--no-final-home-check",
        dest="final_home_check",
        action="store_false",
        help="Do not return head/body/arms to initial position after playback.",
    )
    parser.add_argument("--home-tolerance-deg", type=float, default=5.0)
    parser.add_argument("--reset-duration", type=float, default=1.5)
    parser.add_argument("--reset-attempts", type=int, default=2)
    parser.set_defaults(sound=False, final_home_check=True)
    return parser


def main() -> None:
    """Run the CLI."""
    configure_output_encoding()
    args = build_arg_parser().parse_args()
    args.config = args.config.resolve()
    args.library_dir = args.library_dir.resolve()
    if args.repeat < 1:
        raise ValueError("--repeat must be >= 1")
    signal_map = load_signal_map(args.config)
    if args.list:
        print_available(args.library_dir, signal_map)
        return
    play_signal(args)


if __name__ == "__main__":
    main()
