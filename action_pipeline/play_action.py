"""Play one merged head-plus-arm action from the action pipeline library."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ACTION_PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = ACTION_PIPELINE_DIR.parent
SRC_DIR = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

DEFAULT_LIBRARY_DIR = ACTION_PIPELINE_DIR / "library"
DEFAULT_SIGNAL_MAP = ACTION_PIPELINE_DIR / "config" / "signal_map.json"
ARM_HOME = [0.0, 0.0]


def load_signal_map(path: Path) -> dict[str, str]:
    """Load signal-to-move-name mapping."""
    if not path.exists():
        raise FileNotFoundError(f"Missing signal map: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    signal_map = data.get("signal_map")
    if not isinstance(signal_map, dict):
        raise ValueError(f"{path} must contain a signal_map object")

    resolved: dict[str, str] = {}
    for signal, move_name in signal_map.items():
        if not isinstance(signal, str) or not signal:
            raise ValueError("signal_map keys must be non-empty strings")
        if not isinstance(move_name, str) or not move_name:
            raise ValueError(f"signal_map[{signal!r}] must be a non-empty move name")
        resolved[signal] = move_name
    return resolved


def configure_output_encoding() -> None:
    """Make emoji output safe in terminals with non-UTF-8 defaults."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


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


def resolve_signal(signal: str, signal_map: dict[str, str]) -> str:
    """Resolve one external signal to a move name."""
    if signal in signal_map:
        return signal_map[signal]
    normalized = signal.strip()
    if normalized in signal_map:
        return signal_map[normalized]
    available = " ".join(signal_map) or "(none)"
    raise ValueError(f"Unknown signal {signal!r}. Available signals: {available}")


def print_available(library_dir: Path, signal_map: dict[str, str]) -> None:
    """Print configured signals and generated file status."""
    print(f"Library: {library_dir}")
    print("Configured signals:")
    for signal, move_name in sorted(signal_map.items(), key=lambda item: item[1]):
        status = "ready" if (library_dir / f"{move_name}.json").exists() else "missing"
        print(f"  {signal!r:12} -> {move_name:24} [{status}]")

    move_files = sorted(library_dir.glob("*.json")) if library_dir.exists() else []
    print(f"\nGenerated moves: {len(move_files)}")


def play_move(args: argparse.Namespace, move_name: str) -> None:
    """Connect to the daemon and play one move by name."""
    from reachy_mini import ReachyMini
    from reachy_mini.motion.recorded_move import RecordedMoves

    move_path = args.library_dir / f"{move_name}.json"
    if not move_path.exists():
        raise FileNotFoundError(
            f"Missing {move_path}. Run action_pipeline/build_merged_library.py first."
        )

    recorded_moves = RecordedMoves(str(args.library_dir))
    move = recorded_moves.get(move_name)
    print("Connecting to Reachy Mini daemon at localhost...")
    with ReachyMini(
        connection_mode=args.connection_mode,
        media_backend=args.media_backend,
        automatic_body_yaw=False,
    ) as reachy:
        for index in range(args.repeat):
            print(f"Playing {move_name}, repeat {index + 1}/{args.repeat}.")
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
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--signal", help="External signal or emoji mapped in signal_map.json.")
    group.add_argument("--move", help="Direct move name, for example cheerful1.")
    group.add_argument("--list", action="store_true", help="List configured signals and exit.")
    parser.add_argument("--config", type=Path, default=DEFAULT_SIGNAL_MAP)
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--pause-between", type=float, default=1.0)
    parser.add_argument("--initial-goto-duration", type=float, default=1.0)
    parser.add_argument("--connection-mode", default="localhost_only")
    parser.add_argument("--media-backend", default="default")
    parser.add_argument("--sound", dest="sound", action="store_true")
    parser.add_argument("--no-sound", dest="sound", action="store_false", help=argparse.SUPPRESS)
    parser.add_argument("--no-final-home-check", dest="final_home_check", action="store_false")
    parser.add_argument("--home-tolerance-deg", type=float, default=5.0)
    parser.add_argument("--reset-duration", type=float, default=1.5)
    parser.add_argument("--reset-attempts", type=int, default=2)
    parser.set_defaults(sound=False, final_home_check=True)
    return parser


def main() -> None:
    """Run the action player."""
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

    move_name = args.move if args.move is not None else resolve_signal(args.signal, signal_map)
    play_move(args, move_name)


if __name__ == "__main__":
    main()
