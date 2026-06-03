// Convert SDK `state` event payload to dance-duo applyPose schema.
// SDK reports head { roll, pitch, yaw } and antennas { right, left } in DEGREES.
// applyPose wants pos:[x,y,z], rot:[roll,pitch,yaw] in RADIANS, ant:[right,left], hip.
// SDK does not stream head translation or body_yaw; pos and hip stay at neutral.

const DEG2RAD = Math.PI / 180;

export function stateToFrame(state) {
    const h = state?.head || {};
    const a = state?.antennas || {};
    return {
        pos: [0, 0, 0],
        rot: [
            (h.roll  || 0) * DEG2RAD,
            (h.pitch || 0) * DEG2RAD,
            (h.yaw   || 0) * DEG2RAD
        ],
        ant: [
            (a.right || 0) * DEG2RAD,
            (a.left  || 0) * DEG2RAD
        ],
        hip: 0
    };
}
