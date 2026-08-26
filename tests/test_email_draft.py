from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai.email_draft import generate_followup_email
from ai.errors import GenerationError
from ai.schemas import FollowUpEmail


def _fake_response(parsed):
    message = SimpleNamespace(parsed=parsed)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


@patch("ai.email_draft.get_client")
def test_generate_followup_email_leaves_to_blank(mock_get_client, sample_extracted_data):
    from ai.email_draft import _EmailDraft

    draft = _EmailDraft(subject="打ち合わせのご報告", body="お世話になっております。")
    mock_client = MagicMock()
    mock_client.chat.completions.parse.return_value = _fake_response(draft)
    mock_get_client.return_value = mock_client

    result = generate_followup_email(sample_extracted_data)

    assert isinstance(result, FollowUpEmail)
    assert result.subject == "打ち合わせのご報告"
    assert result.to == ""


@patch("ai.email_draft.get_client")
def test_generate_followup_email_never_leaks_warnings(mock_get_client, sample_extracted_data):
    from ai.email_draft import _EmailDraft

    draft = _EmailDraft(subject="件名", body="本文")
    mock_client = MagicMock()
    mock_client.chat.completions.parse.return_value = _fake_response(draft)
    mock_get_client.return_value = mock_client

    generate_followup_email(sample_extracted_data)

    call_args = mock_client.chat.completions.parse.call_args
    sent_messages = call_args.kwargs["messages"]
    sent_text = "\n".join(m["content"] for m in sent_messages)
    for warning in sample_extracted_data.caution_warnings + sample_extracted_data.ambiguous_warnings:
        assert warning not in sent_text


@patch("ai.email_draft.get_client")
def test_generate_followup_email_raises_generation_error_when_parsed_is_none(
    mock_get_client, sample_extracted_data
):
    mock_client = MagicMock()
    mock_client.chat.completions.parse.return_value = _fake_response(None)
    mock_get_client.return_value = mock_client

    with pytest.raises(GenerationError):
        generate_followup_email(sample_extracted_data)
