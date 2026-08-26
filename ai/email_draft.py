from pydantic import BaseModel

from .client import call_with_retry, get_client
from .config import DEFAULT_MODEL
from .errors import GenerationError
from .prompts import EMAIL_SYSTEM_PROMPT, build_email_user_prompt
from .schemas import ExtractedMeetingData, FollowUpEmail


class _EmailDraft(BaseModel):
    subject: str
    body: str


def generate_followup_email(
    data: ExtractedMeetingData,
    *,
    sender_name: str | None = None,
    model: str = DEFAULT_MODEL,
) -> FollowUpEmail:
    """Generates a follow-up email (subject + body) from extracted meeting
    data. Only decisions/action_items/next_meeting/meeting_info reach the
    prompt — caution_warnings and ambiguous_warnings are never passed in.
    Recipient address is left blank ("to") since it cannot be reliably
    derived from attendee names alone; backend/user fills it in."""
    client = get_client()
    try:
        response = call_with_retry(
            client.chat.completions.parse,
            model=model,
            messages=[
                {"role": "system", "content": EMAIL_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_email_user_prompt(
                        data.model_dump_json(
                            exclude={"caution_warnings", "ambiguous_warnings"}
                        ),
                        sender_name,
                    ),
                },
            ],
            response_format=_EmailDraft,
        )
    except Exception as exc:
        raise GenerationError(f"Failed to generate follow-up email: {exc}") from exc

    draft = response.choices[0].message.parsed
    if draft is None:
        raise GenerationError("Model refused to produce the email draft")

    return FollowUpEmail(to="", subject=draft.subject, body=draft.body)
