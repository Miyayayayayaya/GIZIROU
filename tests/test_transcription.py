from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai.errors import TranscriptionError
from ai.transcription import transcribe_audio


@patch("ai.transcription.get_client")
def test_transcribe_audio_returns_stripped_text(mock_get_client):
    mock_client = MagicMock()
    mock_client.audio.transcriptions.create.return_value = SimpleNamespace(text="  会議の内容です。  ")
    mock_get_client.return_value = mock_client

    result = transcribe_audio(BytesIO(b"fake-audio-bytes"), "meeting.mp3")

    assert result == "会議の内容です。"
    _, kwargs = mock_client.audio.transcriptions.create.call_args
    assert kwargs["language"] == "ja"
    assert kwargs["file"][0] == "meeting.mp3"


@patch("ai.transcription.get_client")
def test_transcribe_audio_raises_when_text_is_empty(mock_get_client):
    mock_client = MagicMock()
    mock_client.audio.transcriptions.create.return_value = SimpleNamespace(text="   ")
    mock_get_client.return_value = mock_client

    with pytest.raises(TranscriptionError):
        transcribe_audio(BytesIO(b"fake-audio-bytes"), "meeting.mp3")


@patch("ai.transcription.get_client")
def test_transcribe_audio_wraps_api_exceptions(mock_get_client):
    mock_client = MagicMock()
    mock_client.audio.transcriptions.create.side_effect = RuntimeError("boom")
    mock_get_client.return_value = mock_client

    with pytest.raises(TranscriptionError):
        transcribe_audio(BytesIO(b"fake-audio-bytes"), "meeting.mp3")
