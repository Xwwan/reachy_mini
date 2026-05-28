"""Interactively test all generated action_call moves one by one."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ACTION_CALL_DIR = Path(__file__).resolve().parent
REPO_ROOT = ACTION_CALL_DIR.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(ACTION_CALL_DIR) not in sys.path:
    sys.path.insert(0, str(ACTION_CALL_DIR))

from play_emotion_action import (  # noqa: E402
    CONFIG_PATH,
    LIBRARY_DIR,
    configure_output_encoding,
    ensure_arms_home,
    load_signal_map,
    reset_full_body_home,
)

EXTRA_MOVE = "test_arm_002"
DEFAULT_LOG_PATH = ACTION_CALL_DIR / "test_results.jsonl"


def load_action_order(library_dir: Path) -> list[str]:
    """Return the generated move order, with the extra test move last."""
    moves = sorted(path.stem for path in library_dir.glob("*.json"))
    if EXTRA_MOVE in moves:
        moves.remove(EXTRA_MOVE)
        moves.append(EXTRA_MOVE)
    return moves


def build_keys_by_move(signal_map: dict[str, str]) -> dict[str, list[str]]:
    """Group configured signal keys by generated move name."""
    keys_by_move: dict[str, list[str]] = {}
    for key, move_name in signal_map.items():
        keys_by_move.setdefault(move_name, []).append(key)
    return {move_name: sorted(keys) for move_name, keys in keys_by_move.items()}


def compact_keys(keys: list[str], limit: int = 8) -> str:
    """Format a short key preview."""
    if not keys:
        return "-"
    shown = keys[:limit]
    suffix = "" if len(keys) <= limit else f" ... (+{len(keys) - limit})"
    return " ".join(shown) + suffix


def append_log(log_path: Path, event: dict[str, Any]) -> None:
    """Append one JSONL test event."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as file:
        json.dump(event, file, ensure_ascii=False)
        file.write("\n")


def parse_start_from(value: str | None, moves: list[str]) -> int:
    """Resolve --start-from as 1-based index or move name."""
    if value is None:
        return 0
    if value.isdigit():
        index = int(value) - 1
        if index < 0 or index >= len(moves):
            raise ValueError(f"--start-from index must be between 1 and {len(moves)}")
        return index
    if value not in moves:
        raise ValueError(f"Unknown --start-from move {value!r}")
    return moves.index(value)


def validate_library(library_dir: Path, moves: list[str]) -> None:
    """Validate the action library before connecting to the robot."""
    missing = [move for move in moves if not (library_dir / f"{move}.json").exists()]
    if missing:
        raise FileNotFoundError(
            "Missing generated moves. Rebuild with action_call/build_action_library.py: "
            + ", ".join(missing)
        )
    if len(moves) != 82:
        raise ValueError(f"Expected 82 generated moves, found {len(moves)} in {library_dir}")


def print_action_list(moves: list[str], keys_by_move: dict[str, list[str]]) -> None:
    """Print all moves in test order."""
    for index, move_name in enumerate(moves, start=1):
        print(f"{index:02d}/82 {move_name:24} keys: {compact_keys(keys_by_move.get(move_name, []))}")


def play_one(
    reachy: object,
    recorded_moves: object,
    move_name: str,
    args: argparse.Namespace,
) -> None:
    """Play one move."""
    move = recorded_moves.get(move_name)
    reachy.enable_motors()
    time.sleep(args.enable_delay)
    reachy.play_move(
        move,
        initial_goto_duration=args.initial_goto_duration,
        sound=args.sound,
    )
    if args.final_home_check:
        reset_full_body_home(reachy, duration=args.reset_duration)
        ensure_arms_home(
            reachy,
            tolerance_deg=args.home_tolerance_deg,
            reset_duration=args.reset_duration,
            reset_attempts=args.reset_attempts,
        )


def interactive_test(args: argparse.Namespace) -> None:
    """Run the interactive full-library test."""
    from reachy_mini import ReachyMini
    from reachy_mini.motion.recorded_move import RecordedMoves

    signal_map = load_signal_map(args.config)
    keys_by_move = build_keys_by_move(signal_map)
    moves = load_action_order(args.library_dir)
    validate_library(args.library_dir, moves)
    start_index = parse_start_from(args.start_from, moves)

    if args.list:
        print_action_list(moves, keys_by_move)
        return

    print(f"Library: {args.library_dir}")
    print(f"Moves:   {len(moves)}")
    print(f"Log:     {args.log_path}")
    print("Controls before play: Enter/p=play, s=skip, q=quit")
    print("Controls after play:  Enter=ok next, r=replay, b=bad next, q=quit")
    print()

    recorded_moves = RecordedMoves(str(args.library_dir))
    with ReachyMini(
        connection_mode=args.connection_mode,
        media_backend=args.media_backend,
        automatic_body_yaw=False,
    ) as reachy:
        for index in range(start_index, len(moves)):
            move_name = moves[index]
            keys = compact_keys(keys_by_move.get(move_name, []))

            while True:
                print(f"\n[{index + 1:02d}/82] {move_name}")
                print(f"keys: {keys}")
                command = "p" if args.auto else input("play? ").strip().lower()
                if command in ("", "p", "play"):
                    break
                if command in ("s", "skip"):
                    append_log(
                        args.log_path,
                        {
                            "time": datetime.now().isoformat(timespec="seconds"),
                            "move": move_name,
                            "index": index + 1,
                            "status": "skipped",
                        },
                    )
                    print("skipped")
                    break
                if command in ("q", "quit", "exit"):
                    print("quit")
                    return
                print("Unknown command. Use Enter/p, s, or q.")

            if command in ("s", "skip"):
                continue

            while True:
                print(f"Playing {move_name}...")
                play_one(reachy, recorded_moves, move_name, args)

                if args.auto:
                    append_log(
                        args.log_path,
                        {
                            "time": datetime.now().isoformat(timespec="seconds"),
                            "move": move_name,
                            "index": index + 1,
                            "status": "played_auto",
                        },
                    )
                    time.sleep(args.pause_between)
                    break

                result = input("result? ").strip().lower()
                if result in ("", "ok", "y", "yes"):
                    append_log(
                        args.log_path,
                        {
                            "time": datetime.now().isoformat(timespec="seconds"),
                            "move": move_name,
                            "index": index + 1,
                            "status": "ok",
                        },
                    )
                    break
                if result in ("r", "replay"):
                    append_log(
                        args.log_path,
                        {
                            "time": datetime.now().isoformat(timespec="seconds"),
                            "move": move_name,
                            "index": index + 1,
                            "status": "replay",
                        },
                    )
                    continue
                if result in ("b", "bad", "n", "no"):
                    append_log(
                        args.log_path,
                        {
                            "time": datetime.now().isoformat(timespec="seconds"),
                            "move": move_name,
                            "index": index + 1,
                            "status": "bad",
                        },
                    )
                    break
                if result in ("q", "quit", "exit"):
                    print("quit")
                    return
                print("Unknown command. Use Enter/ok, r, b, or q.")


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--library-dir", type=Path, default=LIBRARY_DIR)
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--start-from", help="1-based index or move name to resume from.")
    parser.add_argument("--list", action="store_true", help="List test order and exit.")
    parser.add_argument("--auto", action="store_true", help="Play all moves without prompts.")
    parser.add_argument("--pause-between", type=float, default=1.0)
    parser.add_argument("--initial-goto-duration", type=float, default=1.0)
    parser.add_argument("--enable-delay", type=float, default=0.2)
    parser.add_argument("--connection-mode", default="localhost_only")
    parser.add_argument("--media-backend", default="no_media")
    parser.add_argument("--sound", dest="sound", action="store_true")
    parser.add_argument("--no-sound", dest="sound", action="store_false", help=argparse.SUPPRESS)
    parser.add_argument(
        "--no-final-home-check",
        dest="final_home_check",
        action="store_false",
        help="Do not return head/body/arms to initial position after each action.",
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
    args.log_path = args.log_path.resolve()
    if args.pause_between < 0.0:
        raise ValueError("--pause-between must be >= 0")
    interactive_test(args)


if __name__ == "__main__":
    main()
