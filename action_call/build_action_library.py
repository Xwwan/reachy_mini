"""Build the 82-action library from official head moves and recorded arm clips."""

from __future__ import annotations

import argparse
import copy
import shutil
import sys
from pathlib import Path
from typing import Any

ACTION_CALL_DIR = Path(__file__).resolve().parent
REPO_ROOT = ACTION_CALL_DIR.parent
SRC_DIR = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from action_pipeline.pipeline_utils import (  # noqa: E402
    ArmClip,
    iter_move_files,
    load_arm_clip,
    load_json,
    merge_move_with_arm_clip,
    write_json,
)

DEFAULT_SOURCE_DIR = REPO_ROOT / ".run" / "arm_emotions_library"
DEFAULT_CLIP_DIR = REPO_ROOT / "action_pipeline" / "arm_clips"
DEFAULT_LIBRARY_DIR = ACTION_CALL_DIR / "library"
EXTRA_STATIC_MOVE = "test_arm_002"

STATIC_HEAD = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]

CLIP_TO_MOVES: dict[str, tuple[str, ...]] = {
    "open_welcome": (
        "grateful1",
        "helpful1",
        "helpful2",
        "loving1",
        "proud1",
        "welcoming1",
    ),
    "come_here": ("come1",),
    "single_wave": ("cheerful1",),
    "double_wave": ("laughing2", "welcoming2"),
    "arms_raise": ("amazed1", "electric1", "surprised1", "surprised2"),
    "victory_lift": ("enthusiastic1", "proud2", "proud3", "success1", "success2"),
    "excited_bounce": ("enthusiastic2", "laughing1"),
    "dance_swing": ("dance1", "dance2", "dance3"),
    "calm_down": ("calming1", "relief1", "relief2", "serenity1"),
    "question_shrug": (
        "confused1",
        "incomprehensible2",
        "inquiring1",
        "inquiring2",
        "inquiring3",
        "uncertain1",
    ),
    "thinking_pose": ("curious1", "lost1", "thoughtful1", "thoughtful2"),
    "understanding_open": (
        "attentive1",
        "attentive2",
        "understanding1",
        "understanding2",
        "yes1",
    ),
    "sad_drop": ("downcast1", "no_sad1", "resigned1", "sad1", "sad2", "yes_sad1"),
    "tired_slump": (
        "boredom1",
        "boredom2",
        "dying1",
        "exhausted1",
        "lonely1",
        "sleep1",
        "tired1",
    ),
    "shy_cover": ("shy1", "uncomfortable1"),
    "fear_guard": ("anxiety1", "fear1", "scared1"),
    "push_away": (
        "disgusted1",
        "displeased1",
        "displeased2",
        "go_away1",
        "impatient1",
        "impatient2",
        "irritated1",
    ),
    "no_cross": ("contempt1", "indifferent1", "no1", "no_excited1"),
    "angry_slam": (
        "frustrated1",
        "furious1",
        "irritated2",
        "rage1",
        "reprimand1",
        "reprimand2",
        "reprimand3",
    ),
    "oops_flinch": ("oops1", "oops2"),
}


def build_move_to_clip() -> dict[str, str]:
    """Invert the configured clip-to-move mapping."""
    move_to_clip: dict[str, str] = {}
    for clip_id, move_names in CLIP_TO_MOVES.items():
        for move_name in move_names:
            if move_name in move_to_clip:
                raise ValueError(f"Duplicate move mapping: {move_name}")
            move_to_clip[move_name] = clip_id
    return move_to_clip


def validate_mapping(source_moves: set[str], move_to_clip: dict[str, str]) -> None:
    """Ensure the mapping covers exactly the official source moves."""
    mapped_moves = set(move_to_clip)
    missing = sorted(source_moves - mapped_moves)
    extra = sorted(mapped_moves - source_moves)
    errors: list[str] = []
    if missing:
        errors.append("missing official moves: " + ", ".join(missing))
    if extra:
        errors.append("unknown official moves: " + ", ".join(extra))
    if errors:
        raise ValueError("; ".join(errors))


def load_required_clips(clip_dir: Path, required_clip_ids: set[str]) -> dict[str, ArmClip]:
    """Load required arm clips and report all missing clips at once."""
    missing = sorted(
        clip_id for clip_id in required_clip_ids if not (clip_dir / f"{clip_id}.json").exists()
    )
    if missing:
        commands = "\n".join(
            f"/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py "
            f"--clip-id {clip_id} --label \"{clip_id.replace('_', ' ').title()}\" --overwrite"
            for clip_id in missing
        )
        raise FileNotFoundError(
            "Missing required arm clips:\n"
            + "\n".join(f"  - {clip_id}" for clip_id in missing)
            + "\n\nRecord missing clips with:\n"
            + commands
        )

    return {
        clip_id: load_arm_clip(clip_dir / f"{clip_id}.json")
        for clip_id in sorted(required_clip_ids)
    }


def copy_sound(source_path: Path, output_dir: Path) -> bool:
    """Copy matching WAV when present."""
    source_wav = source_path.with_suffix(".wav")
    if not source_wav.exists():
        return False
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_wav, output_dir / source_wav.name)
    return True


def make_static_head_move(clip: ArmClip) -> dict[str, Any]:
    """Create a static head/body move from an arm clip."""
    return {
        "description": f"Static head/body with arm clip {clip.clip_id}",
        "time": list(clip.time),
        "set_target_data": [
            {
                "head": copy.deepcopy(STATIC_HEAD),
                "body_yaw": 0.0,
                "check_collision": False,
                "left_arm": list(left_arm),
                "right_arm": list(right_arm),
            }
            for left_arm, right_arm in zip(clip.left_arm, clip.right_arm)
        ],
    }


def build_library(args: argparse.Namespace) -> None:
    """Build all 82 action JSON files."""
    move_files = iter_move_files(args.source_dir)
    source_moves = {path.stem for path in move_files}
    move_to_clip = build_move_to_clip()
    validate_mapping(source_moves, move_to_clip)

    required_clip_ids = set(CLIP_TO_MOVES) | {EXTRA_STATIC_MOVE}
    clips = load_required_clips(args.clip_dir, required_clip_ids)

    print(f"Source:  {args.source_dir}")
    print(f"Clips:   {args.clip_dir}")
    print(f"Library: {args.output_dir}\n")

    generated = 0
    copied_wav = 0
    if not args.dry_run:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    for source_path in move_files:
        move_name = source_path.stem
        clip_id = move_to_clip[move_name]
        source_move = load_json(source_path)
        merged = merge_move_with_arm_clip(
            source_move,
            source_path,
            clips[clip_id],
            interpolation=args.interpolation,
        )
        merged["description"] = f"{move_name}: official head/body + arm clip {clip_id}"
        if not args.dry_run:
            write_json(args.output_dir / source_path.name, merged)
            if not args.no_copy_wav and copy_sound(source_path, args.output_dir):
                copied_wav += 1
        generated += 1
        print(f"{move_name:20} <- {clip_id:20} frames={len(merged['time']):4d}")

    static_move = make_static_head_move(clips[EXTRA_STATIC_MOVE])
    if not args.dry_run:
        write_json(args.output_dir / f"{EXTRA_STATIC_MOVE}.json", static_move)
    generated += 1
    print(f"{EXTRA_STATIC_MOVE:20} <- {EXTRA_STATIC_MOVE:20} frames={len(static_move['time']):4d}")

    action = "Validated" if args.dry_run else "Generated"
    print(f"\n{action} {generated} moves; copied_wav={copied_wav}.")


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--clip-dir", type=Path, default=DEFAULT_CLIP_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--interpolation", choices=["linear", "minjerk"], default="linear")
    parser.add_argument("--no-copy-wav", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    """Run the action library builder."""
    args = build_arg_parser().parse_args()
    args.source_dir = args.source_dir.resolve()
    args.clip_dir = args.clip_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    build_library(args)


if __name__ == "__main__":
    main()
