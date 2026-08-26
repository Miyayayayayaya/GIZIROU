import re

import pytest

from ai.pipeline import analyze_transcript

pytestmark = pytest.mark.live

GREETING_KEYWORDS = ["お世話になっております", "ありがとうございます", "ありがとうございました"]
DATE_LIKE = re.compile(r"(\d+月\d*日?|\d{4}-\d{2}-\d{2}|未確定)")


@pytest.mark.parametrize(
    "notes_fixture",
    ["notes_basic", "notes_confidential", "notes_no_next_meeting", "notes_ambiguous_owner"],
)
def test_pipeline_produces_structurally_valid_output(notes_fixture, request):
    raw_notes = request.getfixturevalue(notes_fixture)

    result = analyze_transcript(raw_notes)

    assert len(result["action_items"]) >= 1
    for item in result["action_items"]:
        assert item["assignee"].strip() != ""
        assert DATE_LIKE.search(item["deadline"])

    next_meeting = result["next_meeting"]
    if not next_meeting["date_confirmed"]:
        assert next_meeting["date"] is None
        assert next_meeting["start_time"] is None
        assert next_meeting["end_time"] is None
    assert "calendar_url" not in next_meeting

    assert 20 <= len(result["external_minutes"]) <= 4000
    assert 10 <= len(result["email"]["body"]) <= 2000
    assert any(keyword in result["email"]["body"] for keyword in GREETING_KEYWORDS)
    assert result["email"]["to"] == ""
