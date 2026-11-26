from datetime import date

import pytest

from minutes_bot.language_service import chunk_text
from minutes_bot.minutes import build_summary, extract_minutes, find_due_date, split_sentences

MEETING = date(2026, 11, 18)  # A Wednesday.


@pytest.mark.parametrize("phrase,expected", [
    ("by Friday", "2026-11-20"),
    ("by Wednesday", "2026-11-25"),
    ("by next Tuesday", "2026-11-24"),
    ("by tomorrow", "2026-11-19"),
    ("by the day after tomorrow", "2026-11-20"),
    ("by tonight", "2026-11-18"),
    ("by the end of the week", "2026-11-20"),
    ("by the end of next week", "2026-11-27"),
    ("by next week", "2026-11-27"),
    ("by the end of the month", "2026-11-30"),
    ("in three days", "2026-11-21"),
    ("in 2 weeks", "2026-12-02"),
    ("by November 25", "2026-11-25"),
    ("by Nov. 25th", "2026-11-25"),
    ("by the 3rd of December", "2026-12-03"),
    ("by March 3", "2027-03-03"),
    ("by 12/1", "2026-12-01"),
])
def test_due_dates(phrase, expected):
    due, _ = find_due_date(f"Sarah will send it {phrase}.", MEETING)
    assert due.isoformat() == expected


def test_impossible_date_is_ignored():
    assert find_due_date("Send it by February 31.", MEETING) == (None, None)


def test_sample_from_product_brief():
    result = extract_minutes(
        "We decided to launch on March 3. Sarah will send the budget by Friday.", [], MEETING
    )
    assert result["decisions"] == [{"text": "Launch on March 3"}]
    assert result["action_items"] == [{
        "id": "a1", "task": "Send the budget", "owner": "Sarah",
        "due_date": "2026-11-20", "needs_review": False,
    }]


def test_owner_uses_language_entities():
    text = "Okay, Sarah Chen will send the budget by Friday."
    entities = [{"text": "Sarah Chen", "category": "Person", "offset": 6}]
    assert extract_minutes(text, entities, MEETING)["action_items"][0]["owner"] == "Sarah Chen"


@pytest.mark.parametrize("sentence,owner", [
    ("Okay, Sarah will send the budget by Friday.", "Sarah"),
    ("So Tom needs to book the room by Monday.", "Tom"),
    ("Sarah and Tom will prepare the slides by Monday.", "Sarah and Tom"),
    ("Sarah, can you send the budget by Friday?", "Sarah"),
    ("Action item for Sarah: send the budget by Friday.", "Sarah"),
    ("Tom'll book the room by Monday.", "Tom"),
])
def test_owner_patterns(sentence, owner):
    items = extract_minutes(sentence, [], MEETING)["action_items"]
    assert [item["owner"] for item in items] == [owner]


def test_missing_owner_and_date_need_review():
    items = extract_minutes(
        "Someone needs to call the vendor by Thursday. Alicia will look into hosting.", [], MEETING
    )["action_items"]
    assert [(i["owner"], i["due_date"], i["needs_review"]) for i in items] == [
        (None, "2026-11-19", True), ("Alicia", None, True),
    ]


@pytest.mark.parametrize("sentence", [
    "We will see how it goes next month.",
    "Sarah might send the budget by Friday.",
    "Sarah will be out next week.",
    "It will rain on Friday.",
    "Will Sarah send the budget?",
    "Did we decide on the venue?",
    "We have not decided on the venue.",
    "Maria finished the survey on Monday.",
])
def test_non_actions_are_ignored(sentence):
    assert extract_minutes(sentence, [], MEETING) == {"decisions": [], "action_items": []}


def test_named_commitment_inside_decision_is_an_action():
    result = extract_minutes("We agreed that Tom will book the venue by Friday.", [], MEETING)
    assert result["decisions"] == []
    assert result["action_items"][0]["owner"] == "Tom"


def test_sentences_keep_offsets_and_abbreviations():
    text = "Send it by Nov. 25 please. Thanks."
    assert split_sentences(text) == [(0, "Send it by Nov. 25 please."), (27, "Thanks.")]


def test_summary_mentions_required_details():
    minutes = extract_minutes("We decided to launch. Sarah will send the budget.", [], MEETING)
    analysis = {
        "key_phrases": ["budget", "launch"],
        "entities": [{"text": "Sarah", "category": "Person"}],
        "sentiment": {"label": "neutral"},
    }
    summary = build_summary(minutes, analysis)
    for words in ("1 decision", "1 action item", "2 key topics", "budget", "neutral", "1 person"):
        assert words in summary


def test_chunks_respect_limit_and_offsets():
    text = "This is one sentence. " * 600
    chunks = chunk_text(text.strip())
    assert len(chunks) > 1
    assert all(len(chunk) <= 5000 for _, chunk in chunks)
    assert all(text[start:start + len(chunk)] == chunk for start, chunk in chunks)
