from unittest.mock import patch

from main import run_ai_analysis


def analysis_result(*, date_confirmed=True, calendar_placeholder=False):
    body = "会議内容をご確認ください。"
    if calendar_placeholder:
        body += "\n\nGoogle Calendarに追加：\n{calendar_url}"

    return {
        "external_minutes": "議事録",
        "decisions": [],
        "action_items": [],
        "warnings": [],
        "next_meeting": {
            "detected": True,
            "date_confirmed": date_confirmed,
            "title": "進捗確認会",
            "date": "2026-09-03" if date_confirmed else None,
            "start_time": "14:00" if date_confirmed else None,
            "end_time": "15:00" if date_confirmed else None,
        },
        "email": {"to": "", "subject": "件名", "body": body},
        "english": {
            "external_minutes": "English meeting minutes",
            "email": {"subject": "Subject", "body": "English body"},
        },
    }


@patch("main.analyze_transcript")
def test_calendar_url_is_appended_to_email_body(mock_analyze):
    mock_analyze.return_value = analysis_result()

    result = run_ai_analysis("文字起こし")
    calendar_url = result["next_meeting"]["calendar_url"]

    assert calendar_url
    assert calendar_url in result["email"]["body"]
    assert result["email"]["body"].count(calendar_url) == 1


@patch("main.analyze_transcript")
def test_calendar_placeholder_is_replaced_without_duplication(mock_analyze):
    mock_analyze.return_value = analysis_result(calendar_placeholder=True)

    result = run_ai_analysis("文字起こし")
    calendar_url = result["next_meeting"]["calendar_url"]

    assert "{calendar_url}" not in result["email"]["body"]
    assert result["email"]["body"].count(calendar_url) == 1


@patch("main.analyze_transcript")
def test_ambiguous_meeting_does_not_add_calendar_link(mock_analyze):
    mock_analyze.return_value = analysis_result(date_confirmed=False)

    result = run_ai_analysis("文字起こし")

    assert result["next_meeting"]["calendar_url"] == ""
    assert "Google Calendarに追加" not in result["email"]["body"]
