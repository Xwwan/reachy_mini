"""Build a full head-plus-recorded-arms action library."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ACTION_PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = ACTION_PIPELINE_DIR.parent
SRC_DIR = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from action_pipeline.pipeline_utils import (  # noqa: E402
    copy_matching_sound,
    iter_move_files,
    load_arm_clips,
    load_clip_map,
    load_json,
    make_clip_map_template,
    merge_move_with_arm_clip,
    validate_complete_mapping,
    write_json,
)

DEFAULT_SOURCE_DIR = REPO_ROOT / ".run" / "arm_emotions_library"
DEFAULT_CLIP_DIR = ACTION_PIPELINE_DIR / "arm_clips"
DEFAULT_MAP_PATH = ACTION_PIPELINE_DIR / "config" / "arm_clip_map.json"
DEFAULT_LIBRARY_DIR = ACTION_PIPELINE_DIR / "library"


def write_template(args: argparse.Namespace) -> None:
    """Write a fill-in mapping template for all source moves."""
    if args.map_path.exists() and not args.overwrite:
        raise FileExistsError(f"{args.map_path} already exists. Pass --overwrite to replace it.")
    template = make_clip_map_template(args.source_dir)
    write_json(args.map_path, template)
    print(f"Wrote mapping template with {len(template['moves'])} moves: {args.map_path}")


def build_library(args: argparse.Namespace) -> None:
    """Build the merged recorded-move library."""
    move_files = iter_move_files(args.source_dir)
    clips = load_arm_clips(args.clip_dir)
    mapping = load_clip_map(args.map_path)
    validate_complete_mapping(move_files, clips, mapping)

    print(f"Source:  {args.source_dir}")
    print(f"Clips:   {args.clip_dir} ({len(clips)} clips)")
    print(f"Map:     {args.map_path}")
    print(f"Library: {args.output_dir}\n")

    generated_count = 0
    copied_sound_count = 0
    for source_path in move_files:
        source_move = load_json(source_path)
        clip_id = mapping[source_path.stem]
        merged = merge_move_with_arm_clip(
            source_move,
            source_path,
            clips[clip_id],
            interpolation=args.interpolation,
        )
        output_path = args.output_dir / source_path.name
        sound_copied = False
        if not args.dry_run:
            write_json(output_path, merged)
            if not args.no_copy_wav:
                sound_copied = copy_matching_sound(source_path, args.output_dir)
        else:
            sound_copied = source_path.with_suffix(".wav").exists()

        generated_count += 1
        copied_sound_count += 1 if sound_copied else 0
        print(
            f"{source_path.stem:24} <- {clip_id:24} "
            f"frames={len(merged['time']):4d} sound={'yes' if sound_copied else 'no'}"
        )

    action = "Validated" if args.dry_run else "Generated"
    print(
        f"\n{action} {generated_count} moves; "
        f"sounds={'not copied' if args.no_copy_wav else copied_sound_count}."
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--clip-dir", type=Path, default=DEFAULT_CLIP_DIR)
    parser.add_argument("--map", dest="map_path", type=Path, default=DEFAULT_MAP_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--interpolation", choices=["linear", "minjerk"], default="linear")
    parser.add_argument("--init-map-template", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-copy-wav", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    """Run the library builder."""
    args = build_arg_parser().parse_args()
    args.source_dir = args.source_dir.resolve()
    args.clip_dir = args.clip_dir.resolve()
    args.map_path = args.map_path.resolve()
    args.output_dir = args.output_dir.resolve()

    if args.init_map_template:
        write_template(args)
        return
    build_library(args)


if __name__ == "__main__":
    main()
