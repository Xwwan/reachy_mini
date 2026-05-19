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

const els = {
    serviceUrl: document.getElementById("service-url"),
    conversationId: document.getElementById("conversation-id"),
    gesture: document.getElementById("gesture"),
    ttsSampleRate: document.getElementById("tts-sample-rate"),
    healthBtn: document.getElementById("health-btn"),
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
    if (isPlaybackTestRecording) {
        throw new Error("请先停止机器人麦克风回放测试。");
    }
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
        const response = await fetch("/api/robot-mic/stop-stream", { method: "POST" });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.detail || payload.error?.message || "机器人麦克风录音失败");
        }

        let reply = "";
        let transcript = "";
        let finalPayload = null;
        let audioChunkCount = 0;
        els.reply.textContent = "";
        setStatus("正在生成回复...");

        await readSseStream(response, ({ event, data }) => {
            if (event === "recording") {
                lastRecordingStats = data;
                renderMicLevel(data);
                return;
            }
            if (event === "debug") {
                renderDebugInfo(data);
                return;
            }
            if (event === "transcript") {
                transcript = data.transcript || transcript;
                renderRobotTranscript({ text: transcript, error: null });
                els.transcript.textContent = transcript || "(未识别到文本)";
                setStatus("已识别语音，正在流式生成回复...");
                return;
            }
            if (event === "meta") {
                setStatus("已连接模型流");
                return;
            }
            if (event === "delta") {
                reply += data.delta || "";
                els.reply.textContent = reply || " ";
                return;
            }
            if (event === "audio") {
                audioChunkCount += 1;
                setStatus(`正在接收 TTS 音频 ${audioChunkCount}`);
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
                finalPayload = data;
                transcript = data.transcript || transcript;
                reply = data.reply || reply;
                els.transcript.textContent = transcript || "(未识别到文本)";
                els.reply.textContent = reply || "(没有回复)";
                setStatus(
                    data.audio_base64 || audioChunkCount > 0
                        ? "正在播放机器人回复..."
                        : "流式回复完成",
                );
                return;
            }
            if (event === "playback_done") {
                setStatus("机器人回复完成");
                return;
            }
            if (event === "error") {
                throw new Error(data.message || "流式回复失败");
            }
        });

        if (!finalPayload && !reply) {
            els.reply.textContent = "(没有回复)";
        }
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

function updatePlaybackTestTimer() {
    const seconds = Math.floor((Date.now() - playbackTestStartedAt) / 1000);
    const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
    const rest = String(seconds % 60).padStart(2, "0");
    els.playbackTestTime.textContent = `${minutes}:${rest}`;
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

function renderPlaybackTestLevel(level) {
    const normalized = clamp01(Number(level.level || 0));
    els.playbackTestLevelFill.style.width = `${Math.round(normalized * 100)}%`;
    els.playbackTestLevelText.textContent = `RMS ${formatLevel(level.rms)} · 峰值 ${formatLevel(level.peak)}`;
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

els.playbackTestBtn.addEventListener("click", () => {
    togglePlaybackTest().catch((error) => setStatus(error.message || String(error)));
});

for (const input of [els.serviceUrl, els.conversationId, els.gesture, els.ttsSampleRate]) {
    input.addEventListener("change", () => {
        saveSettings().catch((error) => setStatus(error.message || String(error)));
    });
}

loadSettings()
    .then(checkHealth)
    .catch((error) => setStatus(error.message || String(error)));
