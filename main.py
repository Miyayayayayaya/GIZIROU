from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr
from functools import wraps
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2 import id_token as google_id_token
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import httplib2
from urllib3.util import connection as urllib3_connection
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from ai import AIModuleError, TranscriptionError, analyze_transcript, transcribe_audio

# --- database.py から関数・オブジェクトをインポート ---
from database import (
    delete_meeting,
    db,
    get_all_meetings,
    get_meeting_by_id,
    init_db,
    save_meeting,
    update_next_meeting,
)

load_dotenv()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

oauth_redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:5001/oauth2callback")
configured_secret_key = os.getenv("FLASK_SECRET_KEY")

app.config.update(
    MAX_CONTENT_LENGTH=26 * 1024 * 1024,
    MAX_FORM_MEMORY_SIZE=5 * 1024 * 1024,
    SECRET_KEY=configured_secret_key or secrets.token_urlsafe(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=urlparse(oauth_redirect_uri).scheme == "https",
    GOOGLE_OAUTH_REDIRECT_URI=oauth_redirect_uri,
)
if not configured_secret_key:
    app.logger.warning("FLASK_SECRET_KEY is not set; login sessions will be reset when the app restarts")


# データベースの初期化（sqlite:///gizirou.db を作成/接続）
init_db(app)

ALLOWED_EXTENSIONS = {".txt"}
ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}
MAX_AUDIO_FILE_SIZE = 25 * 1024 * 1024  # OpenAIの音声文字起こしAPIの上限
MAX_TEXT_FILE_SIZE = 1024 * 1024
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    GMAIL_SEND_SCOPE,
    CALENDAR_EVENTS_SCOPE,
]
_redirect_host = urlparse(app.config["GOOGLE_OAUTH_REDIRECT_URI"]).hostname
LOCAL_OAUTH_HOSTS = {"localhost", "127.0.0.1", "::1"}
if _redirect_host in LOCAL_OAUTH_HOSTS:
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    # 一部のローカルネットワークではGoogle APIへのIPv6接続が応答待ちになるため、
    # localhost用OAuthの場合だけ外向きHTTP通信をIPv4に限定する。
    urllib3_connection.allowed_gai_family = lambda: socket.AF_INET


@contextmanager
def local_ipv4_dns():
    """httplib2で行うローカルOAuth用通信だけをIPv4に限定する。"""
    if _redirect_host not in LOCAL_OAUTH_HOSTS:
        yield
        return

    original_getaddrinfo = socket.getaddrinfo

    def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = ipv4_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo


def is_valid_email(address: str) -> bool:
    _, parsed_address = parseaddr(address)
    return parsed_address == address and "@" in address and "." in address.rsplit("@", 1)[-1]


def oauth_client_config() -> dict:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("Google OAuthの設定が完了していません。")
    return {"web": {"client_id": client_id, "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "redirect_uris": [app.config["GOOGLE_OAUTH_REDIRECT_URI"]]}}


def create_oauth_flow(state: str | None = None, code_verifier: str | None = None) -> Flow:
    return Flow.from_client_config(oauth_client_config(), scopes=OAUTH_SCOPES, state=state,
                                   code_verifier=code_verifier,
                                   redirect_uri=app.config["GOOGLE_OAUTH_REDIRECT_URI"])


def token_database() -> sqlite3.Connection:
    data_directory = Path(os.getenv("PERSISTENT_DATA_DIR", app.instance_path))
    data_directory.mkdir(parents=True, exist_ok=True)
    database_path = data_directory / "oauth_tokens.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        os.chmod(database_path, 0o600)
    except OSError:
        app.logger.warning("OAuth token database file permissions could not be restricted")
    connection.execute("CREATE TABLE IF NOT EXISTS oauth_tokens (session_id TEXT PRIMARY KEY, token_json TEXT NOT NULL)")
    return connection


def save_credentials(credentials: Credentials) -> None:
    token_id = session.get("oauth_token_id") or secrets.token_urlsafe(32)
    session["oauth_token_id"] = token_id
    with token_database() as connection:
        previous = connection.execute(
            "SELECT token_json FROM oauth_tokens WHERE session_id = ?", (token_id,)
        ).fetchone()
        token_info = json.loads(credentials.to_json())
        if not token_info.get("refresh_token") and previous:
            previous_info = json.loads(previous[0])
            if previous_info.get("refresh_token"):
                token_info["refresh_token"] = previous_info["refresh_token"]
        connection.execute(
            "INSERT OR REPLACE INTO oauth_tokens (session_id, token_json) VALUES (?, ?)",
            (token_id, json.dumps(token_info)),
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
        row = connection.execute("SELECT token_json FROM oauth_tokens WHERE session_id = ?", (token_id,)).fetchone()
    if not row:
        return None
    # 保存済みトークンが実際に持つ権限を維持する。新しい権限を引数で
    # 上書きすると、未認可でも認可済みと誤判定するため scopes は渡さない。
    try:
        credentials = Credentials.from_authorized_user_info(json.loads(row[0]))
    except (ValueError, TypeError, json.JSONDecodeError):
        app.logger.exception("Saved Google OAuth credentials are invalid")
        return None
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except RefreshError:
            clear_credentials()
            session.pop("user_email", None)
            session.pop("user_name", None)
            return None
        save_credentials(credentials)
    return credentials if credentials.valid else None


def is_gmail_connected() -> bool:
    try:
        credentials = get_credentials()
        return bool(credentials and credentials.has_scopes([GMAIL_SEND_SCOPE]))
    except (OSError, sqlite3.Error, ValueError, TypeError):
        app.logger.exception("Unable to check Google connection")
        return False


def has_calendar_permission(credentials: Credentials | None) -> bool:
    return bool(credentials and credentials.has_scopes([CALENDAR_EVENTS_SCOPE]))


def is_logged_in() -> bool:
    return bool(session.get("user_email"))


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if is_logged_in():
            return view(*args, **kwargs)
        if (
            request.path.startswith("/api/")
            or request.args.get("format") == "json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        ):
            return jsonify({"error": "ログインが必要です"}), 401
        return redirect(url_for("index"))

    return wrapped_view


@app.context_processor
def inject_current_user() -> dict:
    return {
        "current_user_email": session.get("user_email"),
        "current_user_name": session.get("user_name"),
    }


def send_follow_up_email(recipients: list[str], subject: str, body: str, cc_recipients: list[str] | None = None) -> None:
    credentials = get_credentials()
    if not credentials:
        raise RuntimeError("Googleアカウントの連携が必要です。")
    message = EmailMessage()
    message["To"] = ", ".join(recipients)
    if cc_recipients:
        message["Cc"] = ", ".join(cc_recipients)
    message["Subject"] = subject
    message.set_content(body)
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    app.logger.info("Sending follow-up email with Gmail API")
    with local_ipv4_dns():
        authorized_http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=15))
        build("gmail", "v1", http=authorized_http, cache_discovery=False).users().messages().send(
            userId="me", body={"raw": raw_message}).execute()
    app.logger.info("Gmail API send completed")


def build_calendar_event(next_meeting: dict) -> dict:
    """AIが抽出した確定済み日時をGoogle Calendar API形式へ変換する。"""
    if not (next_meeting.get("detected") and next_meeting.get("date_confirmed")):
        raise ValueError("次回会議の日時が確定していません。")

    date = next_meeting.get("date")
    start_time = next_meeting.get("start_time")
    end_time = next_meeting.get("end_time")
    if not date or not start_time or not end_time:
        raise ValueError("次回会議の日付・開始時刻・終了時刻が必要です。")

    try:
        timezone = ZoneInfo("Asia/Tokyo")
        start = datetime.fromisoformat(f"{date}T{start_time}").replace(tzinfo=timezone)
        end = datetime.fromisoformat(f"{date}T{end_time}").replace(tzinfo=timezone)
    except (TypeError, ValueError) as error:
        raise ValueError("次回会議の日時形式が正しくありません。") from error

    if end <= start:
        end += timedelta(days=1)

    event = {
        "summary": next_meeting.get("title") or "次回会議",
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Tokyo"},
        "description": next_meeting.get("description") or "議事郎で抽出した次回会議です。",
    }
    if next_meeting.get("location"):
        event["location"] = next_meeting["location"]
    return event


def create_calendar_event(next_meeting: dict) -> dict:
    """ログイン中ユーザーのprimaryカレンダーへ予定を作成する。"""
    credentials = get_credentials()
    if not credentials:
        raise RuntimeError("Googleアカウントの連携が必要です。")
    if not has_calendar_permission(credentials):
        raise PermissionError("Google Calendarの権限がありません。Google連携をやり直してください。")

    event_body = build_calendar_event(next_meeting)
    with local_ipv4_dns():
        authorized_http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=15))
        return build("calendar", "v3", http=authorized_http, cache_discovery=False).events().insert(
            calendarId="primary",
            body=event_body,
        ).execute()


# --- 1. 文字起こしファイル/音声ファイル/テキスト読み込み処理 ---
def read_transcript() -> tuple[str, str | None]:
    """Return submitted transcript text without saving the uploaded file."""
    pasted_text = request.form.get("transcript", "").strip()
    uploaded_file = request.files.get("transcript_file")

    if pasted_text:
        if len(pasted_text.encode("utf-8")) > MAX_TEXT_FILE_SIZE:
            return "", "文字起こし本文は1MB以下にしてください。"
        return pasted_text, None

    if not uploaded_file or not uploaded_file.filename:
        return "", "文字起こしを貼り付けるか、txt・音声ファイルを選択してください。"

    extension = Path(uploaded_file.filename).suffix.lower()

    if extension in ALLOWED_AUDIO_EXTENSIONS:
        audio_bytes = uploaded_file.read()
        if len(audio_bytes) > MAX_AUDIO_FILE_SIZE:
            return "", "音声ファイルのサイズが大きすぎます（最大25MB）。ファイルを短くしてお試しください。"
        try:
            return transcribe_audio(BytesIO(audio_bytes), uploaded_file.filename), None
        except TranscriptionError:
            app.logger.exception("Audio transcription failed")
            return "", "音声ファイルの文字起こしに失敗しました。ファイル形式やサイズをご確認のうえ、もう一度お試しください。"

    if extension not in ALLOWED_EXTENSIONS:
        return "", "アップロードできるファイルは.txt形式、または音声ファイル（mp3・m4a・wav等）です。"

    text_bytes = uploaded_file.read(MAX_TEXT_FILE_SIZE + 1)
    if len(text_bytes) > MAX_TEXT_FILE_SIZE:
        return "", "txtファイルのサイズは1MB以下にしてください。"
    try:
        return text_bytes.decode("utf-8-sig").strip(), None
    except UnicodeDecodeError:
        return "", "ファイルを読み込めませんでした。UTF-8形式のtxtファイルを選択してください。"


# --- 2. Google Calendar URL 生成関数 ---
def build_calendar_url(next_meeting: dict) -> str:
    """detected: True, date_confirmed: True かつ日時情報が存在する場合のみカレンダーURLを生成"""
    if not (next_meeting.get("detected") and next_meeting.get("date_confirmed")):
        return ""

    try:
        event = build_calendar_event(next_meeting)
        start = datetime.fromisoformat(event["start"]["dateTime"])
        end = datetime.fromisoformat(event["end"]["dateTime"])
    except (KeyError, TypeError, ValueError):
        return ""

    query = urlencode(
        {
            "action": "TEMPLATE",
            "text": event["summary"],
            "dates": f"{start.strftime('%Y%m%dT%H%M%S')}/{end.strftime('%Y%m%dT%H%M%S')}",
            "ctz": "Asia/Tokyo",
        }
    )
    return f"https://calendar.google.com/calendar/render?{query}"


# --- 3. AIによる実解析処理 ---
def run_ai_analysis(transcript: str, sender_name: str | None = None) -> dict:
    """ai.analyze_transcript() を呼び出して議事録を解析する"""
    result = analyze_transcript(transcript, sender_name=sender_name or None)

    # Backend側で Calendar URL を生成して埋め込み（AI側では生成しない）
    calendar_url = build_calendar_url(result["next_meeting"])
    result["next_meeting"]["calendar_url"] = calendar_url

    # 日時が確定している場合は、メール本文にもカレンダーURLを必ず1回だけ追加する
    if calendar_url and result.get("email"):
        email_body = result["email"].get("body", "")
        if "{calendar_url}" in email_body:
            result["email"]["body"] = email_body.replace("{calendar_url}", calendar_url)
        elif calendar_url not in email_body:
            calendar_section = f"Google Calendarに追加：\n{calendar_url}"
            result["email"]["body"] = f"{email_body.rstrip()}\n\n{calendar_section}".lstrip()

    english = result.get("english") or {}
    english_email = english.get("email") or {}
    english_minutes = english.get("external_minutes", "").strip()
    if not english_minutes or not english_email.get("subject") or not english_email.get("body"):
        raise AIModuleError("英語版の議事録を生成できませんでした。")

    english_body = english_email["body"].strip()
    if english_minutes not in english_body:
        english_body = f"{english_body}\n\nMeeting Minutes\n{english_minutes}"
    if calendar_url and calendar_url not in english_body:
        english_body = f"{english_body}\n\nAdd to Google Calendar:\n{calendar_url}"
    result["english"] = {
        "external_minutes": english_minutes,
        "email": {"subject": english_email["subject"].strip(), "body": english_body},
    }
    # UI用補助データ
    result["source_excerpt"] = " ".join(transcript.split())[:120]
    result["is_demo"] = False

    return result


# --- 4. Flask ルーティング ---
@app.before_request
def use_oauth_redirect_host():
    """OAuthセッション保持のため、ローカル開発時のホスト名を統一する。"""
    redirect_uri = urlparse(app.config["GOOGLE_OAUTH_REDIRECT_URI"])
    request_url = urlparse(request.host_url)
    request_host = request_url.hostname
    if (
        redirect_uri.hostname == "localhost"
        and request_host in LOCAL_OAUTH_HOSTS
        and request_url.port == redirect_uri.port
        and request_host != "localhost"
    ):
        canonical_url = urlunparse(
            (
                request.scheme,
                redirect_uri.netloc,
                request.path,
                "",
                request.query_string.decode(),
                "",
            )
        )
        return redirect(canonical_url)


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "アップロードできるファイルサイズを超えています。"}), 413
    return (
        render_template(
            "index.html",
            error="アップロードできるファイルサイズを超えています。",
            transcript="",
            sender_name=session.get("sender_name", ""),
        ),
        413,
    )


@app.get("/")
def index():
    if not is_logged_in():
        return render_template("login.html")
    return render_template(
        "index.html",
        error=None,
        transcript="",
        sender_name=session.get("sender_name", ""),
    )


@app.post("/analyze")
@login_required
def analyze():
    transcript, error = read_transcript()
    sender_name = request.form.get("sender_name", "").strip()
    if sender_name:
        session["sender_name"] = sender_name
    else:
        session.pop("sender_name", None)

    if error or not transcript:
        return (
            render_template(
                "index.html",
                error=error or "文字起こしの内容が空です。",
                transcript=request.form.get("transcript", ""),
                sender_name=sender_name,
            ),
            400,
        )

    try:
        analysis_result = run_ai_analysis(transcript, sender_name)

        # ★ SQLite データベースに解析結果と文字起こし原文を自動保存
        saved_meeting = save_meeting(analysis_result, transcript, session["user_email"])
        analysis_result["id"] = saved_meeting.id

        return render_template("review.html", result=analysis_result, gmail_connected=is_gmail_connected())
    except AIModuleError:
        app.logger.exception("AI analysis failed")
        message = "AIによる解析に失敗しました。API設定とネットワーク接続を確認して、もう一度お試しください。"
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Unable to save meeting analysis")
        message = "解析結果を保存できませんでした。アプリを再起動して、もう一度お試しください。"
    except Exception:
        db.session.rollback()
        app.logger.exception("Unexpected analysis failure")
        message = "解析中に予期しないエラーが発生しました。もう一度お試しください。"

    return (
        render_template(
            "index.html",
            error=message,
            transcript=transcript,
            sender_name=sender_name,
        ),
        500,
    )


@app.get("/google/connect")
def google_connect():
    try:
        flow = create_oauth_flow()
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            login_hint=session.get("user_email"),
            prompt="consent",
        )
    except RuntimeError as error:
        return render_template("email_status.html", success=False, message=str(error)), 500
    session["oauth_state"] = state
    session["oauth_code_verifier"] = flow.code_verifier
    return redirect(authorization_url)


@app.get("/oauth2callback")
def oauth2callback():
    if request.args.get("error"):
        return render_template("email_status.html", success=False, message="Google連携がキャンセルされました。"), 400
    state = session.pop("oauth_state", None)
    code_verifier = session.pop("oauth_code_verifier", None)
    app.logger.info(
        "Google OAuth callback received (host=%s, state=%s, code_verifier=%s)",
        request.host,
        bool(state),
        bool(code_verifier),
    )
    if not state or state != request.args.get("state") or not code_verifier:
        return render_template("email_status.html", success=False, message="Google連携の確認に失敗しました。もう一度お試しください。"), 400
    try:
        flow = create_oauth_flow(state, code_verifier)
        app.logger.info("Exchanging Google OAuth authorization code")
        flow.fetch_token(authorization_response=request.url, include_client_id=True, timeout=15)
        app.logger.info("Google OAuth token exchange completed")
    except Exception as error:
        app.logger.exception("Google OAuth token exchange failed: %s", error)
        return render_template(
            "email_status.html",
            success=False,
            message="Google認証コードを交換できませんでした。Client SecretとリダイレクトURIを確認してください。",
        ), 502

    try:
        id_info = google_id_token.verify_oauth2_token(
            flow.credentials.id_token,
            Request(),
            oauth_client_config()["web"]["client_id"],
            clock_skew_in_seconds=10,
        )
        if not id_info.get("email") or not id_info.get("email_verified"):
            raise ValueError("Googleアカウントのメールアドレスを確認できませんでした。")
    except ValueError as error:
        app.logger.exception("Google OAuth ID token validation failed: %s", error)
        return render_template(
            "email_status.html",
            success=False,
            message="Google IDトークンを検証できませんでした。OAuth Client IDが認証に使用したClient IDと一致するか確認してください。",
        ), 502
    except Exception as error:
        app.logger.exception("Google OAuth identity verification failed: %s", error)
        return render_template(
            "email_status.html",
            success=False,
            message="Googleアカウント情報を確認できませんでした。OAuth設定とGoogle APIへの接続を確認してください。",
        ), 502

    try:
        save_credentials(flow.credentials)
        session["user_email"] = id_info["email"]
        session["user_name"] = id_info.get("name") or id_info["email"]
    except Exception as error:
        app.logger.exception("Unable to store Google OAuth credentials: %s", error)
        return render_template("email_status.html", success=False, message="Google連携情報を保存できませんでした。もう一度お試しください。"), 502
    return redirect(url_for("index"))


@app.post("/google/disconnect")
def google_disconnect():
    clear_credentials()
    session.pop("user_email", None)
    session.pop("user_name", None)
    session.pop("oauth_state", None)
    session.pop("oauth_code_verifier", None)
    return redirect(url_for("index"))


@app.post("/send-email")
@login_required
def send_email():
    def recipients_with_language(kind: str) -> list[tuple[str, str]]:
        addresses = [address.strip() for address in request.form.getlist(f"email_{kind}") if address.strip()]
        languages = request.form.getlist(f"email_{kind}_language")
        if not languages:
            languages = ["ja"] * len(addresses)
        if len(addresses) != len(languages):
            raise ValueError("宛先と言語の指定数が一致しません。")
        return list(zip(addresses, languages))

    try:
        to_entries = recipients_with_language("to")
        cc_entries = recipients_with_language("cc")
    except ValueError:
        return render_template("email_status.html", success=False, message="宛先と言語の指定を確認してください。"), 400

    recipients = [address for address, _language in to_entries]
    cc_recipients = [address for address, _language in cc_entries]
    subject = request.form.get("email_subject", "").strip()
    body = request.form.get("email_body", "").strip()
    english_subject = request.form.get("english_email_subject", "").strip()
    english_body = request.form.get("english_email_body", "").strip()
    all_recipients = recipients + cc_recipients
    normalized_recipients = [address.casefold() for address in all_recipients]
    has_duplicate = len(normalized_recipients) != len(set(normalized_recipients))
    has_invalid_subject = any("\r" in value or "\n" in value for value in (subject, english_subject))
    language_groups = {"ja": {"to": [], "cc": []}, "en": {"to": [], "cc": []}}
    for kind, entries in (("to", to_entries), ("cc", cc_entries)):
        for address, language in entries:
            if language not in language_groups:
                return render_template("email_status.html", success=False, message="送信言語の指定が正しくありません。"), 400
            language_groups[language][kind].append(address)

    english_is_selected = bool(language_groups["en"]["to"] or language_groups["en"]["cc"])
    has_cc_without_to = any(group["cc"] and not group["to"] for group in language_groups.values())
    if (
        not recipients
        or not all(is_valid_email(address) for address in all_recipients)
        or has_duplicate
        or not subject
        or not body
        or has_invalid_subject
        or (english_is_selected and (not english_subject or not english_body))
        or has_cc_without_to
    ):
        return render_template(
            "email_status.html",
            success=False,
            message="Toを各送信言語で1件以上追加し、重複のない正しい宛先、件名、メール本文を入力してください。",
        ), 400
    if not is_gmail_connected():
        return render_template("email_status.html", success=False, message="メール送信にはGoogleアカウントの連携が必要です。", connect_url=url_for("google_connect")), 401
    try:
        for language, group in language_groups.items():
            if not group["to"]:
                continue
            selected_subject, selected_body = (subject, body) if language == "ja" else (english_subject, english_body)
            send_follow_up_email(group["to"], selected_subject, selected_body, group["cc"])
    except HttpError:
        app.logger.exception("Gmail API rejected send request")
        return render_template("email_status.html", success=False, message="Gmail APIでメールを送信できませんでした。連携をやり直してください。"), 502
    except (OSError, RuntimeError, ValueError):
        app.logger.exception("Unable to send follow-up email")
        return render_template("email_status.html", success=False, message="メールを送信できませんでした。ネットワーク接続を確認してください。"), 502

    summaries = []
    for language, group in language_groups.items():
        if group["to"]:
            summary = f"{'日本語' if language == 'ja' else 'English'}: To: {', '.join(group['to'])}"
            if group["cc"]:
                summary += f" / CC: {', '.join(group['cc'])}"
            summaries.append(summary)
    return render_template("email_status.html", success=True, recipient=" / ".join(summaries))
@app.post("/api/meetings/<int:meeting_id>/calendar-event")
@login_required
def api_add_calendar_event(meeting_id: int):
    """確定済みの次回会議をログイン中ユーザーのカレンダーへ追加する。"""
    owner_email = session["user_email"]
    meeting = get_meeting_by_id(meeting_id, owner_email)
    if not meeting:
        return jsonify({"error": "指定された議事録が見つかりません。"}), 404

    next_meeting = dict(meeting.next_meeting or {})
    if next_meeting.get("calendar_event_id"):
        return jsonify(
            {
                "message": "この予定はすでにGoogle Calendarへ追加されています。",
                "event_url": next_meeting.get("calendar_event_url", ""),
                "already_added": True,
            }
        )

    try:
        event = create_calendar_event(dict(next_meeting))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except PermissionError as error:
        return jsonify({"error": str(error), "connect_url": url_for("google_connect")}), 403
    except RuntimeError as error:
        return jsonify({"error": str(error), "connect_url": url_for("google_connect")}), 401
    except HttpError:
        app.logger.exception("Google Calendar API rejected event creation")
        return jsonify({"error": "Google Calendarに予定を追加できませんでした。Google連携をやり直してください。"}), 502
    except OSError:
        app.logger.exception("Unable to create Google Calendar event")
        return jsonify({"error": "Google Calendarに接続できませんでした。ネットワーク接続を確認してください。"}), 502

    next_meeting.update(
        {
            "calendar_event_id": event.get("id"),
            "calendar_event_url": event.get("htmlLink", ""),
        }
    )
    try:
        persisted = update_next_meeting(meeting_id, next_meeting, owner_email)
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Calendar event was created but its ID could not be saved")
        persisted = None

    if not persisted:
        return jsonify(
            {
                "message": "Google Calendarへの追加は完了しましたが、履歴へ記録できませんでした。再試行せずCalendarで予定を確認してください。",
                "event_url": next_meeting["calendar_event_url"],
                "already_added": False,
                "persistence_warning": True,
            }
        ), 201

    return jsonify(
        {
            "message": "自分のGoogle Calendarに追加しました。",
            "event_url": next_meeting["calendar_event_url"],
            "already_added": False,
        }
    ), 201

# =====================================================================
# SQLite データベース用 (履歴一覧・詳細表示・削除) ルーティング
# =====================================================================

@app.get("/meetings")
@login_required
def list_meetings():
    """履歴一覧を取得（JSON返却 または HTML描画）"""
    meetings = get_all_meetings(session["user_email"])

    # API(JSON)リクエスト、またはクエリパラメータ format=json の場合
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("format") == "json":
        return jsonify([m.to_dict() for m in meetings])

    return render_template("history.html", meetings=[m.to_dict() for m in meetings])


@app.get("/meetings/<int:meeting_id>")
@login_required
def get_meeting(meeting_id: int):
    """特定の会議詳細を取得"""
    meeting = get_meeting_by_id(meeting_id, session["user_email"])
    if not meeting:
        return jsonify({"error": "指定された議事録が見つかりません"}), 404

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("format") == "json":
        return jsonify(meeting.to_dict())

    return render_template("review.html", result=meeting.to_dict(), gmail_connected=is_gmail_connected())


@app.delete("/api/meetings/<int:meeting_id>")
@login_required
def api_delete_meeting(meeting_id: int):
    """特定会議データの削除API"""
    success = delete_meeting(meeting_id, session["user_email"])
    if not success:
        return jsonify({"error": "削除対象が見つかりませんでした"}), 404

    return jsonify({"message": "正常に削除されました", "id": meeting_id}), 200


if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "5001")),
        debug=os.getenv("FLASK_DEBUG", "").casefold() in {"1", "true", "yes"},
    )