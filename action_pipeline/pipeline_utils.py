"""Shared helpers for the manual arm recording action pipeline."""

from __future__ import annotations

import bisect
import copy
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Interpolation = Literal["linear", "minjerk"]


@dataclass(frozen=True)
class ArmClip:
    """Validated two-arm clip sampled from the physical robot."""

    clip_id: str
    label: str
    created_at: str
    sample_hz: float
    motor_mode: str
    duration: float
    time: list[float]
    left_arm: list[list[float]]
    right_arm: list[list[float]]
    path: Path | None = None


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object from disk."""
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a pretty JSON object with stable UTF-8 formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")


def minimum_jerk(alpha: float) -> float:
    """Return a smooth 0..1 interpolation scalar."""
    return 10 * alpha**3 - 15 * alpha**4 + 6 * alpha**5


def validate_arm_pair(value: Any, field_name: str) -> list[float]:
    """Validate one two-joint arm sample and return floats."""
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{field_name} must be a two-number list")
    try:
        return [float(value[0]), float(value[1])]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain numbers") from exc


def validate_time_series(values: Any, field_name: str) -> list[float]:
    """Validate a monotonic timestamp list."""
    if not isinstance(values, list) or len(values) < 2:
        raise ValueError(f"{field_name} must contain at least two timestamps")
    timestamps: list[float] = []
    previous = -math.inf
    for index, value in enumerate(values):
        try:
            timestamp = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name}[{index}] must be a number") from exc
        if timestamp < previous:
            raise ValueError(f"{field_name} must be sorted ascending")
        timestamps.append(timestamp)
        previous = timestamp
    if abs(timestamps[0]) > 1e-6:
        raise ValueError(f"{field_name} must start at 0.0")
    if timestamps[-1] <= 0.0:
        raise ValueError(f"{field_name} must span a positive duration")
    return timestamps


def arm_clip_to_json(
    *,
    clip_id: str,
    label: str,
    created_at: str,
    sample_hz: float,
    motor_mode: str,
    time_values: list[float],
    left_arm: list[list[float]],
    right_arm: list[list[float]],
) -> dict[str, Any]:
    """Build a serializable arm clip object."""
    return {
        "schema_version": 1,
        "clip_id": clip_id,
        "label": label,
        "created_at": created_at,
        "sample_hz": sample_hz,
        "motor_mode": motor_mode,
        "duration": time_values[-1] if time_values else 0.0,
        "time": time_values,
        "left_arm": left_arm,
        "right_arm": right_arm,
    }


def load_arm_clip(path: Path) -> ArmClip:
    """Load and validate an arm clip JSON file."""
    data = load_json(path)
    if data.get("schema_version") != 1:
        raise ValueError(f"{path} has unsupported schema_version")

    clip_id = data.get("clip_id")
    if not isinstance(clip_id, str) or not clip_id:
        raise ValueError(f"{path} must contain a non-empty clip_id")

    label = data.get("label", "")
    if not isinstance(label, str):
        raise ValueError(f"{path} label must be a string")

    created_at = data.get("created_at", "")
    if not isinstance(created_at, str):
        raise ValueError(f"{path} created_at must be a string")

    sample_hz = float(data.get("sample_hz", 0.0))
    if sample_hz <= 0.0:
        raise ValueError(f"{path} sample_hz must be positive")

    motor_mode = data.get("motor_mode", "")
    if not isinstance(motor_mode, str) or not motor_mode:
        raise ValueError(f"{path} motor_mode must be a non-empty string")

    timestamps = validate_time_series(data.get("time"), f"{path}.time")
    left_raw = data.get("left_arm")
    right_raw = data.get("right_arm")
    if not isinstance(left_raw, list) or not isinstance(right_raw, list):
        raise ValueError(f"{path} must contain left_arm and right_arm lists")
    if len(left_raw) != len(timestamps) or len(right_raw) != len(timestamps):
        raise ValueError(f"{path} time/left_arm/right_arm lengths must match")

    left_arm = [
        validate_arm_pair(sample, f"{path}.left_arm[{index}]")
        for index, sample in enumerate(left_raw)
    ]
    right_arm = [
        validate_arm_pair(sample, f"{path}.right_arm[{index}]")
        for index, sample in enumerate(right_raw)
    ]

    duration = float(data.get("duration", timestamps[-1]))
    if duration <= 0.0:
        raise ValueError(f"{path} duration must be positive")
    if abs(duration - timestamps[-1]) > 1e-3:
        raise ValueError(f"{path} duration must match the final timestamp")

    return ArmClip(
        clip_id=clip_id,
        label=label,
        created_at=created_at,
        sample_hz=sample_hz,
        motor_mode=motor_mode,
        duration=duration,
        time=timestamps,
        left_arm=left_arm,
        right_arm=right_arm,
        path=path,
    )


def load_arm_clips(directory: Path) -> dict[str, ArmClip]:
    """Load all arm clips from a directory keyed by clip_id."""
    if not directory.exists():
        raise FileNotFoundError(f"Arm clip directory does not exist: {directory}")

    clips: dict[str, ArmClip] = {}
    for path in sorted(directory.glob("*.json")):
        clip = load_arm_clip(path)
        if clip.clip_id in clips:
            raise ValueError(
                f"Duplicate arm clip id {clip.clip_id!r}: {clips[clip.clip_id].path} and {path}"
            )
        clips[clip.clip_id] = clip
    if not clips:
        raise ValueError(f"No arm clip JSON files found in {directory}")
    return clips


def iter_move_files(source_dir: Path) -> list[Path]:
    """Return source recorded-move JSON files from root and optional data/."""
    if not source_dir.exists():
        raise FileNotFoundError(f"Source library does not exist: {source_dir}")

    by_name: dict[str, Path] = {}
    for path in sorted(source_dir.glob("*.json")):
        by_name[path.stem] = path

    data_dir = source_dir / "data"
    if data_dir.is_dir():
        for path in sorted(data_dir.glob("*.json")):
            if path.stem in by_name:
                raise ValueError(f"Duplicate move name {path.stem!r} in {source_dir}")
            by_name[path.stem] = path

    if not by_name:
        raise ValueError(f"No recorded move JSON files found in {source_dir}")
    return [by_name[name] for name in sorted(by_name)]


def validate_source_move(
    move: dict[str, Any],
    source_path: Path,
) -> tuple[list[float], list[dict[str, Any]]]:
    """Validate a source recorded move and return timestamps and frames."""
    times_raw = move.get("time")
    frames_raw = move.get("set_target_data")
    if not isinstance(times_raw, list) or not isinstance(frames_raw, list):
        raise ValueError(f"{source_path} must contain list fields time and set_target_data")
    if len(times_raw) != len(frames_raw):
        raise ValueError(f"{source_path} time and set_target_data lengths differ")
    if len(times_raw) < 2:
        raise ValueError(f"{source_path} must contain at least two frames")

    timestamps: list[float] = []
    previous = -math.inf
    for index, value in enumerate(times_raw):
        try:
            timestamp = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{source_path} time[{index}] must be a number") from exc
        if timestamp < previous:
            raise ValueError(f"{source_path} timestamps must be sorted ascending")
        timestamps.append(timestamp)
        previous = timestamp

    frames: list[dict[str, Any]] = []
    for index, frame in enumerate(frames_raw):
        if not isinstance(frame, dict):
            raise ValueError(f"{source_path} frame {index} must be an object")
        if "head" not in frame:
            raise ValueError(f"{source_path} frame {index} is missing head")
        frames.append(frame)

    if timestamps[-1] - timestamps[0] <= 0.0:
        raise ValueError(f"{source_path} has a non-positive duration")
    return timestamps, frames


def interpolate_pair(
    previous_values: list[float],
    next_values: list[float],
    alpha: float,
) -> list[float]:
    """Interpolate one two-joint arm pair."""
    return [
        previous_values[0] + (next_values[0] - previous_values[0]) * alpha,
        previous_values[1] + (next_values[1] - previous_values[1]) * alpha,
    ]


def evaluate_arm_clip(
    clip: ArmClip,
    clip_time: float,
    interpolation: Interpolation,
) -> tuple[list[float], list[float]]:
    """Evaluate an arm clip at one clip-local timestamp."""
    if clip_time <= clip.time[0]:
        return list(clip.left_arm[0]), list(clip.right_arm[0])
    if clip_time >= clip.time[-1]:
        return list(clip.left_arm[-1]), list(clip.right_arm[-1])

    next_index = bisect.bisect_right(clip.time, clip_time)
    previous_index = next_index - 1
    previous_time = clip.time[previous_index]
    next_time = clip.time[next_index]
    span = next_time - previous_time
    alpha = 0.0 if span == 0.0 else (clip_time - previous_time) / span
    if interpolation == "minjerk":
        alpha = minimum_jerk(alpha)

    return (
        interpolate_pair(clip.left_arm[previous_index], clip.left_arm[next_index], alpha),
        interpolate_pair(clip.right_arm[previous_index], clip.right_arm[next_index], alpha),
    )


def merge_move_with_arm_clip(
    source_move: dict[str, Any],
    source_path: Path,
    clip: ArmClip,
    interpolation: Interpolation = "linear",
) -> dict[str, Any]:
    """Return a source move copy with left/right arms replaced by one stretched clip."""
    timestamps, _frames = validate_source_move(source_move, source_path)
    output = copy.deepcopy(source_move)
    output_frames = output["set_target_data"]
    source_start = timestamps[0]
    source_duration = timestamps[-1] - source_start

    for index, source_time in enumerate(timestamps):
        relative_source_time = source_time - source_start
        clip_time = (relative_source_time / source_duration) * clip.duration
        left_arm, right_arm = evaluate_arm_clip(clip, clip_time, interpolation)
        output_frames[index]["left_arm"] = left_arm
        output_frames[index]["right_arm"] = right_arm

    return output


def load_clip_map(path: Path) -> dict[str, str]:
    """Load move-name to arm-clip-id mapping from JSON."""
    data = load_json(path)
    if data.get("schema_version") != 1:
        raise ValueError(f"{path} has unsupported schema_version")
    moves = data.get("moves")
    if not isinstance(moves, dict):
        raise ValueError(f"{path} must contain a moves object")

    mapping: dict[str, str] = {}
    for move_name, clip_id in moves.items():
        if not isinstance(move_name, str) or not move_name:
            raise ValueError(f"{path} contains an invalid move name")
        if not isinstance(clip_id, str) or not clip_id:
            raise ValueError(
                f"{path} moves.{move_name} must be a non-empty clip id; fill the template first"
            )
        mapping[move_name] = clip_id
    return mapping


def validate_complete_mapping(
    move_files: list[Path],
    clips: dict[str, ArmClip],
    mapping: dict[str, str],
) -> None:
    """Ensure every source move has one existing arm clip and no typo entries."""
    source_names = {path.stem for path in move_files}
    mapped_names = set(mapping)
    missing_moves = sorted(source_names - mapped_names)
    extra_moves = sorted(mapped_names - source_names)
    unknown_clips = sorted({clip_id for clip_id in mapping.values() if clip_id not in clips})

    errors: list[str] = []
    if missing_moves:
        errors.append("missing mappings: " + ", ".join(missing_moves))
    if extra_moves:
        errors.append("unknown source moves in mapping: " + ", ".join(extra_moves))
    if unknown_clips:
        errors.append("unknown arm clips: " + ", ".join(unknown_clips))
    if errors:
        raise ValueError("; ".join(errors))


def make_clip_map_template(source_dir: Path) -> dict[str, Any]:
    """Create a fill-in template containing all source move names."""
    move_files = iter_move_files(source_dir)
    return {
        "schema_version": 1,
        "source_library": str(source_dir),
        "default_time_alignment": "stretch",
        "moves": {path.stem: None for path in move_files},
    }


def copy_matching_sound(source_path: Path, output_dir: Path) -> bool:
    """Copy a source WAV next to the generated JSON when present."""
    source_sound = source_path.with_suffix(".wav")
    if not source_sound.exists():
        return False
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_sound, output_dir / source_sound.name)
    return True
