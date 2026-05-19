"""Demonstrate and play all available moves from a dataset for Reachy Mini.

Run :

```python
python examples/recorded_moves.py --dataset .run/arm_emotions_library
```
"""

# START doc_example

import argparse
import math
import time

from reachy_mini import ReachyMini
from reachy_mini.motion.recorded_move import RecordedMove, RecordedMoves

# Dual-arm recorded moves must already use left_arm/right_arm fields.
LIBRARY_DATASETS = {
    "emotions": ".run/arm_emotions_library",
}


ARM_HOME = [0.0, 0.0]


def format_arm_deg(values: list[float]) -> str:
    """Format a two-joint arm position in degrees."""
    return f"[{math.degrees(values[0]):.2f}, {math.degrees(values[1]):.2f}] deg"


def max_home_error_deg(left_arm: list[float], right_arm: list[float]) -> float:
    """Return the maximum absolute arm-home error in degrees."""
    return max(abs(math.degrees(value)) for value in [*left_arm, *right_arm])


def ensure_arms_home(
    reachy: ReachyMini,
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


def main(
    dataset_path: str,
    once: bool = False,
    final_home_check: bool = True,
    home_tolerance_deg: float = 5.0,
    reset_duration: float = 1.5,
    reset_attempts: int = 2,
) -> None:
    """Connect to Reachy and run the main demonstration loop."""
    recorded_moves = RecordedMoves(dataset_path)

    print("Connecting to Reachy Mini...")
    with ReachyMini() as reachy:
        print("Connection successful! Starting recorded move sequence...\n")
        try:
            while True:
                for move_name in recorded_moves.list_moves():
                    move: RecordedMove = recorded_moves.get(move_name)
                    print(f"Playing move: {move_name}: {move.description}\n")
                    reachy.play_move(move, initial_goto_duration=1.0)
                    if final_home_check:
                        ensure_arms_home(
                            reachy,
                            tolerance_deg=home_tolerance_deg,
                            reset_duration=reset_duration,
                            reset_attempts=reset_attempts,
                        )
                if once:
                    break

        except KeyboardInterrupt:
            print("\n Sequence interrupted by user. Shutting down.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Demonstrate and play all available dual-arm recorded moves for Reachy Mini."
    )
    parser.add_argument(
        "-l",
        "--library",
        type=str,
        default="emotions",
        choices=sorted(LIBRARY_DATASETS.keys()),
        help="Pick one of the local converted libraries (default: emotions).",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        help="Local path or HF dataset id with left_arm/right_arm fields. Overrides --library when provided.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Play the selected dataset once instead of looping forever.",
    )
    parser.add_argument(
        "--no-final-home-check",
        action="store_true",
        help="Do not check and force arm home after each move.",
    )
    parser.add_argument(
        "--home-tolerance-deg",
        type=float,
        default=5.0,
        help="Maximum allowed final arm error before forcing home.",
    )
    parser.add_argument(
        "--reset-duration",
        type=float,
        default=1.5,
        help="Duration in seconds for each forced arm-home reset.",
    )
    parser.add_argument(
        "--reset-attempts",
        type=int,
        default=2,
        help="Number of forced arm-home reset attempts after playback.",
    )
    args = parser.parse_args()
    dataset_path = args.dataset or LIBRARY_DATASETS[args.library]
    main(
        dataset_path,
        once=args.once,
        final_home_check=not args.no_final_home_check,
        home_tolerance_deg=args.home_tolerance_deg,
        reset_duration=args.reset_duration,
        reset_attempts=args.reset_attempts,
    )

# END doc_example
