let recordingStartedAt = 0;
let timerId = null;
let levelTimerId = null;
let transcriptTimerId = null;
let isRecording = false;
let lastRecordingStats = null;

const els = {
    serviceUrl: document.getElementById("service-url"),
    conversationId: document.getElementById("conversation-id"),
    gesture: document.getElementById("gesture"),
    ttsSampleRate: document.getElementById("tts-sample-rate"),
    healthBtn: document.getElementById("health-btn"),
    recordBtn: document.getElementById("record-btn"),
    recordingTime: document.getElementById("recording-time"),
    micLevelFill: document.getElementById("mic-level-fill"),
    micLevelText: document.getElementById("mic-level-text"),
    liveTranscript: document.getElementById("live-transcript"),
    statusLine: document.getElementById("status-line"),
    connectionStatus: document.getElementById("connection-status"),
    transcript: document.getElementById("transcript"),
    reply: document.getElementById("reply"),
};

async function loadSettings() {
    const response = await fetch("/api/settings");
    const settings = await response.json();
    els.serviceUrl.value = settings.service_url;
    els.conversationId.value = settings.conversation_id;
    els.gesture.value = settings.gesture;
    els.ttsSampleRate.value = settings.tts_sample_rate;
}

async function saveSettings() {
    const body = {
        service_url: els.serviceUrl.value,
        conversation_id: els.conversationId.value,
        gesture: els.gesture.value,
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

async function toggleRecording() {
    if (isRecording) {
        await stopRecording();
        return;
    }
    await startRecording();
}

async function startRecording() {
    await saveSettings();
    lastRecordingStats = null;
    const response = await fetch("/api/robot-mic/start", { method: "POST" });
    const result = await response.json();
    if (!response.ok || result.error) {
        throw new Error(result.detail || result.error?.message || "机器人麦克风启动失败");
    }
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
    els.recordBtn.textContent = "停止并发送";
    els.recordBtn.classList.add("recording");
    setStatus("正在用机器人麦克风录音");
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
    try {
        const recordingResponse = await fetch("/api/robot-mic/stop", { method: "POST" });
        const recording = await recordingResponse.json();
        if (!recordingResponse.ok || recording.error) {
            throw new Error(recording.detail || recording.error?.message || "机器人麦克风录音失败");
        }
        lastRecordingStats = recording;
        renderMicLevel(recording);
        renderRobotTranscript({
            text: recording.live_transcript,
            error: recording.live_transcript_error,
        });
        els.transcript.textContent = recording.transcript || recording.live_transcript || "(未识别到文本)";
        els.reply.textContent = recording.reply || "(没有回复)";
        setStatus("已发送给机器人播放");
    } catch (error) {
        setStatus(describeError(error));
    } finally {
        els.recordBtn.textContent = "开始录音";
        els.recordBtn.disabled = false;
        els.recordingTime.textContent = "00:00";
    }
}

function updateTimer() {
    const seconds = Math.floor((Date.now() - recordingStartedAt) / 1000);
    const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
    const rest = String(seconds % 60).padStart(2, "0");
    els.recordingTime.textContent = `${minutes}:${rest}`;
}

async function updateMicLevel() {
    if (!isRecording) {
        return;
    }
    const response = await fetch("/api/robot-mic/level");
    const level = await response.json();
    if (!response.ok || level.error) {
        throw new Error(level.detail || level.error?.message || "读取麦克风音量失败");
    }
    renderMicLevel(level);
}

function renderMicLevel(level) {
    const normalized = clamp01(Number(level.level || 0));
    els.micLevelFill.style.width = `${Math.round(normalized * 100)}%`;
    els.micLevelText.textContent = `RMS ${formatLevel(level.rms)} · 峰值 ${formatLevel(level.peak)}`;
}

async function updateRobotTranscript() {
    if (!isRecording) {
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

function renderRobotTranscript(transcript) {
    if (transcript.error) {
        els.liveTranscript.textContent = transcript.error;
        return;
    }
    const text = String(transcript.text || "").trim();
    els.liveTranscript.textContent = text || (isRecording ? "正在听..." : "等待录音");
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

els.healthBtn.addEventListener("click", () => {
    checkHealth().catch((error) => setStatus(error.message || String(error)));
});

els.recordBtn.addEventListener("click", () => {
    toggleRecording().catch((error) => setStatus(error.message || String(error)));
});

for (const input of [els.serviceUrl, els.conversationId, els.gesture, els.ttsSampleRate]) {
    input.addEventListener("change", () => {
        saveSettings().catch((error) => setStatus(error.message || String(error)));
    });
}

loadSettings()
    .then(checkHealth)
    .catch((error) => setStatus(error.message || String(error)));
