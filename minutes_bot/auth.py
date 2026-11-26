"""Email and password accounts backed by a signed session cookie."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .errors import PipelineError
from .storage import get_store

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# Checked for unknown emails so both sign-in failures take a similar amount of time.
UNUSED_PASSWORD_HASH = generate_password_hash("unused-password")

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def current_user_id() -> str | None:
    return session.get("user_id")


def _user_id(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def _text(body: dict, field: str) -> str:
    value = body.get(field)
    return value if isinstance(value, str) else ""


def _email(body: dict) -> str:
    email = _text(body, "email").strip().lower()
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        raise PipelineError("Enter a valid email address.", 400)
    return email


def _start_session(user: dict):
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify({"user": {"name": user["name"], "email": user["email"]}})


@auth_bp.post("/signup")
def signup():
    body = request.get_json(silent=True) or {}
    name = _text(body, "name").strip()
    email = _email(body)
    password = _text(body, "password")
    if not 1 <= len(name) <= 80:
        raise PipelineError("Enter your name.", 400)
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise PipelineError(
            f"Passwords must be {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters long.", 400
        )

    user = {
        "id": _user_id(email),
        "name": name,
        "email": email,
        "password_hash": generate_password_hash(password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if not get_store().create_user(user):
        raise PipelineError("An account with this email already exists. Sign in instead.", 409)
    response = _start_session(user)
    response.status_code = 201
    return response


@auth_bp.post("/signin")
def signin():
    body = request.get_json(silent=True) or {}
    email = _text(body, "email").strip().lower()
    password = _text(body, "password")[:MAX_PASSWORD_LENGTH + 1]
    user = get_store().get_user(_user_id(email))
    password_hash = user["password_hash"] if user else UNUSED_PASSWORD_HASH
    if not check_password_hash(password_hash, password) or user is None:
        raise PipelineError("The email or password is incorrect.", 401)
    return _start_session(user)


@auth_bp.post("/signout")
def signout():
    session.clear()
    return jsonify({"signed_out": True})


@auth_bp.get("/me")
def me():
    user_id = current_user_id()
    user = get_store().get_user(user_id) if user_id else None
    if user is None:
        session.clear()
        return jsonify({"user": None})
    return jsonify({"user": {"name": user["name"], "email": user["email"]}})
