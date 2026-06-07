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
    sessionSnapshot: null,
    runHistory: [],
    runDetail: null,
    onboarding: {
        onboarding_session_id: "",
        stage: null,
        stage_key: "",
        stage_name: "",
        status: "idle",
        onboarding_complete: false,
        collected: {},
        missing_required_slots: [],
    },
    localStream: null,
    audioContext: null,
    sourceNode: null,
    processorNode: null,
    pendingBytes: new Uint8Array(0),
    sendQueue: [],
    sendLoopRunning: false,
    transcriptTimer: null,
    autoVoiceSessionId: "",
    autoVoiceMode: "",
    autoVoiceGateState: "",
    autoVoiceUtteranceId: "",
    autoVoiceUserMessage: null,
    autoVoiceAssistantMessage: null,
    autoVoiceEventSource: null,
    autoStream: null,
    autoAudioContext: null,
    autoSourceNode: null,
    autoProcessorNode: null,
    autoMonitorNode: null,
    autoPendingBytes: new Uint8Array(0),
    autoSendQueue: [],
    autoSendLoopRunning: false,
    autoInputOpen: false,
    autoChunkCount: 0,
    autoAcceptedChunkCount: 0,
    autoDroppedChunkCount: 0,
    autoLatestBackendVad: 0,
    playbackSink: {
        async ensureReady() {},
        unlockFromGesture() {},
        async handleAudio() {},
    },
    followupPending: [],
    followupEventSource: null,
    followupStreaming: false,
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
    autoLocalBtn: document.getElementById("auto-local-btn"),
    autoRobotBtn: document.getElementById("auto-robot-btn"),
    autoStopBtn: document.getElementById("auto-stop-btn"),
    autoVoiceState: document.getElementById("auto-voice-state"),
    autoVoiceStatus: document.getElementById("auto-voice-status"),
    liveState: document.getElementById("live-state"),
    runStatus: document.getElementById("run-status"),
    sessionId: document.getElementById("session-id"),
    runId: document.getElementById("run-id"),
    playbackKey: document.getElementById("playback-key"),
    liveSessionId: document.getElementById("live-session-id"),
    refreshSessionBtn: document.getElementById("refresh-session-btn"),
    listRunsBtn: document.getElementById("list-runs-btn"),
    getRunBtn: document.getElementById("get-run-btn"),
    runList: document.getElementById("run-list"),
    runDetail: document.getElementById("run-detail"),
    onboardingStatus: document.getElementById("onboarding-status"),
    onboardingSessionId: document.getElementById("onboarding-session-id"),
    onboardingStage: document.getElementById("onboarding-stage"),
    onboardingCollected: document.getElementById("onboarding-collected"),
    onboardingMissing: document.getElementById("onboarding-missing"),
    followupState: document.getElementById("followup-state"),
    refreshFollowupsBtn: document.getElementById("refresh-followups-btn"),
    runFollowupsBtn: document.getElementById("run-followups-btn"),
    followupStreamBtn: document.getElementById("followup-stream-btn"),
    followupPending: document.getElementById("followup-pending"),
    memoryCurateBtn: document.getElementById("memory-curate-btn"),
    profileRefreshBtn: document.getElementById("profile-refresh-btn"),
    memoryResult: document.getElementById("memory-result"),
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
    state.playbackSink = mode.web_only ? new BrowserPlaybackSink() : new NullPlaybackSink();
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
    updateOnboardingState(payload);
    appendEvent("session", payload);
    renderStatus();
    renderOnboarding();
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
    await ensurePlaybackContext();
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
    assertBrowserMicAvailable();
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
    await ensurePlaybackContext();
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

async function startAutoVoice(mode) {
    if (state.autoVoiceSessionId) return;
    const normalizedMode = mode === "robot" ? "robot" : "local";
    if (normalizedMode === "local") {
        assertBrowserMicAvailable();
    }
    await ensurePlaybackContext();
    await saveSettings();
    const response = await fetch("/api/auto-voice/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            input_mode: normalizedMode,
            workflow: normalizeWorkflow(),
            conversation_id: els.conversationId.value || "reachy-mini-voice",
            tts_enabled: els.ttsEnabled.checked,
        }),
    });
    const payload = await response.json();
    if (!response.ok) {
        throw new Error(payload.detail || "自动语音启动失败");
    }
    state.autoVoiceSessionId = payload.session_id;
    state.autoVoiceMode = normalizedMode;
    state.autoVoiceGateState = payload.gate_state || "";
    resetAutoVoiceAudioDiagnostics();
    resetAutoVoiceTurn();
    appendEvent("auto_voice_start", payload);
    setAutoVoiceUi(true, payload.state || "listening");
    updateAutoVoiceInputOpen(payload.state || "listening");
    connectAutoVoiceEvents(payload.session_id);
    if (normalizedMode === "local") {
        await startAutoLocalCapture();
    }
}

function connectAutoVoiceEvents(sessionId) {
    closeAutoVoiceEvents();
    if (typeof EventSource !== "function") return;
    const params = new URLSearchParams({ session_id: sessionId });
    const source = new EventSource(`/api/auto-voice/events?${params}`);
    state.autoVoiceEventSource = source;
    source.addEventListener("message", (event) => {
        handleAutoVoiceEvent("message", parseEventSourcePayload(event.data));
    });
    for (const eventName of [
        "snapshot",
        "state",
        "gate_state",
        "level",
        "utterance",
        "speech_start",
        "speech_end",
        "speech_cancelled",
        "input_dropped",
        "input_drained",
        "transcript",
        "meta",
        "delta",
        "audio",
        "state_delta",
        "done",
        "playback_done",
        "wake_detected",
        "wake_ignored",
        "sleep_detected",
        "wake_timeout",
        "warning",
        "error",
    ]) {
        source.addEventListener(eventName, (event) => {
            handleAutoVoiceEvent(eventName, parseEventSourcePayload(event.data));
        });
    }
    source.onerror = () => {
        els.autoVoiceStatus.textContent = "自动语音事件流断开";
    };
}

function closeAutoVoiceEvents() {
    if (state.autoVoiceEventSource) {
        state.autoVoiceEventSource.close();
        state.autoVoiceEventSource = null;
    }
}

function handleAutoVoiceEvent(event, data) {
    appendEvent(`auto:${event}`, data);
    if (data.state) {
        setAutoVoiceUi(Boolean(state.autoVoiceSessionId), data.state);
        updateAutoVoiceInputOpen(data.state);
    }
    if (data.gate_state) {
        state.autoVoiceGateState = data.gate_state;
        els.autoVoiceStatus.textContent = autoVoiceGateText(data.gate_state);
    }
    if (event === "utterance") {
        beginAutoVoiceTurn(data);
    }
    if (event === "speech_start") {
        state.autoInputOpen = true;
        beginAutoVoiceTurn(data);
        els.liveTranscript.textContent = "检测到语音";
    }
    if (event === "speech_end") {
        state.autoInputOpen = false;
        clearAutoPendingAudio();
        els.liveTranscript.textContent = "语音结束，正在识别";
    }
    if (event === "speech_cancelled") {
        state.autoInputOpen = true;
        resetAutoVoiceTurn();
        els.liveTranscript.textContent = "语音太短";
    }
    if (event === "level") {
        handleAutoVoiceLevel(data);
    }
    if (event === "input_dropped") {
        state.autoDroppedChunkCount = Math.max(
            Number(data.dropped_input_chunks || 0),
            state.autoDroppedChunkCount + 1,
        );
        updateAutoVoiceChunkStatus();
    }
    if (event === "input_drained") {
        clearAutoPendingAudio();
    }
    if (event === "wake_detected") {
        els.autoVoiceStatus.textContent = data.reply || "已唤醒，请继续说";
        setStatus(data.reply || "已唤醒，请继续说");
    }
    if (event === "wake_ignored") {
        els.autoVoiceStatus.textContent = "未检测到唤醒词，未发送";
        setStatus("未检测到唤醒词，未发送");
    }
    if (event === "sleep_detected") {
        els.autoVoiceStatus.textContent = data.reply || "已进入等待唤醒";
        setStatus(data.reply || "已进入等待唤醒");
    }
    if (event === "wake_timeout") {
        els.autoVoiceStatus.textContent = "等待唤醒词";
        setStatus("自动语音超时，等待唤醒词");
    }
    if (event === "warning") {
        els.autoVoiceStatus.textContent = data.message || "自动语音 warning";
    }
    if (event === "error") {
        els.autoVoiceStatus.textContent = data.message || "自动语音错误";
        setAutoVoiceUi(false, "error");
    }
    if (event === "transcript") {
        handleAutoVoiceTranscript(data);
        return;
    }
    if (event === "done" && data.transcript) {
        commitAutoVoiceTranscript(data.transcript);
    }
    if (event === "delta" || event === "done") {
        handleStreamEvent(event, data, {
            source: "auto-voice",
            assistantMessage: ensureAutoVoiceAssistantMessage(),
        });
        return;
    }
    if (["meta", "audio", "state_delta", "playback_done"].includes(event)) {
        handleStreamEvent(event, data, { source: "auto-voice" });
    }
}

function beginAutoVoiceTurn(data = {}) {
    const utteranceId = data.utterance_id || "";
    if (utteranceId && utteranceId === state.autoVoiceUtteranceId) return;
    state.autoVoiceUtteranceId = utteranceId || state.autoVoiceUtteranceId || `auto_${Date.now()}`;
    state.autoVoiceUserMessage = null;
    state.autoVoiceAssistantMessage = null;
}

function resetAutoVoiceTurn() {
    state.autoVoiceUtteranceId = "";
    state.autoVoiceUserMessage = null;
    state.autoVoiceAssistantMessage = null;
}

function autoVoiceGateText(gateState) {
    if (gateState === "waiting_wake") return "等待唤醒词";
    if (gateState === "awake") return "已唤醒";
    return `gate ${gateState}`;
}

function handleAutoVoiceTranscript(data) {
    const transcript = data.transcript || data.text || "";
    els.liveTranscript.textContent = transcript || (data.is_final ? "(未识别到文本)" : "正在听");
    if (!transcript && data.is_final) {
        renderStatus();
        return;
    }
    if (!shouldShowAutoVoiceTranscript(data)) {
        renderStatus();
        return;
    }
    const message = ensureAutoVoiceUserMessage();
    message.content = transcript;
    message.status = data.is_final ? "done" : "streaming";
    renderTimeline();
    renderStatus();
}

function shouldShowAutoVoiceTranscript(data) {
    if (state.autoVoiceUserMessage) return true;
    const gateState = data.gate_state || state.autoVoiceGateState;
    return gateState === "awake";
}

function ensureAutoVoiceUserMessage() {
    if (!state.autoVoiceUserMessage) {
        state.autoVoiceUserMessage = addMessage("user", "", "streaming");
    }
    return state.autoVoiceUserMessage;
}

function commitAutoVoiceTranscript(transcript) {
    const text = String(transcript || "").trim();
    if (!text || state.autoVoiceGateState === "waiting_wake") return;
    const message = ensureAutoVoiceUserMessage();
    message.content = text;
    message.status = "done";
}

function ensureAutoVoiceAssistantMessage() {
    if (!state.autoVoiceAssistantMessage) {
        state.autoVoiceAssistantMessage = addMessage("assistant", "", "streaming");
    }
    return state.autoVoiceAssistantMessage;
}

function parseEventSourcePayload(raw) {
    try {
        return JSON.parse(raw);
    } catch {
        return { text: raw };
    }
}

function assertBrowserMicAvailable() {
    const canUseMic = Boolean(
        navigator.mediaDevices
        && typeof navigator.mediaDevices.getUserMedia === "function"
    );
    if (!canUseMic) {
        throw new Error("当前浏览器不支持麦克风输入，或页面不是 localhost/HTTPS 安全上下文。请用 http://127.0.0.1:8043/ 打开。");
    }
    if (window.isSecureContext === false) {
        throw new Error("浏览器麦克风需要 HTTPS 或 localhost。请用 http://127.0.0.1:8043/，不要用 0.0.0.0 或普通局域网 IP。");
    }
}

async function startAutoLocalCapture() {
    state.autoStream = await navigator.mediaDevices.getUserMedia({
        audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        },
    });
    state.autoAudioContext = new AudioContext();
    if (state.autoAudioContext.state === "suspended") {
        await state.autoAudioContext.resume();
    }
    state.autoSourceNode = state.autoAudioContext.createMediaStreamSource(state.autoStream);
    state.autoProcessorNode = state.autoAudioContext.createScriptProcessor(4096, 1, 1);
    state.autoMonitorNode = state.autoAudioContext.createGain();
    state.autoMonitorNode.gain.value = 0;
    state.autoProcessorNode.onaudioprocess = (event) => {
        if (!state.autoVoiceSessionId || state.autoVoiceMode !== "local") return;
        const input = event.inputBuffer.getChannelData(0);
        if (!state.autoInputOpen) {
            state.autoPendingBytes = new Uint8Array(0);
            return;
        }
        enqueueAutoPcm(downsampleToPcm16(input, state.autoAudioContext.sampleRate, TARGET_SAMPLE_RATE));
    };
    state.autoSourceNode.connect(state.autoProcessorNode);
    state.autoProcessorNode.connect(state.autoMonitorNode);
    state.autoMonitorNode.connect(state.autoAudioContext.destination);
    startAutoSendLoop();
}

function enqueueAutoPcm(chunk) {
    if (!chunk.length || !state.autoVoiceSessionId) return;
    const merged = new Uint8Array(state.autoPendingBytes.length + chunk.length);
    merged.set(state.autoPendingBytes, 0);
    merged.set(chunk, state.autoPendingBytes.length);
    let offset = 0;
    while (merged.length - offset >= LIVE_CHUNK_BYTES) {
        state.autoSendQueue.push(merged.slice(offset, offset + LIVE_CHUNK_BYTES));
        offset += LIVE_CHUNK_BYTES;
    }
    state.autoPendingBytes = merged.slice(offset);
}

function startAutoSendLoop() {
    if (state.autoSendLoopRunning) return;
    state.autoSendLoopRunning = true;
    autoSendLoop().catch((error) => setStatus(error.message || String(error)));
}

async function autoSendLoop() {
    while (state.autoSendLoopRunning || state.autoSendQueue.length > 0) {
        const chunk = state.autoSendQueue.shift();
        if (!chunk) {
            await sleep(25);
            continue;
        }
        if (!state.autoVoiceSessionId) continue;
        const response = await fetch("/api/auto-voice/chunk", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: state.autoVoiceSessionId,
                audio_base64: bytesToBase64(chunk),
                sample_rate: TARGET_SAMPLE_RATE,
            }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.error) {
            appendEvent("auto:chunk_error", payload);
            throw new Error(payload.detail || payload.error?.message || "自动语音音频发送失败");
        }
        state.autoChunkCount += 1;
        if (payload.accepted === false) {
            state.autoDroppedChunkCount += 1;
            updateAutoVoiceInputOpen(payload.state);
        } else {
            state.autoAcceptedChunkCount += 1;
        }
        updateAutoVoiceChunkStatus();
    }
}

async function stopAutoVoice() {
    const sessionId = state.autoVoiceSessionId;
    closeAutoVoiceEvents();
    await stopAutoLocalCapture();
    state.autoVoiceSessionId = "";
    state.autoVoiceMode = "";
    state.autoVoiceGateState = "";
    resetAutoVoiceAudioDiagnostics();
    resetAutoVoiceTurn();
    setAutoVoiceUi(false, "idle");
    if (!sessionId) return;
    const response = await fetch("/api/auto-voice/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
    });
    const payload = await response.json().catch(() => ({}));
    appendEvent("auto_voice_stop", payload);
}

async function stopAutoLocalCapture() {
    if (state.autoProcessorNode) state.autoProcessorNode.disconnect();
    if (state.autoMonitorNode) state.autoMonitorNode.disconnect();
    if (state.autoSourceNode) state.autoSourceNode.disconnect();
    if (state.autoStream) {
        for (const track of state.autoStream.getTracks()) track.stop();
    }
    if (state.autoAudioContext) await state.autoAudioContext.close();
    state.autoProcessorNode = null;
    state.autoSourceNode = null;
    state.autoStream = null;
    state.autoAudioContext = null;
    state.autoMonitorNode = null;
    state.autoSendLoopRunning = false;
    state.autoInputOpen = false;
    clearAutoPendingAudio();
}

function updateAutoVoiceInputOpen(sessionState) {
    const inputOpen = ["starting", "listening", "user_speaking"].includes(sessionState);
    state.autoInputOpen = inputOpen;
    if (!inputOpen) clearAutoPendingAudio();
}

function clearAutoPendingAudio() {
    state.autoSendQueue = [];
    state.autoPendingBytes = new Uint8Array(0);
}

function resetAutoVoiceAudioDiagnostics() {
    state.autoInputOpen = false;
    state.autoChunkCount = 0;
    state.autoAcceptedChunkCount = 0;
    state.autoDroppedChunkCount = 0;
    state.autoLatestBackendVad = 0;
    clearAutoPendingAudio();
}

function handleAutoVoiceLevel(data) {
    const probability = Number(data.speech_probability || 0);
    const rms = Number(data.rms || 0);
    const peak = Number(data.peak || 0);
    state.autoLatestBackendVad = probability;
    if (state.autoVoiceSessionId && state.autoInputOpen) {
        els.autoVoiceStatus.textContent = `vad ${probability.toFixed(3)} rms ${rms.toFixed(5)}`;
    }
    if (rms > 0 || peak > 0) {
        els.liveTranscript.textContent = `麦克风 rms ${rms.toFixed(5)} peak ${peak.toFixed(5)}`;
    }
}

function updateAutoVoiceChunkStatus() {
    const text = `chunks ${state.autoAcceptedChunkCount}/${state.autoChunkCount}`;
    if (state.autoDroppedChunkCount > 0) {
        els.autoVoiceStatus.textContent = `${text}, dropped ${state.autoDroppedChunkCount}`;
    }
}

function setAutoVoiceUi(active, label) {
    els.autoLocalBtn.disabled = active;
    els.autoRobotBtn.disabled = active || Boolean(state.appMode.web_only);
    els.autoStopBtn.disabled = !active;
    els.autoVoiceState.textContent = label || (active ? "listening" : "idle");
    els.autoVoiceStatus.textContent = active
        ? `${state.autoVoiceMode || "auto"} ${label || ""}`.trim()
        : "等待启动";
}

async function refreshFollowups() {
    const response = await fetch("/api/followups/pending");
    const payload = await response.json();
    if (!response.ok) {
        throw new Error(payload.detail || "刷新 follow-up 失败");
    }
    state.followupPending = Array.isArray(payload.pending) ? payload.pending : [];
    appendEvent("followups_pending", payload);
    renderFollowups();
    setStatus(`pending follow-up ${state.followupPending.length} 条`);
    return state.followupPending;
}

async function refreshSessionState() {
    if (!state.interactionSessionId) {
        setStatus("还没有 interaction session");
        return null;
    }
    const response = await fetch(`/api/interaction/session/${encodeURIComponent(state.interactionSessionId)}`);
    const payload = await response.json();
    appendEvent("session_state", payload);
    if (!response.ok) {
        throw new Error(payload.detail || "刷新 session 失败");
    }
    state.sessionSnapshot = payload;
    if (payload.workflow) state.workflow = payload.workflow;
    updateOnboardingState(payload);
    els.runDetail.textContent = JSON.stringify(payload, null, 2);
    renderOnboarding();
    renderStatus();
    setStatus("session 状态已刷新");
    return payload;
}

async function listRuns() {
    if (!state.interactionSessionId) {
        setStatus("还没有 interaction session");
        return [];
    }
    const response = await fetch(`/api/interaction/session/${encodeURIComponent(state.interactionSessionId)}/runs?limit=20`);
    const payload = await response.json();
    appendEvent("runs", payload);
    if (!response.ok) {
        throw new Error(payload.detail || "获取 runs 失败");
    }
    state.runHistory = Array.isArray(payload.runs) ? payload.runs : [];
    renderRuns();
    setStatus(`runs ${state.runHistory.length} 条`);
    return state.runHistory;
}

async function getActiveRun() {
    const runId = state.activeRunId || (state.runHistory[0] && state.runHistory[0].run_id);
    if (!runId) {
        setStatus("还没有 run");
        return null;
    }
    const response = await fetch(`/api/interaction/runs/${encodeURIComponent(runId)}`);
    const payload = await response.json();
    appendEvent("run_detail", payload);
    if (!response.ok) {
        throw new Error(payload.detail || "获取 run 失败");
    }
    state.runDetail = payload;
    state.activeRunId = payload.run_id || runId;
    if (payload.playback_key) state.activePlaybackKey = payload.playback_key;
    state.runStatusText = payload.playback_status
        ? `${payload.status || "run"} / ${payload.playback_status}`
        : payload.status || state.runStatusText;
    els.runDetail.textContent = JSON.stringify(payload, null, 2);
    renderStatus();
    setStatus("run 状态已刷新");
    return payload;
}

async function runPendingFollowups() {
    const pending = state.followupPending.length
        ? state.followupPending
        : await refreshFollowups();
    const conversationId = els.conversationId.value || state.settings.conversation_id;
    const candidates = pending.filter((item) => !item.conversation_id || item.conversation_id === conversationId);
    if (!candidates.length) {
        setStatus("当前 conversation 没有可运行的 follow-up");
        return [];
    }
    const results = [];
    for (const item of candidates) {
        const requestId = item.request_id;
        if (!requestId) continue;
        const response = await fetch(`/api/followups/${encodeURIComponent(requestId)}/run`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
        });
        const payload = await response.json();
        appendEvent("followup_run", payload);
        if (!response.ok) {
            addMessage("error", payload.detail || `follow-up ${requestId} 失败`, "done");
            continue;
        }
        results.push(payload);
        if (!state.followupStreaming && payload.decision === "followup" && payload.reply) {
            addMessage("followup", payload.reply, "done");
        }
    }
    await refreshFollowups().catch(() => {});
    renderTimeline();
    setStatus(`运行 follow-up ${results.length} 条`);
    return results;
}

function toggleFollowupStream() {
    if (state.followupEventSource) {
        closeFollowupStream();
        return;
    }
    ensurePlaybackContext().catch((error) => setStatus(error.message || String(error)));
    if (typeof EventSource !== "function") {
        setStatus("当前浏览器不支持 EventSource");
        return;
    }
    const conversationId = els.conversationId.value || state.settings.conversation_id || "reachy-mini-voice";
    const params = new URLSearchParams({
        conversation_id: conversationId,
        tts_enabled: String(Boolean(els.ttsEnabled.checked)),
    });
    const source = new EventSource(`/api/followups/stream?${params}`);
    state.followupEventSource = source;
    state.followupStreaming = true;
    els.followupStreamBtn.textContent = "停止订阅";
    els.followupState.textContent = "streaming";
    for (const eventName of [
        "followup",
        "audio",
        "followup_done",
        "playback_done",
        "behavior",
        "ping",
        "error",
    ]) {
        source.addEventListener(eventName, (event) => {
            handleStreamEvent(eventName, parseEventSourcePayload(event.data), {
                source: "followup",
            });
        });
    }
    source.onerror = () => {
        els.followupState.textContent = "disconnected";
    };
    setStatus("已订阅二次回复");
}

function startDefaultFollowupStream() {
    if (state.followupEventSource) return;
    els.ttsEnabled.checked = true;
    if (typeof EventSource !== "function") return;
    toggleFollowupStream();
}

function closeFollowupStream() {
    if (state.followupEventSource) {
        state.followupEventSource.close();
        state.followupEventSource = null;
    }
    state.followupStreaming = false;
    els.followupStreamBtn.textContent = "订阅二次回复";
    els.followupState.textContent = "idle";
}

async function runMemoryCurate() {
    const response = await fetch("/api/memory/curate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            conversation_id: els.conversationId.value || "reachy-mini-voice",
            history_limit: 50,
        }),
    });
    const payload = await response.json();
    appendEvent("memory_curate", payload);
    els.memoryResult.textContent = JSON.stringify(payload, null, 2);
    if (!response.ok) throw new Error(payload.detail || "整理记忆失败");
    setStatus("记忆整理完成");
    return payload;
}

async function refreshProfile() {
    const response = await fetch("/api/memory/profile/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
    });
    const payload = await response.json();
    appendEvent("profile_refresh", payload);
    els.memoryResult.textContent = JSON.stringify(payload, null, 2);
    if (!response.ok) throw new Error(payload.detail || "刷新画像失败");
    setStatus(payload.should_update ? "用户画像已更新" : "用户画像无需更新");
    return payload;
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
    updateOnboardingState(data);
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
        const stageText = onboardingStageText(data);
        if (stageText) addMessage("system", stageText, "done");
    }
    if (event === "audio") {
        playBrowserPcmChunk(data).catch((error) => setStatus(error.message || String(error)));
        setStatus("收到音频事件");
    }
    if (event === "followup") {
        if (data.reply) {
            addMessage("followup", data.reply, "done");
        }
        els.followupState.textContent = data.followup_type || "followup";
    }
    if (event === "followup_done") {
        els.followupState.textContent = "done";
    }
    if (event === "done") {
        const message = context.assistantMessage || addMessage("assistant", "", "streaming");
        message.content = data.reply || message.content || data.text || "";
        message.status = "done";
        state.runStatusText = data.status || "completed";
        if (data.request_id && data.retrieval_status) {
            els.followupState.textContent = data.retrieval_status;
        }
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
    renderOnboarding();
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

function renderFollowups() {
    els.followupPending.replaceChildren();
    if (!state.followupPending.length) {
        els.followupPending.textContent = "暂无 pending";
        els.followupState.textContent = state.followupStreaming ? "streaming" : "idle";
        return;
    }
    els.followupState.textContent = `${state.followupPending.length} pending`;
    for (const item of state.followupPending) {
        const row = document.createElement("div");
        row.className = "pending-item";
        const title = document.createElement("strong");
        title.textContent = item.request_id || "unknown request";
        const detail = document.createElement("span");
        detail.textContent = `${item.conversation_id || "--"} · ${item.status || "--"}`;
        row.append(title, detail);
        els.followupPending.append(row);
    }
}

function renderRuns() {
    els.runList.replaceChildren();
    if (!state.runHistory.length) {
        els.runList.textContent = "暂无 runs";
        return;
    }
    for (const run of state.runHistory) {
        const row = document.createElement("button");
        row.className = "run-item";
        row.type = "button";
        const title = document.createElement("strong");
        title.textContent = run.run_id || "unknown run";
        const detail = document.createElement("span");
        detail.textContent = [
            run.workflow,
            run.status,
            run.playback_status ? `playback ${run.playback_status}` : "",
        ].filter(Boolean).join(" · ");
        row.append(title, detail);
        row.addEventListener("click", () => {
            state.activeRunId = run.run_id || "";
            getActiveRun().catch((error) => setStatus(error.message));
        });
        els.runList.append(row);
    }
}

function updateOnboardingState(payload) {
    if (!payload || typeof payload !== "object") return;
    const hasOnboardingField = [
        "onboarding_session_id",
        "stage",
        "stage_key",
        "stage_name",
        "status",
        "onboarding_complete",
        "collected",
        "missing_required_slots",
    ].some((key) => Object.prototype.hasOwnProperty.call(payload, key));
    if (!hasOnboardingField) return;

    if (typeof payload.onboarding_session_id === "string") {
        state.onboarding.onboarding_session_id = payload.onboarding_session_id;
    }
    if (payload.stage !== undefined && payload.stage !== null) {
        state.onboarding.stage = payload.stage;
    }
    if (typeof payload.stage_key === "string") {
        state.onboarding.stage_key = payload.stage_key;
    }
    if (typeof payload.stage_name === "string") {
        state.onboarding.stage_name = payload.stage_name;
    }
    if (typeof payload.status === "string") {
        state.onboarding.status = payload.status;
    }
    if (typeof payload.onboarding_complete === "boolean") {
        state.onboarding.onboarding_complete = payload.onboarding_complete;
    }
    if (payload.collected && typeof payload.collected === "object" && !Array.isArray(payload.collected)) {
        state.onboarding.collected = payload.collected;
    }
    if (Array.isArray(payload.missing_required_slots)) {
        state.onboarding.missing_required_slots = payload.missing_required_slots;
    }
}

function onboardingStageText(payload) {
    const stage = payload.stage ?? "";
    const name = payload.stage_name || payload.stage_key || "";
    if (stage === "" && !name) return "";
    return `stage ${stage} ${name}`.trim();
}

function renderOnboarding() {
    const onboarding = state.onboarding;
    const complete = Boolean(onboarding.onboarding_complete);
    els.onboardingStatus.textContent = complete
        ? "complete"
        : onboarding.status || (state.workflow === "onboarding" ? "active" : "idle");
    els.onboardingSessionId.textContent = onboarding.onboarding_session_id || "--";
    els.onboardingStage.textContent = onboarding.stage === null || onboarding.stage === undefined
        ? "--"
        : `${onboarding.stage}${onboarding.stage_name ? ` · ${onboarding.stage_name}` : ""}`;
    els.onboardingCollected.textContent = objectHasKeys(onboarding.collected)
        ? JSON.stringify(onboarding.collected, null, 2)
        : "--";
    els.onboardingMissing.textContent = onboarding.missing_required_slots.length
        ? onboarding.missing_required_slots.join("\n")
        : "--";
}

function objectHasKeys(value) {
    return value && typeof value === "object" && Object.keys(value).length > 0;
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
    renderOnboarding();
    els.finishLiveBtn.disabled = !state.activeLiveMode;
    els.abortLiveBtn.disabled = state.activeLiveMode !== "local";
    if (!state.autoVoiceSessionId) {
        els.autoRobotBtn.disabled = Boolean(state.appMode.web_only);
    }
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

function base64ToBytes(value) {
    if (!value) return new Uint8Array(0);
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
}

async function ensurePlaybackContext() {
    await state.playbackSink.ensureReady();
}

function unlockPlaybackFromGesture() {
    state.playbackSink.unlockFromGesture();
}

async function playBrowserPcmChunk(data) {
    await state.playbackSink.handleAudio(data);
}

class NullPlaybackSink {
    async ensureReady() {}
    unlockFromGesture() {}
    async handleAudio() {}
}

class BrowserPlaybackSink {
    constructor() {
        this.context = null;
        this.nextTime = 0;
    }

    async ensureReady() {
        this.unlockFromGesture();
        if (!this.context) {
            throw new Error("当前浏览器不支持 AudioContext");
        }
        if (this.context.state === "suspended") {
            await this.context.resume();
        }
    }

    unlockFromGesture() {
        const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextCtor) return;
        if (!this.context || this.context.state === "closed") {
            this.context = new AudioContextCtor();
            this.nextTime = this.context.currentTime;
        }
        if (this.context.state === "suspended") {
            this.context.resume().catch(() => {});
        }
    }

    async handleAudio(data) {
        const audioBase64 = data?.audio_base64;
        if (typeof audioBase64 !== "string" || !audioBase64) return;
        await this.ensureReady();
        const bytes = base64ToBytes(audioBase64);
        if (!bytes.length || !this.context) return;
        const rawSampleRate = Number(data.sample_rate || data.audio_sample_rate || 24000);
        const sampleRate = Number.isFinite(rawSampleRate) && rawSampleRate > 0
            ? rawSampleRate
            : 24000;
        const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        const sampleCount = Math.floor(bytes.length / 2);
        const buffer = this.context.createBuffer(1, sampleCount, sampleRate);
        const channel = buffer.getChannelData(0);
        for (let i = 0; i < sampleCount; i += 1) {
            channel[i] = view.getInt16(i * 2, true) / 32768;
        }
        const source = this.context.createBufferSource();
        source.buffer = buffer;
        source.connect(this.context.destination);
        const startAt = Math.max(this.context.currentTime + 0.02, this.nextTime);
        source.start(startAt);
        this.nextTime = startAt + buffer.duration;
        setStatus(`浏览器播放音频 ${Math.round(buffer.duration * 1000)}ms`);
    }
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function wireEvents() {
    document.addEventListener("pointerdown", unlockPlaybackFromGesture, { passive: true });
    els.healthBtn.addEventListener("click", () => checkHealth().catch((error) => setStatus(error.message)));
    els.newSessionBtn.addEventListener("click", () => createInteractionSession("text").catch((error) => setStatus(error.message)));
    els.sendTextBtn.addEventListener("click", () => {
        unlockPlaybackFromGesture();
        sendText().catch((error) => setStatus(error.message));
    });
    els.localLiveBtn.addEventListener("click", () => startLocalLive().catch((error) => setStatus(error.message)));
    els.robotLiveBtn.addEventListener("click", () => startRobotLive().catch((error) => setStatus(error.message)));
    els.finishLiveBtn.addEventListener("click", () => {
        unlockPlaybackFromGesture();
        finishLive().catch((error) => setStatus(error.message));
    });
    els.abortLiveBtn.addEventListener("click", () => abortLive().catch((error) => setStatus(error.message)));
    els.autoLocalBtn.addEventListener("click", () => {
        unlockPlaybackFromGesture();
        startAutoVoice("local").catch((error) => setStatus(error.message));
    });
    els.autoRobotBtn.addEventListener("click", () => {
        unlockPlaybackFromGesture();
        startAutoVoice("robot").catch((error) => setStatus(error.message));
    });
    els.autoStopBtn.addEventListener("click", () => stopAutoVoice().catch((error) => setStatus(error.message)));
    els.refreshSessionBtn.addEventListener("click", () => refreshSessionState().catch((error) => setStatus(error.message)));
    els.listRunsBtn.addEventListener("click", () => listRuns().catch((error) => setStatus(error.message)));
    els.getRunBtn.addEventListener("click", () => getActiveRun().catch((error) => setStatus(error.message)));
    els.refreshFollowupsBtn.addEventListener("click", () => refreshFollowups().catch((error) => setStatus(error.message)));
    els.runFollowupsBtn.addEventListener("click", () => runPendingFollowups().catch((error) => setStatus(error.message)));
    els.followupStreamBtn.addEventListener("click", () => {
        unlockPlaybackFromGesture();
        toggleFollowupStream();
    });
    els.memoryCurateBtn.addEventListener("click", () => runMemoryCurate().catch((error) => setStatus(error.message)));
    els.profileRefreshBtn.addEventListener("click", () => refreshProfile().catch((error) => setStatus(error.message)));
    els.clearEventsBtn.addEventListener("click", clearEvents);
    els.workflow.addEventListener("change", normalizeWorkflow);
}

async function initialize() {
    wireEvents();
    normalizeWorkflow();
    renderTimeline();
    renderRuns();
    renderFollowups();
    renderOnboarding();
    await loadSettings();
    startDefaultFollowupStream();
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
    startAutoVoice,
    stopAutoVoice,
    refreshSessionState,
    listRuns,
    getActiveRun,
    refreshFollowups,
    runPendingFollowups,
    toggleFollowupStream,
    startDefaultFollowupStream,
    closeFollowupStream,
    runMemoryCurate,
    refreshProfile,
    updateOnboardingState,
    renderOnboarding,
    handleStreamEvent,
    consumeSseResponse,
    playbackKeyFromPayload,
    parseSseFrame,
};

initialize().catch((error) => setStatus(error.message || String(error)));
