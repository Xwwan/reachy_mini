let audioContext = null;
let playbackContext = null;
let mediaStream = null;
let sourceNode = null;
let processorNode = null;
let eventSource = null;
let sessionId = null;
let isRunning = false;
let inputOpen = false;
let activeInputMode = null;
let startedAt = 0;
let timerId = null;
let sendLoopPromise = null;
let sendQueue = [];
let pendingBytes = new Uint8Array(0);
let chunkCount = 0;
let acceptedChunkCount = 0;
let droppedChunkCount = 0;
let audioEventCount = 0;
let logCount = 0;
let replyText = "";
let playbackNextTime = 0;
let latestBackendVad = 0;

const TARGET_SAMPLE_RATE = 16000;
const CHUNK_BYTES = 5120;

const els = {
    serviceUrl: document.getElementById("service-url"),
    conversationId: document.getElementById("conversation-id"),
    inputMode: document.getElementById("input-mode"),
    ttsEnabled: document.getElementById("tts-enabled"),
    playAudio: document.getElementById("play-audio"),
    healthBtn: document.getElementById("health-btn"),
    startBtn: document.getElementById("start-btn"),
    stopBtn: document.getElementById("stop-btn"),
    clearLogBtn: document.getElementById("clear-log-btn"),
    recordingTime: document.getElementById("recording-time"),
    vadLevelText: document.getElementById("vad-level-text"),
    vadLevelFill: document.getElementById("vad-level-fill"),
    sessionId: document.getElementById("session-id"),
    state: document.getElementById("state"),
    chunkCount: document.getElementById("chunk-count"),
    localRms: document.getElementById("local-rms"),
    audioCount: document.getElementById("audio-count"),
    transcript: document.getElementById("transcript"),
    reply: document.getElementById("reply"),
    configInfo: document.getElementById("config-info"),
    eventLog: document.getElementById("event-log"),
    logCount: document.getElementById("log-count"),
    connectionStatus: document.getElementById("connection-status"),
    statusLine: document.getElementById("status-line"),
};

async function loadSettings() {
    const [settingsResponse, configResponse, behaviorResponse, modeResponse] = await Promise.all([
        fetch("/api/settings"),
        fetch("/api/auto-voice/config"),
        fetch("/api/behavior-config"),
        fetch("/api/app-mode"),
    ]);
    const settings = await settingsResponse.json();
    const config = await configResponse.json();
    const behavior = await behaviorResponse.json();
    const mode = await modeResponse.json();
    els.serviceUrl.value = settings.service_url;
    els.conversationId.value = settings.conversation_id || "auto-voice-test";
    if (mode.web_only) {
        els.inputMode.value = "local";
        for (const option of els.inputMode.options) {
            if (option.value === "robot") option.disabled = true;
        }
    }
    renderConfig({ auto_voice_endpoint: config, behavior_config: behavior.auto_voice, app_mode: mode });
    logEvent("config", { config, behavior_auto_voice: behavior.auto_voice, app_mode: mode });
}

async function saveSettings() {
    const response = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            service_url: els.serviceUrl.value,
            conversation_id: els.conversationId.value || "auto-voice-test",
        }),
    });
    if (!response.ok) throw new Error("保存设置失败");
    return response.json();
}

async function checkHealth() {
    setStatus("正在检查服务...");
    await saveSettings();
    const response = await fetch("/api/health");
    const result = await response.json();
    logEvent("health", result);
    if (result.ok) {
        els.connectionStatus.textContent = `已连接 ${result.service_url}`;
        setStatus("对话服务在线");
    } else {
        els.connectionStatus.textContent = "服务不可用";
        setStatus(result.error || "对话服务不可用");
    }
}

async function startTest() {
    if (isRunning) return;
    await saveSettings();
    resetCounters();
    await ensurePlaybackContext();
    activeInputMode = els.inputMode.value;
    const response = await fetch("/api/auto-voice/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            input_mode: activeInputMode,
            conversation_id: els.conversationId.value || "auto-voice-test",
            tts_enabled: els.ttsEnabled.value === "true",
        }),
    });
    const payload = await response.json().catch(() => ({}));
    logEvent("start-response", payload);
    if (!response.ok) {
        throw new Error(payload.detail || payload.message || "启动自动语音失败");
    }

    sessionId = payload.session_id;
    isRunning = true;
    inputOpen = payload.state === "listening" || payload.state === "user_speaking";
    startedAt = Date.now();
    els.sessionId.textContent = sessionId;
    els.startBtn.disabled = true;
    els.startBtn.classList.add("recording");
    els.stopBtn.disabled = false;
    els.inputMode.disabled = true;
    els.state.textContent = payload.state || "starting";
    timerId = window.setInterval(updateTimer, 250);
    connectEvents();
    if (activeInputMode === "local") {
        await startLocalMic();
    }
    setStatus(activeInputMode === "local" ? "电脑麦克风监听中" : "机器人麦克风监听中");
}

function connectEvents() {
    if (eventSource) eventSource.close();
    eventSource = new EventSource(
        `/api/auto-voice/events?session_id=${encodeURIComponent(sessionId)}`,
    );
    for (const eventName of [
        "snapshot",
        "state",
        "level",
        "speech_start",
        "speech_end",
        "speech_cancelled",
        "input_dropped",
        "input_drained",
        "utterance",
        "transcript",
        "meta",
        "delta",
        "audio",
        "behavior",
        "done",
        "playback_done",
        "warning",
        "error",
    ]) {
        eventSource.addEventListener(eventName, (event) => {
            handleAutoEvent(eventName, parseEventPayload(event.data));
        });
    }
    eventSource.onerror = () => {
        logEvent("event-source-error", { readyState: eventSource?.readyState });
        setStatus("事件通道断开");
    };
}

async function startLocalMic() {
    mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        },
    });
    audioContext = new AudioContext();
    sourceNode = audioContext.createMediaStreamSource(mediaStream);
    processorNode = audioContext.createScriptProcessor(4096, 1, 1);
    processorNode.onaudioprocess = (event) => {
        if (!isRunning || activeInputMode !== "local") return;
        const input = event.inputBuffer.getChannelData(0);
        updateLocalMicMeter(input);
        if (!inputOpen) {
            pendingBytes = new Uint8Array(0);
            return;
        }
        enqueuePcm(downsampleToPcm16(input, audioContext.sampleRate, TARGET_SAMPLE_RATE));
    };
    sourceNode.connect(processorNode);
    processorNode.connect(audioContext.destination);
    sendLoopPromise = sendLoop();
}

async function sendLoop() {
    while (isRunning || sendQueue.length > 0) {
        const chunk = sendQueue.shift();
        if (!chunk) {
            await sleep(25);
            continue;
        }
        const response = await fetch("/api/auto-voice/chunk", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: sessionId,
                audio_base64: bytesToBase64(chunk),
                sample_rate: TARGET_SAMPLE_RATE,
            }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.error) {
            logEvent("chunk-error", payload);
            throw new Error(payload.detail || payload.error?.message || "音频块发送失败");
        }
        chunkCount += 1;
        if (payload.accepted === false) {
            droppedChunkCount += 1;
        } else {
            acceptedChunkCount += 1;
        }
        els.chunkCount.textContent = `${acceptedChunkCount}/${chunkCount}`;
    }
}

function enqueuePcm(bytes) {
    pendingBytes = concatBytes(pendingBytes, bytes);
    while (pendingBytes.length >= CHUNK_BYTES) {
        sendQueue.push(pendingBytes.slice(0, CHUNK_BYTES));
        pendingBytes = pendingBytes.slice(CHUNK_BYTES);
    }
}

function handleAutoEvent(eventName, data) {
    logEvent(eventName, data);
    if (eventName === "snapshot") {
        els.state.textContent = data.state || els.state.textContent;
        inputOpen = data.state === "listening" || data.state === "user_speaking";
        return;
    }
    if (eventName === "state") {
        els.state.textContent = data.state || "-";
        inputOpen = data.state === "listening" || data.state === "user_speaking";
        if (!inputOpen) {
            sendQueue = [];
            pendingBytes = new Uint8Array(0);
        }
        setStatus(`状态：${data.state || "-"}`);
        return;
    }
    if (eventName === "level") {
        const probability = clamp01(Number(data.speech_probability || 0));
        const rms = Number(data.rms || 0);
        latestBackendVad = probability;
        els.vadLevelFill.style.width = `${Math.round(probability * 100)}%`;
        els.vadLevelText.textContent = `vad ${probability.toFixed(3)} rms ${rms.toFixed(5)}`;
        return;
    }
    if (eventName === "speech_start") {
        inputOpen = true;
        replyText = "";
        els.reply.textContent = "等待回复";
        els.transcript.textContent = "检测到语音";
        return;
    }
    if (eventName === "speech_end") {
        inputOpen = false;
        sendQueue = [];
        pendingBytes = new Uint8Array(0);
        els.transcript.textContent = "语音结束，正在识别";
        return;
    }
    if (eventName === "speech_cancelled") {
        inputOpen = true;
        els.transcript.textContent = "语音太短";
        return;
    }
    if (eventName === "input_dropped") {
        droppedChunkCount = Math.max(
            droppedChunkCount,
            Number(data.dropped_input_chunks || droppedChunkCount),
        );
        els.chunkCount.textContent = `${acceptedChunkCount}/${chunkCount}`;
        return;
    }
    if (eventName === "input_drained") {
        return;
    }
    if (eventName === "transcript") {
        const text = data.transcript || data.text || "";
        els.transcript.textContent = text || "(未识别到文本)";
        return;
    }
    if (eventName === "delta") {
        replyText += data.delta || "";
        els.reply.textContent = replyText || " ";
        return;
    }
    if (eventName === "audio") {
        audioEventCount += 1;
        els.audioCount.textContent = String(audioEventCount);
        if (els.playAudio.checked) {
            playPcmChunk(
                base64ToBytes(data.audio_base64 || ""),
                Number(data.sample_rate || data.audio_sample_rate || 24000),
            );
        }
        return;
    }
    if (eventName === "done") {
        if (data.transcript) els.transcript.textContent = data.transcript;
        if (data.reply) {
            replyText = data.reply;
            els.reply.textContent = replyText;
        }
        return;
    }
    if (eventName === "playback_done") {
        inputOpen = false;
        setStatus("播放完成，等待下一句");
        return;
    }
    if (eventName === "error") {
        setStatus(data.message || "自动语音错误");
    }
}

async function stopTest() {
    const currentSession = sessionId;
    isRunning = false;
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    if (pendingBytes.length > 0) {
        sendQueue.push(pendingBytes);
        pendingBytes = new Uint8Array(0);
    }
    stopLocalMic();
    if (sendLoopPromise) {
        await sendLoopPromise.catch((error) => logEvent("send-loop-error", { message: String(error) }));
        sendLoopPromise = null;
    }
    if (currentSession) {
        await fetch("/api/auto-voice/stop", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: currentSession }),
        }).catch((error) => logEvent("stop-error", { message: String(error) }));
    }
    window.clearInterval(timerId);
    timerId = null;
    sessionId = null;
    inputOpen = false;
    activeInputMode = null;
    els.startBtn.disabled = false;
    els.startBtn.classList.remove("recording");
    els.stopBtn.disabled = true;
    els.inputMode.disabled = false;
    els.recordingTime.textContent = "00:00";
    els.state.textContent = "stopped";
    els.sessionId.textContent = "-";
    setStatus("已停止");
}

function stopLocalMic() {
    if (processorNode) processorNode.disconnect();
    if (sourceNode) sourceNode.disconnect();
    if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
    if (audioContext) audioContext.close();
    processorNode = null;
    sourceNode = null;
    mediaStream = null;
    audioContext = null;
}

function resetCounters() {
    sendQueue = [];
    pendingBytes = new Uint8Array(0);
    chunkCount = 0;
    acceptedChunkCount = 0;
    droppedChunkCount = 0;
    audioEventCount = 0;
    replyText = "";
    latestBackendVad = 0;
    els.chunkCount.textContent = "0/0";
    els.audioCount.textContent = "0";
    els.transcript.textContent = "等待语音";
    els.reply.textContent = "等待事件";
}

function renderConfig(data) {
    els.configInfo.textContent = JSON.stringify(data, null, 2);
}

function logEvent(type, payload = {}) {
    logCount += 1;
    els.logCount.textContent = String(logCount);
    const line = `[${new Date().toISOString()}] ${type}\n${JSON.stringify(payload, null, 2)}\n`;
    if (logCount === 1 || els.eventLog.textContent === "等待测试") {
        els.eventLog.textContent = line;
    } else {
        els.eventLog.textContent += `\n${line}`;
    }
    els.eventLog.scrollTop = els.eventLog.scrollHeight;
}

function clearLog() {
    logCount = 0;
    els.logCount.textContent = "0";
    els.eventLog.textContent = "等待测试";
}

function updateTimer() {
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
    const rest = String(seconds % 60).padStart(2, "0");
    els.recordingTime.textContent = `${minutes}:${rest}`;
}

function updateLocalMicMeter(samples) {
    let sum = 0;
    let peak = 0;
    for (const sample of samples) {
        sum += sample * sample;
        peak = Math.max(peak, Math.abs(sample));
    }
    const rms = Math.sqrt(sum / Math.max(1, samples.length));
    els.localRms.textContent = rms.toFixed(5);
    if (latestBackendVad > 0) return;
    const localLevel = clamp01(rms / 0.08);
    els.vadLevelFill.style.width = `${Math.round(localLevel * 100)}%`;
    els.vadLevelText.textContent = `local rms ${rms.toFixed(5)} peak ${peak.toFixed(5)}`;
}

function parseEventPayload(raw) {
    try {
        return JSON.parse(raw);
    } catch {
        return { raw };
    }
}

async function ensurePlaybackContext() {
    if (!playbackContext || playbackContext.state === "closed") {
        playbackContext = new AudioContext();
        playbackNextTime = playbackContext.currentTime;
    }
    if (playbackContext.state === "suspended") {
        await playbackContext.resume();
    }
}

function playPcmChunk(bytes, sampleRate) {
    if (!bytes.length || !playbackContext) return;
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const sampleCount = Math.floor(bytes.length / 2);
    const buffer = playbackContext.createBuffer(1, sampleCount, sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < sampleCount; i += 1) {
        channel[i] = view.getInt16(i * 2, true) / 32768;
    }
    const source = playbackContext.createBufferSource();
    source.buffer = buffer;
    source.connect(playbackContext.destination);
    const startAt = Math.max(playbackContext.currentTime + 0.02, playbackNextTime);
    source.start(startAt);
    playbackNextTime = startAt + buffer.duration;
}

function downsampleToPcm16(input, sourceRate, targetRate) {
    if (sourceRate === targetRate) {
        return floatToPcm16(input);
    }
    const ratio = sourceRate / targetRate;
    const length = Math.floor(input.length / ratio);
    const output = new Float32Array(length);
    for (let i = 0; i < length; i += 1) {
        const start = Math.floor(i * ratio);
        const end = Math.min(input.length, Math.floor((i + 1) * ratio));
        let sum = 0;
        for (let j = start; j < end; j += 1) sum += input[j];
        output[i] = sum / Math.max(1, end - start);
    }
    return floatToPcm16(output);
}

function floatToPcm16(samples) {
    const bytes = new Uint8Array(samples.length * 2);
    const view = new DataView(bytes.buffer);
    for (let i = 0; i < samples.length; i += 1) {
        const clamped = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    }
    return bytes;
}

function concatBytes(a, b) {
    const output = new Uint8Array(a.length + b.length);
    output.set(a, 0);
    output.set(b, a.length);
    return output;
}

function bytesToBase64(bytes) {
    let binary = "";
    const chunkSize = 0x8000;
    for (let i = 0; i < bytes.length; i += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
    }
    return btoa(binary);
}

function base64ToBytes(value) {
    if (!value) return new Uint8Array(0);
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
}

function clamp01(value) {
    if (!Number.isFinite(value)) return 0;
    return Math.max(0, Math.min(1, value));
}

function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function setStatus(message) {
    els.statusLine.textContent = message;
}

els.healthBtn.addEventListener("click", () => {
    checkHealth().catch((error) => {
        logEvent("health-error", { message: error.message || String(error) });
        setStatus(error.message || String(error));
    });
});

els.startBtn.addEventListener("click", () => {
    startTest().catch((error) => {
        logEvent("start-error", { message: error.message || String(error) });
        setStatus(error.message || String(error));
        stopTest().catch(() => {});
    });
});

els.stopBtn.addEventListener("click", () => {
    stopTest().catch((error) => {
        logEvent("stop-error", { message: error.message || String(error) });
        setStatus(error.message || String(error));
    });
});

els.clearLogBtn.addEventListener("click", clearLog);

window.addEventListener("beforeunload", () => {
    if (eventSource) eventSource.close();
    stopLocalMic();
});

loadSettings()
    .then(() => checkHealth())
    .catch((error) => {
        logEvent("load-error", { message: error.message || String(error) });
        setStatus(error.message || String(error));
    });
