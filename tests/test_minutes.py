from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai.errors import GenerationError
from ai.minutes import generate_minutes
from ai.schemas import MeetingMinutes


def _fake_response(content):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


@patch("ai.minutes.get_client")
def test_generate_minutes_returns_meeting_minutes(mock_get_client, sample_extracted_data):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _fake_response("外部向け議事録本文")
    mock_get_client.return_value = mock_client

    result = generate_minutes(sample_extracted_data)

    assert isinstance(result, MeetingMinutes)
    assert result.external_body == "外部向け議事録本文"


@patch("ai.minutes.get_client")
def test_caution_and_ambiguous_warnings_never_reach_the_prompt(mock_get_client, sample_extracted_data):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _fake_response("外部向け議事録本文")
    mock_get_client.return_value = mock_client

    generate_minutes(sample_extracted_data)

    call_args = mock_client.chat.completions.create.call_args
    sent_messages = call_args.kwargs["messages"]
    sent_text = "\n".join(m["content"] for m in sent_messages)
    for warning in sample_extracted_data.caution_warnings + sample_extracted_data.ambiguous_warnings:
        assert warning not in sent_text
    assert "caution_warnings" not in sent_text
    assert "ambiguous_warnings" not in sent_text


@patch("ai.minutes.get_client")
def test_generate_minutes_raises_generation_error_on_empty_content(mock_get_client, sample_extracted_data):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _fake_response(None)
    mock_get_client.return_value = mock_client

    with pytest.raises(GenerationError):
        generate_minutes(sample_extracted_data)
