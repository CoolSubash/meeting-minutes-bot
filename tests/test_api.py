import io
from datetime import datetime, timezone

import pytest

import app as app_module
import pipeline_metrics
import storage
from errors import PipelineError
from exports import to_ics, to_markdown

TRANSCRIPT = "We decided to launch on March 3. Sarah will send the budget by Friday."


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    storage.reset_store()
    pipeline_metrics.session_log.clear()
    monkeypatch.setattr(app_module, "prepare_audio", lambda source, output: output)
    monkeypatch.setattr(app_module, "transcribe", lambda path: {
        "transcript": TRANSCRIPT, "language": "en-US", "duration_seconds": 6.4,
        "confidence": 0.93, "words": [],
    })
    monkeypatch.setattr(app_module, "analyze_text", lambda text: {
        "key_phrases": ["budget", "launch"],
        "entities": [{"text": "Sarah", "category": "Person", "confidence": 0.98,
                      "offset": TRANSCRIPT.index("Sarah")}],
        "sentiment": {"label": "neutral", "score": 0.91},
    })
    monkeypatch.setattr(app_module, "synthesize", lambda text, voice: {
        "summary_audio": "QUJD", "char_count": len(text), "voice": voice,
    })
    return app_module.app.test_client()


def post_audio(client, filename="meeting.wav", **fields):
    data = {"audio": (io.BytesIO(b"audio"), filename), "title": "Launch planning",
            "meeting_date": "2026-11-18", **fields}
    return client.post("/process", data=data, content_type="multipart/form-data")


def test_process_returns_minutes_and_saves_history(client):
    response = post_audio(client)
    assert response.status_code == 200
    body = response.get_json()
    assert body["decisions"] == [{"text": "Launch on March 3"}]
    assert body["action_items"][0]["due_date"] == "2026-11-20"
    assert body["summary_audio"] == "QUJD"

    listed = client.get("/meetings").get_json()["meetings"]
    assert listed == [{
        "meeting_id": body["meeting_id"], "title": "Launch planning", "meeting_date": "2026-11-18",
        "decision_count": 1, "action_item_count": 1,
    }]
    saved = client.get(f"/meetings/{body['meeting_id']}").get_json()
    assert "summary_audio" not in saved
    assert saved["transcript"] == TRANSCRIPT


def test_exports_and_delete(client):
    meeting_id = post_audio(client).get_json()["meeting_id"]

    markdown = client.get(f"/meetings/{meeting_id}/export?format=md")
    assert markdown.mimetype == "text/markdown"
    assert b"| Send the budget | Sarah | 2026-11-20 | Ready |" in markdown.data

    calendar = client.get(f"/meetings/{meeting_id}/export?format=ics")
    assert calendar.mimetype == "text/calendar"
    assert b"DTSTART;VALUE=DATE:20261120" in calendar.data

    assert client.get(f"/meetings/{meeting_id}/export?format=pdf").status_code == 400
    assert client.delete(f"/meetings/{meeting_id}").get_json() == {"deleted": True, "meeting_id": meeting_id}
    assert client.get(f"/meetings/{meeting_id}").status_code == 404
    assert client.delete(f"/meetings/{meeting_id}").status_code == 404


def test_calendar_export_needs_a_dated_item(client, monkeypatch):
    monkeypatch.setattr(app_module, "transcribe", lambda path: {
        "transcript": "Sarah will send the budget.", "language": "en-US",
        "duration_seconds": 2.0, "confidence": 0.9, "words": [],
    })
    meeting_id = post_audio(client).get_json()["meeting_id"]
    assert client.get(f"/meetings/{meeting_id}/export?format=ics").status_code == 409


@pytest.mark.parametrize("fields,filename,status", [
    ({}, "notes.txt", 415),
    ({}, "no-extension", 415),
    ({"meeting_date": "11/18/2026"}, "meeting.wav", 400),
    ({"voice": "robot"}, "meeting.wav", 400),
])
def test_invalid_requests(client, fields, filename, status):
    response = post_audio(client, filename=filename, **fields)
    assert response.status_code == status
    assert "error" in response.get_json()


def test_missing_audio_and_empty_analysis_text(client):
    assert client.post("/process").status_code == 400
    assert client.post("/analyze", json={"text": "  "}).status_code == 400


def test_stage_failure_returns_clear_error(client, monkeypatch):
    def fail(path):
        raise PipelineError("Azure Speech could not transcribe this audio.", 502)
    monkeypatch.setattr(app_module, "transcribe", fail)
    response = post_audio(client)
    assert response.status_code == 502
    assert response.get_json() == {"error": "Azure Speech could not transcribe this audio."}
    assert client.get("/meetings").get_json() == {"meetings": []}


def test_telemetry_summary(client):
    assert client.get("/telemetry-summary").get_json()["total_calls"] == 0
    post_audio(client)
    summary = client.get("/telemetry-summary").get_json()
    assert summary["total_calls"] == 1
    assert summary["avg_confidence"] == 0.93
    assert summary["avg_action_items"] == 1
    assert summary["sentiment_breakdown"]["neutral"] == 1


def test_ics_format_is_escaped_and_folded():
    meeting = {
        "id": "m-1", "title": "Plan, review; ship",
        "action_items": [{"id": "a1", "task": "Write " + "long " * 30, "owner": "Sam", "due_date": "2026-12-31"}],
    }
    text = to_ics(meeting, datetime(2026, 11, 18, tzinfo=timezone.utc))
    assert "DTEND;VALUE=DATE:20270101" in text
    assert "Plan\\, review\\; ship" in text.replace("\r\n ", "")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))


def test_markdown_handles_empty_minutes():
    text = to_markdown({"title": "Sync", "meeting_date": "2026-11-18", "transcript": "Hello."})
    assert "No decisions recorded." in text and "No action items recorded." in text
