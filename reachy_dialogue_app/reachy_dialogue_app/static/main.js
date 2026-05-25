const state = {
    settings: {
        service_url: "",
        conversation_id: "reachy-mini-voice",
    },
    appMode: { web_only: false },
    interactionSessionId: "",
    workflow: "chat",
    activeRunId: "",
    activePlaybackKey: "",
    activeLiveSessionId: "",
    activeLiveMode: "",
    runStatusText: "idle",
    localStream: null,
    audioContext: null,
    sourceNode: null,
    processorNode: null,
    pendingBytes: new Uint8Array(0),
    sendQueue: [],
    sendLoopRunning: false,
    transcriptTimer: null,
    messages: [],
    eventLog: [],
};

const TARGET_SAMPLE_RATE = 16000;
const LIVE_CHUNK_BYTES = 5120;

const els = {
    serviceUrl: document.getElementById("service-url"),
    conversationId: document.getElementById("conversation-id"),
    workflow: document.getElementById("workflow"),
    ttsEnabled: document.getElementById("tts-enabled"),
    healthBtn: document.getElementById("health-btn"),
    newSessionBtn: document.getElementById("new-session-btn"),
    sessionTitle: document.getElementById("session-title"),
    workflowPill: document.getElementById("workflow-pill"),
    timeline: document.getElementById("timeline"),
    messageInput: document.getElementById("message-input"),
    sendTextBtn: document.getElementById("send-text-btn"),
    localLiveBtn: document.getElementById("local-live-btn"),
    robotLiveBtn: document.getElementById("robot-live-btn"),
    finishLiveBtn: document.getElementById("finish-live-btn"),
    abortLiveBtn: document.getElementById("abort-live-btn"),
    liveTranscript: document.getElementById("live-transcript"),
    liveState: document.getElementById("live-state"),
    runStatus: document.getElementById("run-status"),
    sessionId: document.getElementById("session-id"),
    runId: document.getElementById("run-id"),
    playbackKey: document.getElementById("playback-key"),
    liveSessionId: document.getElementById("live-session-id"),
    eventLog: document.getElementById("event-log"),
    clearEventsBtn: document.getElementById("clear-events-btn"),
    connectionStatus: document.getElementById("connection-status"),
    statusLine: document.getElementById("status-line"),
};

function setStatus(text) {
    els.statusLine.textContent = text;
}

function normalizeWorkflow() {
    const workflow = els.workflow.value || "chat";
    state.workflow = workflow === "onboarding" ? "onboarding" : "chat";
    els.workflow.value = state.workflow;
    els.workflowPill.textContent = state.workflow;
    return state.workflow;
}

async function loadSettings() {
    const [settingsResponse, modeResponse] = await Promise.all([
        fetch("/api/settings"),
        fetch("/api/app-mode"),
    ]);
    const settings = await settingsResponse.json();
    const mode = await modeResponse.json();
    state.settings = settings;
    state.appMode = mode;
    els.serviceUrl.value = settings.service_url || "";
    els.conversationId.value = settings.conversation_id || "reachy-mini-voice";
    if (mode.web_only) {
        els.robotLiveBtn.disabled = true;
    }
    renderStatus();
}

async function saveSettings() {
    const body = {
        service_url: els.serviceUrl.value,
        conversation_id: els.conversationId.value || "reachy-mini-voice",
    };
    const response = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) {
        throw new Error(payload.detail || "保存设置失败");
    }
    state.settings = payload;
    return payload;
}

async function checkHealth() {
    await saveSettings();
    const response = await fetch("/api/health");
    const payload = await response.json();
    appendEvent("health", payload);
    if (payload.ok) {
        els.connectionStatus.textContent = `已连接 ${payload.service_url}`;
        setStatus("服务在线");
    } else {
        els.connectionStatus.textContent = "服务不可用";
        setStatus(payload.error || "服务不可用");
    }
}

async function createInteractionSession(inputMode = "text") {
    await saveSettings();
    const workflow = normalizeWorkflow();
    const response = await fetch("/api/interaction/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            workflow,
            conversation_id: els.conversationId.value || "reachy-mini-voice",
            input_mode: inputMode,
            tts_enabled: els.ttsEnabled.checked,
        }),
    });
    const payload = await response.json();
    if (!response.ok) {
        throw new Error(payload.detail || "创建 session 失败");
    }
    state.interactionSessionId = payload.interaction_session_id;
    state.workflow = payload.workflow || workflow;
    state.runStatusText = "session";
    appendEvent("session", payload);
    renderStatus();
    setStatus("session 已创建");
    return payload;
}

async function ensureSession(inputMode = "text") {
    if (!state.interactionSessionId) {
        await createInteractionSession(inputMode);
    }
    return state.interactionSessionId;
}

async function sendText() {
    const message = els.messageInput.value.trim();
    if (!message) {
        setStatus("请输入内容");
        return;
    }
    await ensureSession("text");
    const userMessage = addMessage("user", message, "done");
    const assistantMessage = addMessage("assistant", "", "streaming");
    state.runStatusText = "streaming";
    renderTimeline();
    renderStatus();
    setStatus("正在发送文本");
    const response = await fetch("/api/interaction/text-stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            interaction_session_id: state.interactionSessionId,
            workflow: normalizeWorkflow(),
            message,
            tts_enabled: els.ttsEnabled.checked,
        }),
    });
    await consumeSseResponse(response, {
        userMessage,
        assistantMessage,
        source: "text",
    });
    els.messageInput.value = "";
}

async function startLocalLive() {
    await ensureSession("local");
    const startPayload = await interactionLiveStart("local");
    state.activeLiveMode = "local";
    state.activeLiveSessionId = startPayload.live_session_id || startPayload.session_id;
    renderStatus();
    state.localStream = await navigator.mediaDevices.getUserMedia({
        audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        },
    });
    state.audioContext = new AudioContext();
    state.sourceNode = state.audioContext.createMediaStreamSource(state.localStream);
    state.processorNode = state.audioContext.createScriptProcessor(4096, 1, 1);
    state.processorNode.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0);
        enqueuePcm(downsampleToPcm16(input, state.audioContext.sampleRate, TARGET_SAMPLE_RATE));
    };
    state.sourceNode.connect(state.processorNode);
    state.processorNode.connect(state.audioContext.destination);
    startSendLoop();
    startTranscriptPolling();
    setLiveUi(true, "local");
    setStatus("本机麦克风监听中");
}

async function startRobotLive() {
    await ensureSession("robot");
    const response = await fetch("/api/robot-mic/start-interaction", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            interaction_session_id: state.interactionSessionId,
            workflow: normalizeWorkflow(),
        }),
    });
    const payload = await response.json();
    if (!response.ok) {
        throw new Error(payload.detail || "机器人麦克风启动失败");
    }
    state.activeLiveMode = "robot";
    state.activeLiveSessionId = "";
    setLiveUi(true, "robot");
    setStatus("机器人麦克风监听中");
    appendEvent("robot_live_start", payload);
}

async function interactionLiveStart(inputMode) {
    const response = await fetch("/api/interaction/live/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            interaction_session_id: state.interactionSessionId,
            workflow: normalizeWorkflow(),
            sample_rate: TARGET_SAMPLE_RATE,
            channels: 1,
            audio_format: "pcm",
            input_mode: inputMode,
        }),
    });
    const payload = await response.json();
    if (!response.ok) {
        throw new Error(payload.detail || "live start 失败");
    }
    appendEvent("live_start", payload);
    return payload;
}

async function finishLive() {
    if (!state.activeLiveMode) return;
    setStatus("正在结束语音");
    stopTranscriptPolling();
    if (state.activeLiveMode === "local") {
        await stopLocalCapture();
        const response = await fetch("/api/interaction/live/finish-stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                interaction_session_id: state.interactionSessionId,
                workflow: normalizeWorkflow(),
                live_session_id: state.activeLiveSessionId,
                tts_enabled: els.ttsEnabled.checked,
            }),
        });
        const assistantMessage = addMessage("assistant", "", "streaming");
        state.runStatusText = "streaming";
        renderTimeline();
        renderStatus();
        await consumeSseResponse(response, { assistantMessage, source: "local-live" });
    } else {
        const response = await fetch("/api/robot-mic/finish-interaction-stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tts_enabled: els.ttsEnabled.checked }),
        });
        const assistantMessage = addMessage("assistant", "", "streaming");
        state.runStatusText = "streaming";
        renderTimeline();
        renderStatus();
        await consumeSseResponse(response, { assistantMessage, source: "robot-live" });
    }
    state.activeLiveMode = "";
    state.activeLiveSessionId = "";
    setLiveUi(false, "idle");
    renderStatus();
}

async function stopLocalCapture() {
    if (state.processorNode) state.processorNode.disconnect();
    if (state.sourceNode) state.sourceNode.disconnect();
    if (state.localStream) {
        for (const track of state.localStream.getTracks()) track.stop();
    }
    if (state.audioContext) await state.audioContext.close();
    state.processorNode = null;
    state.sourceNode = null;
    state.localStream = null;
    state.audioContext = null;
    state.sendLoopRunning = false;
}

async function abortLive() {
    if (state.activeLiveMode === "local" && state.activeLiveSessionId) {
        await fetch("/api/interaction/live/abort", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                interaction_session_id: state.interactionSessionId,
                workflow: normalizeWorkflow(),
                live_session_id: state.activeLiveSessionId,
            }),
        });
    }
    await stopLocalCapture();
    stopTranscriptPolling();
    state.activeLiveMode = "";
    state.activeLiveSessionId = "";
    setLiveUi(false, "idle");
    renderStatus();
}

function enqueuePcm(chunk) {
    if (!chunk.length || !state.activeLiveSessionId) return;
    const merged = new Uint8Array(state.pendingBytes.length + chunk.length);
    merged.set(state.pendingBytes, 0);
    merged.set(chunk, state.pendingBytes.length);
    let offset = 0;
    while (merged.length - offset >= LIVE_CHUNK_BYTES) {
        state.sendQueue.push(merged.slice(offset, offset + LIVE_CHUNK_BYTES));
        offset += LIVE_CHUNK_BYTES;
    }
    state.pendingBytes = merged.slice(offset);
}

function startSendLoop() {
    if (state.sendLoopRunning) return;
    state.sendLoopRunning = true;
    sendLoop().catch((error) => setStatus(error.message || String(error)));
}

async function sendLoop() {
    while (state.sendLoopRunning || state.sendQueue.length > 0) {
        const chunk = state.sendQueue.shift();
        if (!chunk) {
            await sleep(25);
            continue;
        }
        await fetch("/api/interaction/live/chunk", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                interaction_session_id: state.interactionSessionId,
                workflow: normalizeWorkflow(),
                live_session_id: state.activeLiveSessionId,
                audio_base64: bytesToBase64(chunk),
                is_final: false,
            }),
        });
    }
    if (state.pendingBytes.length && state.activeLiveSessionId) {
        const chunk = state.pendingBytes;
        state.pendingBytes = new Uint8Array(0);
        await fetch("/api/interaction/live/chunk", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                interaction_session_id: state.interactionSessionId,
                workflow: normalizeWorkflow(),
                live_session_id: state.activeLiveSessionId,
                audio_base64: bytesToBase64(chunk),
                is_final: false,
            }),
        });
    }
}

function startTranscriptPolling() {
    stopTranscriptPolling();
    state.transcriptTimer = setInterval(updateLiveTranscript, 300);
}

function stopTranscriptPolling() {
    if (state.transcriptTimer) clearInterval(state.transcriptTimer);
    state.transcriptTimer = null;
}

async function updateLiveTranscript() {
    if (!state.activeLiveSessionId) return;
    const params = new URLSearchParams({
        interaction_session_id: state.interactionSessionId,
        workflow: normalizeWorkflow(),
        live_session_id: state.activeLiveSessionId,
    });
    const response = await fetch(`/api/interaction/live/transcript?${params}`);
    const payload = await response.json();
    if (response.ok) {
        els.liveTranscript.textContent = payload.transcript || "正在听";
    }
}

async function consumeSseResponse(response, context = {}) {
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "stream request failed");
    }
    if (!response.body || !response.body.getReader) {
        return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() || "";
        for (const rawFrame of frames) {
            const parsed = parseSseFrame(rawFrame);
            if (parsed) handleStreamEvent(parsed.event, parsed.data, context);
        }
    }
    if (buffer.trim()) {
        const parsed = parseSseFrame(buffer);
        if (parsed) handleStreamEvent(parsed.event, parsed.data, context);
    }
}

function parseSseFrame(rawFrame) {
    let event = "message";
    const dataLines = [];
    for (const line of rawFrame.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim() || "message";
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (!dataLines.length) return null;
    try {
        return { event, data: JSON.parse(dataLines.join("\n")) };
    } catch {
        return { event, data: { text: dataLines.join("\n") } };
    }
}

function handleStreamEvent(event, data, context = {}) {
    appendEvent(event, data);
    if (data.interaction_session_id) state.interactionSessionId = data.interaction_session_id;
    if (data.workflow) state.workflow = data.workflow;
    if (data.run_id) state.activeRunId = data.run_id;
    if (data.playback_key) state.activePlaybackKey = data.playback_key;
    if (!state.activePlaybackKey) {
        state.activePlaybackKey = playbackKeyFromPayload(data) || "";
    }
    if (data.live_session_id) state.activeLiveSessionId = data.live_session_id;
    if (event === "meta") {
        state.runStatusText = "streaming";
    }
    if (event === "transcript") {
        els.liveTranscript.textContent = data.transcript || "";
        if (context.source !== "text" && data.transcript) {
            addMessage("user", data.transcript, "done");
        }
    }
    if (event === "delta") {
        const message = context.assistantMessage || addMessage("assistant", "", "streaming");
        message.content += data.delta || "";
    }
    if (event === "state_delta") {
        addMessage("system", `stage ${data.stage ?? ""} ${data.stage_name || ""}`.trim(), "done");
    }
    if (event === "audio") {
        setStatus("收到音频事件");
    }
    if (event === "done") {
        const message = context.assistantMessage || addMessage("assistant", "", "streaming");
        message.content = data.reply || message.content || data.text || "";
        message.status = "done";
        state.runStatusText = data.status || "completed";
        setStatus("回复完成");
    }
    if (event === "error") {
        addMessage("error", data.message || "stream error", "done");
        state.runStatusText = "error";
        setStatus(data.message || "stream error");
    }
    if (event === "playback_done") {
        state.runStatusText = "playback done";
        setStatus("播放完成");
    }
    renderTimeline();
    renderStatus();
}

function addMessage(role, content, status = "done") {
    const message = {
        id: `msg_${Date.now()}_${state.messages.length}`,
        role,
        content,
        status,
    };
    state.messages.push(message);
    return message;
}

function renderTimeline() {
    els.timeline.replaceChildren();
    if (!state.messages.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "等待 interaction";
        els.timeline.append(empty);
        return;
    }
    for (const message of state.messages) {
        const row = document.createElement("article");
        row.className = `message-row ${message.role}`;
        const label = document.createElement("span");
        label.textContent = message.role;
        const body = document.createElement("p");
        body.textContent = message.content || (message.status === "streaming" ? "..." : "");
        row.append(label, body);
        els.timeline.append(row);
    }
    els.timeline.scrollTop = els.timeline.scrollHeight;
}

function renderStatus() {
    els.sessionId.textContent = state.interactionSessionId || "--";
    els.sessionTitle.textContent = state.interactionSessionId || "未创建 session";
    els.runId.textContent = state.activeRunId || "--";
    els.playbackKey.textContent = state.activePlaybackKey || "--";
    els.liveSessionId.textContent = state.activeLiveSessionId || "--";
    els.workflowPill.textContent = state.workflow || "chat";
    els.runStatus.textContent = state.runStatusText || (state.activeRunId ? "active" : "idle");
    els.liveState.textContent = state.activeLiveMode || "idle";
    els.finishLiveBtn.disabled = !state.activeLiveMode;
    els.abortLiveBtn.disabled = state.activeLiveMode !== "local";
}

function setLiveUi(active, mode) {
    els.localLiveBtn.disabled = active;
    els.robotLiveBtn.disabled = active || Boolean(state.appMode.web_only);
    els.finishLiveBtn.disabled = !active;
    els.abortLiveBtn.disabled = !active || mode !== "local";
    els.liveState.textContent = mode;
}

function appendEvent(event, data) {
    state.eventLog.push({ event, data });
    state.eventLog = state.eventLog.slice(-80);
    els.eventLog.textContent = state.eventLog
        .map((item) => `${item.event} ${JSON.stringify(item.data)}`)
        .join("\n") || "等待事件";
}

function clearEvents() {
    state.eventLog = [];
    els.eventLog.textContent = "等待事件";
}

function playbackKeyFromPayload(payload) {
    const explicit = payloadString(payload, "playback_key");
    if (explicit) return explicit;
    const requestId = payloadString(payload, "request_id")
        || payloadString(payload, "parent_request_id");
    const turnId = payloadString(payload, "followup_turn_id")
        || payloadString(payload, "assistant_turn_id")
        || payloadString(payload, "reply_turn_id")
        || payloadString(payload, "turn_id");
    if (requestId && turnId) return `request:${requestId}:turn:${turnId}`;
    if (requestId) return `request:${requestId}`;
    const runId = payloadString(payload, "run_id");
    if (runId) return `run:${runId}`;
    const conversationId = payloadString(payload, "conversation_id");
    if (conversationId && turnId) return `conversation:${conversationId}:turn:${turnId}`;
    return null;
}

function payloadString(payload, key) {
    const value = payload?.[key];
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

function downsampleToPcm16(input, sourceRate, targetRate) {
    if (sourceRate === targetRate) return floatToPcm16(input);
    const ratio = sourceRate / targetRate;
    const length = Math.floor(input.length / ratio);
    const output = new Float32Array(length);
    for (let i = 0; i < length; i += 1) {
        output[i] = input[Math.floor(i * ratio)] || 0;
    }
    return floatToPcm16(output);
}

function floatToPcm16(input) {
    const output = new Uint8Array(input.length * 2);
    const view = new DataView(output.buffer);
    for (let i = 0; i < input.length; i += 1) {
        const sample = Math.max(-1, Math.min(1, input[i] || 0));
        view.setInt16(i * 2, sample < 0 ? sample * 32768 : sample * 32767, true);
    }
    return output;
}

function bytesToBase64(bytes) {
    let binary = "";
    for (let i = 0; i < bytes.length; i += 1) {
        binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function wireEvents() {
    els.healthBtn.addEventListener("click", () => checkHealth().catch((error) => setStatus(error.message)));
    els.newSessionBtn.addEventListener("click", () => createInteractionSession("text").catch((error) => setStatus(error.message)));
    els.sendTextBtn.addEventListener("click", () => sendText().catch((error) => setStatus(error.message)));
    els.localLiveBtn.addEventListener("click", () => startLocalLive().catch((error) => setStatus(error.message)));
    els.robotLiveBtn.addEventListener("click", () => startRobotLive().catch((error) => setStatus(error.message)));
    els.finishLiveBtn.addEventListener("click", () => finishLive().catch((error) => setStatus(error.message)));
    els.abortLiveBtn.addEventListener("click", () => abortLive().catch((error) => setStatus(error.message)));
    els.clearEventsBtn.addEventListener("click", clearEvents);
    els.workflow.addEventListener("change", normalizeWorkflow);
}

async function initialize() {
    wireEvents();
    normalizeWorkflow();
    renderTimeline();
    await loadSettings();
    await checkHealth().catch(() => {});
}

window.__reachyDialogue = {
    state,
    els,
    initialize,
    createInteractionSession,
    sendText,
    startRobotLive,
    finishLive,
    abortLive,
    handleStreamEvent,
    consumeSseResponse,
    playbackKeyFromPayload,
    parseSseFrame,
};

initialize().catch((error) => setStatus(error.message || String(error)));
