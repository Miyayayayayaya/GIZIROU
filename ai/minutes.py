from .client import call_with_retry, get_client
from .config import DEFAULT_MODEL
from .errors import GenerationError
from .prompts import MINUTES_SYSTEM_PROMPT, build_minutes_user_prompt
from .schemas import ExtractedMeetingData, MeetingMinutes


def generate_minutes(data: ExtractedMeetingData, *, model: str = DEFAULT_MODEL) -> MeetingMinutes:
    """Generates formal, external-facing Japanese meeting minutes from
    extracted meeting data. Only decisions/action_items/next_meeting/
    meeting_info reach the prompt — caution_warnings and ambiguous_warnings
    are never passed in, so they cannot leak into external_body."""
    client = get_client()
    try:
        response = call_with_retry(
            client.chat.completions.create,
            model=model,
            messages=[
                {"role": "system", "content": MINUTES_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_minutes_user_prompt(
                        data.model_dump_json(
                            exclude={"caution_warnings", "ambiguous_warnings"}
                        )
                    ),
                },
            ],
        )
    except Exception as exc:
        raise GenerationError(f"Failed to generate minutes: {exc}") from exc

    content = response.choices[0].message.content
    if not content:
        raise GenerationError("Model returned an empty minutes body")

    title = data.meeting_info.title or "議事録"
    return MeetingMinutes(title=title, external_body=content, source_data=data)
