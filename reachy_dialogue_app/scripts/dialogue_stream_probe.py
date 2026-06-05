#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import platform
import statistics
import sys
import time
import wave
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


DEFAULT_APP_URL = "http://127.0.0.1:8042"
DEFAULT_SERVICE_URL = "http://127.0.0.1:12312"
DEFAULT_TEXT = "请用一句话回答：今天适合和 Reachy Mini 聊些什么？"
DEFAULT_CONVERSATION_ID = "stream-probe"
DEFAULT_WORKFLOW = "chat"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Probe the real dialogue app SSE path and measure streamed audio "
            "arrival timing for Mac/Raspberry Pi comparison."
        )
    )
    parser.add_argument("--label", default="", help="Short label, e.g. mac or pi")
    parser.add_argument("--app-url", default=DEFAULT_APP_URL)
    parser.add_argument("--service-url", default=None)
    parser.add_argument(
        "--direct-service",
        action="store_true",
        help=(
            "Bypass the local dialogue app and probe the Interaction service "
            "SSE endpoint directly."
        ),
    )
    parser.add_argument("--conversation-id", default=DEFAULT_CONVERSATION_ID)
    parser.add_argument("--workflow", choices=("chat", "onboarding"), default=DEFAULT_WORKFLOW)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--print-chunks",
        action="store_true",
        help="Print one timing row per received PCM audio chunk.",
    )
    parser.add_argument(
        "--chunks-csv",
        type=Path,
        default=None,
        help="Write one timing row per received PCM audio chunk to a CSV file.",
    )
    parser.add_argument(
        "--save-audio",
        type=Path,
        default=None,
        help="Save the real received TTS PCM chunks as a WAV file.",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BASELINE_JSON", "CANDIDATE_JSON"),
        help="Compare two saved probe outputs instead of running a new probe.",
    )
    args = parser.parse_args()

    if args.compare:
        baseline = _read_json(Path(args.compare[0]))
        candidate = _read_json(Path(args.compare[1]))
        print(_format_comparison(baseline, candidate))
        return

    result = run_probe(
        label=args.label,
        app_url=args.app_url,
        service_url=args.service_url,
        direct_service=args.direct_service,
        conversation_id=args.conversation_id,
        workflow=args.workflow,
        text=args.text,
        tts_enabled=not args.no_tts,
        timeout=args.timeout,
        save_audio=args.save_audio,
    )
    if args.chunks_csv is not None:
        _write_chunk_csv(result["audio_chunks"], args.chunks_csv)
    encoded = json.dumps(result, indent=2, ensure_ascii=False)
    print(encoded)
    if args.print_chunks:
        print()
        print(_format_chunk_table(result["audio_chunks"]))
    if args.output is not None:
        args.output.write_text(encoded + "\n", encoding="utf-8")


def run_probe(
    *,
    label: str,
    app_url: str,
    service_url: str | None,
    direct_service: bool,
    conversation_id: str,
    workflow: str,
    text: str,
    tts_enabled: bool,
    timeout: float,
    save_audio: Path | None,
) -> dict[str, Any]:
    app_url = _with_trailing_slash(app_url)
    if direct_service:
        service_url = _with_trailing_slash(service_url or DEFAULT_SERVICE_URL)
        session_url = urljoin(service_url, "interaction/sessions")
        stream_url = urljoin(service_url, "interaction/runs/text-stream")
    else:
        if service_url:
            settings_payload = {
                "service_url": service_url,
                "conversation_id": conversation_id,
            }
            settings_response = requests.post(
                urljoin(app_url, "api/settings"),
                json=settings_payload,
                timeout=10,
            )
            settings_response.raise_for_status()
        session_url = urljoin(app_url, "api/interaction/session")
        stream_url = urljoin(app_url, "api/interaction/text-stream")

    session_response = requests.post(
        session_url,
        json={
            "workflow": workflow,
            "conversation_id": conversation_id,
            "input_mode": "text",
            "tts_enabled": tts_enabled,
        },
        timeout=10,
    )
    session_response.raise_for_status()
    session_payload = session_response.json()
    interaction_session_id = session_payload["interaction_session_id"]

    payload = {
        "interaction_session_id": interaction_session_id,
        "workflow": workflow,
        "message": text,
        "tts_enabled": tts_enabled,
    }
    started = time.perf_counter()
    response = requests.post(
        stream_url,
        json=payload,
        stream=True,
        timeout=(10, timeout),
    )
    response.raise_for_status()

    events: list[dict[str, Any]] = []
    audio_payloads: list[tuple[bytes, int]] = []
    try:
        for event in _iter_sse_events(response):
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            audio_info = _audio_info(data)
            audio_payload = _audio_payload(data)
            if audio_payload is not None:
                audio_payloads.append(audio_payload)
            events.append(
                {
                    "event": event["event"],
                    "elapsed_ms": elapsed_ms,
                    "audio": audio_info,
                    "data_keys": sorted(data),
                }
            )
    finally:
        response.close()

    total_elapsed_ms = (time.perf_counter() - started) * 1000.0
    saved_audio = _save_audio_payloads(audio_payloads, save_audio) if save_audio else None
    audio_chunks = _audio_chunk_rows(events)
    return {
        "schema_version": 1,
        "label": label,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "environment": _environment_snapshot(),
        "config": {
            "app_url": app_url,
            "service_url": service_url,
            "direct_service": direct_service,
            "conversation_id": conversation_id,
            "workflow": workflow,
            "interaction_session_id": interaction_session_id,
            "text": text,
            "tts_enabled": tts_enabled,
            "timeout": timeout,
            "save_audio": str(save_audio) if save_audio else None,
        },
        "summary": _summarize(events, total_elapsed_ms),
        "audio_chunks": audio_chunks,
        "saved_audio": saved_audio,
        "events": events,
    }


def _iter_sse_events(response: requests.Response):
    event_name = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines(chunk_size=8192, decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                yield {
                    "event": event_name,
                    "data": _parse_json("\n".join(data_lines)),
                }
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip() or "message"
            continue
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    if data_lines:
        yield {"event": event_name, "data": _parse_json("\n".join(data_lines))}


def _audio_info(data: dict[str, Any]) -> dict[str, Any] | None:
    payload = _audio_payload(data)
    if payload is None:
        return None
    audio_bytes, sample_rate = payload
    return {
        "byte_count": len(audio_bytes),
        "sample_rate": sample_rate,
        "duration_ms": len(audio_bytes) / (2.0 * sample_rate) * 1000.0,
        "chunk_index": _optional_int(data.get("chunk_index")),
        "segment_index": _optional_int(data.get("segment_index")),
        "playback_key": _optional_string(data.get("playback_key")),
        "run_id": _optional_string(data.get("run_id")),
    }


def _audio_payload(data: dict[str, Any]) -> tuple[bytes, int] | None:
    audio_base64 = data.get("audio_base64")
    if not isinstance(audio_base64, str) or not audio_base64:
        return None
    sample_rate = _int(data.get("sample_rate") or data.get("audio_sample_rate"), 24000)
    try:
        audio_bytes = base64.b64decode(audio_base64, validate=False)
    except Exception:
        return None
    return audio_bytes, sample_rate


def _save_audio_payloads(
    audio_payloads: list[tuple[bytes, int]],
    path: Path,
) -> dict[str, Any]:
    if not audio_payloads:
        return {
            "path": str(path),
            "ok": False,
            "reason": "no audio chunks received",
        }
    sample_rates = {sample_rate for _, sample_rate in audio_payloads}
    if len(sample_rates) != 1:
        return {
            "path": str(path),
            "ok": False,
            "reason": f"mixed sample rates: {sorted(sample_rates)}",
        }
    sample_rate = sample_rates.pop()
    audio_bytes = b"".join(payload for payload, _ in audio_payloads)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_bytes)
    return {
        "path": str(path),
        "ok": True,
        "sample_rate": sample_rate,
        "byte_count": len(audio_bytes),
        "duration_ms": len(audio_bytes) / (2.0 * sample_rate) * 1000.0,
        "chunk_count": len(audio_payloads),
    }


def _summarize(events: list[dict[str, Any]], total_elapsed_ms: float) -> dict[str, Any]:
    audio_events = [event for event in events if event.get("audio")]
    audio_arrivals = [float(event["elapsed_ms"]) for event in audio_events]
    audio_durations = [float(event["audio"]["duration_ms"]) for event in audio_events]
    interarrival_ms = [
        audio_arrivals[index] - audio_arrivals[index - 1]
        for index in range(1, len(audio_arrivals))
    ]
    starvation_ms = [
        max(0.0, interarrival_ms[index] - audio_durations[index - 1])
        for index in range(len(interarrival_ms))
    ]
    backlog_ms = _backlog_series(audio_arrivals, audio_durations)
    event_counts: dict[str, int] = {}
    for event in events:
        name = str(event.get("event") or "message")
        event_counts[name] = event_counts.get(name, 0) + 1
    first_audio_ms = audio_arrivals[0] if audio_arrivals else None
    return {
        "total_elapsed_ms": total_elapsed_ms,
        "event_counts": event_counts,
        "audio_event_count": len(audio_events),
        "first_audio_ms": first_audio_ms,
        "audio_duration_ms_total": sum(audio_durations),
        "audio_interarrival_ms": _stats(interarrival_ms),
        "audio_chunk_duration_ms": _stats(audio_durations),
        "starvation_ms": _stats(starvation_ms),
        "starvation_ms_total": sum(starvation_ms),
        "backlog_ms": _stats(backlog_ms),
        "final_backlog_ms": backlog_ms[-1] if backlog_ms else None,
    }


def _audio_chunk_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    backlog_ms = 0.0
    previous_arrival_ms: float | None = None
    previous_duration_ms: float | None = None
    for event_index, event in enumerate(events):
        audio = event.get("audio")
        if not isinstance(audio, dict):
            continue
        arrival_ms = float(event["elapsed_ms"])
        duration_ms = float(audio["duration_ms"])
        interarrival_ms = (
            arrival_ms - previous_arrival_ms
            if previous_arrival_ms is not None
            else None
        )
        starvation_ms = (
            max(0.0, interarrival_ms - previous_duration_ms)
            if interarrival_ms is not None and previous_duration_ms is not None
            else None
        )
        if interarrival_ms is not None:
            backlog_ms = max(0.0, backlog_ms - interarrival_ms)
        backlog_ms += duration_ms
        rows.append(
            {
                "audio_arrival_index": len(rows),
                "event_index": event_index,
                "event": event.get("event"),
                "elapsed_ms": arrival_ms,
                "interarrival_ms": interarrival_ms,
                "duration_ms": duration_ms,
                "previous_duration_ms": previous_duration_ms,
                "starvation_ms": starvation_ms,
                "backlog_ms": backlog_ms,
                "byte_count": audio.get("byte_count"),
                "sample_rate": audio.get("sample_rate"),
                "segment_index": audio.get("segment_index"),
                "chunk_index": audio.get("chunk_index"),
                "playback_key": audio.get("playback_key"),
                "run_id": audio.get("run_id"),
            }
        )
        previous_arrival_ms = arrival_ms
        previous_duration_ms = duration_ms
    return rows


def _backlog_series(arrivals_ms: list[float], durations_ms: list[float]) -> list[float]:
    backlog = 0.0
    previous_arrival = None
    series = []
    for arrival, duration in zip(arrivals_ms, durations_ms):
        if previous_arrival is not None:
            backlog = max(0.0, backlog - (arrival - previous_arrival))
        backlog += duration
        series.append(backlog)
        previous_arrival = arrival
    return series


def _format_comparison(baseline: dict[str, Any], candidate: dict[str, Any]) -> str:
    rows = [
        ("total elapsed ms", ("summary", "total_elapsed_ms")),
        ("first audio ms", ("summary", "first_audio_ms")),
        ("audio event count", ("summary", "audio_event_count")),
        ("audio duration total ms", ("summary", "audio_duration_ms_total")),
        ("interarrival mean ms", ("summary", "audio_interarrival_ms", "mean")),
        ("interarrival p95 ms", ("summary", "audio_interarrival_ms", "p95")),
        ("chunk duration mean ms", ("summary", "audio_chunk_duration_ms", "mean")),
        ("starvation total ms", ("summary", "starvation_ms_total")),
        ("starvation p95 ms", ("summary", "starvation_ms", "p95")),
        ("final backlog ms", ("summary", "final_backlog_ms")),
    ]
    baseline_label = baseline.get("label") or "baseline"
    candidate_label = candidate.get("label") or "candidate"
    lines = [
        f"Compare {baseline_label} -> {candidate_label}",
        "",
        f"{'metric':32} {'baseline':>14} {'candidate':>14} {'x baseline':>12}",
        "-" * 76,
    ]
    for name, keys in rows:
        left = _get(baseline, *keys)
        right = _get(candidate, *keys)
        lines.append(
            f"{name:32} {_fmt(left):>14} {_fmt(right):>14} {_fmt(_ratio(right, left)):>12}"
        )
    return "\n".join(lines)


def _environment_snapshot() -> dict[str, Any]:
    snapshot = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "pid": os.getpid(),
        "cwd": str(Path.cwd()),
    }
    if hasattr(os, "getloadavg"):
        try:
            snapshot["loadavg"] = list(os.getloadavg())
        except OSError:
            pass
    return snapshot


def _parse_json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "mean": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "count": len(values),
        "min": ordered[0],
        "mean": statistics.fmean(values),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


def _with_trailing_slash(value: str) -> str:
    return value.rstrip("/") + "/"


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _write_chunk_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "audio_arrival_index",
        "event_index",
        "event",
        "elapsed_ms",
        "interarrival_ms",
        "duration_ms",
        "previous_duration_ms",
        "starvation_ms",
        "backlog_ms",
        "byte_count",
        "sample_rate",
        "segment_index",
        "chunk_index",
        "playback_key",
        "run_id",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _format_chunk_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No PCM audio chunks received."
    header = (
        f"{'#':>4} {'elapsed':>10} {'gap':>10} {'dur':>10} "
        f"{'starve':>10} {'backlog':>10} {'seg':>5} {'chunk':>7} {'bytes':>8}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row['audio_arrival_index']:>4} "
            f"{_fmt_ms(row['elapsed_ms']):>10} "
            f"{_fmt_ms(row['interarrival_ms']):>10} "
            f"{_fmt_ms(row['duration_ms']):>10} "
            f"{_fmt_ms(row['starvation_ms']):>10} "
            f"{_fmt_ms(row['backlog_ms']):>10} "
            f"{_fmt(row['segment_index']):>5} "
            f"{_fmt(row['chunk_index']):>7} "
            f"{_fmt(row['byte_count']):>8}"
        )
    return "\n".join(lines)


def _get(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _ratio(value: Any, baseline: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    if not isinstance(baseline, (int, float)) or baseline == 0:
        return None
    return value / baseline


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _fmt_ms(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{float(value):.1f}"
    return str(value)


if __name__ == "__main__":
    main()
