let audioContext = null;
let playbackContext = null;
let mediaStream = null;
let sourceNode = null;
let processorNode = null;
let sessionId = null;
let isRecording = false;
let recordingStartedAt = 0;
let recordTimerId = null;
let transcriptTimerId = null;
let sendLoopPromise = null;
let sendQueue = [];
let pendingBytes = new Uint8Array(0);
let sentBytes = 0;
let sentChunks = 0;
let acceptedBytes = 0;
let firstDeltaAt = null;
let playbackNextTime = 0;

const TARGET_SAMPLE_RATE = 16000;
const CHUNK_BYTES = 5120;

const els = {
    serviceUrl: document.getElementById("service-url"),
    conversationId: document.getElementById("conversation-id"),
    ttsEnabled: document.getElementById("tts-enabled"),
    sampleRate: document.getElementById("sample-rate"),
    healthBtn: document.getElementById("health-btn"),
    recordBtn: document.getElementById("record-btn"),
    abortBtn: document.getElementById("abort-btn"),
    recordingTime: document.getElementById("recording-time"),
    micLevelFill: document.getElementById("mic-level-fill"),
    micLevelText: document.getElementById("mic-level-text"),
    liveTranscript: document.getElementById("live-transcript"),
    transcript: document.getElementById("transcript"),
    reply: document.getElementById("reply"),
    connectionStatus: document.getElementById("connection-status"),
    statusLine: document.getElementById("status-line"),
    debugInfo: document.getElementById("debug-info"),
};

async function loadSettings() {
    const response = await fetch("/api/settings");
    const settings = await response.json();
    els.serviceUrl.value = settings.service_url;
    els.conversationId.value = settings.conversation_id || "local-mic-test";
}

async function saveSettings() {
    const response = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            service_url: els.serviceUrl.value,
            conversation_id: els.conversationId.value || "local-mic-test",
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
    if (result.ok) {
        els.connectionStatus.textContent = `已连接 ${result.service_url}`;
        setStatus("对话服务在线");
    } else {
        els.connectionStatus.textContent = "服务不可用";
        setStatus(result.error || "对话服务不可用");
    }
}

async function startRecording() {
    await saveSettings();
    await ensurePlaybackContext();
    const startResponse = await fetch("/api/local-mic/start", { method: "POST" });
    const startPayload = await startResponse.json();
    if (!startResponse.ok || startPayload.error) {
        throw new Error(startPayload.detail || startPayload.error?.message || "启动实时语音失败");
    }

    sessionId = startPayload.session_id;
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
        const input = event.inputBuffer.getChannelData(0);
        updateMicLevel(input);
        enqueuePcm(downsampleToPcm16(input, audioContext.sampleRate, TARGET_SAMPLE_RATE));
    };
    sourceNode.connect(processorNode);
    processorNode.connect(audioContext.destination);

    sentBytes = 0;
    sentChunks = 0;
    acceptedBytes = 0;
    firstDeltaAt = null;
    sendQueue = [];
    pendingBytes = new Uint8Array(0);
    isRecording = true;
    recordingStartedAt = Date.now();
    sendLoopPromise = sendLoop();
    recordTimerId = window.setInterval(updateTimer, 250);
    transcriptTimerId = window.setInterval(() => {
        pollTranscript().catch((error) => setStatus(error.message || String(error)));
    }, 300);
    els.reply.textContent = "";
    els.transcript.textContent = "等待最终识别";
    els.liveTranscript.textContent = "正在听...";
    els.recordBtn.textContent = "停止并生成";
    els.recordBtn.classList.add("recording");
    els.abortBtn.disabled = false;
    setStatus(`录音中：${sessionId}`);
    renderDebug();
}

async function stopAndReply() {
    if (!isRecording) return;
    const stoppedAt = performance.now();
    isRecording = false;
    stopAudioGraph();
    els.recordBtn.disabled = true;
    els.abortBtn.disabled = true;
    els.recordBtn.textContent = "处理中...";
    els.recordBtn.classList.remove("recording");
    setStatus("正在发送剩余音频...");

    if (pendingBytes.length > 0) {
        sendQueue.push(pendingBytes);
        pendingBytes = new Uint8Array(0);
    }
    await sendLoopPromise;
    clearInterval(recordTimerId);
    clearInterval(transcriptTimerId);

    let reply = "";
    let transcript = "";
    let audioChunks = 0;
    setStatus("正在结束 ASR 并等待流式回复...");
    const finishResponse = await fetch("/api/local-mic/finish-stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            session_id: sessionId,
            conversation_id: els.conversationId.value || "local-mic-test",
            tts_enabled: els.ttsEnabled.value === "true",
        }),
    });
    if (!finishResponse.ok) {
        const payload = await finishResponse.json().catch(() => ({}));
        throw new Error(payload.detail || payload.error?.message || "结束流式语音失败");
    }

    await readSseStream(finishResponse, (event) => {
        if (event.event === "transcript") {
            transcript = event.data.transcript || transcript;
            els.transcript.textContent = transcript || "(未识别到文本)";
            els.liveTranscript.textContent = transcript || els.liveTranscript.textContent;
            return;
        }
        if (event.event === "meta") {
            setStatus("已连接模型流");
            return;
        }
        if (event.event === "delta") {
            if (!firstDeltaAt) firstDeltaAt = performance.now();
            reply += event.data.delta || "";
            els.reply.textContent = reply || " ";
            setStatus(`正在接收文字回复，首 token ${Math.round(firstDeltaAt - stoppedAt)} ms`);
            return;
        }
        if (event.event === "audio") {
            audioChunks += 1;
            playPcmChunk(
                base64ToBytes(event.data.audio_base64 || ""),
                event.data.sample_rate || 24000,
            );
            setStatus(`正在播放 TTS 音频 ${audioChunks}`);
            return;
        }
        if (event.event === "done") {
            transcript = event.data.transcript || transcript;
            reply = event.data.reply || reply;
            els.transcript.textContent = transcript || "(未识别到文本)";
            els.reply.textContent = reply || "(没有回复)";
            setStatus("完成");
            return;
        }
        if (event.event === "error") {
            throw new Error(event.data.message || "流式回复失败");
        }
    });
    resetControls();
}

async function abortRecording() {
    const currentSession = sessionId;
    isRecording = false;
    stopAudioGraph();
    clearInterval(recordTimerId);
    clearInterval(transcriptTimerId);
    if (currentSession) {
        await fetch("/api/local-mic/abort", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: currentSession }),
        }).catch(() => {});
    }
    resetControls();
    setStatus("已取消");
}

function enqueuePcm(bytes) {
    pendingBytes = concatBytes(pendingBytes, bytes);
    while (pendingBytes.length >= CHUNK_BYTES) {
        sendQueue.push(pendingBytes.slice(0, CHUNK_BYTES));
        pendingBytes = pendingBytes.slice(CHUNK_BYTES);
    }
}

async function sendLoop() {
    while (isRecording || sendQueue.length > 0) {
        const chunk = sendQueue.shift();
        if (!chunk) {
            await sleep(25);
            continue;
        }
        const response = await fetch("/api/local-mic/chunk", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: sessionId,
                audio_base64: bytesToBase64(chunk),
                is_final: false,
            }),
        });
        const payload = await response.json();
        if (!response.ok || payload.error || !payload.ok) {
            throw new Error(payload.detail || payload.error?.message || "音频块发送失败");
        }
        sentChunks += 1;
        sentBytes += chunk.length;
        acceptedBytes += Number(payload.accepted_bytes || 0);
        renderDebug();
    }
}

async function pollTranscript() {
    if (!sessionId || !isRecording) return;
    const response = await fetch(`/api/local-mic/transcript?session_id=${encodeURIComponent(sessionId)}`);
    const payload = await response.json();
    if (!response.ok || payload.error) {
        throw new Error(payload.detail || payload.error?.message || "读取实时字幕失败");
    }
    els.liveTranscript.textContent = payload.transcript || "正在听...";
    renderDebug({ transcript: payload.transcript, is_final: payload.is_final });
}

function stopAudioGraph() {
    if (processorNode) processorNode.disconnect();
    if (sourceNode) sourceNode.disconnect();
    if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
    if (audioContext) audioContext.close();
    processorNode = null;
    sourceNode = null;
    mediaStream = null;
    audioContext = null;
}

function resetControls() {
    els.recordBtn.textContent = "开始录音";
    els.recordBtn.disabled = false;
    els.recordBtn.classList.remove("recording");
    els.abortBtn.disabled = true;
    els.recordingTime.textContent = "00:00";
    sessionId = null;
}

function updateTimer() {
    const seconds = Math.floor((Date.now() - recordingStartedAt) / 1000);
    const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
    const rest = String(seconds % 60).padStart(2, "0");
    els.recordingTime.textContent = `${minutes}:${rest}`;
}

function updateMicLevel(samples) {
    let sum = 0;
    let peak = 0;
    for (const sample of samples) {
        sum += sample * sample;
        peak = Math.max(peak, Math.abs(sample));
    }
    const rms = Math.sqrt(sum / Math.max(1, samples.length));
    els.micLevelFill.style.width = `${Math.round(Math.min(1, rms / 0.08) * 100)}%`;
    els.micLevelText.textContent = `RMS ${rms.toFixed(5)} · 峰值 ${peak.toFixed(5)}`;
}

function renderDebug(extra = {}) {
    els.debugInfo.textContent = JSON.stringify({
        session_id: sessionId,
        is_recording: isRecording,
        queued_chunks: sendQueue.length,
        pending_bytes: pendingBytes.length,
        sent_chunks: sentChunks,
        sent_bytes: sentBytes,
        accepted_bytes: acceptedBytes,
        ...extra,
    }, null, 2);
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

async function ensurePlaybackContext() {
    if (!playbackContext) {
        playbackContext = new AudioContext();
        playbackNextTime = playbackContext.currentTime;
    }
    if (playbackContext.state === "suspended") await playbackContext.resume();
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

async function readSseStream(response, onEvent) {
    if (!response.body) throw new Error("浏览器不支持流式响应读取");
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split(/\n\n/);
        buffer = frames.pop() || "";
        for (const frame of frames) {
            const event = parseSseFrame(frame);
            if (event) onEvent(event);
        }
    }
    buffer += decoder.decode();
    const tail = buffer.trim();
    if (tail) {
        const event = parseSseFrame(tail);
        if (event) onEvent(event);
    }
}

function parseSseFrame(frame) {
    let event = "message";
    const dataLines = [];
    for (const rawLine of frame.split(/\n/)) {
        const line = rawLine.replace(/\r$/, "");
        if (!line || line.startsWith(":")) continue;
        if (line.startsWith("event:")) event = line.slice("event:".length).trim() || "message";
        if (line.startsWith("data:")) dataLines.push(line.slice("data:".length).trimStart());
    }
    if (!dataLines.length) return null;
    let data;
    try {
        data = JSON.parse(dataLines.join("\n"));
    } catch {
        data = { text: dataLines.join("\n") };
    }
    return { event, data };
}

function bytesToBase64(bytes) {
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary);
}

function base64ToBytes(value) {
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
}

function concatBytes(a, b) {
    const out = new Uint8Array(a.length + b.length);
    out.set(a, 0);
    out.set(b, a.length);
    return out;
}

function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function setStatus(text) {
    els.statusLine.textContent = text;
}

els.healthBtn.addEventListener("click", () => {
    checkHealth().catch((error) => setStatus(error.message || String(error)));
});

els.recordBtn.addEventListener("click", () => {
    if (isRecording) {
        stopAndReply().catch((error) => {
            setStatus(error.message || String(error));
            resetControls();
        });
    } else {
        startRecording().catch((error) => setStatus(error.message || String(error)));
    }
});

els.abortBtn.addEventListener("click", () => {
    abortRecording().catch((error) => setStatus(error.message || String(error)));
});

for (const input of [els.serviceUrl, els.conversationId, els.ttsEnabled]) {
    input.addEventListener("change", () => {
        saveSettings().catch((error) => setStatus(error.message || String(error)));
    });
}

loadSettings()
    .then(checkHealth)
    .catch((error) => setStatus(error.message || String(error)));
