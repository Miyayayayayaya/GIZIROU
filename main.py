from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, render_template, request


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

ALLOWED_EXTENSIONS = {".txt"}


def read_transcript() -> tuple[str, str | None]:
    """Return submitted transcript text without saving the uploaded file."""
    pasted_text = request.form.get("transcript", "").strip()
    uploaded_file = request.files.get("transcript_file")

    if pasted_text:
        return pasted_text, None

    if not uploaded_file or not uploaded_file.filename:
        return "", "文字起こしを貼り付けるか、txtファイルを選択してください。"

    extension = Path(uploaded_file.filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        return "", "アップロードできるファイルは.txt形式のみです。"

    try:
        return uploaded_file.read().decode("utf-8-sig").strip(), None
    except UnicodeDecodeError:
        return "", "ファイルを読み込めませんでした。UTF-8形式のtxtファイルを選択してください。"


def build_calendar_url() -> str:
    """Build sample URL in Backend; replace inputs with extracted meeting data."""
    query = urlencode(
        {
            "action": "TEMPLATE",
            "text": "進捗確認会",
            "dates": "20260903T140000/20260903T150000",
            "details": "前回会議の進捗確認",
            "ctz": "Asia/Tokyo",
        }
    )
    return f"https://calendar.google.com/calendar/render?{query}"


def build_demo_analysis(transcript: str) -> dict:
    """Temporary UI data until the AI service is connected by Backend/AI teams."""
    excerpt = " ".join(transcript.split())[:120]
    calendar_url = build_calendar_url()
    return {
        "external_minutes": (
            "本日はお打ち合わせのお時間をいただき、ありがとうございました。\n\n"
            "新サービス導入に向けた進行方針を確認し、初回提案では対象部門を限定して"
            "検証を開始することで合意しました。検証結果を踏まえ、次回会議で今後の"
            "展開方針を協議します。"
        ),
        "decisions": [
            "初回検証は営業部門を対象に実施する",
            "検証結果を次回会議で共有し、展開方針を決定する",
        ],
        "action_items": [
            {"task": "見積書と導入スケジュール案を送付する", "assignee": "田中", "deadline": "2026-09-01"},
            {"task": "検証参加者を社内で確認する", "assignee": "佐藤", "deadline": "未確定"},
        ],
        "warnings": [
            "「来週まで」という期限が曖昧です。具体的な日付を確認してください。",
            "社内の原価情報が含まれていないか、送信前に確認してください。",
        ],
        "next_meeting": {
            "detected": True,
            "date_confirmed": True,
            "title": "進捗確認会",
            "date": "2026-09-03",
            "start_time": "14:00",
            "end_time": "15:00",
            "calendar_url": calendar_url,
        },
        "email": {
            "to": "",
            "subject": "本日のお打ち合わせ内容と今後の進め方について",
            "body": (
                "ご担当者様\n\n"
                "本日はお打ち合わせのお時間をいただき、ありがとうございました。\n"
                "本日の決定事項と対応事項を、下記のとおり共有いたします。\n\n"
                "【決定事項】\n"
                "・初回検証は営業部門を対象に実施する\n\n"
                "【対応事項】\n"
                "・見積書と導入スケジュール案の送付（担当：田中、期限：2026-09-01）\n\n"
                "【次回会議】\n"
                "進捗確認会（2026-09-03 14:00〜15:00）\n"
                f"Google Calendarに追加：{calendar_url}\n\n"
                "内容をご確認いただき、認識違いなどございましたらお知らせください。\n"
                "引き続きよろしくお願いいたします。"
            ),
        },
        "source_excerpt": excerpt,
        "is_demo": True,
    }


@app.get("/")
def index():
    return render_template("index.html", error=None, transcript="")


@app.post("/analyze")
def analyze():
    transcript, error = read_transcript()
    if error or not transcript:
        return (
            render_template(
                "index.html",
                error=error or "文字起こしの内容が空です。",
                transcript=request.form.get("transcript", ""),
            ),
            400,
        )

    return render_template("review.html", result=build_demo_analysis(transcript))


if __name__ == "__main__":
    app.run(debug=True)
