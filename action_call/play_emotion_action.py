"""Play one generated dual-arm emotion action through the Reachy Mini daemon."""

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

EMOTION_ACTIONS = {
    "cheerful": ("cheerful1", "快乐"),
    "sad": ("sad1", "悲伤"),
    "fear": ("fear1", "恐惧"),
    "furious": ("furious1", "愤怒"),
    "surprised": ("surprised1", "惊讶"),
}

EMOTION_ALIASES = {
    "happy": "cheerful",
    "joy": "cheerful",
    "scared": "fear",
    "angry": "furious",
    "surprise": "surprised",
}

DISPLAY_ORDER = tuple(EMOTION_ACTIONS)


def configure_output_encoding() -> None:
    """Make emoji output safe on Windows terminals using non-UTF-8 defaults."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def canonical_emotion(value: str) -> str | None:
    """Return the canonical emotion name for a raw value."""
    normalized = value.strip().lower()
    if normalized in EMOTION_ACTIONS:
        return normalized
    return EMOTION_ALIASES.get(normalized)


def load_signal_map(config_path: Path) -> dict[str, str]:
    """Load and validate the emoji/signal to emotion mapping."""
    if not config_path.exists():
        raise FileNotFoundError(f"Missing config file: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        config = json.load(file)

    signal_map = config.get("signal_map")
    if not isinstance(signal_map, dict):
        raise ValueError(f"{config_path} must contain a JSON object field named signal_map.")

    resolved_map: dict[str, str] = {}
    supported = ", ".join(DISPLAY_ORDER)
    for signal, emotion in signal_map.items():
        if not isinstance(signal, str) or not signal:
            raise ValueError("signal_map keys must be non-empty strings.")
        if not isinstance(emotion, str):
            raise ValueError(f"signal_map[{signal!r}] must be a string emotion.")

        resolved_emotion = canonical_emotion(emotion)
        if resolved_emotion is None:
            raise ValueError(
                f"signal_map[{signal!r}] has unsupported emotion {emotion!r}; "
                f"expected one of: {supported}."
            )
        resolved_map[signal] = resolved_emotion

    return resolved_map


def resolve_signal_to_emotion(signal: str, signal_map: dict[str, str]) -> str:
    """Resolve a caller-provided emoji/signal into one of the five emotions."""
    if signal in signal_map:
        return signal_map[signal]

    legacy_emotion = canonical_emotion(signal)
    if legacy_emotion is not None:
        return legacy_emotion

    available_signals = " ".join(signal_map) or "(none)"
    raise ValueError(
        f"Unknown signal {signal!r}. Add it to {CONFIG_PATH.name} signal_map. "
        f"Available signals: {available_signals}"
    )


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


def print_available_emotions(library_dir: Path, signal_map: dict[str, str]) -> None:
    """Print the supported signal to emotion mapping."""
    print(f"Library: {library_dir}")
    print("Available signal map:")
    for emotion in DISPLAY_ORDER:
        move_name, label_zh = EMOTION_ACTIONS[emotion]
        status = "ready" if (library_dir / f"{move_name}.json").exists() else "missing"
        signals = " ".join(
            signal for signal, mapped_emotion in signal_map.items() if mapped_emotion == emotion
        )
        print(f"  {emotion:9} {label_zh:4} -> {move_name:10} [{status}] signals: {signals}")
    print("\nLegacy emotion aliases are still accepted: happy=cheerful, angry=furious, scared=fear.")


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
    """Play a mapped signal using an already-open Reachy Mini connection."""
    from reachy_mini.motion.recorded_move import RecordedMoves

    if repeat < 1:
        raise ValueError("repeat must be >= 1")

    config_path = config_path.resolve()
    library_dir = library_dir.resolve()
    signal_map = load_signal_map(config_path)
    emotion = resolve_signal_to_emotion(signal, signal_map)
    move_name, label_zh = EMOTION_ACTIONS[emotion]
    move_path = library_dir / f"{move_name}.json"
    if not move_path.exists():
        raise FileNotFoundError(
            f"Missing {move_path}. Run python .\\action_call\\build_action_library.py first."
        )

    recorded_moves = RecordedMoves(str(library_dir))
    move = recorded_moves.get(move_name)
    for index in range(repeat):
        print(
            f"Playing {signal!r} -> {emotion} / {label_zh} "
            f"({move_name}), repeat {index + 1}/{repeat}."
        )
        reachy.play_move(
            move,
            initial_goto_duration=initial_goto_duration,
            sound=sound,
        )
        if final_home_check:
            ensure_arms_home(
                reachy,
                tolerance_deg=home_tolerance_deg,
                reset_duration=reset_duration,
                reset_attempts=reset_attempts,
            )
        if index < repeat - 1:
            time.sleep(pause_between)
    return emotion


def play_emotion(args: argparse.Namespace) -> None:
    """Connect to the daemon and play one emotion."""
    from reachy_mini import ReachyMini
    from reachy_mini.motion.recorded_move import RecordedMoves

    move_name, label_zh = EMOTION_ACTIONS[args.emotion]
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
                f"Playing {args.signal!r} -> {args.emotion} / {label_zh} "
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
    group.add_argument(
        "--signal",
        "--emoji",
        "--emotion",
        dest="signal",
        help="Emoji/signal to play. --emotion is kept as a legacy option name.",
    )
    group.add_argument("--list", action="store_true", help="List generated emotions and exit.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--library-dir", type=Path, default=LIBRARY_DIR)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--pause-between", type=float, default=1.0)
    parser.add_argument("--initial-goto-duration", type=float, default=1.0)
    parser.add_argument("--connection-mode", default="localhost_only")
    parser.add_argument("--media-backend", default="default")
    parser.add_argument(
        "--sound",
        dest="sound",
        action="store_true",
        help="Play the WAV sound together with the motion.",
    )
    parser.add_argument(
        "--no-sound",
        dest="sound",
        action="store_false",
        help=argparse.SUPPRESS,
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
    parser.set_defaults(final_home_check=True, sound=False)
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
        print_available_emotions(args.library_dir, signal_map)
        return

    args.emotion = resolve_signal_to_emotion(args.signal, signal_map)
    play_emotion(args)


if __name__ == "__main__":
    main()
