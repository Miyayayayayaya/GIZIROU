from pydantic import ValidationError

from .client import call_with_retry, get_client
from .config import DEFAULT_MODEL
from .errors import ExtractionError
from .prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_user_prompt
from .schemas import ExtractedMeetingData


def extract_meeting_data(raw_notes: str, *, model: str = DEFAULT_MODEL) -> ExtractedMeetingData:
    """Extracts structured meeting data (decisions, ToDo, caution notes,
    next meeting, summary) from a raw Japanese bullet-point meeting memo."""
    client = get_client()
    try:
        response = call_with_retry(
            client.chat.completions.parse,
            model=model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": build_extraction_user_prompt(raw_notes)},
            ],
            response_format=ExtractedMeetingData,
        )
    except Exception as exc:
        raise ExtractionError(f"Failed to extract meeting data: {exc}") from exc

    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise ExtractionError("Model refused to produce structured output")

    return parsed
