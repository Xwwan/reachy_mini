let recordingStartedAt = 0;
let timerId = null;
let levelTimerId = null;
let transcriptTimerId = null;
let isRecording = false;
let lastRecordingStats = null;
let playbackTestStartedAt = 0;
let playbackTestTimerId = null;
let playbackTestLevelTimerId = null;
let isPlaybackTestRecording = false;
let isTextSending = false;
let appMode = { web_only: false };
let activeVoiceMode = null;
let isAutoVoiceActive = false;
let autoVoiceSessionId = null;
let autoVoiceSource = null;
let autoVoiceExchange = null;
let autoVoiceInputOpen = false;

let localAudioContext = null;
let localPlaybackContext = null;
let localMediaStream = null;
let localSourceNode = null;
let localProcessorNode = null;
let localSessionId = null;
let localSendLoopPromise = null;
let localSendQueue = [];
let localPendingBytes = new Uint8Array(0);
let localSentBytes = 0;
let localSentChunks = 0;
let localAcceptedBytes = 0;
let localPlaybackNextTime = 0;

const LOCAL_TARGET_SAMPLE_RATE = 16000;
const LOCAL_CHUNK_BYTES = 5120;
const LOCAL_PLAYBACK_START_BUFFER_SECONDS = 0.08;

class AudioPlaybackScheduler {
    constructor({ ensureReady, playBytes, onStatus }) {
        this.ensureReady = ensureReady;
        this.playBytes = playBytes;
        this.onStatus = onStatus || (() => {});
        this.groups = new Map();
        this.queue = [];
        this.arrivalIndex = 0;
        this.isPlaying = false;
    }

    enqueueAudio(data, { fallbackKey = null } = {}) {
        const key = this.resolveKey(data, fallbackKey);
        const group = this.ensureGroup(key);
        group.chunks.push({
            bytes: base64ToBytes(data.audio_base64 || ""),
            sampleRate: Number(data.sample_rate || data.audio_sample_rate || 24000),
            chunkIndex: optionalNumber(data.chunk_index),
            segmentIndex: optionalNumber(data.segment_index),
            arrivalIndex: ++this.arrivalIndex,
        });
        this.drain().catch((error) => {
            this.onStatus(error.message || String(error));
        });
        return key;
    }

    complete(data, { fallbackKey = null } = {}) {
        const key = this.resolveKey(data, fallbackKey);
        const group = this.ensureGroup(key);
        group.completed = true;
        this.drain().catch((error) => {
            this.onStatus(error.message || String(error));
        });
        return key;
    }

    abort(key) {
        if (!key) {
            return;
        }
        this.groups.delete(key);
        this.queue = this.queue.filter((queuedKey) => queuedKey !== key);
    }

    resolveKey(data, fallbackKey) {
        if (fallbackKey && this.groups.has(fallbackKey)) {
            return fallbackKey;
        }
        return playbackKeyFromPayload(data) || fallbackKey || makeId("audio");
    }

    ensureGroup(key) {
        if (!this.groups.has(key)) {
            this.groups.set(key, {
                key,
                chunks: [],
                completed: false,
                emittedCount: 0,
            });
            this.queue.push(key);
        }
        return this.groups.get(key);
    }

    async drain() {
        if (this.isPlaying) {
            return;
        }
        this.isPlaying = true;
        try {
            while (this.queue.length > 0) {
                const key = this.queue[0];
                const group = this.groups.get(key);
                if (!group) {
                    this.queue.shift();
                    continue;
                }

                if (group.emittedCount === 0 && group.chunks.length > 1) {
                    group.chunks.sort((a, b) => (
                        (a.segmentIndex ?? 0) - (b.segmentIndex ?? 0)
                        || (a.chunkIndex ?? a.arrivalIndex) - (b.chunkIndex ?? b.arrivalIndex)
                        || a.arrivalIndex - b.arrivalIndex
                    ));
                }
                while (group.emittedCount < group.chunks.length) {
                    const chunk = group.chunks[group.emittedCount];
                    group.emittedCount += 1;
                    if (chunk.bytes.length > 0) {
                        await this.ensureReady();
                        this.onStatus("正在播放语音回复");
                        this.playBytes(chunk.bytes, chunk.sampleRate || 24000);
                    }
                }

                if (!group.completed) {
                    return;
                }
                this.queue.shift();
                this.groups.delete(key);
            }
        } finally {
            this.isPlaying = false;
        }
    }
}

const localAudioPlaybackScheduler = new AudioPlaybackScheduler({
    ensureReady: ensureLocalPlaybackContext,
    playBytes: playLocalPcmBytes,
    onStatus: setStatus,
});

const chatState = {
    messages: [],
    requests: new Map(),
    followupSource: null,
    followupConnected: false,
    eventLog: [],
    seenFollowups: new Set(),
    followupPlaybackKey: null,
    followupPlaybackFallbackKey: null,
};

const els = {
    serviceUrl: document.getElementById("service-url"),
    conversationId: document.getElementById("conversation-id"),
    ttsSampleRate: document.getElementById("tts-sample-rate"),
    speakerVolume: document.getElementById("speaker-volume"),
    speakerVolumeValue: document.getElementById("speaker-volume-value"),
    microphoneVolume: document.getElementById("microphone-volume"),
    microphoneVolumeValue: document.getElementById("microphone-volume-value"),
    refreshVolumeBtn: document.getElementById("refresh-volume-btn"),
    manualText: document.getElementById("manual-text"),
    sendTextBtn: document.getElementById("send-text-btn"),
    textTtsEnabled: document.getElementById("text-tts-enabled"),
    healthBtn: document.getElementById("health-btn"),
    voiceInputMode: document.getElementById("voice-input-mode"),
    localAbortBtn: document.getElementById("local-abort-btn"),
    autoVoiceBtn: document.getElementById("auto-voice-btn"),
    autoVoiceStatus: document.getElementById("auto-voice-status"),
    recordBtn: document.getElementById("record-btn"),
    recordingTime: document.getElementById("recording-time"),
    playbackTestBtn: document.getElementById("playback-test-btn"),
    playbackTestTime: document.getElementById("playback-test-time"),
    playbackTestStatus: document.getElementById("playback-test-status"),
    playbackTestLevelFill: document.getElementById("playback-test-level-fill"),
    playbackTestLevelText: document.getElementById("playback-test-level-text"),
    playbackTestResult: document.getElementById("playback-test-result"),
    micLevelFill: document.getElementById("mic-level-fill"),
    micLevelText: document.getElementById("mic-level-text"),
    liveTranscript: document.getElementById("live-transcript"),
    statusLine: document.getElementById("status-line"),
    connectionStatus: document.getElementById("connection-status"),
    transcript: document.getElementById("transcript"),
    reply: document.getElementById("reply"),
    debugInfo: document.getElementById("debug-info"),
    timeline: document.getElementById("timeline"),
    followupDot: document.getElementById("followup-dot"),
    followupStatus: document.getElementById("followup-status"),
    requestCount: document.getElementById("request-count"),
    inspectorConversation: document.getElementById("inspector-conversation"),
    inspectorFollowup: document.getElementById("inspector-followup"),
    requestList: document.getElementById("request-list"),
    pendingFollowupsBtn: document.getElementById("pending-followups-btn"),
    curateMemoryBtn: document.getElementById("curate-memory-btn"),
    refreshProfileBtn: document.getElementById("refresh-profile-btn"),
    volumePanel: document.querySelector(".volume-panel"),
    voicePanel: document.querySelector(".voice-panel"),
    playbackTestPanel: document.querySelector(".playback-test-panel"),
    debugPanel: document.querySelector(".debug-panel"),
};

async function loadSettings() {
    const response = await fetch("/api/settings");
    const settings = await response.json();
    els.serviceUrl.value = settings.service_url;
    els.conversationId.value = settings.conversation_id;
    els.ttsSampleRate.value = settings.tts_sample_rate;
    renderInspector();
}

async function loadAppMode() {
    const response = await fetch("/api/app-mode");
    if (!response.ok) {
        return { web_only: false };
    }
    return response.json();
}

function applyAppMode(mode) {
    appMode = mode || { web_only: false };
    if (!appMode.web_only) {
        els.voiceInputMode.value = "robot";
        return;
    }
    document.body.classList.add("web-only");
    for (const panel of [
        els.volumePanel,
        els.playbackTestPanel,
    ]) {
        panel?.classList.add("hidden");
    }
    els.voiceInputMode.value = "local";
    els.voiceInputMode.disabled = true;
    els.liveTranscript.textContent = "web-only 模式使用电脑麦克风";
}

async function saveSettings() {
    const body = {
        service_url: els.serviceUrl.value,
        conversation_id: els.conversationId.value,
        tts_sample_rate: Number(els.ttsSampleRate.value || 24000),
    };
    const response = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!response.ok) {
        throw new Error("保存设置失败");
    }
    const settings = await response.json();
    els.serviceUrl.value = settings.service_url;
    els.conversationId.value = settings.conversation_id;
    els.ttsSampleRate.value = settings.tts_sample_rate;
    renderInspector();
    return settings;
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

function connectFollowups() {
    closeFollowups();
    const conversationId = normalizedConversationId();
    if (!conversationId) {
        setFollowupState(false, "缺少 conversation_id");
        return;
    }
    const params = new URLSearchParams({
        conversation_id: conversationId,
        tts_enabled: "true",
    });
    const url = `/api/followups/stream?${params.toString()}`;
    const source = new EventSource(url);
    chatState.followupSource = source;
    setFollowupState(false, "记忆通道连接中");

    source.onopen = () => {
        setFollowupState(true, "记忆通道已连接");
        appendEventLog("followup:open", { conversation_id: conversationId });
    };
    source.onerror = () => {
        setFollowupState(false, "记忆通道断开或服务未启动");
        appendEventLog("followup:error", { conversation_id: conversationId });
    };

    for (const eventName of [
        "message",
        "followup",
        "supplement",
        "correction",
        "audio",
        "followup_done",
        "behavior",
        "done",
        "followup_error",
    ]) {
        source.addEventListener(eventName, (event) => {
            const payload = parseEventSourcePayload(event.data);
            handleFollowupPayload(payload, eventName);
        });
    }
}

function closeFollowups() {
    if (chatState.followupSource) {
        chatState.followupSource.close();
        chatState.followupSource = null;
    }
    setFollowupState(false, "记忆通道未连接");
}

function setFollowupState(connected, text) {
    chatState.followupConnected = connected;
    els.followupStatus.textContent = text;
    els.inspectorFollowup.textContent = connected ? "connected" : "disconnected";
    els.followupDot.classList.toggle("ok", connected);
    els.followupDot.classList.toggle("muted", !connected);
    renderInspector();
}

function parseEventSourcePayload(raw) {
    try {
        return JSON.parse(raw);
    } catch {
        return { text: raw };
    }
}

function handleFollowupPayload(payload, eventName) {
    appendEventLog(`followup:${eventName}`, payload);
    if (eventName === "audio") {
        if (appMode.web_only || currentVoiceMode() === "local") {
            chatState.followupPlaybackFallbackKey =
                chatState.followupPlaybackFallbackKey || makeId("followup-audio");
            chatState.followupPlaybackKey = localAudioPlaybackScheduler.enqueueAudio(
                payload,
                {
                    fallbackKey:
                        chatState.followupPlaybackKey
                        || chatState.followupPlaybackFallbackKey,
                },
            );
        }
        setStatus("正在接收记忆补充语音");
        return;
    }
    if (eventName === "followup_done") {
        if (appMode.web_only || currentVoiceMode() === "local") {
            chatState.followupPlaybackKey = localAudioPlaybackScheduler.complete(
                payload,
                {
                    fallbackKey:
                        chatState.followupPlaybackKey
                        || chatState.followupPlaybackFallbackKey
                        || makeId("followup-audio"),
                },
            );
        }
        chatState.followupPlaybackKey = null;
        chatState.followupPlaybackFallbackKey = null;
        setStatus("记忆补充语音完成");
        return;
    }
    if (eventName === "behavior") {
        setStatus(formatBehaviorStatus(payload));
        return;
    }
    if (payload.message && !payload.request_id && !payload.reply) {
        setStatus(payload.message);
        return;
    }
    if (eventName === "followup_error" || payload.error || payload.message === "error") {
        localAudioPlaybackScheduler.abort(
            chatState.followupPlaybackKey || chatState.followupPlaybackFallbackKey,
        );
        chatState.followupPlaybackKey = null;
        chatState.followupPlaybackFallbackKey = null;
        setStatus(payload.error || payload.message || "follow-up 通道错误");
        return;
    }

    const requestId = payload.request_id || payload.parent_request_id;
    if (requestId) {
        const request = ensureRequest(requestId);
        request.followupStatus = payload.followup_type || "received";
        request.lastFollowup = payload;
        markRequestRetrieval(requestId, "completed");
    }

    const followupType = payload.followup_type || eventName;
    if (followupType === "none" || !payload.reply) {
        renderTimeline();
        renderInspector();
        return;
    }

    const fingerprint = [
        requestId || "",
        payload.parent_user_turn_id || "",
        payload.parent_initial_reply_turn_id || "",
        followupType,
        payload.reply,
    ].join("|");
    if (chatState.seenFollowups.has(fingerprint)) {
        return;
    }
    chatState.seenFollowups.add(fingerprint);

    addMessage({
        id: makeId("followup"),
        role: "assistant",
        kind: "followup",
        content: String(payload.reply || ""),
        requestId,
        parentRequestId: requestId,
        parentUserTurnId: payload.parent_user_turn_id,
        parentInitialReplyTurnId: payload.parent_initial_reply_turn_id,
        originalUserQuery: payload.original_user_query,
        initialReply: payload.initial_reply,
        followupType,
        status: "done",
        createdAt: Date.now(),
    });
    setStatus(formatFollowupStatus(followupType));
}

async function loadVolumeControls() {
    const response = await fetch("/api/audio-volume");
    const result = await response.json();
    if (!response.ok) {
        throw new Error(result.detail || "读取音量失败");
    }
    renderVolume("speaker", result.speaker);
    renderVolume("microphone", result.microphone);
}

async function setVolume(kind, value) {
    const endpoint = kind === "speaker"
        ? "/api/audio-volume/speaker"
        : "/api/audio-volume/microphone";
    const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ volume: Number(value) }),
    });
    const result = await response.json();
    if (!response.ok) {
        throw new Error(result.detail || "设置音量失败");
    }
    renderVolume(kind, result);
    setStatus(kind === "speaker" ? "扬声器音量已更新" : "麦克风音量已更新");
}

function renderVolume(kind, payload) {
    const volume = Number(payload?.volume);
    const text = Number.isFinite(volume) ? `${volume}%` : "--";
    if (kind === "speaker") {
        if (Number.isFinite(volume)) {
            els.speakerVolume.value = String(volume);
        }
        els.speakerVolumeValue.textContent = text;
        return;
    }
    if (Number.isFinite(volume)) {
        els.microphoneVolume.value = String(volume);
    }
    els.microphoneVolumeValue.textContent = text;
}

async function toggleRecording() {
    if (currentVoiceMode() === "local") {
        if (isRecording) {
            await stopLocalRecordingAndReply();
            return;
        }
        await startLocalRecording();
        return;
    }
    if (isRecording) {
        await stopRecording();
        return;
    }
    await startRecording();
}

async function startRecording() {
    if (isPlaybackTestRecording) {
        throw new Error("请先停止机器人麦克风回放测试。");
    }
    if (isTextSending) {
        throw new Error("请等当前文本回复结束后再录音。");
    }
    await saveSettings();
    connectFollowups();
    lastRecordingStats = null;
    const response = await fetch("/api/robot-mic/start", { method: "POST" });
    const result = await response.json();
    if (!response.ok || result.error) {
        throw new Error(result.detail || result.error?.message || "机器人麦克风启动失败");
    }
    activeVoiceMode = "robot";
    isRecording = true;
    recordingStartedAt = Date.now();
    timerId = window.setInterval(updateTimer, 250);
    levelTimerId = window.setInterval(() => {
        updateMicLevel().catch((error) => setStatus(error.message || String(error)));
    }, 200);
    transcriptTimerId = window.setInterval(() => {
        updateRobotTranscript().catch((error) => setStatus(error.message || String(error)));
    }, 300);
    updateTimer();
    updateMicLevel().catch((error) => setStatus(error.message || String(error)));
    renderRobotTranscript({ text: "", error: null });
    updateRobotTranscript().catch((error) => setStatus(error.message || String(error)));
    updateDebugInfo().catch((error) => setStatus(error.message || String(error)));
    els.recordBtn.textContent = "停止并发送";
    els.recordBtn.classList.add("recording");
    setStatus("正在用机器人麦克风录音");
}

async function togglePlaybackTest() {
    if (isPlaybackTestRecording) {
        await stopPlaybackTest();
        return;
    }
    await startPlaybackTest();
}

async function startPlaybackTest() {
    if (isRecording) {
        throw new Error("请先停止语音聊天录音。");
    }
    if (isTextSending) {
        throw new Error("请等当前文本回复结束后再测试回放。");
    }
    const response = await fetch("/api/robot-mic/playback-test/start", { method: "POST" });
    const result = await response.json();
    if (!response.ok || result.error) {
        throw new Error(result.detail || result.error?.message || "机器人麦克风回放测试启动失败");
    }
    isPlaybackTestRecording = true;
    playbackTestStartedAt = Date.now();
    playbackTestTimerId = window.setInterval(updatePlaybackTestTimer, 250);
    playbackTestLevelTimerId = window.setInterval(() => {
        updatePlaybackTestLevel().catch((error) => setStatus(error.message || String(error)));
    }, 200);
    els.playbackTestBtn.textContent = "停止并播放";
    els.playbackTestBtn.classList.add("recording");
    els.recordBtn.disabled = true;
    els.playbackTestStatus.textContent = "录音中";
    els.playbackTestResult.textContent = `采样率 ${result.sample_rate} Hz`;
    updatePlaybackTestTimer();
    updatePlaybackTestLevel().catch((error) => setStatus(error.message || String(error)));
    setStatus("正在录制机器人麦克风回放测试");
}

async function stopPlaybackTest() {
    if (!isPlaybackTestRecording) {
        return;
    }
    isPlaybackTestRecording = false;
    window.clearInterval(playbackTestTimerId);
    window.clearInterval(playbackTestLevelTimerId);
    playbackTestTimerId = null;
    playbackTestLevelTimerId = null;
    els.playbackTestBtn.textContent = "正在播放...";
    els.playbackTestBtn.disabled = true;
    els.playbackTestBtn.classList.remove("recording");
    els.playbackTestStatus.textContent = "播放中";
    setStatus("正在从机器人扬声器播放刚录到的麦克风音频");
    try {
        const response = await fetch("/api/robot-mic/playback-test/stop", { method: "POST" });
        const result = await response.json();
        if (!response.ok || result.error) {
            throw new Error(result.detail || result.error?.message || "机器人麦克风回放测试失败");
        }
        renderPlaybackTestLevel(result);
        els.playbackTestStatus.textContent = result.playback_finished ? "完成" : "已发送";
        els.playbackTestResult.textContent =
            `${formatSeconds(result.duration_seconds)} · RMS ${formatLevel(result.rms)} · 峰值 ${formatLevel(result.peak)}`;
        setStatus(result.playback_finished ? "机器人麦克风回放测试完成" : "机器人麦克风音频已发送到播放器");
    } catch (error) {
        els.playbackTestStatus.textContent = "失败";
        els.playbackTestResult.textContent = describeError(error);
        setStatus(describeError(error));
    } finally {
        els.playbackTestBtn.textContent = "开始测试";
        els.playbackTestBtn.disabled = false;
        els.recordBtn.disabled = false;
        els.playbackTestTime.textContent = "00:00";
    }
}

async function sendManualText() {
    if (isTextSending) {
        return;
    }
    if (isRecording) {
        throw new Error("请先停止语音聊天录音。");
    }
    if (isPlaybackTestRecording) {
        throw new Error("请先停止机器人麦克风回放测试。");
    }
    await saveSettings();
    connectFollowups();
    const text = els.manualText.value.trim();
    if (!text) {
        throw new Error("请输入文本。");
    }

    isTextSending = true;
    els.sendTextBtn.disabled = true;
    els.recordBtn.disabled = true;
    els.playbackTestBtn.disabled = true;

    const exchange = createExchange({
        userText: text,
        source: "text",
    });
    els.transcript.textContent = text;
    els.reply.textContent = "";
    renderRobotTranscript({ text, error: null });
    setStatus("正在发送文本...");

    try {
        const response = await fetch("/api/text-chat-stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                text,
                conversation_id: normalizedConversationId(),
                tts_enabled: els.textTtsEnabled.checked,
            }),
        });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.detail || payload.error?.message || "文本发送失败");
        }
        await renderDialogueStream(response, {
            waitingStatus: "正在生成文本回复...",
            transcriptFallback: text,
            userMessageId: exchange.userMessage.id,
            assistantMessageId: exchange.assistantMessage.id,
        });
        els.manualText.value = "";
    } catch (error) {
        markMessageError(exchange.assistantMessage.id, describeError(error));
        setStatus(describeError(error));
    } finally {
        isTextSending = false;
        els.sendTextBtn.disabled = false;
        els.recordBtn.disabled = false;
        if (!appMode.web_only) {
            els.playbackTestBtn.disabled = false;
        }
    }
}

async function stopRecording() {
    if (!isRecording) {
        return;
    }
    isRecording = false;
    window.clearInterval(timerId);
    window.clearInterval(levelTimerId);
    levelTimerId = null;
    window.clearInterval(transcriptTimerId);
    transcriptTimerId = null;
    els.recordBtn.textContent = "处理中...";
    els.recordBtn.disabled = true;
    els.recordBtn.classList.remove("recording");
    setStatus("正在读取机器人麦克风音频");
    await sendRecording();
}

async function sendRecording() {
    const context = {
        userMessageId: null,
        assistantMessageId: null,
        transcriptFallback: "(语音输入)",
    };
    try {
        const response = await fetch("/api/robot-mic/stop-stream", { method: "POST" });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.detail || payload.error?.message || "机器人麦克风录音失败");
        }

        const streamState = await renderDialogueStream(response, {
            waitingStatus: "正在生成回复...",
            transcriptFallback: "(未识别到文本)",
            userMessageId: context.userMessageId,
            assistantMessageId: context.assistantMessageId,
            createExchangeOnTranscript: true,
            onExchangeCreated: (exchange) => {
                context.userMessageId = exchange.userMessage.id;
                context.assistantMessageId = exchange.assistantMessage.id;
            },
            onRecording: (data) => {
                lastRecordingStats = data;
                renderMicLevel(data);
            },
            onDebug: renderDebugInfo,
            onTranscript: () => {
                setStatus("已识别语音，正在流式生成回复...");
            },
        });

        if (!streamState.finalPayload && !streamState.reply) {
            const assistant = getMessage(context.assistantMessageId);
            if (assistant) {
                assistant.content = "(没有回复)";
                assistant.status = "done";
                renderTimeline();
            }
        }
    } catch (error) {
        if (context.assistantMessageId) {
            markMessageError(context.assistantMessageId, describeError(error));
        }
        setStatus(describeError(error));
    } finally {
        activeVoiceMode = null;
        els.recordBtn.textContent = "开始录音";
        els.recordBtn.disabled = false;
        els.localAbortBtn.disabled = true;
        els.recordingTime.textContent = "00:00";
    }
}

async function startLocalRecording() {
    if (isPlaybackTestRecording) {
        throw new Error("请先停止机器人麦克风回放测试。");
    }
    if (isTextSending) {
        throw new Error("请等当前文本回复结束后再录音。");
    }
    await saveSettings();
    connectFollowups();
    await ensureLocalPlaybackContext();

    const startResponse = await fetch("/api/local-mic/start", { method: "POST" });
    const startPayload = await startResponse.json();
    if (!startResponse.ok || startPayload.error) {
        throw new Error(startPayload.detail || startPayload.error?.message || "启动本机实时语音失败");
    }

    localSessionId = startPayload.session_id;
    localMediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        },
    });
    localAudioContext = new AudioContext();
    localSourceNode = localAudioContext.createMediaStreamSource(localMediaStream);
    localProcessorNode = localAudioContext.createScriptProcessor(4096, 1, 1);
    localProcessorNode.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0);
        updateLocalMicLevel(input);
        enqueueLocalPcm(
            downsampleToPcm16(
                input,
                localAudioContext.sampleRate,
                LOCAL_TARGET_SAMPLE_RATE,
            ),
        );
    };
    localSourceNode.connect(localProcessorNode);
    localProcessorNode.connect(localAudioContext.destination);

    localSendQueue = [];
    localPendingBytes = new Uint8Array(0);
    localSentBytes = 0;
    localSentChunks = 0;
    localAcceptedBytes = 0;
    activeVoiceMode = "local";
    isRecording = true;
    recordingStartedAt = Date.now();
    localSendLoopPromise = sendLocalMicLoop();
    timerId = window.setInterval(updateTimer, 250);
    transcriptTimerId = window.setInterval(() => {
        pollLocalTranscript().catch((error) => setStatus(error.message || String(error)));
    }, 300);
    els.reply.textContent = "";
    els.transcript.textContent = "等待最终识别";
    els.liveTranscript.textContent = "正在听电脑麦克风...";
    els.recordBtn.textContent = "停止并生成";
    els.recordBtn.classList.add("recording");
    els.localAbortBtn.disabled = false;
    els.sendTextBtn.disabled = true;
    if (!appMode.web_only) {
        els.playbackTestBtn.disabled = true;
    }
    setStatus(`本机麦克风录音中：${localSessionId}`);
    renderDebugInfo(localMicDebug());
}

async function stopLocalRecordingAndReply() {
    if (!isRecording || activeVoiceMode !== "local") {
        return;
    }
    isRecording = false;
    stopLocalAudioGraph();
    window.clearInterval(timerId);
    timerId = null;
    window.clearInterval(transcriptTimerId);
    transcriptTimerId = null;
    els.recordBtn.disabled = true;
    els.localAbortBtn.disabled = true;
    els.recordBtn.textContent = "处理中...";
    els.recordBtn.classList.remove("recording");
    setStatus("正在发送剩余本机麦克风音频...");

    if (localPendingBytes.length > 0) {
        localSendQueue.push(localPendingBytes);
        localPendingBytes = new Uint8Array(0);
    }
    await localSendLoopPromise;

    const context = {
        userMessageId: null,
        assistantMessageId: null,
    };
    const playbackFallbackKey = makeId(`local-audio-${localSessionId || "session"}`);
    let playbackKey = null;
    try {
        const finishResponse = await fetch("/api/local-mic/finish-stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: localSessionId,
                conversation_id: normalizedConversationId(),
                tts_enabled: els.textTtsEnabled.checked,
            }),
        });
        if (!finishResponse.ok) {
            const payload = await finishResponse.json().catch(() => ({}));
            throw new Error(payload.detail || payload.error?.message || "结束本机语音失败");
        }
        await renderDialogueStream(finishResponse, {
            waitingStatus: "正在结束 STT 并生成回复...",
            transcriptFallback: "(本机语音输入)",
            userMessageId: context.userMessageId,
            assistantMessageId: context.assistantMessageId,
            createExchangeOnTranscript: true,
            onExchangeCreated: (exchange) => {
                context.userMessageId = exchange.userMessage.id;
                context.assistantMessageId = exchange.assistantMessage.id;
            },
            onAudio: (data) => {
                playbackKey = localAudioPlaybackScheduler.enqueueAudio(
                    data,
                    { fallbackKey: playbackKey || playbackFallbackKey },
                );
            },
            onDone: (data) => {
                playbackKey = localAudioPlaybackScheduler.complete(
                    data,
                    { fallbackKey: playbackKey || playbackFallbackKey },
                );
            },
            onError: () => {
                localAudioPlaybackScheduler.abort(playbackKey || playbackFallbackKey);
            },
            onTranscript: () => {
                setStatus("已识别本机语音，正在流式生成回复...");
            },
        });
    } catch (error) {
        localAudioPlaybackScheduler.abort(playbackKey || playbackFallbackKey);
        if (context.assistantMessageId) {
            markMessageError(context.assistantMessageId, describeError(error));
        }
        setStatus(describeError(error));
    } finally {
        resetLocalRecordingControls();
    }
}

async function abortLocalRecording() {
    const session = localSessionId;
    isRecording = false;
    activeVoiceMode = null;
    stopLocalAudioGraph();
    window.clearInterval(timerId);
    timerId = null;
    window.clearInterval(transcriptTimerId);
    transcriptTimerId = null;
    if (session) {
        await fetch("/api/local-mic/abort", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: session }),
        }).catch(() => {});
    }
    resetLocalRecordingControls();
    setStatus("已取消本机麦克风录音");
}

async function toggleAutoVoice() {
    if (isAutoVoiceActive) {
        await stopAutoVoice();
        return;
    }
    await startAutoVoice();
}

async function startAutoVoice() {
    if (isRecording || isTextSending || isPlaybackTestRecording) {
        throw new Error("请先结束当前录音、文本发送或回放测试。");
    }
    await saveSettings();
    connectFollowups();
    const inputMode = currentVoiceMode();
    const response = await fetch("/api/auto-voice/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            input_mode: inputMode,
            conversation_id: normalizedConversationId(),
            tts_enabled: els.textTtsEnabled.checked,
        }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(payload.detail || payload.message || "启动自动对话失败");
    }

    autoVoiceSessionId = payload.session_id;
    isAutoVoiceActive = true;
    autoVoiceInputOpen = payload.state === "listening" || payload.state === "user_speaking";
    isRecording = true;
    activeVoiceMode = inputMode === "local" ? "auto-local" : "auto-robot";
    autoVoiceExchange = null;
    els.autoVoiceBtn.textContent = "退出对话模式";
    els.autoVoiceBtn.classList.add("recording");
    els.autoVoiceStatus.textContent = "正在连接";
    els.recordBtn.disabled = true;
    els.localAbortBtn.disabled = true;
    els.sendTextBtn.disabled = true;
    if (!appMode.web_only) {
        els.playbackTestBtn.disabled = true;
    }
    recordingStartedAt = Date.now();
    timerId = window.setInterval(updateTimer, 250);

    autoVoiceSource = new EventSource(
        `/api/auto-voice/events?session_id=${encodeURIComponent(autoVoiceSessionId)}`,
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
        autoVoiceSource.addEventListener(eventName, (event) => {
            handleAutoVoiceEvent(eventName, parseEventSourcePayload(event.data));
        });
    }
    autoVoiceSource.onerror = () => {
        setStatus("自动对话事件通道断开");
        els.autoVoiceStatus.textContent = "事件通道断开";
    };

    if (inputMode === "local") {
        await startAutoLocalAudioCapture();
    }
    setStatus(inputMode === "local" ? "自动对话：电脑麦克风监听中" : "自动对话：机器人麦克风监听中");
}

async function startAutoLocalAudioCapture() {
    await ensureLocalPlaybackContext();
    localMediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        },
    });
    localAudioContext = new AudioContext();
    localSourceNode = localAudioContext.createMediaStreamSource(localMediaStream);
    localProcessorNode = localAudioContext.createScriptProcessor(4096, 1, 1);
    localProcessorNode.onaudioprocess = (event) => {
        if (!isAutoVoiceActive || activeVoiceMode !== "auto-local") {
            return;
        }
        const input = event.inputBuffer.getChannelData(0);
        updateLocalMicLevel(input);
        if (!autoVoiceInputOpen) {
            localPendingBytes = new Uint8Array(0);
            return;
        }
        enqueueLocalPcm(
            downsampleToPcm16(
                input,
                localAudioContext.sampleRate,
                LOCAL_TARGET_SAMPLE_RATE,
            ),
        );
    };
    localSourceNode.connect(localProcessorNode);
    localProcessorNode.connect(localAudioContext.destination);
    localSendQueue = [];
    localPendingBytes = new Uint8Array(0);
    localSentBytes = 0;
    localSentChunks = 0;
    localAcceptedBytes = 0;
    localSendLoopPromise = sendAutoLocalMicLoop();
}

async function sendAutoLocalMicLoop() {
    while (isAutoVoiceActive || localSendQueue.length > 0) {
        const chunk = localSendQueue.shift();
        if (!chunk) {
            await sleep(25);
            continue;
        }
        const response = await fetch("/api/auto-voice/chunk", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: autoVoiceSessionId,
                audio_base64: bytesToBase64(chunk),
                sample_rate: LOCAL_TARGET_SAMPLE_RATE,
            }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.error) {
            throw new Error(payload.detail || payload.error?.message || "自动对话音频块发送失败");
        }
        localSentChunks += 1;
        localSentBytes += chunk.length;
        if (payload.accepted !== false) {
            localAcceptedBytes += chunk.length;
        }
        renderDebugInfo(localMicDebug({ mode: "auto-local" }));
    }
}

async function stopAutoVoice() {
    const sessionId = autoVoiceSessionId;
    isAutoVoiceActive = false;
    isRecording = false;
    if (autoVoiceSource) {
        autoVoiceSource.close();
        autoVoiceSource = null;
    }
    if (activeVoiceMode === "auto-local") {
        if (localPendingBytes.length > 0) {
            localSendQueue.push(localPendingBytes);
            localPendingBytes = new Uint8Array(0);
        }
        stopLocalAudioGraph();
        if (localSendLoopPromise) {
            await localSendLoopPromise.catch(() => {});
        }
    }
    if (sessionId) {
        await fetch("/api/auto-voice/stop", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId }),
        }).catch(() => {});
    }
    window.clearInterval(timerId);
    timerId = null;
    activeVoiceMode = null;
    autoVoiceSessionId = null;
    autoVoiceExchange = null;
    autoVoiceInputOpen = false;
    els.autoVoiceBtn.textContent = "进入对话模式";
    els.autoVoiceBtn.classList.remove("recording");
    els.autoVoiceStatus.textContent = "手动录音";
    els.recordBtn.disabled = false;
    els.sendTextBtn.disabled = false;
    els.recordingTime.textContent = "00:00";
    if (!appMode.web_only) {
        els.playbackTestBtn.disabled = false;
    }
    setStatus("已退出自动对话模式");
}

function handleAutoVoiceEvent(event, data) {
    appendEventLog(`auto:${event}`, data);
    if (event === "state") {
        autoVoiceInputOpen = data.state === "listening" || data.state === "user_speaking";
        if (!autoVoiceInputOpen) {
            localSendQueue = [];
            localPendingBytes = new Uint8Array(0);
        }
        els.autoVoiceStatus.textContent = autoVoiceStateText(data.state);
        setStatus(`自动对话：${autoVoiceStateText(data.state)}`);
        return;
    }
    if (event === "level") {
        renderMicLevel({
            rms: data.rms,
            peak: data.peak,
            level: clamp01(Number(data.speech_probability || 0)),
        });
        return;
    }
    if (event === "speech_start") {
        autoVoiceInputOpen = true;
        autoVoiceExchange = createAutoVoiceExchange("(正在说话...)");
        els.liveTranscript.textContent = "检测到语音，正在听...";
        return;
    }
    if (event === "speech_end") {
        autoVoiceInputOpen = false;
        localSendQueue = [];
        localPendingBytes = new Uint8Array(0);
        els.liveTranscript.textContent = "语音结束，正在识别...";
        return;
    }
    if (event === "speech_cancelled") {
        autoVoiceInputOpen = true;
        els.liveTranscript.textContent = "语音太短，继续听...";
        autoVoiceExchange = null;
        return;
    }
    if (event === "input_dropped" || event === "input_drained") {
        setStatus(data.reason ? `半双工丢弃输入：${data.reason}` : "半双工清空残留输入");
        return;
    }
    if (event === "utterance") {
        els.autoVoiceStatus.textContent = "正在识别";
        return;
    }
    if (event === "transcript") {
        const text = data.transcript || data.text || "";
        const exchange = ensureAutoVoiceExchange(text || "(未识别到文本)");
        if (exchange.userMessage) {
            exchange.userMessage.content = text || "(未识别到文本)";
        }
        els.transcript.textContent = text || "(未识别到文本)";
        els.liveTranscript.textContent = text || "正在识别...";
        renderTimeline();
        return;
    }
    if (event === "meta") {
        const exchange = ensureAutoVoiceExchange(autoVoiceExchange?.transcript || "(语音输入)");
        bindRequestToExchange(data, {
            userMessageId: exchange.userMessage.id,
            assistantMessageId: exchange.assistantMessage.id,
        });
        return;
    }
    if (event === "delta") {
        const exchange = ensureAutoVoiceExchange(autoVoiceExchange?.transcript || "(语音输入)");
        const assistant = exchange.assistantMessage;
        const delta = data.delta || "";
        assistant.content += delta;
        assistant.status = "streaming";
        autoVoiceExchange.reply = assistant.content;
        els.reply.textContent = assistant.content || " ";
        renderTimeline();
        return;
    }
    if (event === "audio") {
        if (currentVoiceMode() === "local") {
            autoVoiceExchange = autoVoiceExchange || {};
            autoVoiceExchange.playbackKey = localAudioPlaybackScheduler.enqueueAudio(
                data,
                {
                    fallbackKey:
                        autoVoiceExchange.playbackKey
                        || autoVoiceExchange.playbackFallbackKey
                        || makeId("auto-local-audio"),
                },
            );
        }
        return;
    }
    if (event === "behavior") {
        setStatus(formatBehaviorStatus(data));
        return;
    }
    if (event === "done") {
        const exchange = ensureAutoVoiceExchange(data.transcript || "(语音输入)");
        if (data.transcript) {
            exchange.userMessage.content = data.transcript;
            els.transcript.textContent = data.transcript;
        }
        exchange.assistantMessage.content = data.reply || exchange.assistantMessage.content || "(没有回复)";
        exchange.assistantMessage.status = "done";
        exchange.assistantMessage.retrievalStatus = "pending";
        bindRequestToExchange(data, {
            userMessageId: exchange.userMessage.id,
            assistantMessageId: exchange.assistantMessage.id,
        });
        if (currentVoiceMode() === "local") {
            autoVoiceExchange.playbackKey = localAudioPlaybackScheduler.complete(
                data,
                {
                    fallbackKey:
                        autoVoiceExchange.playbackKey
                        || autoVoiceExchange.playbackFallbackKey
                        || makeId("auto-local-audio"),
                },
            );
        }
        els.reply.textContent = exchange.assistantMessage.content;
        renderTimeline();
        return;
    }
    if (event === "playback_done") {
        autoVoiceExchange = null;
        autoVoiceInputOpen = false;
        els.autoVoiceStatus.textContent = "正在听";
        return;
    }
    if (event === "warning") {
        setStatus(data.message || "自动对话警告");
        return;
    }
    if (event === "error") {
        if (autoVoiceExchange?.assistantMessage?.id) {
            markMessageError(autoVoiceExchange.assistantMessage.id, data.message || "自动对话失败");
        }
        setStatus(data.message || "自动对话失败");
    }
}

function createAutoVoiceExchange(text) {
    const exchange = createExchange({
        userText: text,
        source: "voice",
    });
    exchange.transcript = text;
    exchange.reply = "";
    exchange.playbackFallbackKey = makeId("auto-local-audio");
    autoVoiceExchange = exchange;
    return exchange;
}

function ensureAutoVoiceExchange(text) {
    if (autoVoiceExchange?.userMessage && autoVoiceExchange?.assistantMessage) {
        if (text && autoVoiceExchange.userMessage.content === "(正在说话...)") {
            autoVoiceExchange.userMessage.content = text;
        }
        autoVoiceExchange.transcript = text || autoVoiceExchange.transcript || "";
        return autoVoiceExchange;
    }
    return createAutoVoiceExchange(text || "(语音输入)");
}

function autoVoiceStateText(state) {
    const labels = {
        starting: "正在启动",
        listening: "正在听",
        user_speaking: "你正在说话",
        transcribing: "正在识别",
        assistant_streaming: "正在回答",
        speaking: "正在播放",
        cooldown: "准备继续听",
        error: "出错",
        stopped: "已停止",
    };
    return labels[state] || state || "未知";
}

async function renderDialogueStream(response, options = {}) {
    let reply = "";
    let transcript = "";
    let finalPayload = null;
    let audioChunkCount = 0;
    let userMessageId = options.userMessageId || null;
    let assistantMessageId = options.assistantMessageId || null;
    let requestId = null;
    let textStreamKey = null;

    setStatus(options.waitingStatus || "正在生成回复...");

    const ensureExchange = (text) => {
        if (userMessageId && assistantMessageId) {
            return {
                userMessage: getMessage(userMessageId),
                assistantMessage: getMessage(assistantMessageId),
            };
        }
        const exchange = createExchange({
            userText: text || options.transcriptFallback || "(语音输入)",
            source: "voice",
        });
        userMessageId = exchange.userMessage.id;
        assistantMessageId = exchange.assistantMessage.id;
        options.onExchangeCreated?.(exchange);
        return exchange;
    };

    await readSseStream(response, ({ event, data }) => {
        appendEventLog(event, data);
        if (event === "recording") {
            options.onRecording?.(data);
            return;
        }
        if (event === "debug") {
            options.onDebug?.(data);
            return;
        }
        if (event === "transcript") {
            transcript = data.transcript || transcript || options.transcriptFallback || "";
            const exchange = ensureExchange(transcript);
            if (exchange.userMessage) {
                exchange.userMessage.content = transcript || "(未识别到文本)";
            }
            renderRobotTranscript({ text: transcript, error: null });
            els.transcript.textContent = transcript || "(未识别到文本)";
            renderTimeline();
            options.onTranscript?.(data);
            return;
        }
        if (event === "meta") {
            ensureExchange(transcript);
            textStreamKey = textStreamKey || playbackKeyFromPayload(data);
            requestId = bindRequestToExchange(data, {
                userMessageId,
                assistantMessageId,
                fallbackRequestId: requestId,
            });
            setStatus("已连接模型流");
            return;
        }
        if (event === "delta") {
            const deltaKey = playbackKeyFromPayload(data);
            if (deltaKey && textStreamKey && deltaKey !== textStreamKey) {
                appendEventLog("delta:buffered_other_stream", data);
                return;
            }
            textStreamKey = textStreamKey || deltaKey;
            ensureExchange(transcript || options.transcriptFallback);
            const assistant = getMessage(assistantMessageId);
            const delta = data.delta || "";
            reply += delta;
            if (assistant) {
                assistant.content += delta;
                assistant.status = "streaming";
                els.reply.textContent = assistant.content || " ";
                renderTimeline();
            }
            return;
        }
        if (event === "audio") {
            audioChunkCount += 1;
            textStreamKey = textStreamKey || playbackKeyFromPayload(data);
            options.onAudio?.(data);
            setStatus(`正在接收 TTS 音频 ${audioChunkCount}`);
            return;
        }
        if (event === "behavior") {
            setStatus(formatBehaviorStatus(data));
            return;
        }
        if (event === "emoji") {
            if (data.ok) {
                setStatus(`已触发表情 ${data.emotion || data.signal}`);
            } else {
                setStatus(`表情触发失败：${data.error || data.status_code || "未知错误"}`);
            }
            return;
        }
        if (event === "done") {
            ensureExchange(transcript || data.transcript || options.transcriptFallback);
            textStreamKey = textStreamKey || playbackKeyFromPayload(data);
            options.onDone?.(data);
            finalPayload = data;
            transcript = data.transcript || transcript || options.transcriptFallback || "";
            reply = data.reply || reply;
            if (!requestId || (data.request_id && data.request_id !== requestId)) {
                requestId = bindRequestToExchange(data, {
                    userMessageId,
                    assistantMessageId,
                    fallbackRequestId: requestId,
                });
            }
            const user = getMessage(userMessageId);
            const assistant = getMessage(assistantMessageId);
            if (user) {
                user.content = transcript || user.content || "(未识别到文本)";
                user.turnId = data.user_turn_id || data.turn_id || user.turnId;
            }
            if (assistant) {
                assistant.content = reply || assistant.content || "(没有回复)";
                assistant.status = "done";
                assistant.turnId = data.assistant_turn_id || data.reply_turn_id || assistant.turnId;
                assistant.retrievalStatus = "pending";
            }
            if (requestId) {
                const request = ensureRequest(requestId);
                request.initialStatus = "done";
                request.retrievalStatus = "pending";
                request.donePayload = data;
                request.reply = reply;
            }
            els.transcript.textContent = transcript || "(未识别到文本)";
            els.reply.textContent = reply || "(没有回复)";
            setStatus(
                data.audio_base64 || data.response_audio_base64 || audioChunkCount > 0
                    ? "正在播放机器人回复..."
                    : "流式回复完成，等待异步记忆补充",
            );
            renderTimeline();
            renderInspector();
            return;
        }
        if (event === "playback_done") {
            setStatus("机器人回复完成");
            return;
        }
        if (event === "error") {
            ensureExchange(transcript || options.transcriptFallback);
            markMessageError(assistantMessageId, data.message || "流式回复失败");
            options.onError?.(data);
            throw new Error(data.message || "流式回复失败");
        }
    });

    return { reply, transcript, finalPayload, audioChunkCount };
}

function createExchange({ userText, source }) {
    const userMessage = addMessage({
        id: makeId("user"),
        role: "user",
        kind: "initial",
        source,
        content: userText,
        status: "done",
        createdAt: Date.now(),
    });
    const assistantMessage = addMessage({
        id: makeId("assistant"),
        role: "assistant",
        kind: "initial",
        source,
        content: "",
        status: "streaming",
        retrievalStatus: "idle",
        createdAt: Date.now() + 1,
    });
    return { userMessage, assistantMessage };
}

function bindRequestToExchange(data, context) {
    const requestId = data.request_id || data.id || context.fallbackRequestId || makeId("local-request");
    if (context.fallbackRequestId && context.fallbackRequestId !== requestId) {
        chatState.requests.delete(context.fallbackRequestId);
    }
    const user = getMessage(context.userMessageId);
    const assistant = getMessage(context.assistantMessageId);
    const request = ensureRequest(requestId);
    request.requestId = requestId;
    request.conversationId = data.conversation_id || normalizedConversationId();
    request.userMessageId = context.userMessageId;
    request.assistantMessageId = context.assistantMessageId;
    request.userTurnId = data.user_turn_id || data.parent_user_turn_id || request.userTurnId;
    request.assistantTurnId = data.assistant_turn_id || data.reply_turn_id || request.assistantTurnId;
    request.initialStatus = request.initialStatus || "streaming";
    request.retrievalStatus = request.retrievalStatus || "idle";
    request.meta = { ...(request.meta || {}), ...data };
    if (user) {
        user.requestId = requestId;
        user.turnId = request.userTurnId || user.turnId;
    }
    if (assistant) {
        assistant.requestId = requestId;
        assistant.turnId = request.assistantTurnId || assistant.turnId;
    }
    renderTimeline();
    renderInspector();
    return requestId;
}

function addMessage(message) {
    chatState.messages.push(message);
    renderTimeline();
    renderInspector();
    return message;
}

function getMessage(messageId) {
    if (!messageId) {
        return null;
    }
    return chatState.messages.find((message) => message.id === messageId) || null;
}

function markMessageError(messageId, error) {
    const message = getMessage(messageId);
    if (!message) {
        return;
    }
    message.status = "error";
    message.error = error;
    renderTimeline();
    renderInspector();
}

function ensureRequest(requestId) {
    if (!chatState.requests.has(requestId)) {
        chatState.requests.set(requestId, {
            requestId,
            createdAt: Date.now(),
            initialStatus: "streaming",
            retrievalStatus: "idle",
            followupStatus: "waiting",
        });
    }
    return chatState.requests.get(requestId);
}

function markRequestRetrieval(requestId, status) {
    const request = ensureRequest(requestId);
    request.retrievalStatus = status;
    for (const message of chatState.messages) {
        if (message.requestId === requestId && message.role === "assistant" && message.kind === "initial") {
            message.retrievalStatus = status;
        }
    }
}

function renderTimeline() {
    els.timeline.replaceChildren();
    if (chatState.messages.length === 0) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        const title = document.createElement("strong");
        title.textContent = "准备开始对话";
        const text = document.createElement("p");
        text.textContent = "文本和语音回复会出现在这里。记忆补充稍后到达时会作为独立消息插入。";
        empty.append(title, text);
        els.timeline.append(empty);
        return;
    }
    for (const message of chatState.messages) {
        els.timeline.append(renderMessage(message));
    }
    els.timeline.scrollTop = els.timeline.scrollHeight;
}

function renderMessage(message) {
    const row = document.createElement("article");
    row.className = `chat-message ${message.role} ${message.kind || "initial"} ${message.status || "done"}`;

    const meta = document.createElement("div");
    meta.className = "message-meta";
    const label = document.createElement("span");
    label.textContent = messageLabel(message);
    const status = document.createElement("strong");
    status.textContent = messageStatusText(message);
    meta.append(label, status);

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    if (message.kind === "followup") {
        const title = document.createElement("h3");
        title.textContent = followupTitle(message.followupType);
        bubble.append(title);
        if (message.originalUserQuery) {
            const quote = document.createElement("blockquote");
            quote.textContent = `关于：“${message.originalUserQuery}”`;
            bubble.append(quote);
        }
    }
    const content = document.createElement("p");
    content.textContent = message.content || (message.status === "streaming" ? "正在生成..." : "");
    bubble.append(content);

    if (message.error) {
        const error = document.createElement("div");
        error.className = "message-error";
        error.textContent = message.error;
        bubble.append(error);
    }

    if (message.requestId || message.turnId) {
        const ids = document.createElement("div");
        ids.className = "message-ids";
        ids.textContent = [message.requestId, message.turnId].filter(Boolean).join(" · ");
        bubble.append(ids);
    }

    row.append(meta, bubble);
    return row;
}

function messageLabel(message) {
    if (message.role === "user") {
        return message.source === "voice" ? "你说" : "你";
    }
    if (message.kind === "followup") {
        return "助手补充";
    }
    return "助手";
}

function messageStatusText(message) {
    if (message.status === "streaming") {
        return "streaming";
    }
    if (message.status === "error") {
        return "error";
    }
    if (message.kind === "followup") {
        return message.followupType || "follow-up";
    }
    if (message.retrievalStatus === "pending") {
        return "记忆检索中";
    }
    if (message.retrievalStatus === "completed") {
        return "记忆已检查";
    }
    if (message.retrievalStatus === "failed") {
        return "记忆检查失败";
    }
    return "done";
}

function followupTitle(type) {
    if (type === "correction") {
        return "修正刚才的回答";
    }
    return "补充刚才的问题";
}

function formatFollowupStatus(type) {
    if (type === "correction") {
        return "收到一条记忆修正";
    }
    return "收到一条记忆补充";
}

function renderInspector() {
    els.inspectorConversation.textContent = normalizedConversationId() || "--";
    els.requestCount.textContent = `${chatState.requests.size} requests`;
    els.inspectorFollowup.textContent = chatState.followupConnected ? "connected" : "disconnected";
    els.requestList.replaceChildren();

    const requests = Array.from(chatState.requests.values())
        .sort((a, b) => b.createdAt - a.createdAt)
        .slice(0, 8);
    if (requests.length === 0) {
        const empty = document.createElement("div");
        empty.className = "request-empty";
        empty.textContent = "还没有 request";
        els.requestList.append(empty);
    }
    for (const request of requests) {
        const item = document.createElement("div");
        item.className = "request-item";
        const main = document.createElement("button");
        main.type = "button";
        main.className = "request-main";
        main.textContent = request.requestId;
        main.addEventListener("click", () => renderDebugInfo(request));
        const meta = document.createElement("span");
        meta.textContent = [
            request.initialStatus || "initial",
            request.retrievalStatus || "retrieval",
            request.followupStatus || "follow-up",
        ].join(" · ");
        const run = document.createElement("button");
        run.type = "button";
        run.className = "request-run";
        run.textContent = "Run";
        run.addEventListener("click", () => runFollowup(request.requestId));
        item.append(main, meta, run);
        els.requestList.append(item);
    }
}

function appendEventLog(event, data) {
    chatState.eventLog.unshift({
        at: new Date().toISOString(),
        event,
        data,
    });
    chatState.eventLog = chatState.eventLog.slice(0, 20);
    if (event !== "delta") {
        renderDebugInfo(chatState.eventLog[0]);
    }
}

async function runFollowup(requestId) {
    try {
        const response = await fetch(`/api/followups/${encodeURIComponent(requestId)}/run`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: normalizedConversationId() }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.detail || payload.message || "手动触发 follow-up 失败");
        }
        renderDebugInfo(payload);
        setStatus("已触发 follow-up 判断");
    } catch (error) {
        setStatus(describeError(error));
        renderDebugInfo({ error: describeError(error) });
    }
}

async function loadPendingFollowups() {
    try {
        const url = `/api/followups/pending?conversation_id=${encodeURIComponent(normalizedConversationId())}`;
        const response = await fetch(url);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.detail || payload.message || "读取 pending follow-up 失败");
        }
        renderDebugInfo(payload);
        setStatus("已读取 pending follow-up");
    } catch (error) {
        setStatus(describeError(error));
        renderDebugInfo({ error: describeError(error) });
    }
}

async function runMemoryAction(endpoint, label) {
    try {
        const response = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: normalizedConversationId() }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.detail || payload.message || `${label} 失败`);
        }
        renderDebugInfo(payload);
        setStatus(`${label} 已完成`);
    } catch (error) {
        setStatus(describeError(error));
        renderDebugInfo({ error: describeError(error) });
    }
}

function updateTimer() {
    const seconds = Math.floor((Date.now() - recordingStartedAt) / 1000);
    const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
    const rest = String(seconds % 60).padStart(2, "0");
    els.recordingTime.textContent = `${minutes}:${rest}`;
}

function updatePlaybackTestTimer() {
    const seconds = Math.floor((Date.now() - playbackTestStartedAt) / 1000);
    const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
    const rest = String(seconds % 60).padStart(2, "0");
    els.playbackTestTime.textContent = `${minutes}:${rest}`;
}

async function updateMicLevel() {
    if (!isRecording || activeVoiceMode !== "robot") {
        return;
    }
    const response = await fetch("/api/robot-mic/level");
    const level = await response.json();
    if (!response.ok || level.error) {
        throw new Error(level.detail || level.error?.message || "读取麦克风音量失败");
    }
    renderMicLevel(level);
    updateDebugInfo().catch((error) => setStatus(error.message || String(error)));
}

async function updatePlaybackTestLevel() {
    if (!isPlaybackTestRecording) {
        return;
    }
    const response = await fetch("/api/robot-mic/playback-test/level");
    const level = await response.json();
    if (!response.ok || level.error) {
        throw new Error(level.detail || level.error?.message || "读取回放测试音量失败");
    }
    renderPlaybackTestLevel(level);
}

function renderMicLevel(level) {
    const normalized = clamp01(Number(level.level || 0));
    els.micLevelFill.style.width = `${Math.round(normalized * 100)}%`;
    els.micLevelText.textContent = `RMS ${formatLevel(level.rms)} · 峰值 ${formatLevel(level.peak)}`;
}

function formatBehaviorStatus(data) {
    const moduleName = data.module || "behavior";
    const key = data.key || data.signal || "";
    if (data.ok) {
        return `已触发 ${moduleName} ${key}`;
    }
    const reason = data.error || data.status_code || "未知错误";
    return `${moduleName} ${key} 触发失败：${reason}`;
}

function renderPlaybackTestLevel(level) {
    const normalized = clamp01(Number(level.level || 0));
    els.playbackTestLevelFill.style.width = `${Math.round(normalized * 100)}%`;
    els.playbackTestLevelText.textContent = `RMS ${formatLevel(level.rms)} · 峰值 ${formatLevel(level.peak)}`;
}

async function updateRobotTranscript() {
    if (!isRecording || activeVoiceMode !== "robot") {
        return;
    }
    const response = await fetch("/api/robot-mic/transcript");
    const transcript = await response.json();
    if (!response.ok || transcript.error) {
        renderRobotTranscript(transcript);
        return;
    }
    renderRobotTranscript(transcript);
}

async function updateDebugInfo() {
    const response = await fetch("/api/robot-mic/debug");
    const debug = await response.json();
    if (!response.ok || debug.error) {
        throw new Error(debug.detail || debug.error?.message || "读取诊断信息失败");
    }
    renderDebugInfo(debug);
}

function renderDebugInfo(debug) {
    els.debugInfo.textContent = JSON.stringify(debug, null, 2);
}

function renderRobotTranscript(transcript) {
    if (transcript.error) {
        els.liveTranscript.textContent = transcript.error;
        return;
    }
    const text = String(transcript.text || "").trim();
    els.liveTranscript.textContent = text || (isRecording ? "正在听..." : "等待录音");
}

function currentVoiceMode() {
    return appMode.web_only ? "local" : (els.voiceInputMode.value || "robot");
}

function resetLocalRecordingControls() {
    activeVoiceMode = null;
    localSessionId = null;
    els.recordBtn.textContent = "开始录音";
    els.recordBtn.disabled = false;
    els.recordBtn.classList.remove("recording");
    els.localAbortBtn.disabled = true;
    els.sendTextBtn.disabled = false;
    els.recordingTime.textContent = "00:00";
    if (!appMode.web_only) {
        els.playbackTestBtn.disabled = false;
    }
}

function stopLocalAudioGraph() {
    if (localProcessorNode) {
        localProcessorNode.disconnect();
    }
    if (localSourceNode) {
        localSourceNode.disconnect();
    }
    if (localMediaStream) {
        localMediaStream.getTracks().forEach((track) => track.stop());
    }
    if (localAudioContext) {
        localAudioContext.close();
    }
    localProcessorNode = null;
    localSourceNode = null;
    localMediaStream = null;
    localAudioContext = null;
}

function enqueueLocalPcm(bytes) {
    localPendingBytes = concatBytes(localPendingBytes, bytes);
    while (localPendingBytes.length >= LOCAL_CHUNK_BYTES) {
        localSendQueue.push(localPendingBytes.slice(0, LOCAL_CHUNK_BYTES));
        localPendingBytes = localPendingBytes.slice(LOCAL_CHUNK_BYTES);
    }
}

async function sendLocalMicLoop() {
    while (isRecording || localSendQueue.length > 0) {
        const chunk = localSendQueue.shift();
        if (!chunk) {
            await sleep(25);
            continue;
        }
        const response = await fetch("/api/local-mic/chunk", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: localSessionId,
                audio_base64: bytesToBase64(chunk),
                is_final: false,
            }),
        });
        const payload = await response.json();
        if (!response.ok || payload.error || !payload.ok) {
            throw new Error(payload.detail || payload.error?.message || "本机音频块发送失败");
        }
        localSentChunks += 1;
        localSentBytes += chunk.length;
        localAcceptedBytes += Number(payload.accepted_bytes || 0);
        renderDebugInfo(localMicDebug());
    }
}

async function pollLocalTranscript() {
    if (!localSessionId || !isRecording || activeVoiceMode !== "local") {
        return;
    }
    const response = await fetch(`/api/local-mic/transcript?session_id=${encodeURIComponent(localSessionId)}`);
    const payload = await response.json();
    if (!response.ok || payload.error) {
        throw new Error(payload.detail || payload.error?.message || "读取本机实时字幕失败");
    }
    els.liveTranscript.textContent = payload.transcript || "正在听电脑麦克风...";
    renderDebugInfo(localMicDebug({
        transcript: payload.transcript,
        is_final: payload.is_final,
    }));
}

function updateLocalMicLevel(samples) {
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

function localMicDebug(extra = {}) {
    return {
        mode: "local-mic",
        session_id: localSessionId,
        is_recording: isRecording && activeVoiceMode === "local",
        queued_chunks: localSendQueue.length,
        pending_bytes: localPendingBytes.length,
        sent_chunks: localSentChunks,
        sent_bytes: localSentBytes,
        accepted_bytes: localAcceptedBytes,
        ...extra,
    };
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
        for (let j = start; j < end; j += 1) {
            sum += input[j];
        }
        output[i] = sum / Math.max(1, end - start);
    }
    return floatToPcm16(output);
}

function floatToPcm16(samples) {
    const bytes = new Uint8Array(samples.length * 2);
    const view = new DataView(bytes.buffer);
    for (let i = 0; i < samples.length; i += 1) {
        const clamped = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(
            i * 2,
            clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff,
            true,
        );
    }
    return bytes;
}

async function ensureLocalPlaybackContext() {
    if (!localPlaybackContext) {
        localPlaybackContext = new AudioContext();
        localPlaybackNextTime = localPlaybackContext.currentTime;
    }
    if (localPlaybackContext.state === "suspended") {
        await localPlaybackContext.resume();
    }
}

function playLocalPcmBytes(bytes, sampleRate) {
    if (!bytes.length || !localPlaybackContext) {
        return 0;
    }
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const sampleCount = Math.floor(bytes.length / 2);
    const buffer = localPlaybackContext.createBuffer(1, sampleCount, sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < sampleCount; i += 1) {
        channel[i] = view.getInt16(i * 2, true) / 32768;
    }
    const source = localPlaybackContext.createBufferSource();
    source.buffer = buffer;
    source.connect(localPlaybackContext.destination);
    const currentTime = localPlaybackContext.currentTime;
    const startAt = localPlaybackNextTime > currentTime
        ? localPlaybackNextTime
        : currentTime + LOCAL_PLAYBACK_START_BUFFER_SECONDS;
    localPlaybackNextTime = startAt + buffer.duration;
    source.onended = () => source.disconnect();
    source.start(startAt);
    return localPlaybackNextTime;
}

async function readSseStream(response, onEvent) {
    if (!response.body) {
        throw new Error("浏览器不支持流式响应读取");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
        const { value, done } = await reader.read();
        if (done) {
            break;
        }
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split(/\n\n/);
        buffer = frames.pop() || "";
        for (const frame of frames) {
            const event = parseSseFrame(frame);
            if (event) {
                onEvent(event);
            }
        }
    }

    buffer += decoder.decode();
    const tail = buffer.trim();
    if (tail) {
        const event = parseSseFrame(tail);
        if (event) {
            onEvent(event);
        }
    }
}

function parseSseFrame(frame) {
    let event = "message";
    const dataLines = [];
    for (const rawLine of frame.split(/\n/)) {
        const line = rawLine.replace(/\r$/, "");
        if (!line || line.startsWith(":")) {
            continue;
        }
        if (line.startsWith("event:")) {
            event = line.slice("event:".length).trim() || "message";
            continue;
        }
        if (line.startsWith("data:")) {
            dataLines.push(line.slice("data:".length).trimStart());
        }
    }
    if (dataLines.length === 0) {
        return null;
    }
    const payload = dataLines.join("\n");
    let data;
    try {
        data = JSON.parse(payload);
    } catch {
        data = { text: payload };
    }
    return { event, data };
}

function normalizedConversationId() {
    return (els.conversationId.value || "").trim();
}

function bytesToBase64(bytes) {
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary);
}

function base64ToBytes(value) {
    if (!value) {
        return new Uint8Array(0);
    }
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
}

function playbackKeyFromPayload(payload) {
    const requestId = payloadString(payload, "request_id")
        || payloadString(payload, "parent_request_id");
    const turnId = payloadString(payload, "followup_turn_id")
        || payloadString(payload, "assistant_turn_id")
        || payloadString(payload, "reply_turn_id")
        || payloadString(payload, "turn_id");
    if (requestId && turnId) {
        return `request:${requestId}:turn:${turnId}`;
    }
    if (requestId) {
        return `request:${requestId}`;
    }
    const conversationId = payloadString(payload, "conversation_id");
    if (conversationId && turnId) {
        return `conversation:${conversationId}:turn:${turnId}`;
    }
    return null;
}

function payloadString(payload, key) {
    const value = payload?.[key];
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

function optionalNumber(value) {
    if (value === null || value === undefined || value === "") {
        return null;
    }
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function concatBytes(a, b) {
    const out = new Uint8Array(a.length + b.length);
    out.set(a, 0);
    out.set(b, a.length);
    return out;
}

function makeId(prefix) {
    if (window.crypto?.randomUUID) {
        return `${prefix}_${window.crypto.randomUUID()}`;
    }
    return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function setStatus(text) {
    els.statusLine.textContent = text;
}

function describeError(error) {
    const message = error.message || String(error);
    if (message.includes("speech recognition produced an empty transcript") && lastRecordingStats) {
        return `语音识别返回了空文本。录音指标：${formatSeconds(lastRecordingStats.duration_seconds)}，RMS ${formatLevel(lastRecordingStats.rms)}，峰值 ${formatLevel(lastRecordingStats.peak)}。如果 RMS 很低，机器人麦克风没有收到清晰人声；如果 RMS 正常，请检查识别服务的密钥、语言和采样率配置。`;
    }
    return message;
}

function formatSeconds(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
        return "--";
    }
    return `${number.toFixed(1)}s`;
}

function formatLevel(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
        return "--";
    }
    return number.toFixed(5);
}

function clamp01(value) {
    if (!Number.isFinite(value)) {
        return 0;
    }
    return Math.min(1, Math.max(0, value));
}

function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}

els.healthBtn.addEventListener("click", () => {
    checkHealth()
        .then(connectFollowups)
        .catch((error) => setStatus(error.message || String(error)));
});

els.recordBtn.addEventListener("click", () => {
    toggleRecording().catch((error) => setStatus(error.message || String(error)));
});

els.autoVoiceBtn.addEventListener("click", () => {
    toggleAutoVoice().catch((error) => setStatus(error.message || String(error)));
});

els.localAbortBtn.addEventListener("click", () => {
    abortLocalRecording().catch((error) => setStatus(error.message || String(error)));
});

els.voiceInputMode.addEventListener("change", () => {
    if (els.voiceInputMode.value === "local") {
        els.liveTranscript.textContent = "电脑麦克风准备就绪";
    } else {
        els.liveTranscript.textContent = "等待录音";
    }
});

els.playbackTestBtn.addEventListener("click", () => {
    togglePlaybackTest().catch((error) => setStatus(error.message || String(error)));
});

els.sendTextBtn.addEventListener("click", () => {
    sendManualText().catch((error) => setStatus(error.message || String(error)));
});

els.manualText.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        sendManualText().catch((error) => setStatus(error.message || String(error)));
    }
});

els.refreshVolumeBtn.addEventListener("click", () => {
    loadVolumeControls().catch((error) => setStatus(error.message || String(error)));
});

els.pendingFollowupsBtn.addEventListener("click", () => {
    loadPendingFollowups();
});

els.curateMemoryBtn.addEventListener("click", () => {
    runMemoryAction("/api/memory/curate", "记忆整理");
});

els.refreshProfileBtn.addEventListener("click", () => {
    runMemoryAction("/api/memory/profile/refresh", "画像刷新");
});

els.speakerVolume.addEventListener("input", () => {
    els.speakerVolumeValue.textContent = `${els.speakerVolume.value}%`;
});

els.microphoneVolume.addEventListener("input", () => {
    els.microphoneVolumeValue.textContent = `${els.microphoneVolume.value}%`;
});

els.speakerVolume.addEventListener("change", () => {
    setVolume("speaker", els.speakerVolume.value)
        .catch((error) => setStatus(error.message || String(error)));
});

els.microphoneVolume.addEventListener("change", () => {
    setVolume("microphone", els.microphoneVolume.value)
        .catch((error) => setStatus(error.message || String(error)));
});

for (const input of [els.serviceUrl, els.conversationId, els.ttsSampleRate]) {
    input.addEventListener("change", () => {
        saveSettings()
            .then(() => {
                if (input === els.serviceUrl || input === els.conversationId) {
                    connectFollowups();
                }
            })
            .catch((error) => setStatus(error.message || String(error)));
    });
}

renderTimeline();
renderInspector();
loadSettings()
    .then(async () => {
        applyAppMode(await loadAppMode());
        await checkHealth();
        connectFollowups();
        if (!appMode.web_only) {
            await loadVolumeControls();
        }
    })
    .catch((error) => {
        setStatus(error.message || String(error));
        connectFollowups();
    });
