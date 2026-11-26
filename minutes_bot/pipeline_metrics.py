"""Custom metrics, pipeline events, and the /telemetry-summary session log."""
from __future__ import annotations

import math
import statistics

from opentelemetry import metrics, trace

meter = metrics.get_meter("meeting-minutes-bot")

stt_confidence_gauge = meter.create_gauge("stt_confidence")
stt_duration_gauge = meter.create_gauge("stt_duration_seconds")
stt_word_count_gauge = meter.create_gauge("stt_word_count")
entity_count_gauge = meter.create_gauge("language_entity_count")
keyphrase_count_gauge = meter.create_gauge("language_keyphrase_count")
sentiment_gauge = meter.create_gauge("language_sentiment")
tts_char_count_gauge = meter.create_gauge("tts_char_count")

stage_stt_hist = meter.create_histogram("stage_stt_ms")
stage_language_hist = meter.create_histogram("stage_language_ms")
stage_extraction_hist = meter.create_histogram("stage_extraction_ms")
stage_storage_hist = meter.create_histogram("stage_storage_ms")
stage_tts_hist = meter.create_histogram("stage_tts_ms")

# A histogram keeps every meeting. A gauge would keep only the last value per export.
action_items_hist = meter.create_histogram("action_items_per_meeting")

session_log: list[dict] = []


def emit_pipeline_metrics(stt: dict, analysis: dict, minutes: dict, tts: dict,
                          timings: dict, audio_format: str) -> None:
    attrs = {"audio_format": audio_format, "language": stt["language"]}
    stt_confidence_gauge.set(stt["confidence"], attrs)
    stt_duration_gauge.set(stt["duration_seconds"], attrs)
    stt_word_count_gauge.set(len(stt["transcript"].split()), attrs)
    entity_count_gauge.set(len(analysis["entities"]), attrs)
    keyphrase_count_gauge.set(len(analysis["key_phrases"]), attrs)
    sentiment_map = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}
    sentiment_gauge.set(sentiment_map.get(analysis["sentiment"]["label"], 0.0), attrs)
    tts_char_count_gauge.set(tts["char_count"], attrs)

    stage_stt_hist.record(timings["stt_ms"], attrs)
    stage_language_hist.record(timings["language_ms"], attrs)
    stage_extraction_hist.record(timings["extraction_ms"], attrs)
    stage_storage_hist.record(timings["storage_ms"], attrs)
    stage_tts_hist.record(timings["tts_ms"], attrs)
    action_items_hist.record(len(minutes["action_items"]), attrs)


def emit_pipeline_event(stt: dict | None = None, analysis: dict | None = None,
                        minutes: dict | None = None, audio_format: str = "unknown",
                        error_stage: str | None = None, error_message: str | None = None) -> None:
    """Record the outcome as attributes on the current request span."""
    span = trace.get_current_span()
    span.set_attribute("audio.format", audio_format)
    if error_stage or error_message:
        span.set_attribute("event.name", "pipeline_error")
        span.set_attribute("error.stage", error_stage or "unknown")
        span.set_attribute("error.message", error_message or "Unknown error")
        return
    span.set_attribute("event.name", "pipeline_completed")
    span.set_attribute("stt.confidence", stt["confidence"])
    span.set_attribute("stt.language", stt["language"])
    span.set_attribute("entities.count", len(analysis["entities"]))
    span.set_attribute("sentiment", analysis["sentiment"]["label"])
    span.set_attribute("action_items.count", len(minutes["action_items"]))
    span.set_attribute("decisions.count", len(minutes["decisions"]))


def log_pipeline_call(stt: dict, analysis: dict, minutes: dict, timings: dict) -> None:
    session_log.append({
        "confidence": stt["confidence"],
        "language": stt["language"],
        "entity_count": len(analysis["entities"]),
        "sentiment": analysis["sentiment"]["label"],
        "action_item_count": len(minutes["action_items"]),
        "decision_count": len(minutes["decisions"]),
        **{key: round(value, 1) for key, value in timings.items()},
    })


def telemetry_summary() -> dict:
    if not session_log:
        return {"message": "No calls yet", "total_calls": 0}
    confidences = [entry["confidence"] for entry in session_log]
    stt_times = sorted(entry["stt_ms"] for entry in session_log)
    p95_index = math.ceil(len(stt_times) * 0.95) - 1
    return {
        "total_calls": len(session_log),
        "avg_confidence": round(statistics.mean(confidences), 3),
        "min_confidence": round(min(confidences), 3),
        "p95_stt_ms": round(stt_times[p95_index], 1),
        "sentiment_breakdown": {
            label: sum(1 for entry in session_log if entry["sentiment"] == label)
            for label in ("positive", "neutral", "negative")
        },
        "avg_action_items": round(
            statistics.mean(entry["action_item_count"] for entry in session_log), 2
        ),
        "calls": session_log[-10:],
    }
