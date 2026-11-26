"""Turn a meeting transcript into decisions and action items.

Everything here is plain Python with no Azure calls, so it can be unit tested
without credentials. Owners come from the sentence itself ("Sarah will ...")
because speaker separation is not available on the free Speech tier.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_WEEKDAY = "|".join(WEEKDAYS)
_COUNT = r"\d+|" + "|".join(NUMBER_WORDS)

_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_ABBREVIATION = re.compile(
    r"\b(?:jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|mr|mrs|ms|dr)\.$", re.I
)
_DUE_PREFIX = re.compile(r"(?:\b(?:by|before|on|due|until|no later than)\s+(?:the\s+)?)$", re.I)

_MODAL = (
    r"will|shall|must|should|needs? to|has to|have to|"
    r"(?:is|are|am) going to|(?:is|are|am) gonna|gonna|"
    r"(?:is|are) responsible for|volunteered to|agreed to|promised to|offered to"
)
_NAME = r"[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+)?"
_SUBJECT = (
    rf"{_NAME}(?:\s*(?:,|and|&)\s*{_NAME})*|I|we|someone|somebody|the team|you|he|she|they"
)
_ACTION = re.compile(
    rf"(?<![\w'’])(?P<subject>{_SUBJECT})(?:\s+(?:also|then|just|now))?"
    rf"(?:\s+(?:{_MODAL})|['’]ll)\s+(?P<rest>.+)$"
)
_REQUEST = re.compile(
    rf"(?<![\w'’])(?P<subject>{_NAME}),?\s+(?:can you|could you|would you|please)\s+(?P<rest>.+)$"
)
_ITEM_FOR = re.compile(rf"\baction item for (?P<subject>{_NAME})[:,]?\s+(?P<rest>.+)$", re.I)
_DECISION = re.compile(
    r"\b(?:we(?:['’]ve| have)?\s+(?:all\s+|just\s+|officially\s+)?"
    r"(?P<verb>decided|agreed|approved|chose|picked|settled on)"
    r"|(?P<go>we(?:['’]re| are) going with|we(?:['’]ll| will) go with|let['’]s go with)"
    r"|(?:the|our|final) decision is)\b",
    re.I,
)
_HEDGE = re.compile(
    r"\b(?:we will see|we['’]ll see|wait and see|maybe|might|perhaps|possibly|hopefully|"
    r"not sure|unsure|if we|if you|if they|have not decided|haven['’]t decided|"
    r"not decided|undecided|no decision|not approved)\b",
    re.I,
)

_PRONOUNS = {"i", "we", "someone", "somebody", "the team", "you", "he", "she", "they"}
_NOT_A_SUBJECT = {"it", "this", "that", "there", "these", "those", "what", "who", "which", "nobody"}
_FILLERS = {
    "so", "okay", "ok", "and", "then", "next", "also", "but", "well", "great", "yes",
    "right", "alright", "good", "now", "first", "second", "third", "finally", "thanks",
    "perfect", "sure", "fine", "cool", "hi", "hello", "hey", "today", "tomorrow",
    *WEEKDAYS,
}
_NOT_A_TASK = {"be", "not", "never", "probably", "likely", "maybe", "see"}


def split_sentences(text: str) -> list[tuple[int, str]]:
    """Return (start offset, sentence) pairs."""
    sentences: list[tuple[int, str]] = []
    start = 0

    def add(chunk: str, offset: int) -> None:
        stripped = chunk.strip()
        if stripped:
            sentences.append((offset + len(chunk) - len(chunk.lstrip()), stripped))

    for match in _BOUNDARY.finditer(text):
        chunk = text[start:match.start()]
        if _ABBREVIATION.search(chunk):
            continue
        add(chunk, start)
        start = match.end()
    add(text[start:], start)
    return sentences


def _friday_of_week(day: date, weeks_ahead: int = 0) -> date:
    return day - timedelta(days=day.weekday()) + timedelta(days=4, weeks=weeks_ahead)


def _month_day(meeting: date, month: int, day: int, year: str | None) -> date:
    if year:
        full_year = int(year) + 2000 if len(year) == 2 else int(year)
        return date(full_year, month, day)
    result = date(meeting.year, month, day)
    return result if result >= meeting else date(meeting.year + 1, month, day)


def _this_friday(meeting: date) -> date:
    friday = _friday_of_week(meeting)
    return friday if friday >= meeting else _friday_of_week(meeting, 1)


def _month_end(meeting: date, months_ahead: int = 0) -> date:
    index = meeting.month - 1 + months_ahead
    year, month = meeting.year + index // 12, index % 12 + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _relative(meeting: date, count: str, unit: str) -> date:
    amount = int(count) if count.isdigit() else NUMBER_WORDS[count.lower()]
    return meeting + timedelta(days=amount * (7 if unit.lower() == "week" else 1))


def _weekday(meeting: date, qualifier: str | None, name: str) -> date:
    target = WEEKDAYS.index(name.lower())
    if (qualifier or "").lower() == "next":
        monday = meeting - timedelta(days=meeting.weekday())
        return monday + timedelta(days=7 + target)
    return meeting + timedelta(days=(target - meeting.weekday()) % 7 or 7)


_DATE_RULES = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
     lambda m, d: date(int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.I),
     lambda m, d: _month_day(d, MONTHS[m[1].lower()], int(m[2]), m[3])),
    (re.compile(rf"\b(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+of\s+({_MONTH})\b", re.I),
     lambda m, d: _month_day(d, MONTHS[m[2].lower()], int(m[1]), None)),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b"),
     lambda m, d: _month_day(d, int(m[1]), int(m[2]), m[3])),
    (re.compile(r"\b(?:the\s+)?day after tomorrow\b", re.I), lambda m, d: d + timedelta(days=2)),
    (re.compile(r"\btomorrow\b", re.I), lambda m, d: d + timedelta(days=1)),
    (re.compile(r"\b(?:(?:the\s+)?end of (?:the\s+)?day|eod|today|tonight)\b", re.I), lambda m, d: d),
    (re.compile(r"\b(?:the\s+)?end of next week\b", re.I), lambda m, d: _friday_of_week(d, 1)),
    (re.compile(r"\b(?:(?:the\s+)?end of (?:the\s+|this\s+)?week|this week)\b", re.I),
     lambda m, d: _this_friday(d)),
    (re.compile(r"\bnext week\b", re.I), lambda m, d: _friday_of_week(d, 1)),
    (re.compile(r"\b(?:the\s+)?end of next month\b|\bnext month\b", re.I), lambda m, d: _month_end(d, 1)),
    (re.compile(r"\b(?:(?:the\s+)?end of (?:the\s+|this\s+)?month|this month)\b", re.I),
     lambda m, d: _month_end(d)),
    (re.compile(rf"\bin\s+({_COUNT})\s+(day|week)s?\b", re.I), lambda m, d: _relative(d, m[1], m[2])),
    (re.compile(rf"\b(?:(next|this|coming)\s+)?({_WEEKDAY})\b", re.I), lambda m, d: _weekday(d, m[1], m[2])),
]


def find_due_date(sentence: str, meeting_date: date) -> tuple[date | None, tuple[int, int] | None]:
    """Return the first date mentioned, resolved against the meeting date."""
    for pattern, resolve in _DATE_RULES:
        match = pattern.search(sentence)
        if not match:
            continue
        try:
            return resolve(match, meeting_date), match.span()
        except ValueError:  # An impossible date such as February 31.
            continue
    return None, None


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" ,;:.!?-")
    return text[:1].upper() + text[1:]


def _task_text(rest: str, meeting_date: date) -> str:
    _, span = find_due_date(rest, meeting_date)
    if span:
        before = _DUE_PREFIX.sub("", rest[: span[0]])
        rest = f"{before} {rest[span[1]:]}"
    return _clean_text(re.sub(r"^(?:to|also)\s+", "", rest.strip(), flags=re.I))


def _clean_name(part: str) -> str | None:
    words = part.split()
    while words and words[0].lower() in _FILLERS:
        words.pop(0)
    return " ".join(words) or None


def _owner(subject: str, start: int, persons: list[dict]) -> str | None:
    """Prefer names found by Azure Language, then the capitalized subject."""
    if subject.lower() in _PRONOUNS:
        return None
    end = start + len(subject)
    recognized = [
        p["text"] for p in persons if start <= p.get("offset", -1) < end
    ]
    if recognized:
        return " and ".join(dict.fromkeys(recognized))
    names = [_clean_name(part) for part in re.split(r"\s*(?:,|\band\b|&)\s*", subject)]
    return " and ".join(name for name in names if name) or None


def _parse_action(sentence: str, offset: int, meeting_date: date, persons: list[dict]) -> dict | None:
    match = _ITEM_FOR.search(sentence) or _REQUEST.search(sentence)
    if not match:
        if sentence.endswith("?"):
            return None
        match = _ACTION.search(sentence)
    if not match:
        return None

    subject, rest = match["subject"], match["rest"]
    first_word = _clean_name(subject)
    if first_word and first_word.lower() in _NOT_A_SUBJECT:
        return None
    if subject.lower() in _NOT_A_SUBJECT or rest.split()[0].lower().strip(",.") in _NOT_A_TASK:
        return None

    task = _task_text(rest, meeting_date)
    if not task:
        return None
    due, _ = find_due_date(sentence, meeting_date)
    owner = _owner(subject, offset + match.start("subject"), persons)
    return {
        "task": task,
        "owner": owner,
        "due_date": due.isoformat() if due else None,
        "needs_review": owner is None or due is None,
    }


def _parse_decision(sentence: str) -> dict | None:
    match = _DECISION.search(sentence)
    if not match or sentence.endswith("?"):
        return None
    rest = re.sub(r"^(?:to be|to|that|on|is)\s+", "", sentence[match.end():].strip(" ,:"), flags=re.I)
    verb = (match["verb"] or "").lower()
    if match["go"]:
        rest = f"go with {rest}"
    elif verb in {"approved", "chose", "picked", "settled on"}:
        rest = f"{verb} {rest}"
    return {"text": _clean_text(rest or sentence)}


def extract_minutes(transcript: str, entities: list[dict], meeting_date: date) -> dict:
    """Return {"decisions": [...], "action_items": [...]} for a transcript."""
    persons = [e for e in entities if e.get("category") == "Person"]
    decisions: list[dict] = []
    action_items: list[dict] = []

    for offset, sentence in split_sentences(transcript):
        if _HEDGE.search(sentence):
            continue
        action = _parse_action(sentence, offset, meeting_date, persons)
        decision = _parse_decision(sentence)
        if action and (action["owner"] or not decision):
            action_items.append({"id": f"a{len(action_items) + 1}", **action})
        elif decision:
            decisions.append(decision)

    return {"decisions": decisions, "action_items": action_items}


def _count(number: int, singular: str, plural: str | None = None) -> str:
    return f"{number} {singular if number == 1 else plural or singular + 's'}"


def build_summary(minutes: dict, analysis: dict) -> str:
    """Write the sentence read aloud by text-to-speech."""
    decisions, items = minutes["decisions"], minutes["action_items"]
    parts = [
        f"This meeting produced {_count(len(decisions), 'decision')} "
        f"and {_count(len(items), 'action item')}."
    ]
    review = sum(1 for item in items if item["needs_review"])
    if review:
        parts.append(f"{_count(review, 'action item')} still {'needs' if review == 1 else 'need'} review.")

    phrases = analysis.get("key_phrases", [])
    if phrases:
        topics = phrases[:3]
        listed = topics[0] if len(topics) == 1 else ", ".join(topics[:-1]) + f" and {topics[-1]}"
        parts.append(f"It covered {_count(len(phrases), 'key topic')}, including {listed}.")

    parts.append(f"The overall tone was {analysis.get('sentiment', {}).get('label', 'neutral')}.")

    labels = {
        "Person": ("person", "people"), "Organization": ("organization", "organizations"),
        "DateTime": ("date", "dates"), "Location": ("location", "locations"),
        "Quantity": ("quantity", "quantities"),
    }
    counts = {
        _count(sum(1 for e in analysis.get("entities", []) if e.get("category") == key), *names)
        for key, names in labels.items()
        if any(e.get("category") == key for e in analysis.get("entities", []))
    }
    if counts:
        parts.append(f"I detected {', '.join(sorted(counts))}.")
    return " ".join(parts)
