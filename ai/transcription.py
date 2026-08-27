from .client import call_with_retry, get_client
from .config import TRANSCRIPTION_MODEL
from .errors import TranscriptionError


def transcribe_audio(file_obj, filename: str, *, model: str = TRANSCRIPTION_MODEL) -> str:
    """Transcribes an audio file (mp3/mp4/mpeg/mpga/m4a/wav/webm) to
    Japanese text using OpenAI's audio transcription API. `file_obj` must
    be a file-like object positioned at the start of the audio data."""
    client = get_client()
    try:
        response = call_with_retry(
            client.audio.transcriptions.create,
            model=model,
            file=(filename, file_obj),
            language="ja",
        )
    except Exception as exc:
        raise TranscriptionError(f"Failed to transcribe audio: {exc}") from exc

    text = (response.text or "").strip()
    if not text:
        raise TranscriptionError("Transcription returned empty text")
    return text
