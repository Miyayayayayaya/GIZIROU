from concurrent.futures import ThreadPoolExecutor

from .config import DEFAULT_MODEL
from .email_draft import generate_followup_email
from .extraction import extract_meeting_data
from .minutes import generate_minutes
from .translation import generate_english_followup_content


def analyze_transcript(
    raw_notes: str, *, model: str = DEFAULT_MODEL, sender_name: str | None = None
) -> dict:
    """Runs extraction, minutes generation, and email generation, and
    returns a plain dict matching the shape the frontend/backend expect
    (see review.html / main.py's build_demo_analysis in the frontend PR):

        {
            "external_minutes": str,
            "english": {
                "external_minutes": str,
                "email": {"subject": str, "body": str},
            },
            "decisions": [str, ...],
            "action_items": [{"task": str, "assignee": str, "deadline": str}, ...],
            "warnings": [str, ...],
            "next_meeting": {
                "detected": bool, "date_confirmed": bool, "title": str | None,
                "date": str | None, "start_time": str | None, "end_time": str | None,
            },
            "email": {"to": str, "subject": str, "body": str},
        }

    Note: "next_meeting" intentionally has no "calendar_url" key — that is
    built by backend from date/start_time/end_time, never by the AI.
    Unknown assignee/deadline are "未確定"; unknown next_meeting date/time
    fields are None rather than guessed.
    """
    extracted = extract_meeting_data(raw_notes, model=model)
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_minutes = executor.submit(generate_minutes, extracted, model=model)
        future_email = executor.submit(
            generate_followup_email, extracted, sender_name=sender_name, model=model
        )
        
        minutes = future_minutes.result()
        email = future_email.result()

    english = generate_english_followup_content(minutes.external_body, email, model=model)

    return {
        "external_minutes": minutes.external_body,
        "english": {
            "external_minutes": english.external_minutes,
            "email": {"subject": english.subject, "body": english.body},
        },
        "decisions": extracted.decisions,
        "action_items": [
            {"task": item.task, "assignee": item.assignee, "deadline": item.deadline}
            for item in extracted.action_items
        ],
        "warnings": extracted.caution_warnings + extracted.ambiguous_warnings,
        "next_meeting": {
            "detected": extracted.next_meeting.detected,
            "date_confirmed": extracted.next_meeting.date_confirmed,
            "title": extracted.next_meeting.title,
            "date": extracted.next_meeting.date,
            "start_time": extracted.next_meeting.start_time,
            "end_time": extracted.next_meeting.end_time,
        },
        "email": {"to": email.to, "subject": email.subject, "body": email.body},
    }
