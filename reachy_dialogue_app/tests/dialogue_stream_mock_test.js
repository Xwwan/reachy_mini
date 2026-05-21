#!/usr/bin/env node

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class FakeClassList {
    constructor() {
        this.values = new Set();
    }

    add(...names) {
        for (const name of names) this.values.add(name);
    }

    remove(...names) {
        for (const name of names) this.values.delete(name);
    }

    toggle(name, force) {
        const enabled = force === undefined ? !this.values.has(name) : Boolean(force);
        if (enabled) {
            this.values.add(name);
        } else {
            this.values.delete(name);
        }
        return enabled;
    }
}

class FakeElement {
    constructor(tagName = "div", id = "") {
        this.tagName = tagName.toUpperCase();
        this.id = id;
        this.children = [];
        this.listeners = new Map();
        this.classList = new FakeClassList();
        this.style = {};
        this.dataset = {};
        this.attributes = {};
        this.textContent = "";
        this.value = "";
        this.checked = false;
        this.disabled = false;
        this.type = "";
        this.className = "";
        this.scrollTop = 0;
        this.scrollHeight = 0;
        this.options = [];
    }

    append(...children) {
        this.children.push(...children);
        this.scrollHeight = this.children.length;
    }

    appendChild(child) {
        this.append(child);
        return child;
    }

    replaceChildren(...children) {
        this.children = [...children];
        this.scrollHeight = this.children.length;
    }

    addEventListener(type, handler) {
        if (!this.listeners.has(type)) this.listeners.set(type, []);
        this.listeners.get(type).push(handler);
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    querySelector() {
        return new FakeElement("div");
    }
}

function createElementForId(id) {
    const element = new FakeElement("div", id);
    if (id === "voice-input-mode") {
        element.value = "local";
        element.options = [{ value: "local", disabled: false }, { value: "robot", disabled: false }];
    } else if (id === "text-tts-enabled") {
        element.checked = true;
    } else if (id === "service-url") {
        element.value = "http://127.0.0.1:12312";
    } else if (id === "conversation-id") {
        element.value = "stream-test";
    } else if (id === "tts-sample-rate") {
        element.value = "24000";
    } else if (id === "speaker-volume" || id === "microphone-volume") {
        element.value = "50";
    }
    return element;
}

function makeJsonResponse(payload, ok = true) {
    return {
        ok,
        status: ok ? 200 : 500,
        json: async () => payload,
    };
}

function createSandbox() {
    const elements = new Map();
    const eventSourceUrls = [];

    class FakeEventSource {
        constructor(url) {
            this.url = url;
            this.readyState = 0;
            this.listeners = new Map();
            eventSourceUrls.push(url);
        }

        addEventListener(type, handler) {
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
        }

        close() {
            this.readyState = 2;
        }
    }

    const document = {
        body: new FakeElement("body"),
        createElement: (tagName) => new FakeElement(tagName),
        getElementById: (id) => {
            if (!elements.has(id)) elements.set(id, createElementForId(id));
            return elements.get(id);
        },
        querySelector: (selector) => {
            if (!elements.has(selector)) elements.set(selector, new FakeElement("div", selector));
            return elements.get(selector);
        },
    };

    const fetch = async (url) => {
        const pathOnly = String(url).split("?")[0];
        if (pathOnly === "/api/settings") {
            return makeJsonResponse({
                service_url: "http://127.0.0.1:12312",
                conversation_id: "stream-test",
                tts_sample_rate: 24000,
            });
        }
        if (pathOnly === "/api/app-mode") {
            return makeJsonResponse({ web_only: true });
        }
        if (pathOnly === "/api/health") {
            return makeJsonResponse({ ok: true, service_url: "http://127.0.0.1:12312" });
        }
        if (pathOnly === "/api/audio-volume") {
            return makeJsonResponse({
                speaker: { volume: null, available: false },
                microphone: { volume: null, available: false },
            });
        }
        return makeJsonResponse({ ok: true });
    };

    const sandbox = {
        assert,
        console,
        document,
        EventSource: FakeEventSource,
        fetch,
        navigator: {
            mediaDevices: {
                getUserMedia: async () => ({ getTracks: () => [] }),
            },
        },
        AudioContext: class {
            constructor() {
                this.currentTime = 0;
                this.sampleRate = 48000;
                this.state = "running";
                this.destination = {};
            }

            createBuffer() {
                return {
                    duration: 0,
                    getChannelData: () => new Float32Array(1),
                };
            }

            createBufferSource() {
                return {
                    connect() {},
                    disconnect() {},
                    start() {},
                    set buffer(value) {
                        this._buffer = value;
                    },
                };
            }

            createMediaStreamSource() {
                return { connect() {}, disconnect() {} };
            }

            createScriptProcessor() {
                return { connect() {}, disconnect() {}, onaudioprocess: null };
            }

            resume() {
                return Promise.resolve();
            }

            close() {
                return Promise.resolve();
            }
        },
        URLSearchParams,
        TextDecoder,
        TextEncoder,
        Uint8Array,
        DataView,
        Float32Array,
        Map,
        Set,
        Date,
        JSON,
        Math,
        Number,
        String,
        Boolean,
        Promise,
        Error,
        setTimeout,
        clearTimeout,
        setInterval: () => 1,
        clearInterval: () => {},
        __eventSourceUrls: eventSourceUrls,
    };
    sandbox.window = sandbox;
    return sandbox;
}

function frame(event, data) {
    return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

async function main() {
    const repoRoot = path.resolve(__dirname, "..", "..");
    const mainJsPath = path.join(
        repoRoot,
        "reachy_dialogue_app",
        "reachy_dialogue_app",
        "static",
        "main.js",
    );
    const source = fs.readFileSync(mainJsPath, "utf8");
    const sandbox = createSandbox();
    const context = vm.createContext(sandbox);

    vm.runInContext(source, context, { filename: mainJsPath });

    await vm.runInContext(`
        (async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
            await new Promise((resolve) => setTimeout(resolve, 0));
        })()
    `, context);

    await vm.runInContext(`
        (async () => {
            function resetState() {
                chatState.messages = [];
                chatState.requests = new Map();
                chatState.eventLog = [];
                chatState.seenFollowups = new Set();
                chatState.followupMessages = new Map();
                chatState.followupPlaybackGroups = new Map();
                chatState.followupPlaybackKey = null;
                chatState.followupPlaybackFallbackKey = null;
                appMode = { web_only: true };
                els.voiceInputMode.value = "local";
                els.textTtsEnabled.checked = true;
                els.transcript.textContent = "";
                els.reply.textContent = "";
                renderTimeline();
                renderInspector();
            }

            function installPlaybackSpy() {
                const calls = [];
                const activeGroups = new Set();
                function resolvePlaybackKey(data, fallbackKey) {
                    if (fallbackKey && activeGroups.has(fallbackKey)) {
                        return fallbackKey;
                    }
                    return playbackKeyFromPayload(data) || fallbackKey || "fallback-audio";
                }
                localAudioPlaybackScheduler.enqueueAudio = (data, options = {}) => {
                    const key = resolvePlaybackKey(data, options.fallbackKey);
                    activeGroups.add(key);
                    calls.push({
                        type: "audio",
                        key,
                        data,
                    });
                    return calls[calls.length - 1].key;
                };
                localAudioPlaybackScheduler.complete = (data, options = {}) => {
                    const key = resolvePlaybackKey(data, options.fallbackKey);
                    calls.push({
                        type: "complete",
                        key,
                        data,
                    });
                    activeGroups.delete(key);
                    return calls[calls.length - 1].key;
                };
                localAudioPlaybackScheduler.abort = (key) => {
                    calls.push({ type: "abort", key });
                    activeGroups.delete(key);
                };
                return calls;
            }

            function makeSseResponse(events) {
                const encoder = new TextEncoder();
                let index = 0;
                return {
                    body: {
                        getReader() {
                            return {
                                read() {
                                    if (index >= events.length) {
                                        return Promise.resolve({ done: true, value: undefined });
                                    }
                                    const value = encoder.encode(events[index]);
                                    index += 1;
                                    return Promise.resolve({ done: false, value });
                                },
                            };
                        },
                    },
                };
            }

            resetState();
            const textPlayback = installPlaybackSpy();
            els.manualText.value = "测试一下";
            fetch = async (url, options = {}) => {
                if (String(url) === "/api/settings") {
                    return {
                        ok: true,
                        json: async () => ({
                            service_url: "http://127.0.0.1:12312/",
                            conversation_id: "stream-test",
                            tts_sample_rate: 24000,
                        }),
                    };
                }
                if (String(url) === "/api/text-chat-stream" && options.method === "POST") {
                    const body = JSON.parse(options.body);
                    assert.equal(body.conversation_id, "stream-test");
                    assert.equal(body.text, "测试一下");
                    assert.equal(body.tts_enabled, true);
                    return {
                        ok: true,
                        json: async () => ({}),
                        body: makeSseResponse([
                            ${JSON.stringify(frame("transcript", {
                                conversation_id: "stream-test",
                                transcript: "测试一下",
                            }))},
                            ${JSON.stringify(frame("meta", {
                                request_id: "req-initial",
                                conversation_id: "stream-test",
                                user_turn_id: "u1",
                                assistant_turn_id: "a1",
                            }))},
                            ${JSON.stringify(frame("delta", {
                                request_id: "req-initial",
                                assistant_turn_id: "a1",
                                delta: "你好",
                            }))},
                            ${JSON.stringify(frame("delta", {
                                request_id: "req-initial",
                                assistant_turn_id: "a1",
                                delta: "世界",
                            }))},
                            ${JSON.stringify(frame("audio", {
                                request_id: "req-initial",
                                assistant_turn_id: "a1",
                                audio_base64: "AQIDBA==",
                                sample_rate: 24000,
                                chunk_index: 0,
                            }))},
                            ${JSON.stringify(frame("done", {
                                request_id: "req-initial",
                                conversation_id: "stream-test",
                                transcript: "测试一下",
                                reply: "你好世界",
                                user_turn_id: "u1",
                                assistant_turn_id: "a1",
                            }))},
                            ${JSON.stringify(frame("playback_done", {
                                ok: true,
                            }))},
                        ]).body,
                    };
                }
                return {
                    ok: true,
                    json: async () => ({ ok: true }),
                };
            };
            await sendManualText();

            assert.equal(els.reply.textContent, "你好世界");
            assert.equal(els.transcript.textContent, "测试一下");
            assert.equal(chatState.requests.get("req-initial").initialStatus, "done");
            assert.deepEqual(
                textPlayback.map((call) => call.type),
                ["audio", "complete"],
            );
            assert.equal(textPlayback[0].key, "request:req-initial:turn:a1");
            assert.equal(textPlayback[1].key, textPlayback[0].key);

            resetState();
            const followupPlayback = installPlaybackSpy();
            handleFollowupPayload({
                event: "meta",
                data: {
                    request_id: "req-followup",
                    followup_type: "supplement",
                    original_user_query: "刚才的问题",
                },
            }, "message");
            handleFollowupPayload({
                event: "delta",
                data: {
                    request_id: "req-followup",
                    followup_type: "supplement",
                    delta: "补充",
                },
            }, "message");
            handleFollowupPayload({
                event: "delta",
                data: {
                    request_id: "req-followup",
                    followup_type: "supplement",
                    delta: "内容",
                },
            }, "message");
            handleFollowupPayload({
                event: "audio",
                data: {
                    request_id: "req-followup",
                    followup_turn_id: "f1",
                    followup_type: "supplement",
                    audio_base64: "BQYHCA==",
                    sample_rate: 24000,
                    chunk_index: 0,
                },
            }, "message");
            handleFollowupPayload({
                event: "followup_done",
                data: {
                    request_id: "req-followup",
                    followup_turn_id: "f1",
                    followup_type: "supplement",
                    reply: "补充内容",
                },
            }, "message");

            const followupMessage = chatState.messages.find((message) => message.kind === "followup");
            assert.ok(followupMessage, "follow-up message should be rendered");
            assert.equal(followupMessage.content, "补充内容");
            assert.equal(followupMessage.status, "done");
            assert.deepEqual(
                followupPlayback.map((call) => call.type),
                ["audio", "complete"],
            );

            resetState();
            const donePayloadPlayback = installPlaybackSpy();
            handleFollowupPayload({
                request_id: "req-followup-done-audio",
                followup_turn_id: "f2",
                followup_type: "supplement",
                reply: "结束事件里带音频",
                audio_base64: "CQoLDA==",
                sample_rate: 24000,
            }, "done");

            assert.deepEqual(
                donePayloadPlayback.map((call) => call.type),
                ["audio", "complete"],
            );
            assert.equal(donePayloadPlayback[0].data.audio_base64, "CQoLDA==");

            resetState();
            const interleavedPlayback = installPlaybackSpy();
            handleFollowupPayload({
                event: "audio",
                data: {
                    request_id: "req-followup-a",
                    followup_turn_id: "fa",
                    audio_base64: "AQIDBA==",
                    sample_rate: 24000,
                    chunk_index: 0,
                },
            }, "message");
            handleFollowupPayload({
                event: "audio",
                data: {
                    request_id: "req-followup-b",
                    followup_turn_id: "fb",
                    audio_base64: "BQYHCA==",
                    sample_rate: 24000,
                    chunk_index: 0,
                },
            }, "message");
            handleFollowupPayload({
                event: "followup_done",
                data: {
                    request_id: "req-followup-b",
                    followup_turn_id: "fb",
                },
            }, "message");
            handleFollowupPayload({
                event: "followup_done",
                data: {
                    request_id: "req-followup-a",
                    followup_turn_id: "fa",
                },
            }, "message");

            assert.deepEqual(
                interleavedPlayback.map((call) => call.type),
                ["audio", "audio", "complete", "complete"],
            );
            assert.notEqual(
                interleavedPlayback[0].key,
                interleavedPlayback[1].key,
                "interleaved follow-up audio should use separate playback groups",
            );
            assert.equal(interleavedPlayback[2].key, interleavedPlayback[1].key);
            assert.equal(interleavedPlayback[3].key, interleavedPlayback[0].key);

            els.textTtsEnabled.checked = true;
            connectFollowups();
            assert.ok(
                __eventSourceUrls.at(-1).includes("tts_enabled=true"),
                "follow-up EventSource should request TTS when text TTS is enabled",
            );
            els.textTtsEnabled.checked = false;
            connectFollowups();
            assert.ok(
                __eventSourceUrls.at(-1).includes("tts_enabled=false"),
                "follow-up EventSource should disable TTS when text TTS is disabled",
            );
        })()
    `, context);

    console.log("dialogue stream mock test passed");
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
