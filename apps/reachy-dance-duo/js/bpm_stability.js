// Port of live_groove.py lines 908-910, 1014-1036.
//
// 6-sample ring buffer of recent BPM detections. Emits state transitions
// matching the original Python:
//   Gathering -> not enough samples (< buffer size)
//   Locked    -> std(last N) < threshold
//   Unstable  -> std >= threshold (caller coasts on last_active_bpm)
//
// Caller pushes raw BPMs from the analyzer; reads .state, .smoothedBpm,
// .std after each push. Emits a "bpmStateChange" CustomEvent on the
// supplied EventTarget when state changes.

const STABILITY_BUFFER = 6;
const STABILITY_THRESHOLD = 5.0;

export class BpmStabilityTracker extends EventTarget {
    constructor({ bufferSize = STABILITY_BUFFER, threshold = STABILITY_THRESHOLD } = {}) {
        super();
        this.bufferSize = bufferSize;
        this.threshold = threshold;
        this._buf = [];
        this._state = "Gathering";
        this._smoothed = 0;
        this._std = 0;
        this._hasEverLocked = false;
    }

    get state() { return this._state; }
    get smoothedBpm() { return this._smoothed; }
    get std() { return this._std; }
    get hasEverLocked() { return this._hasEverLocked; }

    reset() {
        this._buf.length = 0;
        this._setState("Gathering");
        this._smoothed = 0;
        this._std = 0;
    }

    // Push a clamped BPM (already passed through the half/double clamp).
    // Matches the Python `if tempo_val > 40: bpm_hist.append(...)` gate.
    push(bpm) {
        if (!(bpm > 40)) return;
        this._buf.push(bpm);
        if (this._buf.length > this.bufferSize) this._buf.shift();

        const n = this._buf.length;
        const mean = this._buf.reduce((a, b) => a + b, 0) / n;
        this._smoothed = mean;
        this._std = n > 1
            ? Math.sqrt(this._buf.reduce((a, b) => a + (b - mean) ** 2, 0) / n)
            : 0;

        let next;
        if (n < this.bufferSize) next = "Gathering";
        else if (this._std < this.threshold) { next = "Locked"; this._hasEverLocked = true; }
        else next = "Unstable";

        this._setState(next);
    }

    _setState(s) {
        if (s === this._state) return;
        const prev = this._state;
        this._state = s;
        this.dispatchEvent(new CustomEvent("bpmStateChange", { detail: { from: prev, to: s, bpm: this._smoothed } }));
    }
}

// Force BPM into [min, max] by halving or doubling. Port of _clamp_bpm
// from live_groove.py lines 878-886.
export function clampBpm(bpm, min = 70, max = 140) {
    if (!(bpm > 0)) return bpm;
    let b = bpm;
    while (b < min) b *= 2;
    while (b > max) b /= 2;
    return b;
}
