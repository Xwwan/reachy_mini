"""Module for defining motion moves on the ReachyMini robot."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class MoveTarget:
    """Evaluated target for a Reachy Mini move."""

    head: npt.NDArray[np.float64] | None = None
    left_arm: npt.NDArray[np.float64] | None = None
    right_arm: npt.NDArray[np.float64] | None = None
    body_yaw: float | None = None


class Move(ABC):
    """Abstract base class for defining a move on the ReachyMini robot."""

    @property
    def sound_path(self) -> Optional[Path]:
        """Get the sound path associated with the move, if any."""
        return None

    @property
    @abstractmethod
    def duration(self) -> float:
        """Duration of the move in seconds."""
        pass

    @abstractmethod
    def evaluate(
        self,
        t: float,
    ) -> MoveTarget:
        """Evaluate the move at time t, typically called at a high-frequency (eg. 100Hz).

        Arguments:
            t: The time at which to evaluate the move (in seconds). It will always be between 0 and duration.

        Returns:
            A structured target with optional head pose, left arm, right arm,
            and body yaw targets.

        """
        pass
