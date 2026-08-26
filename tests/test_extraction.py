from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai.errors import ExtractionError
from ai.extraction import extract_meeting_data
from ai.schemas import ActionItem, ExtractedMeetingData, MeetingInfo, NextMeetingInfo


def _fake_response(parsed):
    message = SimpleNamespace(parsed=parsed)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


@patch("ai.extraction.get_client")
def test_extract_meeting_data_maps_parsed_response_to_schema(mock_get_client):
    expected = ExtractedMeetingData(
        meeting_info=MeetingInfo(title="定例会議"),
        decisions=["A案を採用する"],
        action_items=[ActionItem(task="資料作成", assignee="佐藤", deadline="9月10日")],
        caution_warnings=[],
        ambiguous_warnings=[],
        next_meeting=NextMeetingInfo(detected=True, date_confirmed=True, date="2026-09-20"),
        summary="A案を採用することで合意した。",
    )
    mock_client = MagicMock()
    mock_client.chat.completions.parse.return_value = _fake_response(expected)
    mock_get_client.return_value = mock_client

    result = extract_meeting_data("ダミーの会議メモ")

    assert isinstance(result, ExtractedMeetingData)
    assert result.decisions == ["A案を採用する"]
    assert result.action_items[0].assignee == "佐藤"
    assert result.next_meeting.date == "2026-09-20"


@patch("ai.extraction.get_client")
def test_extract_meeting_data_raises_extraction_error_when_parsed_is_none(mock_get_client):
    mock_client = MagicMock()
    mock_client.chat.completions.parse.return_value = _fake_response(None)
    mock_get_client.return_value = mock_client

    with pytest.raises(ExtractionError):
        extract_meeting_data("ダミーの会議メモ")


@patch("ai.extraction.get_client")
def test_extract_meeting_data_wraps_api_exceptions(mock_get_client):
    mock_client = MagicMock()
    mock_client.chat.completions.parse.side_effect = RuntimeError("boom")
    mock_get_client.return_value = mock_client

    with pytest.raises(ExtractionError):
        extract_meeting_data("ダミーの会議メモ")
