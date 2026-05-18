"""Build the five common dual-arm emotion action JSON files.

This script keeps the original recorded head/body trajectory and regenerates
only left_arm/right_arm from human-readable degree presets.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ACTION_CALL_DIR = Path(__file__).resolve().parent
REPO_ROOT = ACTION_CALL_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from apply_arm_motion_spec import apply_arm_motion, write_json  # noqa: E402


@dataclass(frozen=True)
class EmotionAction:
    """One generated emotion action."""

    alias: str
    move_name: str
    label_zh: str
    preset_id: int
    main_deg: float
    swing_deg: float
    repeats: int


ACTIONS: tuple[EmotionAction, ...] = (
    EmotionAction("cheerful", "cheerful1", "快乐", 1, 30.0, 45.0, 2),
    EmotionAction("sad", "sad1", "悲伤", 5, -30.0, 30.0, 3),
    EmotionAction("fear", "fear1", "恐惧", 3, -60.0, 45.0, 4),
    EmotionAction("furious", "furious1", "愤怒", 4, -60.0, 60.0, 3),
    EmotionAction("surprised", "surprised1", "惊讶", 2, -60.0, 45.0, 3),
)


DEFAULT_SOURCE_DIR = REPO_ROOT / ".run" / "arm_emotions_library"
DEFAULT_LIBRARY_DIR = ACTION_CALL_DIR / "library"
DEFAULT_SPEC_DIR = ACTION_CALL_DIR / "arm_motion_specs"


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object from disk."""
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def source_duration(source_move: dict[str, Any], source_path: Path) -> float:
    """Return the source recorded move duration."""
    times = source_move.get("time")
    if not isinstance(times, list) or len(times) < 2:
        raise ValueError(f"{source_path} must contain at least two timestamps")
    duration = float(times[-1]) - float(times[0])
    if duration <= 0:
        raise ValueError(f"{source_path} has a non-positive duration")
    return duration


def add_keyframe(
    keyframes: list[dict[str, Any]],
    time_s: float,
    left_arm: tuple[float, float],
    right_arm: tuple[float, float],
) -> None:
    """Append one degree keyframe."""
    keyframes.append(
        {
            "time": time_s,
            "left_arm": [left_arm[0], left_arm[1]],
            "right_arm": [right_arm[0], right_arm[1]],
        }
    )


def raw_keyframes(action: EmotionAction, args: argparse.Namespace) -> list[dict[str, Any]]:
    """Build unscaled degree keyframes using the tested tuning timing."""
    right_main = -action.main_deg
    right_swing = -action.swing_deg

    keyframes: list[dict[str, Any]] = []
    t = 0.0
    add_keyframe(keyframes, t, (0.0, 0.0), (0.0, 0.0))

    t += args.home_duration
    add_keyframe(keyframes, t, (0.0, 0.0), (0.0, 0.0))

    t += args.main_duration
    add_keyframe(keyframes, t, (action.main_deg, 0.0), (right_main, 0.0))

    for _ in range(action.repeats):
        t += args.swing_duration
        add_keyframe(
            keyframes,
            t,
            (action.main_deg, action.swing_deg),
            (right_main, right_swing),
        )
        t += args.swing_duration
        add_keyframe(
            keyframes,
            t,
            (action.main_deg, -action.swing_deg),
            (right_main, -right_swing),
        )

    t += args.second_return_duration
    add_keyframe(keyframes, t, (action.main_deg, 0.0), (right_main, 0.0))

    t += args.main_return_duration
    add_keyframe(keyframes, t, (0.0, 0.0), (0.0, 0.0))

    return keyframes


def scaled_spec(
    action: EmotionAction,
    source_move: dict[str, Any],
    source_path: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Create the human-readable spec scaled to the source move duration."""
    raw = raw_keyframes(action, args)
    raw_duration = float(raw[-1]["time"])
    target_duration = source_duration(source_move, source_path)
    scale = target_duration / raw_duration

    keyframes = []
    for frame in raw:
        keyframes.append(
            {
                "time": float(frame["time"]) * scale,
                "left_arm": frame["left_arm"],
                "right_arm": frame["right_arm"],
            }
        )

    # Keep the last keyframe exact so the final recorded frame evaluates home.
    keyframes[-1]["time"] = target_duration

    return {
        "description": (
            f"{action.label_zh} / {action.alias}: generated from preset "
            f"{action.preset_id} for {action.move_name}."
        ),
        "units": "deg",
        "source_move": action.move_name,
        "emotion": action.alias,
        "emotion_label_zh": action.label_zh,
        "preset": {
            "id": action.preset_id,
            "main_left_deg": action.main_deg,
            "main_right_deg": -action.main_deg,
            "swing_left_deg": action.swing_deg,
            "swing_right_deg": -action.swing_deg,
            "repeats": action.repeats,
        },
        "timing": {
            "source_duration": target_duration,
            "raw_sequence_duration": raw_duration,
            "time_scale": scale,
            "interpolation": args.interpolation,
        },
        "keyframes": keyframes,
    }


def copy_sound(source_dir: Path, library_dir: Path, move_name: str) -> bool:
    """Copy the matching WAV file if the source move has one."""
    source_wav = source_dir / f"{move_name}.wav"
    if not source_wav.exists():
        return False
    library_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_wav, library_dir / source_wav.name)
    return True


def build_one(action: EmotionAction, args: argparse.Namespace) -> None:
    """Generate one spec and merged recorded move."""
    source_path = args.source_dir / f"{action.move_name}.json"
    if not source_path.exists():
        raise FileNotFoundError(f"Missing source move: {source_path}")

    source_move = load_json(source_path)
    arm_spec = scaled_spec(action, source_move, source_path, args)
    generated = apply_arm_motion(
        source_move,
        arm_spec,
        interpolation=args.interpolation,
        max_abs_deg=args.max_abs_deg,
    )

    spec_path = args.spec_dir / f"{action.move_name}.json"
    output_path = args.library_dir / f"{action.move_name}.json"

    if not args.dry_run:
        write_json(spec_path, arm_spec)
        write_json(output_path, generated)
        sound_copied = False if args.no_copy_wav else copy_sound(args.source_dir, args.library_dir, action.move_name)
    else:
        sound_copied = (args.source_dir / f"{action.move_name}.wav").exists()

    print(
        f"{action.alias:9} -> {action.move_name:10} | "
        f"frames={len(generated['time']):4d} | "
        f"duration={arm_spec['timing']['source_duration']:.3f}s | "
        f"preset={action.preset_id} | "
        f"sound={'yes' if sound_copied else 'no'}"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR)
    parser.add_argument("--interpolation", choices=["linear", "minjerk"], default="linear")
    parser.add_argument("--max-abs-deg", type=float, default=100.0)
    parser.add_argument("--home-duration", type=float, default=1.2)
    parser.add_argument("--main-duration", type=float, default=0.8)
    parser.add_argument("--swing-duration", type=float, default=0.45)
    parser.add_argument("--second-return-duration", type=float, default=0.5)
    parser.add_argument("--main-return-duration", type=float, default=0.9)
    parser.add_argument("--no-copy-wav", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    """Generate the action_call library."""
    args = build_arg_parser().parse_args()
    args.source_dir = args.source_dir.resolve()
    args.library_dir = args.library_dir.resolve()
    args.spec_dir = args.spec_dir.resolve()

    print(f"Source:  {args.source_dir}")
    print(f"Library: {args.library_dir}")
    print(f"Specs:   {args.spec_dir}\n")
    for action in ACTIONS:
        build_one(action, args)


if __name__ == "__main__":
    main()
