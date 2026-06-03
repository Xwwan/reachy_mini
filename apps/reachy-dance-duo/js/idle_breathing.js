// Lissajous breathing: Y-sway + head-roll, antennae held still.
// Frame schema matches dance-duo applyPose: { pos:[x,y,z], rot:[roll,pitch,yaw], ant:[r,l], hip }.

const A_SWAY = 0.012;
const A_ROLL = 0.05;
const F1     = 0.13;
const F2     = 0.26;
const DELTA  = Math.PI / 4;
const PHI_RIGHT = Math.PI / 3;

const TWO_PI = Math.PI * 2;

export function getBreathingFrame(tSec, side) {
    const phi = side === 'right' ? PHI_RIGHT : 0;
    const sway = A_SWAY * Math.sin(TWO_PI * F1 * tSec + phi);
    const roll = A_ROLL * Math.sin(TWO_PI * F2 * tSec + phi + DELTA);
    return {
        pos: [0, sway, 0],
        rot: [roll, 0, 0],
        ant: [0, 0],
        hip: 0
    };
}
