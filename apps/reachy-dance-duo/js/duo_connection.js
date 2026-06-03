import { stateToFrame } from './state_to_pose.js';

// Manages two ReachyMini SDK instances side by side (Lite + Wireless).
// Each side exposes:
//   instance        the ReachyMini SDK object
//   robotId         currently-bound robot id (or null)
//   streaming       true once the `streaming` event fired
//   robotStream     MediaStream from the `videoTrack` event (mic + cam)
//   lastFrame       most recent dance-duo-shape pose frame from `state` events
//
// The page wires lifecycle in this order:
//   1. duo.bootstrap()       checks auth, returns 'authenticated' or 'needs-login'
//   2. duo.login()           HF OAuth redirect (one call covers both robots)
//   3. duo.connect()         opens signaling on BOTH instances
//   4. duo.startSession(side, robotId)   pairs a discovered robot to a side
//
// Multi-robot caveat: HF signaling is a single account. Both instances see the
// same `robots` list via `robotsChanged`. The page picks which robot id maps to
// which side; this module stores the mapping and starts sessions accordingly.

export class DuoConnection extends EventTarget {
    constructor(ReachyMini) {
        super();
        this.ReachyMini = ReachyMini;
        this.lite = this._makeSide('lite');
        this.wireless = this._makeSide('wireless');
        this.username = null;
    }

    _makeSide(side) {
        // Only the wireless side (Beat Bandit) needs to send audio out: the page
        // routes the music stream into its WebRTC mic so the wireless robot
        // plays the song. The lite side (Live Groove) listens to ambient sound
        // via its own onboard mic and must not receive the music stream, so we
        // leave enableMicrophone off on that side.
        const inst = new this.ReachyMini({
            enableMicrophone: side === 'wireless',
            appName: `dance-duo-${side}`,
            signalingUrl: 'https://pollen-robotics-reachy-mini-central.hf.space',
        });
        // Kill the SDK's built-in auto-start — we control session lifecycle
        inst._maybeAutoStart = () => {};
        const s = {
            side,
            instance: inst,
            robotId: null,
            streaming: false,
            connected: false,
            robotStream: null,
            lastFrame: null,
        };
        const tag = `[duo:${side}]`;
        inst.addEventListener('connected', (e) => {
            console.log(`${tag} connected, peerId:`, e.detail?.peerId);
            s.connected = true;
            this._emit('side-connected', { side });
        });
        inst.addEventListener('disconnected', (e) => {
            console.warn(`${tag} disconnected:`, e.detail?.reason);
            s.connected = false;
            s.streaming = false;
            this._emit('side-disconnected', { side, reason: e.detail?.reason });
        });
        inst.addEventListener('streaming', (e) => {
            console.log(`${tag} streaming, session:`, e.detail?.sessionId);
            s.streaming = true;
            this._emit('side-streaming', { side, sessionId: e.detail?.sessionId });
        });
        inst.addEventListener('sessionStopped', (e) => {
            console.log(`${tag} session stopped:`, e.detail?.reason);
            s.streaming = false;
            this._emit('side-session-stopped', { side, reason: e.detail?.reason });
        });
        inst.addEventListener('sessionRejected', (e) => {
            console.warn(`${tag} session rejected, activeApp:`, e.detail?.activeApp);
            this._emit('side-session-rejected', { side, activeApp: e.detail?.activeApp });
        });
        inst.addEventListener('robotsChanged', (e) => {
            const robots = e.detail?.robots || [];
            console.log(`${tag} robotsChanged: ${robots.length} robot(s)`, JSON.stringify(robots));
            this._emit('robots-changed', { side, robots });
        });
        inst.addEventListener('videoTrack', (e) => {
            s.robotStream = e.detail?.stream || null;
            this._emit('side-stream', { side, stream: s.robotStream });
        });
        inst.addEventListener('state', (e) => {
            s.lastFrame = stateToFrame(e.detail);
            this._emit('side-state', { side, frame: s.lastFrame });
        });
        inst.addEventListener('error', (e) => {
            console.error(`${tag} ERROR [${e.detail?.source}]:`, e.detail?.error);
            this._emit('side-error', { side, source: e.detail?.source, error: e.detail?.error });
        });
        return s;
    }

    _emit(type, detail) {
        this.dispatchEvent(new CustomEvent(type, { detail }));
    }

    async bootstrap() {
        try {
            const okLite = await this.lite.instance.authenticate();
            const okWireless = await this.wireless.instance.authenticate();
            console.log(`[duo] bootstrap: lite=${okLite}, wireless=${okWireless}`);
            const ok = okLite && okWireless;
            if (ok) {
                this.username = this.lite.instance.username || this.wireless.instance.username || 'you';
            }
            return ok;
        } catch (e) {
            console.error('[duo] bootstrap error:', e);
            return false;
        }
    }

    login() {
        this.lite.instance.login();
    }

    async connect() {
        console.log('[duo] connect: opening signaling on both instances...');
        await Promise.all([
            this.lite.instance.connect().catch(e => {
                console.error('[duo] lite connect failed:', e);
                this._emit('side-error', { side: 'lite', source: 'connect', error: e?.message });
            }),
            this.wireless.instance.connect().catch(e => {
                console.error('[duo] wireless connect failed:', e);
                this._emit('side-error', { side: 'wireless', source: 'connect', error: e?.message });
            }),
        ]);
        console.log('[duo] connect: both instances resolved');
    }

    async refreshRobots() {
        console.log('[duo] refreshRobots: querying signaling server...');

        // Try the REST endpoint first — it returns richer data than SSE list
        try {
            const owned = await this.lite.instance._fetchOwnedRobots({ filterBusy: false });
            console.log(`[duo] /api/robot-status returned ${owned.length} robot(s):`, JSON.stringify(owned));
            if (owned.length > 0) {
                const robots = owned.map(r => ({ id: r.id, meta: { name: r.name, ...r.meta } }));
                this._emit('robots-changed', { side: 'lite', robots });
                return;
            }
        } catch (e) {
            console.warn('[duo] _fetchOwnedRobots failed:', e);
        }

        // Fallback: send list request through signaling
        for (const side of ['lite', 'wireless']) {
            try {
                const res = await this[side].instance._sendToServer({ type: 'list' });
                const robots = res?.producers || [];
                console.log(`[duo] refreshRobots ${side}: ${robots.length} producer(s)`, JSON.stringify(robots));
                this._emit('robots-changed', { side, robots });
            } catch (e) {
                console.warn(`[duo] refreshRobots ${side} failed:`, e);
            }
        }
    }

    async startSession(side, robotId) {
        const s = this[side];
        if (!s) throw new Error(`unknown side ${side}`);
        await s.instance.startSession(robotId);
        s.robotId = robotId;
    }

    async stopSession(side) {
        const s = this[side];
        if (!s) return;
        try { await s.instance.stopSession(); } catch {}
        s.robotId = null;
        s.streaming = false;
    }

    sideOf(side) {
        return this[side];
    }
}
