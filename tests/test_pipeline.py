from unittest.mock import patch

from ai.pipeline import analyze_transcript
from ai.schemas import FollowUpEmail, MeetingMinutes
from ai.translation import EnglishFollowUpContent


@patch("ai.pipeline.generate_english_followup_content")
@patch("ai.pipeline.generate_followup_email")
@patch("ai.pipeline.generate_minutes")
@patch("ai.pipeline.extract_meeting_data")
def test_analyze_transcript_returns_the_contract_shape(
    mock_extract, mock_minutes, mock_email, mock_english, sample_extracted_data
):
    mock_extract.return_value = sample_extracted_data
    mock_minutes.return_value = MeetingMinutes(
        title="定例会議",
        external_body="外部向け議事録本文",
        source_data=sample_extracted_data,
    )
    mock_email.return_value = FollowUpEmail(to="", subject="件名", body="本文")
    mock_english.return_value = EnglishFollowUpContent(
        external_minutes="English meeting minutes", subject="Subject", body="Body"
    )

    result = analyze_transcript("ダミーの会議メモ")

    assert set(result.keys()) == {
        "external_minutes",
        "english",
        "decisions",
        "action_items",
        "warnings",
        "next_meeting",
        "email",
    }
    assert result["external_minutes"] == "外部向け議事録本文"
    assert result["english"] == {
        "external_minutes": "English meeting minutes",
        "email": {"subject": "Subject", "body": "Body"},
    }
    assert result["decisions"] == sample_extracted_data.decisions
    assert result["action_items"] == [
        {"task": "導入手順書の送付", "assignee": "佐藤", "deadline": "9月10日"},
        {"task": "社内承認の取得", "assignee": "山田様", "deadline": "9月15日"},
    ]
    assert result["warnings"] == sample_extracted_data.caution_warnings + sample_extracted_data.ambiguous_warnings
    assert set(result["next_meeting"].keys()) == {
        "detected",
        "date_confirmed",
        "title",
        "date",
        "start_time",
        "end_time",
    }
    assert "calendar_url" not in result["next_meeting"]
    assert result["email"] == {"to": "", "subject": "件名", "body": "本文"}
