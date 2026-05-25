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
    if (id === "service-url") {
        element.value = "http://127.0.0.1:12312";
    } else if (id === "conversation-id") {
        element.value = "stream-test";
    } else if (id === "workflow") {
        element.value = "chat";
    } else if (id === "tts-enabled") {
        element.checked = true;
    } else if (id === "message-input") {
        element.value = "";
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

function makeSseResponse(frames) {
    const encoder = new TextEncoder();
    let index = 0;
    return {
        ok: true,
        status: 200,
        json: async () => ({}),
        body: {
            getReader() {
                return {
                    read() {
                        if (index >= frames.length) {
                            return Promise.resolve({ done: true, value: undefined });
                        }
                        const value = encoder.encode(frames[index]);
                        index += 1;
                        return Promise.resolve({ done: false, value });
                    },
                };
            },
        },
    };
}

function frame(event, data) {
    return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function createSandbox() {
    const elements = new Map();
    const calls = [];

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

    const fetch = async (url, options = {}) => {
        const pathOnly = String(url).split("?")[0];
        const method = options.method || "GET";
        const body = options.body ? JSON.parse(options.body) : null;
        calls.push({ url: String(url), path: pathOnly, method, body });

        if (pathOnly === "/api/settings" && method === "GET") {
            return makeJsonResponse({
                service_url: "http://127.0.0.1:12312",
                conversation_id: "stream-test",
                tts_sample_rate: 24000,
            });
        }
        if (pathOnly === "/api/settings" && method === "POST") {
            return makeJsonResponse({
                service_url: body.service_url,
                conversation_id: body.conversation_id,
                tts_sample_rate: 24000,
            });
        }
        if (pathOnly === "/api/app-mode") {
            return makeJsonResponse({ web_only: false });
        }
        if (pathOnly === "/api/health") {
            return makeJsonResponse({ ok: true, service_url: "http://127.0.0.1:12312" });
        }
        if (pathOnly === "/api/interaction/session") {
            assert.equal(method, "POST");
            assert.equal(body.workflow, "chat");
            assert.equal(body.conversation_id, "stream-test");
            assert.equal(body.tts_enabled, true);
            assert.ok(["text", "robot"].includes(body.input_mode));
            return makeJsonResponse({
                interaction_session_id: "isess_1",
                workflow: "chat",
                conversation_id: "stream-test",
                input_mode: body.input_mode,
                status: "active",
            });
        }
        if (pathOnly === "/api/interaction/text-stream") {
            assert.equal(method, "POST");
            assert.equal(body.interaction_session_id, "isess_1");
            assert.equal(body.workflow, "chat");
            assert.equal(body.message, "测试一下");
            assert.equal(body.tts_enabled, true);
            return makeSseResponse([
                frame("meta", {
                    workflow: "chat",
                    interaction_session_id: "isess_1",
                    run_id: "irun_1",
                    request_id: "req_1",
                }),
                frame("delta", {
                    workflow: "chat",
                    interaction_session_id: "isess_1",
                    run_id: "irun_1",
                    delta: "你好",
                }),
                frame("delta", {
                    workflow: "chat",
                    interaction_session_id: "isess_1",
                    run_id: "irun_1",
                    delta: "世界",
                }),
                frame("audio", {
                    workflow: "chat",
                    interaction_session_id: "isess_1",
                    run_id: "irun_1",
                    playback_key: "chat-tts-irun_1",
                    audio_base64: "AQIDBA==",
                    sample_rate: 24000,
                }),
                frame("done", {
                    workflow: "chat",
                    interaction_session_id: "isess_1",
                    run_id: "irun_1",
                    request_id: "req_1",
                    playback_key: "chat-tts-irun_1",
                    transcript: "测试一下",
                    reply: "你好世界",
                    status: "completed",
                }),
                frame("playback_done", {
                    ok: true,
                    run_id: "irun_1",
                    playback_key: "chat-tts-irun_1",
                }),
            ]);
        }
        if (pathOnly === "/api/robot-mic/start-interaction") {
            assert.equal(method, "POST");
            assert.equal(body.interaction_session_id, "isess_1");
            assert.equal(body.workflow, "chat");
            return makeJsonResponse({ ok: true, status: "recording" });
        }
        if (pathOnly === "/api/robot-mic/finish-interaction-stream") {
            assert.equal(method, "POST");
            assert.equal(body.tts_enabled, true);
            return makeSseResponse([
                frame("transcript", {
                    workflow: "chat",
                    interaction_session_id: "isess_1",
                    run_id: "irun_voice",
                    transcript: "语音测试",
                    is_final: true,
                }),
                frame("delta", {
                    workflow: "chat",
                    interaction_session_id: "isess_1",
                    run_id: "irun_voice",
                    delta: "收到",
                }),
                frame("done", {
                    workflow: "chat",
                    interaction_session_id: "isess_1",
                    run_id: "irun_voice",
                    reply: "收到",
                    status: "completed",
                }),
            ]);
        }
        return makeJsonResponse({ ok: true });
    };

    const sandbox = {
        assert,
        console,
        document,
        fetch,
        navigator: {
            mediaDevices: {
                getUserMedia: async () => ({ getTracks: () => [] }),
            },
        },
        AudioContext: class {
            constructor() {
                this.sampleRate = 48000;
                this.destination = {};
            }

            createMediaStreamSource() {
                return { connect() {}, disconnect() {} };
            }

            createScriptProcessor() {
                return { connect() {}, disconnect() {}, onaudioprocess: null };
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
        btoa: (value) => Buffer.from(value, "binary").toString("base64"),
        __calls: calls,
    };
    sandbox.window = sandbox;
    return sandbox;
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
            const api = window.__reachyDialogue;
            api.els.messageInput.value = "测试一下";
            await api.sendText();

            assert.equal(api.state.interactionSessionId, "isess_1");
            assert.equal(api.state.activeRunId, "irun_1");
            assert.equal(api.state.activePlaybackKey, "chat-tts-irun_1");
            assert.equal(api.state.runStatusText, "playback done");
            assert.equal(api.els.sessionId.textContent, "isess_1");
            assert.equal(api.els.runId.textContent, "irun_1");
            assert.equal(api.els.playbackKey.textContent, "chat-tts-irun_1");
            assert.ok(api.state.messages.some((message) => message.role === "user" && message.content === "测试一下"));
            assert.ok(api.state.messages.some((message) => message.role === "assistant" && message.content === "你好世界"));
            assert.ok(api.els.eventLog.textContent.includes("audio"));
            assert.equal(api.playbackKeyFromPayload({ run_id: "irun_fallback" }), "run:irun_fallback");

            await api.startRobotLive();
            assert.equal(api.state.activeLiveMode, "robot");
            assert.equal(api.els.finishLiveBtn.disabled, false);
            await api.finishLive();
            assert.equal(api.state.activeLiveMode, "");
            assert.equal(api.els.liveTranscript.textContent, "语音测试");
            assert.ok(api.state.messages.some((message) => message.role === "assistant" && message.content === "收到"));

            const sessionCalls = __calls.filter((call) => call.path === "/api/interaction/session");
            assert.equal(sessionCalls.length, 1, "text and robot voice should reuse one interaction session");
            assert.ok(__calls.some((call) => call.path === "/api/interaction/text-stream"));
            assert.ok(__calls.some((call) => call.path === "/api/robot-mic/start-interaction"));
            assert.ok(__calls.some((call) => call.path === "/api/robot-mic/finish-interaction-stream"));
        })()
    `, context);

    console.log("dialogue interaction mock test passed");
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
