import io

import pytest

from minutes_bot import app as app_module
from minutes_bot import storage

ACCOUNT = {"name": "Maya Lee", "email": "Maya@Example.com", "password": "correct-horse"}


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    storage.reset_store()
    monkeypatch.setattr(app_module, "prepare_audio", lambda source, output: output)
    monkeypatch.setattr(app_module, "transcribe", lambda path: {
        "transcript": "Sarah will send the budget by Friday.", "language": "en-US",
        "duration_seconds": 3.0, "confidence": 0.9, "words": [],
    })
    monkeypatch.setattr(app_module, "analyze_text", lambda text: {
        "key_phrases": [], "entities": [], "sentiment": {"label": "neutral", "score": 0.9},
    })
    monkeypatch.setattr(app_module, "synthesize", lambda text, voice: {
        "summary_audio": "QUJD", "char_count": len(text), "voice": voice,
    })
    return app_module.app.test_client(), app_module.app.test_client()


def upload(client):
    data = {"audio": (io.BytesIO(b"audio"), "meeting.wav"), "title": "Budget", "meeting_date": "2026-11-18"}
    return client.post("/process", data=data, content_type="multipart/form-data")


def test_signup_signs_in_and_never_returns_the_password(clients):
    client, _ = clients
    response = client.post("/auth/signup", json=ACCOUNT)
    assert response.status_code == 201
    assert response.get_json() == {"user": {"name": "Maya Lee", "email": "maya@example.com"}}
    assert client.get("/auth/me").get_json()["user"]["email"] == "maya@example.com"

    saved = next(iter(storage.get_store().users.values()))
    assert ACCOUNT["password"] not in saved["password_hash"]


def test_signin_and_signout(clients):
    client, other = clients
    client.post("/auth/signup", json=ACCOUNT)
    client.post("/auth/signout")
    assert client.get("/auth/me").get_json() == {"user": None}

    assert other.post("/auth/signin", json={**ACCOUNT, "password": "wrong-password"}).status_code == 401
    assert other.post("/auth/signin", json={"email": "nobody@example.com", "password": "x"}).status_code == 401
    assert other.post("/auth/signin", json={"email": "maya@example.com", "password": ACCOUNT["password"]}).status_code == 200
    assert other.get("/auth/me").get_json()["user"]["name"] == "Maya Lee"


def test_duplicate_email_is_rejected(clients):
    client, other = clients
    client.post("/auth/signup", json=ACCOUNT)
    assert other.post("/auth/signup", json={**ACCOUNT, "email": "maya@example.com"}).status_code == 409


@pytest.mark.parametrize("change", [
    {"name": "  "},
    {"email": "not-an-email"},
    {"password": "short"},
    {"password": "x" * 129},
    {"password": 12345678},
])
def test_invalid_signup_is_rejected(clients, change):
    client, _ = clients
    response = client.post("/auth/signup", json={**ACCOUNT, **change})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_meetings_are_private_to_their_owner(clients):
    owner, stranger = clients
    owner.post("/auth/signup", json=ACCOUNT)
    stranger.post("/auth/signup", json={**ACCOUNT, "email": "sam@example.com"})

    body = upload(owner).get_json()
    meeting_id = body["meeting_id"]
    assert "owner_id" not in body
    assert "owner_id" not in owner.get(f"/meetings/{meeting_id}").get_json()
    assert [m["meeting_id"] for m in owner.get("/meetings").get_json()["meetings"]] == [meeting_id]

    assert stranger.get("/meetings").get_json() == {"meetings": []}
    assert stranger.get(f"/meetings/{meeting_id}").status_code == 404
    assert stranger.get(f"/meetings/{meeting_id}/export?format=md").status_code == 404
    assert stranger.delete(f"/meetings/{meeting_id}").status_code == 404
    assert owner.get(f"/meetings/{meeting_id}").status_code == 200


def test_signed_out_callers_cannot_read_account_meetings(clients):
    owner, anonymous = clients
    owner.post("/auth/signup", json=ACCOUNT)
    meeting_id = upload(owner).get_json()["meeting_id"]
    assert anonymous.get("/meetings").get_json() == {"meetings": []}
    assert anonymous.get(f"/meetings/{meeting_id}").status_code == 404
