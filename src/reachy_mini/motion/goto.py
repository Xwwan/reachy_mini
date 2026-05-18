"""A goto move to a target head pose and/or arm position."""

import numpy as np
import numpy.typing as npt

from reachy_mini.utils.interpolation import (
    InterpolationTechnique,
    linear_pose_interpolation,
    time_trajectory,
)

from .move import Move, MoveTarget


class GotoMove(Move):
    """A goto move to a target head pose and/or arm position."""

    def __init__(
        self,
        start_head_pose: npt.NDArray[np.float64],
        target_head_pose: npt.NDArray[np.float64] | None,
        start_left_arm: npt.NDArray[np.float64],
        target_left_arm: npt.NDArray[np.float64] | None,
        start_right_arm: npt.NDArray[np.float64],
        target_right_arm: npt.NDArray[np.float64] | None,
        start_body_yaw: float,
        target_body_yaw: float | None,
        duration: float,
        method: InterpolationTechnique,
    ):
        """Set up the goto move."""
        self.start_head_pose = start_head_pose
        self.target_head_pose = (
            target_head_pose if target_head_pose is not None else start_head_pose
        )
        self.start_left_arm = start_left_arm
        self.target_left_arm = (
            target_left_arm if target_left_arm is not None else start_left_arm
        )
        self.start_right_arm = start_right_arm
        self.target_right_arm = (
            target_right_arm if target_right_arm is not None else start_right_arm
        )
        self.start_body_yaw = start_body_yaw
        self.target_body_yaw = (
            target_body_yaw if target_body_yaw is not None else start_body_yaw
        )

        self._duration = duration
        self.method = method

    @property
    def duration(self) -> float:
        """Duration of the goto in seconds."""
        return self._duration

    def evaluate(self, t: float) -> MoveTarget:
        """Evaluate the goto at time t."""
        interp_time = time_trajectory(t / self.duration, method=self.method)

        interp_head_pose = linear_pose_interpolation(
            self.start_head_pose, self.target_head_pose, interp_time
        )
        interp_left_arm = (
            self.start_left_arm
            + (self.target_left_arm - self.start_left_arm) * interp_time
        )
        interp_right_arm = (
            self.start_right_arm
            + (self.target_right_arm - self.start_right_arm) * interp_time
        )
        interp_body_yaw_joint = (
            self.start_body_yaw
            + (self.target_body_yaw - self.start_body_yaw) * interp_time
        )

        return MoveTarget(
            head=interp_head_pose,
            left_arm=interp_left_arm,
            right_arm=interp_right_arm,
            body_yaw=interp_body_yaw_joint,
        )
