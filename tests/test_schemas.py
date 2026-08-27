import pytest
from pydantic import ValidationError

from ai.schemas import ActionItem, ExtractedMeetingData, MeetingInfo, NextMeetingInfo


def test_action_item_requires_task():
    with pytest.raises(ValidationError):
        ActionItem()


def test_action_item_defaults_to_mikakutei_when_unspecified():
    item = ActionItem(task="資料作成")
    assert item.assignee == "未確定"
    assert item.deadline == "未確定"


def test_next_meeting_info_defaults_to_not_detected():
    info = NextMeetingInfo()
    assert info.detected is False
    assert info.date_confirmed is False
    assert info.date is None


def test_incomplete_confirmed_next_meeting_is_downgraded_to_ambiguous():
    info = NextMeetingInfo(
        detected=True,
        date_confirmed=True,
        date="2026-09-20",
        start_time="14:00",
        end_time=None,
    )

    assert info.detected is True
    assert info.date_confirmed is False
    assert info.date is None
    assert info.start_time is None
    assert info.end_time is None


def test_invalid_confirmed_next_meeting_is_downgraded_to_ambiguous():
    info = NextMeetingInfo(
        detected=True,
        date_confirmed=True,
        date="2026-02-30",
        start_time="25:00",
        end_time="26:00",
    )

    assert info.date_confirmed is False
    assert info.date is None


def test_extracted_meeting_data_defaults_are_empty_not_none():
    data = ExtractedMeetingData(meeting_info=MeetingInfo())
    assert data.decisions == []
    assert data.action_items == []
    assert data.caution_warnings == []
    assert data.ambiguous_warnings == []


def test_extracted_meeting_data_rejects_wrong_type_for_decisions():
    with pytest.raises(ValidationError):
        ExtractedMeetingData(meeting_info=MeetingInfo(), decisions="not a list")
