"""Read arm Dynamixel calibration values and compare them with hardware_config.yaml.

This script is read-only: it does not enable torque and does not send goal positions.

Run it with the daemon, Dynamixel Wizard, and any other COM3 user closed:

    python examples/debug_arm_offsets.py --serial COM3
"""

from __future__ import annotations

import argparse
import math
import struct
from pathlib import Path
from collections.abc import Iterable
from typing import Any

import yaml
from reachy_mini_motor_controller import ReachyMiniMotorController


DEFAULT_HARDWARE_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "reachy_mini"
    / "assets"
    / "config"
    / "hardware_config.yaml"
)

ARM_IDS = {
    "left_arm_1": 17,
    "left_arm_2": 18,
    "right_arm_1": 19,
    "right_arm_2": 20,
}

ALL_EXPECTED_IDS = list(range(10, 21))

POSITION_MODE_VALID_HOMING_OFFSET_MIN = -1024
POSITION_MODE_VALID_HOMING_OFFSET_MAX = 1024

# Dynamixel X-series control table addresses.
HOMING_OFFSET_ADDR = 20
OPERATING_MODE_ADDR = 11
TORQUE_ENABLE_ADDR = 64
PRESENT_POSITION_ADDR = 132


def read_i32(raw: list[int]) -> int:
    """Decode a little-endian signed int32 from raw register bytes."""
    return struct.unpack("<i", bytes(raw))[0]


def ticks_to_deg(ticks: int) -> float:
    """Convert Dynamixel raw position ticks to degrees."""
    return ticks * 360.0 / 4096.0


def suggested_offset_for_current_pose_as_home(current_offset: int, raw_present: int) -> int:
    """Return the homing offset that would make the current physical pose read as 0 rad.

    rustypot reports 0 rad around raw present position 2048. If the current physical
    pose should be the calibrated home, shift the homing offset by that raw error.
    """
    return current_offset + (2048 - raw_present)


def position_error_ticks(present_rad: float) -> int:
    """Convert a radian position error to Dynamixel ticks."""
    return round(present_rad * 4096.0 / (2.0 * math.pi))


def is_valid_joint_mode_offset(offset: int) -> bool:
    """Return whether an offset is valid in Dynamixel position-control mode."""
    return (
        POSITION_MODE_VALID_HOMING_OFFSET_MIN
        <= offset
        <= POSITION_MODE_VALID_HOMING_OFFSET_MAX
    )


def recommended_valid_offset(current_offset: int, present_rad: float) -> tuple[int, bool]:
    """Recommend a joint-mode-valid offset if the current physical pose is home.

    If the current EEPROM offset is outside the valid joint-mode range, Dynamixel
    ignores it for position control. In that case compute from a zero base.
    """
    base_offset = current_offset if is_valid_joint_mode_offset(current_offset) else 0
    recommended = base_offset - position_error_ticks(present_rad)
    return recommended, is_valid_joint_mode_offset(recommended)


def wrap_to_pi(angle_rad: float) -> float:
    """Wrap an angle to [-pi, pi]."""
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


def normalize_missing_ids(value: Any) -> list[int]:
    """Normalize rmmc missing-id output to a printable integer list."""
    if isinstance(value, (bytes, bytearray)):
        return list(value)
    if isinstance(value, Iterable) and not isinstance(value, (str, dict)):
        return [int(v) for v in value]
    return [int(value)]


def load_yaml_offsets(path: Path) -> tuple[dict[str, int], dict[str, float]]:
    """Load arm offsets from hardware_config.yaml."""
    with path.open("r", encoding="utf-8") as fp:
        data: dict[str, Any] = yaml.safe_load(fp)

    offsets: dict[str, int] = {}
    software_offsets: dict[str, float] = {}
    for motor_entry in data.get("motors", []):
        if not isinstance(motor_entry, dict):
            continue
        for name, params in motor_entry.items():
            if name in ARM_IDS:
                offsets[name] = int(params["offset"])
                software_offsets[name] = float(
                    params.get("software_zero_offset_rad", 0.0)
                )
    missing = sorted(set(ARM_IDS).difference(offsets))
    if missing:
        raise ValueError(f"Missing arm offsets in {path}: {missing}")
    return offsets, software_offsets


def main() -> None:
    """Read and print arm calibration values."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default="COM3", help="Robot serial port.")
    parser.add_argument(
        "--hardware-config",
        type=Path,
        default=DEFAULT_HARDWARE_CONFIG,
        help="hardware_config.yaml to compare against.",
    )
    args = parser.parse_args()

    yaml_offsets, software_offsets = load_yaml_offsets(args.hardware_config)
    controller = ReachyMiniMotorController(args.serial)
    missing = normalize_missing_ids(controller.check_missing_ids())
    if missing:
        print(f"Missing motor IDs on bus: {missing}")
        if sorted(missing) == ALL_EXPECTED_IDS:
            raise RuntimeError(
                f"{args.serial} opens, but no Reachy Mini motors answered. "
                "Check robot power, USB cable, port number, and that no other tool owns the bus."
            )
        missing_arms = {
            name: motor_id
            for name, motor_id in ARM_IDS.items()
            if motor_id in set(missing)
        }
        if missing_arms:
            print(f"Missing arm motors: {missing_arms}")
            print(
                "Skipping detailed arm position read because rmmc read_all_positions() "
                "requires every expected motor to answer."
            )
            return
    else:
        print("All expected motor IDs responded.")

    print(f"Comparing arm offsets against {args.hardware_config}")
    print(
        "name          id  yaml_offset  motor_offset  suggested_if_current_home  match  "
        "valid_recommendation  valid?  software_zero_deg  logical_deg  "
        "motor_offset_deg  present_rad  present_deg  mode  torque"
    )
    all_positions = controller.read_all_positions()
    present_by_name = {
        "left_arm_1": float(all_positions[7]),
        "left_arm_2": float(all_positions[8]),
        "right_arm_1": float(all_positions[9]),
        "right_arm_2": float(all_positions[10]),
    }

    for name, motor_id in ARM_IDS.items():
        motor_offset = read_i32(
            controller.read_raw_bytes(motor_id, HOMING_OFFSET_ADDR, 4)
        )
        mode = controller.read_raw_bytes(motor_id, OPERATING_MODE_ADDR, 1)[0]
        torque = controller.read_raw_bytes(motor_id, TORQUE_ENABLE_ADDR, 1)[0]
        # Read this mostly as a raw sanity check; read_all_positions() is the
        # control value used by rmmc/reachy_mini.
        raw_present = read_i32(
            controller.read_raw_bytes(motor_id, PRESENT_POSITION_ADDR, 4)
        )
        present_rad = present_by_name[name]
        suggested_offset = suggested_offset_for_current_pose_as_home(
            motor_offset, raw_present
        )
        valid_recommended_offset, valid_recommendation = recommended_valid_offset(
            motor_offset, present_rad
        )
        software_zero = software_offsets[name]
        logical_rad = wrap_to_pi(present_rad - software_zero)
        offset_note = "" if is_valid_joint_mode_offset(motor_offset) else " INVALID_OFFSET"
        print(
            f"{name:<13} {motor_id:>2}  "
            f"{yaml_offsets[name]:>11}  {motor_offset:>12}  "
            f"{suggested_offset:>25}  "
            f"{'yes' if yaml_offsets[name] == motor_offset else 'NO ':>5}  "
            f"{valid_recommended_offset:>20}  "
            f"{'yes' if valid_recommendation else 'NO ':>6}  "
            f"{math.degrees(software_zero):>17.2f}  "
            f"{math.degrees(logical_rad):>11.2f}  "
            f"{ticks_to_deg(motor_offset):>16.2f}  "
            f"{present_rad:>11.4f}  {math.degrees(present_rad):>11.2f}  "
            f"{mode:>4}  {torque:>6}  raw_present={raw_present}{offset_note}"
        )


if __name__ == "__main__":
    main()
