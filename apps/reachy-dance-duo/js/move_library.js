// Fetch + interpolate moves from the Pollen Robotics dance library on HF Datasets.
//
// Dataset:  https://huggingface.co/datasets/pollen-robotics/reachy-mini-dances-library
// Each move is a JSON file with:
//   { description: str,
//     time: number[]                 (seconds, monotonic, ~96 Hz)
//     set_target_data: [{
//        head: number[4][4]          (homogeneous transform; rotation in upper-left 3x3,
//                                     translation in column 3)
//        antennas: [r, l]            (radians)
//        body_yaw: number            (radians)
//        check_collision: any
//     }, ...] }
//
// Per-move amplitude dampening is a verbatim copy of
// /Users/twinpeakstownie/reachy_dance_duo_source/reachy_dance_duo/move_dampening.json.
// Mirror policy is a verbatim copy of move_mirror.json. headbanger_combo
// is excluded from this port (was the calibration move in Python; we have
// no calibration phase here).

const DATASET_BASE = "https://huggingface.co/datasets/pollen-robotics/reachy-mini-dances-library/resolve/main";

export const AVAILABLE_MOVE_NAMES = [
    "chicken_peck", "chin_lead", "dizzy_spin", "grid_snap",
    "groovy_sway_and_roll", "head_tilt_roll", "interwoven_spirals",
    "jackson_square", "neck_recoil", "pendulum_swing", "polyrhythm_combo",
    "sharp_side_tilt", "side_glance_flick", "side_peekaboo",
    "side_to_side_sway", "simple_nod", "stumble_and_recover",
    "uh_huh_tilt", "yeah_nod",
];

export const MOVE_AMPLITUDE_OVERRIDES = {
    chicken_peck: 0.75,
    chin_lead: 0.8,
    dizzy_spin: 0.85,
    grid_snap: 0.85,
    groovy_sway_and_roll: 0.7,
    head_tilt_roll: 0.7,
    interwoven_spirals: 0.7,
    jackson_square: 0.85,
    neck_recoil: 0.9,
    pendulum_swing: 0.85,
    polyrhythm_combo: 0.9,
    sharp_side_tilt: 0.95,
    side_glance_flick: 0.9,
    side_peekaboo: 0.7,
    side_to_side_sway: 0.7,
    simple_nod: 0.6,
    stumble_and_recover: 1.0,
    uh_huh_tilt: 0.8,
    yeah_nod: 0.85,
};

// Per-move mirror policy. Only listed moves with `true` get a _mirrored
// variant in the cycle. Moves not in this dict default to false (no
// mirror), matching the Python behavior where move_mirror.json is the
// only source of mirror eligibility.
export const MOVE_MIRROR_POLICY = {
    dizzy_spin: false,
    grid_snap: true,
    head_tilt_roll: false,
    interwoven_spirals: true,
    jackson_square: true,
    pendulum_swing: true,
    polyrhythm_combo: false,
    sharp_side_tilt: false,
    side_glance_flick: true,
    side_peekaboo: false,
    side_to_side_sway: false,
    stumble_and_recover: true,
    uh_huh_tilt: true,
};

export function dampeningFor(moveName) {
    const base = stripMirror(moveName);
    return MOVE_AMPLITUDE_OVERRIDES[base] ?? 1.0;
}

export function isMirrorable(moveName) {
    return MOVE_MIRROR_POLICY[stripMirror(moveName)] === true;
}

function stripMirror(name) {
    return name.endsWith("_mirrored") ? name.slice(0, -"_mirrored".length) : name;
}

const _cache = new Map();   // name -> parsed move
const _inflight = new Map();

export async function loadMove(name) {
    const base = stripMirror(name);
    if (_cache.has(base)) return _cache.get(base);
    if (_inflight.has(base)) return _inflight.get(base);
    const p = (async () => {
        const res = await fetch(`${DATASET_BASE}/${base}.json`);
        if (!res.ok) throw new Error(`move fetch failed for ${base}: ${res.status}`);
        const raw = await res.json();
        const move = _normalize(raw);
        _cache.set(base, move);
        _inflight.delete(base);
        return move;
    })();
    _inflight.set(base, p);
    return p;
}

export async function loadAllMoves(names = AVAILABLE_MOVE_NAMES) {
    const moves = await Promise.all(names.map(async (name) => {
        const move = await loadMove(name);
        return { ...move, name }; // Tag the move with its canonical identifier
    }));
    return moves;
}

function _normalize(raw) {
    const time = raw.time;
    const frames = raw.set_target_data.map(f => ({
        head: f.head,           // 4x4 row-major
        antennas: f.antennas,   // [r, l] radians
        bodyYaw: typeof f.body_yaw === "number" ? f.body_yaw : 0,
    }));
    return {
        description: raw.description,
        duration: time[time.length - 1],
        time,
        frames,
    };
}

// Decompose a homogeneous transform's rotation block into ZYX Euler
// (roll-pitch-yaw, radians). Mirrors what the SDK's matrixToRpy does so
// we don't need to import it just for moves.
export function rpyFromHead(H) {
    const r00 = H[0][0], r01 = H[0][1], r02 = H[0][2];
    const r10 = H[1][0];
    const r20 = H[2][0], r21 = H[2][1], r22 = H[2][2];

    const sy = Math.sqrt(r00 * r00 + r10 * r10);
    if (sy > 1e-6) {
        return {
            roll:  Math.atan2(r21, r22),
            pitch: Math.atan2(-r20, sy),
            yaw:   Math.atan2(r10, r00),
        };
    }
    return {
        roll:  Math.atan2(-r01, r01 === 0 ? 1 : r01),  // gimbal lock fallback
        pitch: Math.atan2(-r20, sy),
        yaw:   0,
    };
}

// Translation lives in the 4th column of the 4x4 transform.
function translation(H) { return [H[0][3], H[1][3], H[2][3]]; }

// Linear interpolation of a move at an absolute time in seconds.
// Returns { position, orientation: [roll, pitch, yaw], antennas, bodyYaw }.
// Times outside the move clamp to first / last frame.
export function sampleMoveAt(move, tSec) {
    const { time, frames } = move;
    const n = time.length;
    if (n === 0) return null;
    if (tSec <= time[0]) return _frameToTuple(frames[0]);
    if (tSec >= time[n - 1]) return _frameToTuple(frames[n - 1]);

    // Binary search for the interval [i, i+1] containing tSec.
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (time[mid] <= tSec) lo = mid; else hi = mid;
    }
    const t0 = time[lo], t1 = time[hi];
    const a = (tSec - t0) / (t1 - t0);
    return _interpolateFrames(frames[lo], frames[hi], a);
}

function _frameToTuple(f) {
    const rpy = rpyFromHead(f.head);
    return {
        position: translation(f.head),
        orientation: [rpy.roll, rpy.pitch, rpy.yaw],
        antennas: f.antennas.slice(),
        bodyYaw: f.bodyYaw,
    };
}

function _interpolateFrames(a, b, t) {
    const pa = translation(a.head), pb = translation(b.head);
    const ra = rpyFromHead(a.head), rb = rpyFromHead(b.head);
    return {
        position: [
            pa[0] + (pb[0] - pa[0]) * t,
            pa[1] + (pb[1] - pa[1]) * t,
            pa[2] + (pb[2] - pa[2]) * t,
        ],
        orientation: [
            ra.roll  + _angleDelta(ra.roll,  rb.roll)  * t,
            ra.pitch + _angleDelta(ra.pitch, rb.pitch) * t,
            ra.yaw   + _angleDelta(ra.yaw,   rb.yaw)   * t,
        ],
        antennas: [
            a.antennas[0] + (b.antennas[0] - a.antennas[0]) * t,
            a.antennas[1] + (b.antennas[1] - a.antennas[1]) * t,
        ],
        bodyYaw: a.bodyYaw + _angleDelta(a.bodyYaw, b.bodyYaw) * t,
    };
}

// Shortest-path angular delta to handle wrap at +-pi.
function _angleDelta(a, b) {
    let d = b - a;
    while (d > Math.PI) d -= 2 * Math.PI;
    while (d < -Math.PI) d += 2 * Math.PI;
    return d;
}

// Apply mirrored-variant transformation (Y position and yaw flip) per
// live_groove.py:845-850. Only position[1] and orientation[2] flip;
// bodyYaw is NOT flipped in Python (moves do not emit bodyYaw at all,
// the field defaults to 0).
export function applyMirroredFlip(sample, isMirrored) {
    if (!isMirrored) return sample;
    return {
        position: [sample.position[0], -sample.position[1], sample.position[2]],
        orientation: [sample.orientation[0], sample.orientation[1], -sample.orientation[2]],
        antennas: sample.antennas.slice(),
        bodyYaw: sample.bodyYaw,
    };
}

// Time-stretch helper: given a target tempo and number of beats to fill,
// scale a move so its full duration covers `beatsToFill / (bpm/60)` seconds.
// Returns the seconds you should sample the move at, given an elapsed time
// in seconds since the move started.
export function stretchedTime(move, elapsedSec, bpm, beatsToFill = 4) {
    const targetDuration = beatsToFill * (60 / bpm);
    const ratio = move.duration / targetDuration;
    return elapsedSec * ratio;
}

// Apply per-move amplitude dampening to a sample. Dampening only affects
// position and orientation OFFSETS (relative to neutral), and antennas.
// Body yaw is left alone here because the original Python applies amp to
// move offsets only; the safety mixer's intensity scalar already covers
// the global dampening axis.
export function dampenSample(sample, scale, neutralPos = [0, 0, 0.01], neutralEul = [0, 0, 0]) {
    return {
        position: [
            neutralPos[0] + (sample.position[0] - neutralPos[0]) * scale,
            neutralPos[1] + (sample.position[1] - neutralPos[1]) * scale,
            neutralPos[2] + (sample.position[2] - neutralPos[2]) * scale,
        ],
        orientation: [
            neutralEul[0] + (sample.orientation[0] - neutralEul[0]) * scale,
            neutralEul[1] + (sample.orientation[1] - neutralEul[1]) * scale,
            neutralEul[2] + (sample.orientation[2] - neutralEul[2]) * scale,
        ],
        antennas: sample.antennas.map(v => v * scale),
        bodyYaw: sample.bodyYaw,
    };
}
