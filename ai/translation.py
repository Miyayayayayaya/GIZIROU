from pydantic import BaseModel

from .client import call_with_retry, get_client
from .config import DEFAULT_MODEL
from .errors import GenerationError
from .schemas import FollowUpEmail


class EnglishFollowUpContent(BaseModel):
    external_minutes: str
    subject: str
    body: str


_TRANSLATION_SYSTEM_PROMPT = """You are a professional business translator. Translate the provided Japanese external meeting minutes and follow-up email into clear, formal business English.

Rules:
- Preserve all facts, decisions, names, dates, deadlines, and URLs exactly. Do not add, omit, or infer information.
- Translate the full meeting minutes, not a summary.
- Write a polite, natural follow-up email suitable for external business recipients.
- Do not include warnings or confidential information that is not present in the supplied text.
- Return only the requested structured fields."""


def generate_english_followup_content(
    japanese_minutes: str,
    japanese_email: FollowUpEmail,
    *,
    model: str = DEFAULT_MODEL,
) -> EnglishFollowUpContent:
    """Generate the English minutes and email draft from reviewed Japanese drafts."""
    client = get_client()
    user_content = (
        "Translate the following Japanese meeting minutes and follow-up email.\n\n"
        f"[Japanese meeting minutes]\n{japanese_minutes}\n\n"
        f"[Japanese email subject]\n{japanese_email.subject}\n\n"
        f"[Japanese email body]\n{japanese_email.body}"
    )
    try:
        response = call_with_retry(
            client.chat.completions.parse,
            model=model,
            messages=[
                {"role": "system", "content": _TRANSLATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format=EnglishFollowUpContent,
        )
    except Exception as exc:
        raise GenerationError(f"Failed to generate English follow-up content: {exc}") from exc

    translated = response.choices[0].message.parsed
    if translated is None or not translated.external_minutes.strip() or not translated.subject.strip() or not translated.body.strip():
        raise GenerationError("Model returned incomplete English follow-up content")
    return translated