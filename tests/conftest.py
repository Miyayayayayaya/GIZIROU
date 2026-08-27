from pathlib import Path

import pytest

from ai.schemas import ActionItem, ExtractedMeetingData, MeetingInfo, NextMeetingInfo

DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def notes_basic() -> str:
    return (DATA_DIR / "notes_basic.txt").read_text(encoding="utf-8")


@pytest.fixture
def notes_confidential() -> str:
    return (DATA_DIR / "notes_confidential.txt").read_text(encoding="utf-8")


@pytest.fixture
def notes_no_next_meeting() -> str:
    return (DATA_DIR / "notes_no_next_meeting.txt").read_text(encoding="utf-8")


@pytest.fixture
def notes_ambiguous_owner() -> str:
    return (DATA_DIR / "notes_ambiguous_owner.txt").read_text(encoding="utf-8")


@pytest.fixture
def sample_extracted_data() -> ExtractedMeetingData:
    """A hand-written ExtractedMeetingData fixture, used to unit-test
    generate_minutes/generate_followup_email without depending on the
    (mocked) extraction step's output shape."""
    return ExtractedMeetingData(
        meeting_info=MeetingInfo(
            title="サンプル商事 定例打ち合わせ",
            date="2026年9月1日",
            location_or_medium="Zoom",
            attendees_external=["山田様", "鈴木様"],
            attendees_internal=["佐藤"],
        ),
        decisions=["新製品Aを10月1日から導入することで合意"],
        action_items=[
            ActionItem(task="導入手順書の送付", assignee="佐藤", deadline="9月10日"),
            ActionItem(task="社内承認の取得", assignee="山田様", deadline="9月15日"),
        ],
        caution_warnings=["値引き率は社外秘のため送信前に確認してください"],
        ambiguous_warnings=[],
        next_meeting=NextMeetingInfo(
            detected=True,
            date_confirmed=True,
            title=None,
            date="2026-09-20",
            start_time="14:00",
            end_time="15:00",
        ),
        summary="新製品Aの導入スケジュールについて合意した。",
    )
