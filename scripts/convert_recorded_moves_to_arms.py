"""Convert recorded antenna moves into two-DOF arm moves.

The official Reachy Mini recorded-move datasets store each frame as:

    {"head": ..., "antennas": [right, left], "body_yaw": ...}

This helper keeps the root fields such as ``description`` and ``time`` intact,
removes ``antennas`` from every frame, and writes:

    {"head": ..., "left_arm": [left_joint_1, left_joint_2],
     "right_arm": [right_joint_1, right_joint_2], "body_yaw": ...}

The generated files can be used as a first pass for the local rmmc fork that
exposes ``left_arm`` and ``right_arm`` instead of ``antennas``.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from huggingface_hub import snapshot_download

DEFAULT_DATASET = "pollen-robotics/reachy-mini-emotions-library"


def resolve_dataset(dataset: str) -> Path:
    """Return a local path for either a filesystem dataset or a HF dataset."""
    dataset_path = Path(dataset)
    if dataset_path.exists():
        return dataset_path

    return Path(snapshot_download(dataset, repo_type="dataset"))


def iter_move_files(dataset_path: Path) -> list[Path]:
    """Find JSON move files in the root folder and optional data/ folder."""
    move_files = sorted(dataset_path.glob("*.json"))
    data_dir = dataset_path / "data"
    if data_dir.is_dir():
        move_files.extend(sorted(data_dir.glob("*.json")))
    return move_files


def convert_frame(
    frame: dict[str, Any],
    arm_scale: float,
    left_second_joint: float,
    right_second_joint: float,
    swap_arms: bool,
) -> dict[str, Any]:
    """Convert one frame from antenna joints to arm joints."""
    converted = {key: value for key, value in frame.items() if key != "antennas"}

    antennas = frame.get("antennas")
    if antennas is None:
        if "left_arm" not in frame or "right_arm" not in frame:
            raise ValueError("frame has neither antennas nor left_arm/right_arm")
        return converted

    if len(antennas) != 2:
        raise ValueError(f"expected two antenna joints, got {len(antennas)}")

    # Official convention is [right_antenna, left_antenna].
    right_joint = float(antennas[0]) * arm_scale
    left_joint = float(antennas[1]) * arm_scale
    if swap_arms:
        left_joint, right_joint = right_joint, left_joint

    converted["left_arm"] = [left_joint, left_second_joint]
    converted["right_arm"] = [right_joint, right_second_joint]
    return converted


def convert_move(
    source: Path,
    destination: Path,
    arm_scale: float,
    left_second_joint: float,
    right_second_joint: float,
    swap_arms: bool,
) -> None:
    """Convert one recorded move JSON file."""
    with source.open("r", encoding="utf-8") as fp:
        move = json.load(fp)

    frames = move["set_target_data"]
    move["set_target_data"] = [
        convert_frame(
            frame,
            arm_scale=arm_scale,
            left_second_joint=left_second_joint,
            right_second_joint=right_second_joint,
            swap_arms=swap_arms,
        )
        for frame in frames
    ]

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as fp:
        json.dump(move, fp, indent=2)
        fp.write("\n")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        help="HuggingFace dataset name or local dataset folder.",
    )
    parser.add_argument(
        "--output",
        default=".run/arm_emotions_library",
        help="Output folder for converted JSON/WAV files.",
    )
    parser.add_argument(
        "--arm-scale",
        type=float,
        default=1.0,
        help="Scale applied to the original antenna joint angles.",
    )
    parser.add_argument(
        "--left-second-joint",
        type=float,
        default=0.0,
        help="Constant radian target for the second left-arm DOF.",
    )
    parser.add_argument(
        "--right-second-joint",
        type=float,
        default=0.0,
        help="Constant radian target for the second right-arm DOF.",
    )
    parser.add_argument(
        "--swap-arms",
        action="store_true",
        help="Swap the original left/right antenna sources before writing arms.",
    )
    parser.add_argument(
        "--no-copy-sounds",
        action="store_true",
        help="Do not copy matching .wav files into the output folder.",
    )
    args = parser.parse_args()

    dataset_path = resolve_dataset(args.dataset)
    output_path = Path(args.output)
    move_files = iter_move_files(dataset_path)
    if not move_files:
        raise SystemExit(f"No JSON move files found in {dataset_path}")

    for source in move_files:
        destination = output_path / source.name
        convert_move(
            source,
            destination,
            arm_scale=args.arm_scale,
            left_second_joint=args.left_second_joint,
            right_second_joint=args.right_second_joint,
            swap_arms=args.swap_arms,
        )

        sound = source.with_suffix(".wav")
        if sound.exists() and not args.no_copy_sounds:
            shutil.copy2(sound, output_path / sound.name)

    print(f"Converted {len(move_files)} moves")
    print(f"Source: {dataset_path}")
    print(f"Output: {output_path.resolve()}")


if __name__ == "__main__":
    main()
