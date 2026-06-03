// Port of /Users/twinpeakstownie/reachy_dance_duo_source/reachy_dance_duo/core/safety_mixer.py
//
// Inputs an "intent" object with the same shape as MovementIntent:
//   { position: [x,y,z] meters,
//     orientation: [roll,pitch,yaw] radians,
//     antennas: [right, left] radians,    // python comment says [left,right] but base value is [-0.15, 0.15]
//     bodyYaw: radians }
//
// Pipeline (same order as Python send_intent):
//   1. intensity scale
//   2. inverse collision limiter (low Z forces pitch up; high Z clamps pitch down)
//   3. LERP smoothing (head + body_yaw; antennas snap)
//   4. absolute clamp
//   5. emit through caller-provided sink (the ReachyMini SDK calls)

const NEUTRAL_ANTENNAS = [-0.15, 0.15];

export const SAFETY_DEFAULTS = {
    zThreshold: 0.005,
    maxPitchAtLowZ: 0.15,
    maxPitchAtHighZ: 0.20,
    highZThreshold: 0.025,
    // Smoothing time constant (seconds). Per-frame alpha is computed
    // as 1 - exp(-dt / smoothingTau) so the effective smoothing rate
    // is constant in real time and immune to tick-rate jitter. The
    // default 0.0615s reproduces the Python's fixed alpha=0.15 at the
    // original 10ms tick: 1 - exp(-0.01/0.0615) ~= 0.15.
    smoothingTau: 0.0615,
    maxPosition: [0.05, 0.05, 0.028],
    minPosition: [-0.05, -0.05, -0.03],
    maxOrientation: [0.5, 0.35, 0.7],
    minOrientation: [-0.5, -0.4, -0.7],
    maxAntenna: 4.0,
    minAntenna: -4.0,
    maxBodyYaw: 0.8,
    minBodyYaw: -0.8,
    intensity: 1.0,
};

const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
const lerp = (a, b, t) => a * (1 - t) + b * t;

export function neutralIntent() {
    return {
        position: [0, 0, 0],
        orientation: [0, 0, 0],
        antennas: NEUTRAL_ANTENNAS.slice(),
        bodyYaw: 0,
    };
}

export class SafetyMixer {
    constructor(sink, config = {}) {
        this.sink = sink;
        this.config = { ...SAFETY_DEFAULTS, ...config };
        this._current = neutralIntent();
        this._initialized = false;
    }

    updateConfig(patch) { Object.assign(this.config, patch); }

    // dt is the seconds since the previous sendIntent call. The smoother
    // uses it to compute a real-time-rate-stable alpha. Callers should
    // pass dt; if omitted we fall back to a uniform-rate assumption
    // matching the canonical 50Hz tick.
    sendIntent(intent, dt = 0.02) {
        const scaled = this._applyIntensity(intent);
        const safe = this._applyCollisionLimits(scaled);
        const smoothed = this._applySmoothing(safe, dt);
        const clamped = this._clampToLimits(smoothed);
        this._current = clamped;
        this.sink(clamped);
        return clamped;
    }

    reset() {
        this._current = neutralIntent();
        this._initialized = false;
        this.sink(this._current);
    }

    getState() { return _copyIntent(this._current); }

    _applyIntensity(intent) {
        const k = this.config.intensity;
        if (k >= 1.0) return _copyIntent(intent);
        const out = _copyIntent(intent);
        out.position = intent.position.map(v => v * k);
        out.orientation = intent.orientation.map(v => v * k);
        out.antennas = intent.antennas.map((v, i) => NEUTRAL_ANTENNAS[i] + (v - NEUTRAL_ANTENNAS[i]) * k);
        out.bodyYaw = intent.bodyYaw * k;
        return out;
    }

    _applyCollisionLimits(intent) {
        const out = _copyIntent(intent);
        const z = intent.position[2];
        const pitch = intent.orientation[1];
        if (z < this.config.zThreshold) {
            out.orientation[1] = Math.min(pitch, this.config.maxPitchAtLowZ);
        } else if (z > this.config.highZThreshold) {
            out.orientation[1] = Math.min(pitch, this.config.maxPitchAtHighZ);
        }
        return out;
    }

    _applySmoothing(intent, dt) {
        if (!this._initialized) {
            this._current = _copyIntent(intent);
            this._initialized = true;
            return _copyIntent(this._current);
        }
        // dt-aware alpha: 1 - exp(-dt/tau). At dt=0 alpha=0; at dt>>tau
        // alpha approaches 1 (snap). This decouples smoothing rate from
        // the tick rate so jitter in tick interval does not turn into
        // amplitude jitter in the output. Matches the steady-state
        // response of the previous fixed-alpha smoother at the original
        // 10ms tick when smoothingTau=0.0615.
        const a = 1 - Math.exp(-dt / this.config.smoothingTau);
        const out = _copyIntent(this._current);
        out.position = this._current.position.map((v, i) => lerp(v, intent.position[i], a));
        out.orientation = this._current.orientation.map((v, i) => lerp(v, intent.orientation[i], a));
        out.bodyYaw = lerp(this._current.bodyYaw, intent.bodyYaw, a);
        out.antennas = intent.antennas.slice();
        return out;
    }

    _clampToLimits(intent) {
        const c = this.config;
        return {
            position: intent.position.map((v, i) => clamp(v, c.minPosition[i], c.maxPosition[i])),
            orientation: intent.orientation.map((v, i) => clamp(v, c.minOrientation[i], c.maxOrientation[i])),
            antennas: intent.antennas.map(v => clamp(v, c.minAntenna, c.maxAntenna)),
            bodyYaw: clamp(intent.bodyYaw, c.minBodyYaw, c.maxBodyYaw),
        };
    }
}

function _copyIntent(i) {
    return {
        position: i.position.slice(),
        orientation: i.orientation.slice(),
        antennas: i.antennas.slice(),
        bodyYaw: i.bodyYaw,
    };
}

// Build a row-major flattened 4x4 head pose matrix. Rotation in the upper
// 3x3 from a ZYX-extrinsic / XYZ-intrinsic Euler triple in radians, plus
// the [x, y, z] translation in column 3. Matches what
// utils.create_head_pose(*pos, *orient, degrees=False) builds in
// /Users/twinpeakstownie/galaxy-staging/reachy_mini/src/reachy_mini/utils/__init__.py:13-46
// and what the SDK's rpyToMatrix builds, just with translation merged in
// and inputs in radians instead of degrees.
function buildHeadMatrix(position, orientationRad) {
    const [r, p, y] = orientationRad;
    const [tx, ty, tz] = position;
    const cy = Math.cos(y), sy = Math.sin(y);
    const cp = Math.cos(p), sp = Math.sin(p);
    const cr = Math.cos(r), sr = Math.sin(r);
    return [
        cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, tx,
        sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, ty,
        -sp,     cp * sr,                cp * cr,                tz,
        0,       0,                      0,                      1,
    ];
}

// Build a sink that pushes the safe intent through the ReachyMini SDK as
// a single set_full_target command, mirroring the Python original at
// /Users/twinpeakstownie/galaxy-staging/reachy_mini/src/reachy_mini/reachy_mini.py:432-473
// where mini.set_target(head=4x4, antennas=[a0,a1], body_yaw=...) sends
// one SetFullTargetCmd per call. Critically this carries the Y / Z
// translation from intent.position into the matrix's last column, which
// the SDK's setHeadPose(roll, pitch, yaw) silently drops.
export function makeReachySink(robot) {
    return (intent) => {
        try {
            const head = buildHeadMatrix(intent.position, intent.orientation);
            robot.sendRaw({
                type: "set_full_target",
                head,
                antennas: [intent.antennas[0], intent.antennas[1]],
                body_yaw: intent.bodyYaw,
            });
        } catch (e) {
            // Robot not in 'streaming' state yet, or SDK rejected the call.
            // Silent: the next tick will retry.
        }
    };
}
