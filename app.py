# Application Insights must be initialized before Flask and the Azure SDKs load.
from dotenv import load_dotenv

load_dotenv()

from telemetry import init_telemetry

init_telemetry()

import logging
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone

from flask import Flask, Response, jsonify, request, send_from_directory
from opentelemetry import trace
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from errors import PipelineError
from exports import dated_items, to_ics, to_markdown
from language_service import analyze_text
from minutes import build_summary, extract_minutes
from pipeline_metrics import (
    emit_pipeline_event,
    emit_pipeline_metrics,
    log_pipeline_call,
    telemetry_summary,
)
from speech_service import (
    ALLOWED_EXTENSIONS,
    DEFAULT_VOICE,
    VOICES,
    prepare_audio,
    synthesize,
    transcribe,
)
from storage import get_store

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("meeting-minutes-bot")


@app.errorhandler(PipelineError)
def handle_pipeline_error(error: PipelineError):
    return jsonify({"error": error.message}), error.status


@app.errorhandler(RequestEntityTooLarge)
def handle_too_large(_error):
    return jsonify({"error": "Audio files must be 25 MB or smaller."}), 413


@app.errorhandler(HTTPException)
def handle_http_error(error: HTTPException):
    return jsonify({"error": error.description}), error.code


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception):
    logger.exception("Unexpected server error", exc_info=error)
    return jsonify({"error": "Something went wrong on the server."}), 500


@contextmanager
def stage(span_name: str, key: str, timings: dict):
    """Run one pipeline stage inside its own span and record its duration."""
    started = time.perf_counter()
    try:
        with tracer.start_as_current_span(span_name) as span:
            yield span
    except PipelineError as error:
        error.stage = error.stage or key
        raise
    finally:
        timings[f"{key}_ms"] = (time.perf_counter() - started) * 1000


def audio_upload() -> tuple:
    upload = request.files.get("audio")
    if upload is None or not upload.filename:
        raise PipelineError("Attach an audio file in the 'audio' field.", 400)
    extension = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise PipelineError(f"Unsupported audio format. Use one of: {allowed}.", 415)
    return upload, extension


def parse_meeting_date(value: str | None) -> date:
    if not value:
        return datetime.now(timezone.utc).date()
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise PipelineError("meeting_date must use the format YYYY-MM-DD.", 400) from error


def transcribe_upload(upload, extension: str, folder: str) -> dict:
    source = os.path.join(folder, f"upload.{extension}")
    upload.save(source)
    wav_path = prepare_audio(source, os.path.join(folder, "audio.wav"))
    result = transcribe(wav_path)
    if not result["transcript"]:
        raise PipelineError("No speech was recognized in this recording.", 422)
    return result


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/transcribe")
def transcribe_endpoint():
    upload, extension = audio_upload()
    with tempfile.TemporaryDirectory() as folder:
        return jsonify(transcribe_upload(upload, extension, folder))


@app.post("/analyze")
def analyze_endpoint():
    body = request.get_json(silent=True) or {}
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise PipelineError("Send JSON with a non-empty 'text' field.", 400)
    return jsonify(analyze_text(text))


@app.post("/process")
def process():
    audio_format = "unknown"
    try:
        upload, audio_format = audio_upload()
        title = (request.form.get("title") or "Untitled meeting").strip()[:120]
        meeting_date = parse_meeting_date(request.form.get("meeting_date"))
        voice = request.form.get("voice") or DEFAULT_VOICE
        if voice not in VOICES:
            raise PipelineError("Choose a supported voice.", 400)

        timings: dict = {}
        with tracer.start_as_current_span("pipeline.process") as root_span:
            root_span.set_attribute("audio.format", audio_format)

            with stage("stage.speech_to_text", "stt", timings) as span:
                with tempfile.TemporaryDirectory() as folder:
                    stt = transcribe_upload(upload, audio_format, folder)
                span.set_attribute("stt.confidence", stt["confidence"])
                span.set_attribute("stt.word_count", len(stt["transcript"].split()))

            with stage("stage.language_analysis", "language", timings) as span:
                analysis = analyze_text(stt["transcript"])
                span.set_attribute("entity_count", len(analysis["entities"]))
                span.set_attribute("sentiment", analysis["sentiment"]["label"])

            with stage("stage.minutes_extraction", "extraction", timings) as span:
                minutes = extract_minutes(stt["transcript"], analysis["entities"], meeting_date)
                span.set_attribute("decision_count", len(minutes["decisions"]))
                span.set_attribute("action_item_count", len(minutes["action_items"]))

            meeting_id = f"m-{meeting_date:%Y%m%d}-{uuid.uuid4().hex[:8]}"
            summary_text = build_summary(minutes, analysis)
            meeting = {
                "id": meeting_id,
                "meeting_id": meeting_id,
                "title": title,
                "meeting_date": meeting_date.isoformat(),
                "transcript": stt["transcript"],
                "language": stt["language"],
                "duration_seconds": stt["duration_seconds"],
                "confidence": stt["confidence"],
                **minutes,
                "decision_count": len(minutes["decisions"]),
                "action_item_count": len(minutes["action_items"]),
                "key_phrases": analysis["key_phrases"],
                "entities": analysis["entities"],
                "sentiment": analysis["sentiment"],
                "summary_text": summary_text,
            }

            with stage("stage.meeting_storage", "storage", timings):
                try:
                    get_store().save(meeting)
                except PipelineError:
                    raise
                except Exception as error:
                    raise PipelineError("The meeting could not be saved.", 502) from error

            with stage("stage.text_to_speech", "tts", timings) as span:
                tts = synthesize(summary_text, voice)
                span.set_attribute("char_count", tts["char_count"])

            emit_pipeline_metrics(stt, analysis, minutes, tts, timings, audio_format)

        emit_pipeline_event(stt, analysis, minutes, audio_format)
        log_pipeline_call(stt, analysis, minutes, timings)
        response = {key: value for key, value in meeting.items() if key != "id"}
        return jsonify({**response, "summary_audio": tts["summary_audio"], "voice": tts["voice"]})
    except PipelineError as error:
        emit_pipeline_event(audio_format=audio_format, error_stage=error.stage or "request",
                            error_message=error.message)
        raise


def require_meeting(meeting_id: str) -> dict:
    meeting = get_store().get(meeting_id)
    if meeting is None:
        raise PipelineError("Meeting not found.", 404)
    return meeting


@app.get("/meetings")
def list_meetings():
    return jsonify({"meetings": get_store().list()})


@app.get("/meetings/<meeting_id>")
def get_meeting(meeting_id: str):
    meeting = require_meeting(meeting_id)
    return jsonify({key: value for key, value in meeting.items() if key != "id"})


@app.get("/meetings/<meeting_id>/export")
def export_meeting(meeting_id: str):
    meeting = require_meeting(meeting_id)
    export_format = request.args.get("format", "md")
    if export_format == "md":
        body, mimetype = to_markdown(meeting), "text/markdown"
    elif export_format == "ics":
        if not dated_items(meeting):
            raise PipelineError("This meeting has no action items with due dates.", 409)
        body, mimetype = to_ics(meeting), "text/calendar"
    else:
        raise PipelineError("format must be md or ics.", 400)
    headers = {"Content-Disposition": f'attachment; filename="{meeting_id}.{export_format}"'}
    return Response(body, mimetype=mimetype, headers=headers)


@app.delete("/meetings/<meeting_id>")
def delete_meeting(meeting_id: str):
    if not get_store().delete(meeting_id):
        raise PipelineError("Meeting not found.", 404)
    return jsonify({"deleted": True, "meeting_id": meeting_id})


@app.get("/telemetry-summary")
def telemetry_summary_endpoint():
    return jsonify(telemetry_summary())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
