// Beat Bandit: drive the robot in sync with a pre-analyzed song.
//
// Verbatim port of /Users/twinpeakstownie/reachy_dance_duo_source/reachy_dance_duo/behaviors/connected_choreographer.py
// (the Python "ConnectedChoreographer" class). Replaces an earlier port
// that played pre-recorded moves from the dance library; that approach
// did not match what the Python original does and produced none of the
// per-beat reactivity, asymmetric body-yaw physics, or energy-driven
// antenna behavior the original is designed around.
//
// Audio sync uses the HTML5 <audio> element's currentTime as the
// master clock (better than Python's wall-clock time.time(), which
// drifts against the audio thread).

import {
    SafetyMixer,
    SAFETY_DEFAULTS,
    makeReachySink,
} from "./safety_mixer.js";

// EIGHT_BEAT_SEQUENCES: connected_choreographer.py:46-185, copied
// byte-for-byte. Each sequence is 8 beats of [x, y, z, roll, pitch, yaw]
// in (cm, cm, cm, deg, deg, deg). _coords_to_offset converts to meters
// and radians and applies amplitude_scale. yaw maps to body_yaw (hip
// sway) per Python:1247, not head orientation.
export const EIGHT_BEAT_SEQUENCES = {
    high: [
        [
            { name: "Sharp snap left",     coords: [0, 0.5, 0.25, -15, 0, -24] },
            { name: "Sharp snap right",    coords: [0, 0.5, 0.25,  15, 0,  24] },
            { name: "Sharp snap left",     coords: [0, 0.5, 0.25, -15, 0, -24] },
            { name: "Sharp snap right",    coords: [0, 0.5, 0.25,  15, 0,  24] },
            { name: "Sharp snap left",     coords: [0, 0.5, 0.25, -15, 0, -24] },
            { name: "Sharp snap right",    coords: [0, 0.5, 0.25,  15, 0,  24] },
            { name: "Sharp snap left",     coords: [0, 0.5, 0.25, -15, 0, -24] },
            { name: "Sharp snap right",    coords: [0, 0.5, 0.25,  15, 0,  24] },
        ],
        [
            { name: "Strong head drop",    coords: [0, 0, -1.9, 0, -22, 0] },
            { name: "Head snap up",        coords: [0, 0,  2.2, 0,  19, 0] },
            { name: "Strong head drop",    coords: [0, 0, -1.9, 0, -22, 0] },
            { name: "Head snap up",        coords: [0, 0,  2.2, 0,  19, 0] },
            { name: "Strong head drop",    coords: [0, 0, -1.9, 0, -22, 0] },
            { name: "Head snap up",        coords: [0, 0,  2.2, 0,  19, 0] },
            { name: "Strong head drop",    coords: [0, 0, -1.9, 0, -22, 0] },
            { name: "Head snap up",        coords: [0, 0,  2.2, 0,  19, 0] },
        ],
        [
            { name: "Head thrust forward", coords: [0,  2.6, 0, 0,  -9, 0] },
            { name: "Head jerk back",      coords: [0, -1.9, 0, 0,  11, 0] },
            { name: "Head thrust forward", coords: [0,  2.6, 0, 0,  -9, 0] },
            { name: "Head jerk back",      coords: [0, -1.9, 0, 0,  11, 0] },
            { name: "Head thrust forward", coords: [0,  2.6, 0, 0,  -9, 0] },
            { name: "Head jerk back",      coords: [0, -1.9, 0, 0,  11, 0] },
            { name: "Head thrust forward", coords: [0,  2.6, 0, 0,  -9, 0] },
            { name: "Head jerk back",      coords: [0, -1.9, 0, 0,  11, 0] },
        ],
        [
            { name: "Head whip left",      coords: [0,  0,    0,   -24,   0, -27] },
            { name: "Head slam down",      coords: [0,  1.1, -1.9,   0, -26,   0] },
            { name: "Head whip right",     coords: [0,  0,    0,    24,   0,  27] },
            { name: "Head throw back",     coords: [0, -1.5,  1.9,   0,  21,   0] },
            { name: "Diagonal tilt left",  coords: [0,  0.8,  0,   -24, -11, -15] },
            { name: "Power nod center",    coords: [0,  0,   -1.1,   0, -24,   0] },
            { name: "Diagonal tilt right", coords: [0, -0.8,  0,    24, -11,  15] },
            { name: "Head explosion up",   coords: [0,  0,    2.6,   0,  24,   0] },
        ],
    ],
    medium: [
        [
            { name: "Flow left",          coords: [0,  1.1,  0.4, -15, -9, -19] },
            { name: "Flow center up",     coords: [0,  0,    1.1,   0, 11,   0] },
            { name: "Flow right",         coords: [0,  0.8,  0.4,  15, -6,  19] },
            { name: "Flow back center",   coords: [0, -0.8,  0.8,   0, 15,   0] },
            { name: "Flow diagonal 1",    coords: [0,  1.5,  0,   -19, -11, -15] },
            { name: "Flow diagonal 2",    coords: [0, -0.8,  1.5,  11,  15,  11] },
            { name: "Flow circle left",   coords: [0,  0,    0.8, -11,  -4, -22] },
            { name: "Flow circle right",  coords: [0,  0,    0.8,  11,  -4,  22] },
        ],
        [
            { name: "Wave left start",    coords: [0,  0.8,  0,   -13, -6, -15] },
            { name: "Wave center dip",    coords: [0,  0,   -0.8,   0, -11,  0] },
            { name: "Wave right rise",    coords: [0,  0.8,  0.8,  13,   8, 15] },
            { name: "Wave back center",   coords: [0, -0.8,  0,     0,   9,  0] },
            { name: "Wave forward left",  coords: [0,  1.5,  0,   -11,  -8, -11] },
            { name: "Wave up right",      coords: [0,  0,    1.5,  11,  11,  11] },
            { name: "Wave down left",     coords: [0, -0.8, -0.8,  -9,  -9, -13] },
            { name: "Wave reset center",  coords: [0,  0.8,  0.8,   0,   6,   0] },
        ],
        [
            { name: "Emphasis left nod",  coords: [0, 0,   0,   -15, -13, -19] },
            { name: "Soft center up",     coords: [0, 0,   0.8,   0,   8,   0] },
            { name: "Emphasis right nod", coords: [0, 0,   0,    15, -13,  19] },
            { name: "Soft center up",     coords: [0, 0,   0.8,   0,   8,   0] },
            { name: "Strong forward",     coords: [0, 2.2, 0,     0, -16,   0] },
            { name: "Gentle back",        coords: [0, -0.8, 0.8,  0,   6,   0] },
            { name: "Side emphasis left", coords: [0,  0.8, 0,  -19,   0, -15] },
            { name: "Side emphasis right",coords: [0,  0.8, 0,   19,   0,  15] },
        ],
        [
            { name: "Figure-8 start",        coords: [0,  1,  1,  -15, -10, -18] },
            { name: "Figure-8 cross center", coords: [0,  0,  0,    0,   0,   0] },
            { name: "Figure-8 right loop",   coords: [0,  1,  1,   15,  10,  18] },
            { name: "Figure-8 back cross",   coords: [0, -1, -1,    0,   5,   0] },
            { name: "Figure-8 left down",    coords: [0,  0, -1,  -18, -15, -20] },
            { name: "Figure-8 up cross",     coords: [0,  1,  1,    0,  12,   0] },
            { name: "Figure-8 right down",   coords: [0,  0, -1,   18, -15,  20] },
            { name: "Figure-8 complete",     coords: [0, -1,  0,    0,   8,   0] },
        ],
    ],
    low: [
        [
            { name: "Gentle nod down", coords: [0, 0, 0, 0, -22, 0] },
            { name: "Gentle nod up",   coords: [0, 0, 0, 0,  18, 0] },
            { name: "Gentle nod down", coords: [0, 0, 0, 0, -22, 0] },
            { name: "Gentle nod up",   coords: [0, 0, 0, 0,  18, 0] },
            { name: "Gentle nod down", coords: [0, 0, 0, 0, -22, 0] },
            { name: "Gentle nod up",   coords: [0, 0, 0, 0,  18, 0] },
            { name: "Gentle nod down", coords: [0, 0, 0, 0, -22, 0] },
            { name: "Gentle nod up",   coords: [0, 0, 0, 0,  18, 0] },
        ],
        [
            { name: "Soft turn left",  coords: [0, 0, 0, 0, 0, -18] },
            { name: "Soft turn right", coords: [0, 0, 0, 0, 0,  18] },
            { name: "Soft turn left",  coords: [0, 0, 0, 0, 0, -18] },
            { name: "Soft turn right", coords: [0, 0, 0, 0, 0,  18] },
            { name: "Soft turn left",  coords: [0, 0, 0, 0, 0, -18] },
            { name: "Soft turn right", coords: [0, 0, 0, 0, 0,  18] },
            { name: "Soft turn left",  coords: [0, 0, 0, 0, 0, -18] },
            { name: "Soft turn right", coords: [0, 0, 0, 0, 0,  18] },
        ],
        [
            { name: "Gentle nod down",     coords: [0, 0, 0,   0, -12, 0] },
            { name: "Gentle nod up",       coords: [0, 0, 0,   0,   8, 0] },
            { name: "Soft turn left",      coords: [0, 0, 0,   0,   0, -18] },
            { name: "Soft turn right",     coords: [0, 0, 0,   0,   0,  18] },
            { name: "Light tilt left",     coords: [0, 0, 0, -15,   0,   0] },
            { name: "Light tilt right",    coords: [0, 0, 0,  15,   0,   0] },
            { name: "Gentle pitch forward",coords: [0, 0, 0,   0,  -8,   0] },
            { name: "Gentle pitch back",   coords: [0, 0, 0,   0,   6,   0] },
        ],
        [
            { name: "Thoughtful nod",       coords: [0, 0, 0,   0, -10,   0] },
            { name: "Curious tilt left",    coords: [0, 0, 0,  -8,   0, -12] },
            { name: "Ponder left turn",     coords: [0, 0, 0,  -6,   0, -16] },
            { name: "Ponder right turn",    coords: [0, 0, 0,   6,   0,  16] },
            { name: "Meditative tilt right",coords: [0, 0, 0,  12,  -2,  10] },
            { name: "Peaceful center",      coords: [0, 0, 0,   0,   0,   0] },
            { name: "Gentle roll left",     coords: [0, 0, 0, -10,   2,  -8] },
            { name: "Gentle roll right",    coords: [0, 0, 0,  10,   2,   8] },
        ],
    ],
};

// Defaults from Python connected_choreographer.py:298-316
// (ConnectedChoreographerConfig dataclass) overlaid with the runtime
// values in mode_settings.json that the local Python app actually
// uses. Where the two differ the mode_settings value is the tuned one
// and wins.
export const BEAT_BANDIT_DEFAULTS = {
    beatsPerBlock: 8,
    controlLoopMs: 20,                 // 50Hz to match the robot loop

    // Global movement scale applied to every choreography coord.
    // mode_settings.json: 0.5
    amplitudeScale: 0.5,

    // EMA blend toward each beat's target coordinate.
    // mode_settings.json: 0.25 (dataclass default is 0.3)
    interpolationAlpha: 0.25,

    // Antenna control. Python's ConnectedChoreographerConfig defaults
    // are 1.0/3.15/20.0/0.25 but mode_settings.json tunes them down to
    // the values below for the real robot. Use the tuned values.
    antennaSensitivity: 0.75,
    antennaAmplitude: 2.1,
    antennaGain: 3.0,
    antennaEnergyThreshold: 0.2,
    antennaRestPosition: -0.1,         // Python:314

    // Asymmetric body-yaw physics: snap fast to a new target, decay
    // slowly back. Verbatim from BODY_YAW_PHYSICS at
    // connected_choreographer.py:781.
    bodyYawAttack: 0.25,
    bodyYawDecay: 0.15,

    // Breathing pose runs continuously (not just when paused). Y sway
    // and head roll at offset frequencies so the pattern never repeats.
    // connected_choreographer.py:308-311 + live_groove.py:803-819.
    neutralPos: [0, 0, 0.01],
    neutralEul: [0, 0, 0],
    breathingYAmplitude: 0.016,
    breathingYHz: 0.2,
    breathingRollAmplitude: 0.222,
    breathingRollHz: 0.15,
};

export class BeatBandit extends EventTarget {
    constructor({
        robot,
        audioElement,
        analysis,
        config = {},
        safetyConfig = {},
    }) {
        super();
        if (!analysis || !Array.isArray(analysis.beats) || !Array.isArray(analysis.sequence_assignments)) {
            throw new Error("BeatBandit requires an analysis object with beats[] and sequence_assignments[]");
        }
        this.robot = robot;
        this.audio = audioElement;
        this.analysis = analysis;
        this.config = { ...BEAT_BANDIT_DEFAULTS, ...config };
        this.mixer = new SafetyMixer(makeReachySink(robot), { ...SAFETY_DEFAULTS, ...safetyConfig });

        // Round-robin per energy level so the same sequence does not
        // repeat back-to-back when the same energy block recurs.
        // connected_choreographer.py:824, 1209-1211.
        this._lastSequenceIdx = { high: -1, medium: -1, low: -1 };

        // EMA state: blended pose offset that chases the current beat's
        // target coords each frame. connected_choreographer.py:1336.
        this._currentOffset = [0, 0, 0, 0, 0, 0];

        // Asymmetric yaw state. connected_choreographer.py:830, 1386-1392.
        this._currentBodyYaw = 0;

        this._loopHandle = null;
        this._currentBlock = -1;
        this._currentSequence = null;
        this._currentEnergy = "medium";
        this._breathingT = 0;
        this._lastTickAt = 0;
    }

    async start() {
        if (this._loopHandle != null) return;
        this._lastTickAt = performance.now();
        this._loopHandle = setInterval(() => this._tick(), this.config.controlLoopMs);
        this.dispatchEvent(new CustomEvent("started"));
    }

    stop() {
        if (this._loopHandle != null) {
            clearInterval(this._loopHandle);
            this._loopHandle = null;
        }
        this.mixer.reset();
        this._currentBlock = -1;
        this._currentSequence = null;
        this._currentOffset = [0, 0, 0, 0, 0, 0];
        this._currentBodyYaw = 0;
        this.dispatchEvent(new CustomEvent("stopped"));
    }

    updateConfig(patch) {
        Object.assign(this.config, patch);
    }

    _tick() {
        const nowMs = performance.now();
        const dt = (nowMs - this._lastTickAt) / 1000;
        this._lastTickAt = nowMs;
        this._breathingT += dt;

        // Master clock is the audio element. When paused or before
        // playback starts, fall back to breathing only with antennas
        // at their resting position.
        const playing = this.audio && !this.audio.paused && !this.audio.ended;
        if (!playing) {
            const intent = this._breathingPose(this._breathingT);
            intent.antennas = this._restingAntennas();
            this.mixer.sendIntent(intent, dt);
            this.dispatchEvent(new CustomEvent("tick", { detail: { phase: "idle" } }));
            return;
        }

        const t = this.audio.currentTime;
        const beats = this.analysis.beats;
        const beatIdx = _beatIndexAt(t, beats);
        if (beatIdx < 0) {
            // Lead-in before the first detected beat.
            const intent = this._breathingPose(this._breathingT);
            intent.antennas = this._continuousAntennas(t);
            this.mixer.sendIntent(intent, dt);
            this.dispatchEvent(new CustomEvent("tick", { detail: { phase: "lead-in" } }));
            return;
        }

        const blockIdx = Math.floor(beatIdx / this.config.beatsPerBlock);
        const energy = this.analysis.sequence_assignments[blockIdx] ?? "medium";

        if (blockIdx !== this._currentBlock) {
            this._enterBlock(blockIdx, energy);
        }

        const breathing = this._breathingPose(this._breathingT);
        const antennas = this._continuousAntennas(t);

        if (!this._currentSequence) {
            const intent = breathing;
            intent.antennas = antennas;
            this.mixer.sendIntent(intent, dt);
            return;
        }

        // beat_in_block in [0, 7] selects the target coord for this beat.
        // connected_choreographer.py:1369-1374.
        const beatInBlock = Math.min(beatIdx % this.config.beatsPerBlock, this._currentSequence.length - 1);
        const target = this._currentSequence[beatInBlock].coords;

        // EMA blend the held offset toward the new target.
        // connected_choreographer.py:1379-1380.
        const a = this.config.interpolationAlpha;
        for (let i = 0; i < 6; i++) {
            this._currentOffset[i] += (target[i] - this._currentOffset[i]) * a;
        }

        // Convert blended coords to position/orientation offsets.
        // connected_choreographer.py:1230-1257.
        const { position: posOffset, orientation: oriOffset } = _coordsToOffset(this._currentOffset, this.config.amplitudeScale);

        // Asymmetric yaw: snap fast toward target, decay slow back.
        // connected_choreographer.py:1382-1392. target_yaw is read directly
        // from the move's coord, NOT through the slow EMA on _currentOffset
        // (Python comment line 1382).
        const targetYaw = _toRadians(target[5] * this.config.amplitudeScale);
        const yawAlpha = Math.abs(targetYaw) > Math.abs(this._currentBodyYaw)
            ? this.config.bodyYawAttack
            : this.config.bodyYawDecay;
        this._currentBodyYaw += (targetYaw - this._currentBodyYaw) * yawAlpha;

        const intent = {
            position: [
                breathing.position[0] + posOffset[0],
                breathing.position[1] + posOffset[1],
                breathing.position[2] + posOffset[2],
            ],
            orientation: [
                breathing.orientation[0] + oriOffset[0],
                breathing.orientation[1] + oriOffset[1],
                breathing.orientation[2] + oriOffset[2],
            ],
            antennas,
            bodyYaw: this._currentBodyYaw,
        };
        this.mixer.sendIntent(intent, dt);

        this.dispatchEvent(new CustomEvent("tick", {
            detail: {
                phase: "playing",
                beat: beatIdx,
                block: blockIdx,
                energy,
                move: this._currentSequence[beatInBlock].name,
                t,
            },
        }));
    }

    _enterBlock(blockIdx, energy) {
        this._currentBlock = blockIdx;
        this._currentEnergy = energy;
        this._currentSequence = this._selectSequence(energy);
        const seqIdx = this._lastSequenceIdx[energy] ?? 0;
        this.dispatchEvent(new CustomEvent("blockEntered", {
            detail: {
                block: blockIdx,
                energy,
                move: `${energy} #${seqIdx}`,
                mirrored: false,
            },
        }));
    }

    // Round-robin pick within the energy level so the same sequence does
    // not appear twice in a row. connected_choreographer.py:1204-1213.
    _selectSequence(energy) {
        const sequences = EIGHT_BEAT_SEQUENCES[energy] ?? EIGHT_BEAT_SEQUENCES.medium;
        const last = this._lastSequenceIdx[energy] ?? -1;
        const next = (last + 1) % sequences.length;
        this._lastSequenceIdx[energy] = next;
        return sequences[next];
    }

    // Continuous breathing pose (Y sway + head roll). Always runs.
    // connected_choreographer.py:1215-1228.
    _breathingPose(t) {
        const yOff = this.config.breathingYAmplitude * Math.sin(2 * Math.PI * this.config.breathingYHz * t);
        const rollOff = this.config.breathingRollAmplitude * Math.sin(2 * Math.PI * this.config.breathingRollHz * t);
        return {
            position: [
                this.config.neutralPos[0],
                this.config.neutralPos[1] + yOff,
                this.config.neutralPos[2],
            ],
            orientation: [
                this.config.neutralEul[0] + rollOff,
                this.config.neutralEul[1],
                this.config.neutralEul[2],
            ],
            antennas: this._restingAntennas(),
            bodyYaw: 0,
        };
    }

    _restingAntennas() {
        const rest = this.config.antennaRestPosition;
        return [rest, -rest];
    }

    // Drive antenna splay off the song's RMS energy envelope.
    // connected_choreographer.py:1274-1310. Falls back to resting if the
    // analyzer response did not include energy_envelope (older Spaces).
    _continuousAntennas(currentTime) {
        const env = this.analysis.energy_envelope;
        if (!env || env.length === 0) return this._restingAntennas();

        const sr = this.analysis.envelope_sr;
        const hop = this.analysis.hop_length;
        if (!sr || !hop) return this._restingAntennas();

        let idx = Math.floor(currentTime * sr / hop);
        if (idx < 0) return this._restingAntennas();
        if (idx >= env.length) {
            // Tail past the end of detected envelope: snap to rest.
            if (idx > env.length + 10) return this._restingAntennas();
            idx = env.length - 1;
        }

        const rawRms = env[idx];

        // Noise gate.
        const threshold = this.config.antennaEnergyThreshold;
        let signal = rawRms < threshold ? 0 : rawRms - threshold;

        // Pre-amp + sensitivity.
        signal = signal * this.config.antennaGain * this.config.antennaSensitivity;

        // Map to splay, capped at antennaAmplitude.
        const splay = Math.min(signal, this.config.antennaAmplitude);

        const rest = this.config.antennaRestPosition;
        const left = rest - splay;
        const right = -rest + splay;
        return [left, right];
    }
}

// Largest i such that beats[i] <= t. -1 if t precedes the first beat.
function _beatIndexAt(t, beats) {
    if (beats.length === 0 || t < beats[0]) return -1;
    let lo = 0, hi = beats.length - 1;
    if (t >= beats[hi]) return hi;
    while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (beats[mid] <= t) lo = mid; else hi = mid;
    }
    return lo;
}

// connected_choreographer.py:1230-1257. coords are
// [x_cm, y_cm, z_cm, roll_deg, pitch_deg, yaw_deg]; this returns
// position offset (meters) and orientation offset (radians) with the
// global amplitude scale baked in. body_yaw is handled separately by
// the asymmetric smoother so it is not returned here.
function _coordsToOffset(coords, scale) {
    return {
        position: [
            coords[0] * 0.01 * scale,
            coords[1] * 0.01 * scale,
            coords[2] * 0.01 * scale,
        ],
        orientation: [
            _toRadians(coords[3] * scale),
            _toRadians(coords[4] * scale),
            0,
        ],
    };
}

function _toRadians(deg) {
    return deg * Math.PI / 180;
}
