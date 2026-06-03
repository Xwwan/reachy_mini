// Browser port of /Users/twinpeakstownie/reachy_dance_duo_source/reachy_dance_duo/behaviors/live_groove.py
//
// Audio source is the ROBOT's USB mic, streamed through the WebRTC peer
// connection set up by the Reachy Mini SDK. We do NOT call getUserMedia.
// The Python original reads `mini.media.get_audio_sample()` (robot mic);
// the JS equivalent is the audio track on the SDK's `videoTrack` event
// (whose stream contains both video and audio of the robot). We feed that
// stream into realtime-bpm-analyzer and run the same BPM stability state
// machine the Python uses.
//
// The control loop (start move on Locked + confident, run for N beats,
// breathe in between, coast on last_active_bpm during Unstable) follows
// live_groove.py lines 1056-1137 directly.

import { BpmStabilityTracker, clampBpm } from "./bpm_stability.js";
import {
    SafetyMixer,
    SAFETY_DEFAULTS,
    makeReachySink,
    neutralIntent,
} from "./safety_mixer.js";
import {
    AVAILABLE_MOVE_NAMES,
    loadAllMoves,
    sampleMoveAt,
    stretchedTime,
    dampenSample,
    applyMirroredFlip,
    dampeningFor,
    isMirrorable,
} from "./move_library.js";

// Cache the BPM analyzer worklet + biquad filter per AudioContext.
// realtime-bpm-analyzer v4 unconditionally re-registers the processor on
// every createRealTimeBpmProcessor call, which throws NotSupportedError
// from the worklet thread on the second registration. We dodge this by
// only ever building the graph once per context and reusing the nodes.
const BPM_GRAPHS = new WeakMap();

// Fisher-Yates in-place shuffle. Equivalent to Python's random.shuffle
// in live_groove.py:286 (called on cycle wrap so the dance order is not
// predictable across cycles).
function _shuffleInPlace(arr) {
    for (let i = arr.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        const tmp = arr[i];
        arr[i] = arr[j];
        arr[j] = tmp;
    }
}

async function getOrBuildBpmGraph(ctx) {
    let cached = BPM_GRAPHS.get(ctx);
    if (cached) return cached;
    const mod = await import("https://cdn.jsdelivr.net/npm/realtime-bpm-analyzer@4/+esm");
    const { createRealTimeBpmProcessor, getBiquadFilter } = mod;
    const analyzerNode = await createRealTimeBpmProcessor(ctx);
    const filter = getBiquadFilter(ctx);
    filter.connect(analyzerNode);
    cached = { analyzerNode, filter };
    BPM_GRAPHS.set(ctx, cached);
    return cached;
}

export const LIVE_GROOVE_DEFAULTS = {
    audioRate: 16000,
    audioWindowSec: 1.6,
    bpmMin: 70,
    bpmMax: 140,
    bpmStabilityBuffer: 6,
    bpmStabilityThreshold: 5.0,
    silenceTimeoutSec: 2.0,
    volumeGateThreshold: 0.005,
    musicConfidenceRatio: 1.5,
    beatsPerSequence: 4,
    minBreathingBetweenMovesSec: 0,
    // Robot-side control loop runs at 50Hz (per Reachy Mini SDK
    // 7.3-control-loop-architecture and the AGENTS.md note that 50Hz
    // is right for "most interactive apps"). Sending faster wastes
    // bandwidth and overwrites our own commands before the motor
    // cycle reads them. The browser main thread also has less work
    // to fight per second.
    controlLoopMs: 20,
    neutralPos: [0, 0, 0.01],
    neutralEul: [0, 0, 0],
    // Breathing parameters carry-over from live_groove.py:803-819. The Y sway
    // and head roll run at different frequencies on purpose so the pattern
    // does not repeat exactly, which is what gives the idle motion its
    // organic feel. Do not "simplify" these, they were tuned by hand.
    breathingYAmplitude: 0.016,   // meters of lateral sway
    breathingYHz: 0.2,
    breathingRollAmplitude: 0.222, // radians (~12.7 deg) of head roll
    breathingRollHz: 0.15,
};

export class LiveGroove extends EventTarget {
    constructor({
        robot,
        audioContext,
        robotStream,
        config = {},
        safetyConfig = {},
    }) {
        super();
        if (!robotStream) {
            throw new Error("LiveGroove requires robotStream (the MediaStream from the SDK's videoTrack event)");
        }
        this.robot = robot;
        this.audioContext = audioContext;
        this.robotStream = robotStream;
        this.config = { ...LIVE_GROOVE_DEFAULTS, ...config };
        this.mixer = new SafetyMixer(makeReachySink(robot), { ...SAFETY_DEFAULTS, ...safetyConfig });
        this.bpmTracker = new BpmStabilityTracker({
            bufferSize: this.config.bpmStabilityBuffer,
            threshold: this.config.bpmStabilityThreshold,
        });
        this.bpmTracker.addEventListener("bpmStateChange", (e) => {
            this.dispatchEvent(new CustomEvent("bpmStateChange", { detail: e.detail }));
        });
        this._lastEventTime = 0;
        this._lastActiveBpm = 0;
        this._musicConfident = false;
        this._streamSource = null;
        this._audioSink = null;          // hidden <audio> that keeps the WebRTC stream flowing
        this._analyzerNode = null;
        this._loopHandle = null;
        this._moveCycleIdx = 0;
        this._isExecuting = false;
        this._moveBeatsElapsed = 0;
        this._moveStartedAt = 0;
        this._currentMove = null;
        this._currentMoveName = null;
        this._isMirrored = false;
        this._moves = [];
        this._cycleNames = [];
        this._breathingT = 0;
        this._lastTickAt = 0;
        // Earliest time (perf.now()/1000) at which a new move can start
        // after the previous one finishes. Mirrors live_groove.py:1098,
        // 1110-1112 force_breathing_until logic.
        this._forceBreathingUntil = 0;
    }

    // Live-tune a subset of config fields without restarting the loop.
    // Currently the only knob exposed in the UI is volumeGateThreshold,
    // but the merge is generic so adding more later is straightforward.
    // The musicConfident floor depends on volumeGateThreshold * ratio,
    // so the gate change takes effect on the next analyzer message.
    updateConfig(patch) {
        Object.assign(this.config, patch);
    }

    // No mic gesture concerns now that audio comes from the WebRTC stream.
    // Kept as a no-op-ish prewarm hook so callers can still front-load moves.
    async prewarm({ moves = AVAILABLE_MOVE_NAMES } = {}) {
        if (!this._moves.length) {
            this._moves = await loadAllMoves(moves);
        }
        return this._moves;
    }

    async start({ moves = AVAILABLE_MOVE_NAMES } = {}) {
        if (this._loopHandle != null) return;

        if (!this._moves.length) {
            this._moves = await loadAllMoves(moves);
        }
        // Build the cycle from the per-move mirror policy in
        // move_library.MOVE_MIRROR_POLICY (port of move_mirror.json).
        // Only moves whose policy is true get a _mirrored variant; the
        // rest appear once. Matches what Python's MoveChoreographer
        // builds via move_config.is_mirrored().
        this._cycleNames = moves.flatMap(n =>
            isMirrorable(n) ? [n, `${n}_mirrored`] : [n]
        );
        // Initial shuffle so each session starts in a different order.
        // Python shuffles on every wrap (live_groove.py:284-286); we
        // also reshuffle in _advanceMove when the cycle wraps.
        _shuffleInPlace(this._cycleNames);

        await this._wireAudioGraph();

        this._lastTickAt = performance.now();
        this._loopHandle = setInterval(() => this._tick(), this.config.controlLoopMs);

        this.dispatchEvent(new CustomEvent("debug", { detail: { msg: "LiveGroove v1.1 starting..." } }));
        this.dispatchEvent(new CustomEvent("debug", { detail: { msg: `Audio state: ${this.audioContext.state}` } }));
        this.dispatchEvent(new CustomEvent("debug", { detail: { msg: `Mic tracks: ${this._localMicStream?.getAudioTracks().length ?? 0}` } }));

        // RMS heartbeat: every second, sample the source's amplitude so the
        // operator can tell if audio is actually flowing. If RMS stays at 0
        // the WebRTC track is silent (daemon not sending mic, or workaround
        // not effective). If RMS > 0 but BPM stays 0, the analyzer is not
        // finding peaks (signal too weak / not music-like).
        this._rmsHandle = setInterval(() => {
            if (!this._probe) return;
            const buf = new Float32Array(this._probe.fftSize);
            this._probe.getFloatTimeDomainData(buf);
            let sumSq = 0;
            for (let i = 0; i < buf.length; i++) sumSq += buf[i] * buf[i];
            const rms = Math.sqrt(sumSq / buf.length);

            // Update confidence in real-time so the control loop can start
            // breathing/moving even before the first beat is detected.
            const confidenceFloor = this.config.volumeGateThreshold * this.config.musicConfidenceRatio;
            this._musicConfident = rms > confidenceFloor;

            this.dispatchEvent(new CustomEvent("rms", { detail: { rms } }));
        }, 1000);

        this.dispatchEvent(new CustomEvent("started"));
    }

    async stop() {
        if (this._loopHandle != null) {
            clearInterval(this._loopHandle);
            this._loopHandle = null;
        }
        if (this._rmsHandle != null) {
            clearInterval(this._rmsHandle);
            this._rmsHandle = null;
        }
        if (this._streamSource) {
            try { this._streamSource.disconnect(); } catch {}
            this._streamSource = null;
        }
        if (this._gain) {
            try { this._gain.disconnect(); } catch {}
            this._gain = null;
        }
        if (this._audioSink) {
            try { this._audioSink.pause(); } catch {}
            this._audioSink.srcObject = null;
            this._audioSink = null;
        }
        // Do NOT disconnect filter / analyzerNode: they live on the cached
        // per-context graph and get reused next start(). Only the source-side
        // edge gets torn down. We do NOT stop the WebRTC tracks either; the
        // SDK still owns the peer connection and may need them.
        this._filter = null;
        this._probe = null;
        this._analyzerNode = null;
        this.bpmTracker.reset();
        this.mixer.reset();
        this._isExecuting = false;
        this._lastActiveBpm = 0;
        this.dispatchEvent(new CustomEvent("stopped"));
    }

    async _wireAudioGraph() {
        // Chrome bug 933677: createMediaStreamSource on a remote WebRTC
        // audio track produces silence unless the same stream is also
        // attached to an HTMLMediaElement that is playing. Workaround: a
        // hidden <audio> element with srcObject = robotStream, muted so it
        // does not double-play through the user's speakers.
        const sink = new Audio();
        sink.srcObject = this.robotStream;
        sink.muted = true;
        sink.autoplay = true;
        sink.playsInline = true;
        try {
            await sink.play();
            this.dispatchEvent(new CustomEvent("debug", { detail: { msg: "Robot audio sink playing" } }));
        } catch (e) {
            this.dispatchEvent(new CustomEvent("debug", { detail: { msg: `Robot audio sink failed: ${e.message}` } }));
        }
        this._audioSink = sink;

        // Log audio track state for debugging
        const audioTracks = this.robotStream.getAudioTracks();
        for (const t of audioTracks) {
            this.dispatchEvent(new CustomEvent("debug", { detail: { msg: `Robot audio track: enabled=${t.enabled} state=${t.readyState}` } }));
        }

        this._streamSource = this.audioContext.createMediaStreamSource(this.robotStream);

        // WebRTC audio comes in quiet (opus codec attenuates bass, RMS ~0.03).
        // realtime-bpm-analyzer needs PEAKS crossing a threshold that sweeps
        // from 0.95 down to 0.2; with our raw signal almost nothing crosses
        // even the lowest threshold. Boost the signal to fill the buffer.
        const gain = this.audioContext.createGain();
        gain.gain.value = 20.0; // Restored high boost for WebRTC Opus stream
        this._gain = gain;

        // Add a compressor to prevent clipping when boosting quiet mics.
        // This ensures peaks stay near 1.0 without becoming flat plateaus.
        const compressor = this.audioContext.createDynamicsCompressor();
        compressor.threshold.value = -10;
        compressor.knee.value = 40;
        compressor.ratio.value = 12;
        compressor.attack.value = 0;
        compressor.release.value = 0.25;
        this._compressor = compressor;

        // realtime-bpm-analyzer v4 chain: source -> gain -> biquad lowpass -> analyzer.
        // The processor 'realtime-bpm-processor' can only be registered once
        // per AudioContext; calling createRealTimeBpmProcessor a second time
        // throws NotSupportedError from the worklet thread. Cache the
        // analyzer + filter per-context so start/stop/start cycles work.
        const { analyzerNode, filter } = await getOrBuildBpmGraph(this.audioContext);

        // Volume probe runs in parallel with the BPM chain so we can
        // implement the volume_gate_threshold + music_confident logic.
        const probe = this.audioContext.createAnalyser();
        probe.fftSize = 16384; // Large window (approx 370ms) to bridge gaps in music
        const probeBuf = new Float32Array(probe.fftSize);

        this._streamSource.connect(probe);
        
        // Connect source -> gain -> compressor -> analyzer.
        // We bypass the library's default biquad filter because it can be 
        // too aggressive (150Hz lowpass) for laptop microphones.
        this._streamSource.connect(gain).connect(compressor).connect(analyzerNode);
        // filter -> analyzerNode is wired once inside getOrBuildBpmGraph
        // and persists across start/stop cycles.

        analyzerNode.port.onmessage = (e) => {
            if (!e.data) return;
            const msg = e.data.message;
            if (msg !== "BPM" && msg !== "BPM_STABLE") {
                if (msg) this.dispatchEvent(new CustomEvent("debug", { detail: { msg: `Analyzer msg: ${msg}` } }));
                return;
            }
            const candidates = e.data.data?.bpm ?? e.data.result ?? [];
            if (!candidates.length) {
                // Log the raw data once to see if the property name changed
                this.dispatchEvent(new CustomEvent("debug", { detail: { msg: `Analyzer empty. Keys: ${Object.keys(e.data.data || e.data || {}).join(",")}` } }));
                return;
            }
            const top = candidates[0];
            const raw = typeof top === "object" ? (top.tempo ?? top.bpm) : top;
            this.dispatchEvent(new CustomEvent("debug", { detail: { msg: `Analyzer beat: ${raw.toFixed(1)} BPM` } }));
            this._handleRawBpm(raw, probe, probeBuf);
        };

        this._analyzerNode = analyzerNode;
        this._filter = filter;
        this._probe = probe;
    }

    _handleRawBpm(rawBpm, probe, probeBuf) {
        // Volume gate (live_groove.py lines 970-979 + 982-986)
        probe.getFloatTimeDomainData(probeBuf);
        let sumSq = 0;
        for (let i = 0; i < probeBuf.length; i++) sumSq += probeBuf[i] * probeBuf[i];
        const rms = Math.sqrt(sumSq / probeBuf.length);

        const confidenceFloor = this.config.volumeGateThreshold * this.config.musicConfidenceRatio;
        this._musicConfident = rms > confidenceFloor;

        if (rms < this.config.volumeGateThreshold) {
            this.bpmTracker.reset();
            return;
        }

        // Half/double clamp to [bpmMin, bpmMax]
        const tempo = clampBpm(rawBpm, this.config.bpmMin, this.config.bpmMax);
        this._lastEventTime = performance.now() / 1000;
        this.bpmTracker.push(tempo);
    }

    // 100Hz control loop. Mirrors live_groove.py lines 1056-1137.
    _tick() {
        const nowMs = performance.now();
        const dt = (nowMs - this._lastTickAt) / 1000;
        this._lastTickAt = nowMs;
        const nowSec = nowMs / 1000;

        const bpm = this.bpmTracker.smoothedBpm;
        const state = this.bpmTracker.state;
        const activeBpm = (nowSec - this._lastEventTime) < this.config.silenceTimeoutSec ? bpm : 0;

        const canStart = (
            activeBpm > 0
            && this.bpmTracker.hasEverLocked
            && state === "Locked"
            && this._musicConfident
        );
        const inForcedBreathing = nowSec < this._forceBreathingUntil;

        if (this._isExecuting) {
            const bpmForMove = activeBpm > 0 ? activeBpm : this._lastActiveBpm;
            const beatsThisFrame = dt * (bpmForMove / 60);
            this._moveBeatsElapsed += beatsThisFrame;

            if (this._moveBeatsElapsed >= this.config.beatsPerSequence) {
                this._isExecuting = false;
                this._forceBreathingUntil = nowSec + this.config.minBreathingBetweenMovesSec;
                this._advanceMove();
            } else {
                // Sample the move using its progress in BEATS. This matches the Python
                // logic (live_groove.py:1116) where the playhead is driven by the
                // incremental t_beats accumulator, making it immune to BPM jitter.
                const targetDuration = this.config.beatsPerSequence * (60 / bpmForMove);
                const playheadSec = (this._moveBeatsElapsed / this.config.beatsPerSequence) * this._currentMove.duration;
                
                let sample = sampleMoveAt(this._currentMove, playheadSec);
                if (!sample) { this._isExecuting = false; return; }
                sample = applyMirroredFlip(sample, this._isMirrored);
                sample = dampenSample(sample, dampeningFor(this._currentMoveName),
                    this.config.neutralPos, this.config.neutralEul);
                this.mixer.sendIntent(sample, dt);
                this.dispatchEvent(new CustomEvent("tick", { detail: { phase: "move", bpm: bpmForMove, beats: this._moveBeatsElapsed } }));
            }
        } else if (canStart && !inForcedBreathing) {
            this._isExecuting = true;
            this._moveBeatsElapsed = 0;
            this._moveStartedAt = nowMs;
            this._lastActiveBpm = activeBpm;
            this._currentMoveName = this._cycleNames[this._moveCycleIdx];
            this._isMirrored = this._currentMoveName.endsWith("_mirrored");
            const baseName = this._isMirrored ? this._currentMoveName.slice(0, -"_mirrored".length) : this._currentMoveName;
            this._currentMove = this._moves.find(m => m.name === baseName);
            if (!this._currentMove) {
                this.dispatchEvent(new CustomEvent("debug", { detail: { msg: `Move not found: ${baseName}` } }));
                this._isExecuting = false;
                return;
            }
            this.dispatchEvent(new CustomEvent("moveStarted", { detail: { name: this._currentMoveName, bpm: activeBpm } }));
        } else {
            this._breathingT += dt;
            const intent = this._breathingPose(this._breathingT);
            this.mixer.sendIntent(intent, dt);
            this.dispatchEvent(new CustomEvent("tick", { detail: { phase: "breathing", bpm } }));
        }
    }

    _advanceMove() {
        this._moveCycleIdx = (this._moveCycleIdx + 1) % this._cycleNames.length;
        // Reshuffle the cycle every time it wraps. Mirrors
        // live_groove.py:284-286 (random.shuffle on wrap) so the dance
        // order does not become predictable across cycles.
        if (this._moveCycleIdx === 0) {
            _shuffleInPlace(this._cycleNames);
        }
        this.dispatchEvent(new CustomEvent("moveAdvanced", { detail: { next: this._cycleNames[this._moveCycleIdx] } }));
    }

    _breathingPose(t) {
        // Direct port of live_groove.py:803-819. Lateral Y sway at 0.2 Hz
        // and head roll at 0.15 Hz. The two run at offset frequencies so
        // they never line up the same way twice, which is the secret sauce
        // that makes the idle motion feel alive instead of clockwork.
        const yOffset = this.config.breathingYAmplitude * Math.sin(2 * Math.PI * this.config.breathingYHz * t);
        const rollOffset = this.config.breathingRollAmplitude * Math.sin(2 * Math.PI * this.config.breathingRollHz * t);
        return {
            position: [
                this.config.neutralPos[0],
                this.config.neutralPos[1] + yOffset,
                this.config.neutralPos[2],
            ],
            orientation: [
                this.config.neutralEul[0] + rollOffset,
                this.config.neutralEul[1],
                this.config.neutralEul[2],
            ],
            antennas: [-0.15, 0.15],
            bodyYaw: 0,
        };
    }
}
