"""Export saved minutes as Markdown or an iCalendar (.ics) file."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def to_markdown(meeting: dict) -> str:
    lines = [f"# {meeting['title']}", "", f"**Date:** {meeting['meeting_date']}", "", "## Decisions", ""]
    decisions = meeting.get("decisions", [])
    lines += [f"- {d['text']}" for d in decisions] or ["No decisions recorded."]
    lines += ["", "## Action items", ""]

    items = meeting.get("action_items", [])
    if items:
        lines += ["| Task | Owner | Due date | Status |", "| --- | --- | --- | --- |"]
        for item in items:
            task = item["task"].replace("|", "\\|")
            status = "Needs review" if item.get("needs_review") else "Ready"
            lines.append(
                f"| {task} | {item.get('owner') or 'Unassigned'} | {item.get('due_date') or 'No date'} | {status} |"
            )
    else:
        lines.append("No action items recorded.")

    lines += ["", "## Transcript", "", meeting.get("transcript", ""), ""]
    return "\n".join(lines)


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
        .replace("\r\n", "\\n").replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold a line at 75 bytes without splitting a character (RFC 5545)."""
    parts, current, size = [], "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > 75:
            parts.append(current)
            current, size = " ", 1
        current += char
        size += width
    parts.append(current)
    return "\r\n".join(parts)


def dated_items(meeting: dict) -> list[dict]:
    return [item for item in meeting.get("action_items", []) if item.get("due_date")]


def to_ics(meeting: dict, now: datetime | None = None) -> str:
    """Create one all-day calendar event for each dated action item."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Meeting Minutes Bot//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
    ]
    for item in dated_items(meeting):
        due = date.fromisoformat(item["due_date"])
        owner = item.get("owner") or "Unassigned"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{meeting['id']}-{item['id']}@meeting-minutes-bot",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{due:%Y%m%d}",
            f"DTEND;VALUE=DATE:{due + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_escape(item['task'])} ({_escape(owner)})",
            f"DESCRIPTION:{_escape('From meeting: ' + meeting['title'])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
