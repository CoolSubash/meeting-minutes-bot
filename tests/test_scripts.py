import re
from datetime import date
from pathlib import Path

import pytest

from minutes import extract_minutes

SCRIPT_FILE = Path(__file__).resolve().parents[1] / "docs" / "test-scripts.md"
SECTION = re.compile(
    r"Expected: (\d+) decisions?, (\d+) action items?(?:, (\d+) marked for review)?\.\n\n> (.+)"
)
RECORDINGS = SECTION.findall(SCRIPT_FILE.read_text(encoding="utf-8"))


def test_all_five_scripts_are_present():
    assert len(RECORDINGS) == 5


@pytest.mark.parametrize("decisions,actions,review,script", RECORDINGS)
def test_script_produces_expected_minutes(decisions, actions, review, script):
    result = extract_minutes(script, [], date(2026, 11, 18))
    assert len(result["decisions"]) == int(decisions)
    assert len(result["action_items"]) == int(actions)
    assert sum(i["needs_review"] for i in result["action_items"]) == int(review or 0)
