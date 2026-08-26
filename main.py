from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import urlencode, urlparse

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session, url_for
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY", secrets.token_urlsafe(32)),
    GOOGLE_OAUTH_REDIRECT_URI=os.getenv(
        "GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:5000/oauth2callback"
    ),
)

ALLOWED_EXTENSIONS = {".txt"}
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
# Google permits HTTP only for local OAuth callbacks. Keep every non-local setup on HTTPS.
_redirect_host = urlparse(app.config["GOOGLE_OAUTH_REDIRECT_URI"]).hostname
if _redirect_host in {"localhost", "127.0.0.1", "::1"}:
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


def is_valid_email(address: str) -> bool:
    """Perform a deliberately strict validation before submission."""
    _, parsed_address = parseaddr(address)
    return parsed_address == address and "@" in address and "." in address.rsplit("@", 1)[-1]


def oauth_client_config() -> dict:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("Google OAuthの設定が完了していません。")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [app.config["GOOGLE_OAUTH_REDIRECT_URI"]],
        }
    }


def create_oauth_flow(
    state: str | None = None, code_verifier: str | None = None
) -> Flow:
    return Flow.from_client_config(
        oauth_client_config(),
        scopes=[GMAIL_SEND_SCOPE],
        state=state,
        code_verifier=code_verifier,
        redirect_uri=app.config["GOOGLE_OAUTH_REDIRECT_URI"],
    )


def token_database() -> sqlite3.Connection:
    """Open the local, server-side token store (never put tokens in cookies)."""
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(Path(app.instance_path) / "oauth_tokens.sqlite3")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS oauth_tokens (session_id TEXT PRIMARY KEY, token_json TEXT NOT NULL)"
    )
    return connection


def save_credentials(credentials: Credentials) -> None:
    token_id = session.get("oauth_token_id") or secrets.token_urlsafe(32)
    session["oauth_token_id"] = token_id
    with token_database() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO oauth_tokens (session_id, token_json) VALUES (?, ?)",
            (token_id, credentials.to_json()),
        )


def clear_credentials() -> None:
    token_id = session.pop("oauth_token_id", None)
    if token_id:
        with token_database() as connection:
            connection.execute("DELETE FROM oauth_tokens WHERE session_id = ?", (token_id,))


def get_credentials() -> Credentials | None:
    token_id = session.get("oauth_token_id")
    if not token_id:
        return None
    with token_database() as connection:
        row = connection.execute(
            "SELECT token_json FROM oauth_tokens WHERE session_id = ?", (token_id,)
        ).fetchone()
    if not row:
        return None
    credentials = Credentials.from_authorized_user_info(json.loads(row[0]), [GMAIL_SEND_SCOPE])
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except RefreshError:
            clear_credentials()
            return None
        save_credentials(credentials)
    return credentials if credentials.valid else None


def is_gmail_connected() -> bool:
    return get_credentials() is not None


def send_follow_up_email(recipient: str, subject: str, body: str) -> None:
    credentials = get_credentials()
    if not credentials:
        raise RuntimeError("Googleアカウントの連携が必要です。")
    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    build("gmail", "v1", credentials=credentials, cache_discovery=False).users().messages().send(
        userId="me", body={"raw": raw_message}
    ).execute()


def read_transcript() -> tuple[str, str | None]:
    """Return submitted transcript text without saving the uploaded file."""
    pasted_text = request.form.get("transcript", "").strip()
    uploaded_file = request.files.get("transcript_file")
    if pasted_text:
        return pasted_text, None
    if not uploaded_file or not uploaded_file.filename:
        return "", "文字起こしを貼り付けるか、txtファイルを選択してください。"
    if Path(uploaded_file.filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        return "", "アップロードできるファイルは.txt形式のみです。"
    try:
        return uploaded_file.read().decode("utf-8-sig").strip(), None
    except UnicodeDecodeError:
        return "", "ファイルを読み込めませんでした。UTF-8形式のtxtファイルを選択してください。"


def build_calendar_url() -> str:
    query = urlencode({"action": "TEMPLATE", "text": "進捗確認会", "dates": "20260903T140000/20260903T150000", "details": "前回会議の進捗確認", "ctz": "Asia/Tokyo"})
    return f"https://calendar.google.com/calendar/render?{query}"


def build_demo_analysis(transcript: str) -> dict:
    excerpt = " ".join(transcript.split())[:120]
    calendar_url = build_calendar_url()
    return {
        "external_minutes": "本日はお打ち合わせのお時間をいただき、ありがとうございました。\n\n新サービス導入に向けた進行方針を確認し、初回提案では対象部門を限定して検証を開始することで合意しました。検証結果を踏まえ、次回会議で今後の展開方針を協議します。",
        "decisions": ["初回検証は営業部門を対象に実施する", "検証結果を次回会議で共有し、展開方針を決定する"],
        "action_items": [{"task": "見積書と導入スケジュール案を送付する", "assignee": "田中", "deadline": "2026-09-01"}, {"task": "検証参加者を社内で確認する", "assignee": "佐藤", "deadline": "未確定"}],
        "warnings": ["「来週まで」という期限が曖昧です。具体的な日付を確認してください。", "社内の原価情報が含まれていないか、送信前に確認してください。"],
        "next_meeting": {"detected": True, "date_confirmed": True, "title": "進捗確認会", "date": "2026-09-03", "start_time": "14:00", "end_time": "15:00", "calendar_url": calendar_url},
        "email": {"to": "", "subject": "本日のお打ち合わせ内容と今後の進め方について", "body": f"ご担当者様\n\n本日はお打ち合わせのお時間をいただき、ありがとうございました。\n本日の決定事項と対応事項を、下記のとおり共有いたします。\n\n【決定事項】\n・初回検証は営業部門を対象に実施する\n\n【対応事項】\n・見積書と導入スケジュール案の送付（担当：田中、期限：2026-09-01）\n\n【次回会議】\n進捗確認会（2026-09-03 14:00〜15:00）\nGoogle Calendarに追加：{calendar_url}\n\n内容をご確認いただき、認識違いなどございましたらお知らせください。\n引き続きよろしくお願いいたします。"},
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
        return render_template("index.html", error=error or "文字起こしの内容が空です。", transcript=request.form.get("transcript", "")), 400
    return render_template("review.html", result=build_demo_analysis(transcript), gmail_connected=is_gmail_connected())


@app.get("/google/connect")
def google_connect():
    try:
        flow = create_oauth_flow()
        authorization_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    except RuntimeError as error:
        return render_template("email_status.html", success=False, message=str(error)), 500
    session["oauth_state"] = state
    # The callback must reuse this PKCE verifier when exchanging the code.
    session["oauth_code_verifier"] = flow.code_verifier
    return redirect(authorization_url)


@app.get("/oauth2callback")
def oauth2callback():
    if request.args.get("error"):
        return render_template("email_status.html", success=False, message="Google連携がキャンセルされました。"), 400
    state = session.pop("oauth_state", None)
    code_verifier = session.pop("oauth_code_verifier", None)
    if not state or state != request.args.get("state") or not code_verifier:
        return render_template("email_status.html", success=False, message="Google連携の確認に失敗しました。もう一度お試しください。"), 400
    try:
        flow = create_oauth_flow(state)
        flow.fetch_token(authorization_response=request.url, include_client_id=True)
        save_credentials(flow.credentials)
    except Exception as error:
        app.logger.exception("Unable to complete Google OAuth")
        error_name = type(error).__name__
        if error_name == "InvalidClientError":
            message = "Google OAuthのClient IDまたはClient Secretが正しくありません。"
        elif error_name == "InvalidGrantError":
            message = "認可コードが無効または期限切れです。もう一度Google連携を開始してください。"
        else:
            detail = getattr(error, "description", None)
            if detail and detail != "Bad Request":
                message = f"Google連携を完了できませんでした（{error_name}: {detail}）。"
            else:
                message = f"Google連携を完了できませんでした（{error_name}）。OAuth設定を確認してください。"
        return render_template("email_status.html", success=False, message=message), 502
    return render_template("email_status.html", success=True, recipient=None, message="Googleアカウントを連携しました。メール送信が利用できます。")


@app.post("/google/disconnect")
def google_disconnect():
    clear_credentials()
    return redirect(url_for("index"))


@app.post("/send-email")
def send_email():
    recipient = request.form.get("email_to", "").strip()
    subject = request.form.get("email_subject", "").strip()
    body = request.form.get("email_body", "").strip()
    if not is_valid_email(recipient) or not subject or not body:
        return render_template("email_status.html", success=False, message="宛先、件名、メール本文を正しく入力してください。"), 400
    if not is_gmail_connected():
        return render_template("email_status.html", success=False, message="メール送信にはGoogleアカウントの連携が必要です。", connect_url=url_for("google_connect")), 401
    try:
        send_follow_up_email(recipient, subject, body)
    except HttpError:
        app.logger.exception("Gmail API rejected send request")
        return render_template("email_status.html", success=False, message="Gmail APIでメールを送信できませんでした。連携をやり直してください。"), 502
    except (OSError, RuntimeError):
        app.logger.exception("Unable to send follow-up email")
        return render_template("email_status.html", success=False, message="メールを送信できませんでした。ネットワーク接続を確認してください。"), 502
    return render_template("email_status.html", success=True, recipient=recipient)


if __name__ == "__main__":
    app.run(debug=True)