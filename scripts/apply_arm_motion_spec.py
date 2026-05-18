"""Apply a human-readable arm motion spec to a recorded move JSON.

The source recorded move keeps its original head/body timing. Only
``left_arm`` and ``right_arm`` are regenerated from the keyframes.

Example:

    python scripts/apply_arm_motion_spec.py ^
      --source .run/arm_emotions_library/amazed1.json ^
      --arm-spec .run/arm_motion_specs/amazed1.json ^
      --output .run/arm_emotions_library_generated/amazed1.json
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any, Literal


Interpolation = Literal["linear", "minjerk"]


def minimum_jerk(alpha: float) -> float:
    """Return a smooth 0..1 interpolation scalar."""
    return 10 * alpha**3 - 15 * alpha**4 + 6 * alpha**5


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object."""
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def validate_arm_pair(value: Any, field_name: str) -> list[float]:
    """Validate and normalize one two-joint arm target."""
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{field_name} must be a two-number list")
    try:
        return [float(value[0]), float(value[1])]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain numbers") from exc


def load_keyframes(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate keyframes and convert arm targets to radians."""
    units = spec.get("units", "deg")
    if units not in ("deg", "rad"):
        raise ValueError("arm spec units must be 'deg' or 'rad'")
    scale = math.pi / 180.0 if units == "deg" else 1.0

    keyframes_raw = spec.get("keyframes")
    if not isinstance(keyframes_raw, list) or len(keyframes_raw) < 2:
        raise ValueError("arm spec must contain at least two keyframes")

    keyframes: list[dict[str, Any]] = []
    last_time = -math.inf
    for index, keyframe in enumerate(keyframes_raw):
        if not isinstance(keyframe, dict):
            raise ValueError(f"keyframe {index} must be an object")
        try:
            time_s = float(keyframe["time"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"keyframe {index} has an invalid time") from exc
        if time_s < last_time:
            raise ValueError("keyframe times must be sorted ascending")
        last_time = time_s

        left = validate_arm_pair(keyframe.get("left_arm"), f"keyframe {index}.left_arm")
        right = validate_arm_pair(keyframe.get("right_arm"), f"keyframe {index}.right_arm")
        keyframes.append(
            {
                "time": time_s,
                "left_arm": [left[0] * scale, left[1] * scale],
                "right_arm": [right[0] * scale, right[1] * scale],
            }
        )
    return keyframes


def lerp(v0: float, v1: float, alpha: float) -> float:
    """Linearly interpolate two floats."""
    return v0 + (v1 - v0) * alpha


def interpolate_pair(values0: list[float], values1: list[float], alpha: float) -> list[float]:
    """Interpolate a two-joint arm target."""
    return [lerp(values0[0], values1[0], alpha), lerp(values0[1], values1[1], alpha)]


def evaluate_keyframes(
    keyframes: list[dict[str, Any]],
    t: float,
    interpolation: Interpolation,
) -> tuple[list[float], list[float]]:
    """Evaluate arm keyframes at time t."""
    if t <= keyframes[0]["time"]:
        return list(keyframes[0]["left_arm"]), list(keyframes[0]["right_arm"])
    if t >= keyframes[-1]["time"]:
        return list(keyframes[-1]["left_arm"]), list(keyframes[-1]["right_arm"])

    for index in range(1, len(keyframes)):
        prev_frame = keyframes[index - 1]
        next_frame = keyframes[index]
        if t <= next_frame["time"]:
            span = next_frame["time"] - prev_frame["time"]
            alpha = 0.0 if span == 0.0 else (t - prev_frame["time"]) / span
            if interpolation == "minjerk":
                alpha = minimum_jerk(alpha)
            return (
                interpolate_pair(prev_frame["left_arm"], next_frame["left_arm"], alpha),
                interpolate_pair(prev_frame["right_arm"], next_frame["right_arm"], alpha),
            )

    return list(keyframes[-1]["left_arm"]), list(keyframes[-1]["right_arm"])


def validate_source_move(move: dict[str, Any]) -> tuple[list[float], list[dict[str, Any]]]:
    """Return validated recorded-move timestamps and frames."""
    times = move.get("time")
    frames = move.get("set_target_data")
    if not isinstance(times, list) or not isinstance(frames, list):
        raise ValueError("source must contain list fields 'time' and 'set_target_data'")
    if len(times) != len(frames):
        raise ValueError("source time and set_target_data lengths differ")
    if not times:
        raise ValueError("source move has no frames")
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise ValueError(f"source frame {index} must be an object")
    return [float(t) for t in times], frames


def validate_max_angle(
    left_arm: list[float],
    right_arm: list[float],
    max_abs_deg: float,
    t: float,
) -> None:
    """Reject arm targets outside the configured degree limit."""
    max_rad = math.radians(max_abs_deg)
    values = [*left_arm, *right_arm]
    biggest = max(abs(v) for v in values)
    if biggest > max_rad:
        raise ValueError(
            f"arm target at t={t:.3f}s reaches {math.degrees(biggest):.1f} deg, "
            f"above --max-abs-deg={max_abs_deg:.1f}"
        )


def apply_arm_motion(
    source_move: dict[str, Any],
    arm_spec: dict[str, Any],
    interpolation: Interpolation,
    max_abs_deg: float,
) -> dict[str, Any]:
    """Return a copy of source_move with generated left/right arm targets."""
    times, frames = validate_source_move(source_move)
    keyframes = load_keyframes(arm_spec)
    output = copy.deepcopy(source_move)
    out_frames = output["set_target_data"]
    start_time = times[0]

    for index, absolute_time in enumerate(times):
        relative_time = absolute_time - start_time
        left_arm, right_arm = evaluate_keyframes(keyframes, relative_time, interpolation)
        validate_max_angle(left_arm, right_arm, max_abs_deg, relative_time)
        out_frames[index]["left_arm"] = left_arm
        out_frames[index]["right_arm"] = right_arm

    return output


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write pretty JSON, creating the parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=False)
        fp.write("\n")


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Source recorded move JSON.")
    parser.add_argument("--arm-spec", required=True, type=Path, help="Human-readable arm motion spec.")
    parser.add_argument("--output", required=True, type=Path, help="Generated recorded move JSON.")
    parser.add_argument(
        "--interpolation",
        choices=["linear", "minjerk"],
        default="linear",
        help="Interpolation between human keyframes.",
    )
    parser.add_argument(
        "--max-abs-deg",
        type=float,
        default=60.0,
        help="Safety cap applied after converting generated targets to degrees.",
    )
    return parser


def main() -> None:
    """Apply the arm motion spec."""
    args = build_arg_parser().parse_args()
    source_move = load_json(args.source)
    arm_spec = load_json(args.arm_spec)
    output = apply_arm_motion(
        source_move,
        arm_spec,
        interpolation=args.interpolation,
        max_abs_deg=args.max_abs_deg,
    )
    write_json(args.output, output)
    print(
        f"Wrote {args.output} with {len(output['time'])} frames; "
        f"only left_arm/right_arm were regenerated."
    )


if __name__ == "__main__":
    main()
