from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import sqlite3
from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import parseaddr
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
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
from ai import analyze_transcript

# --- database.py から関数・オブジェクトをインポート ---
from database import (
    delete_meeting,
    get_all_meetings,
    get_meeting_by_id,
    init_db,
    save_meeting,
)

load_dotenv()

app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY", secrets.token_urlsafe(32)),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    GOOGLE_OAUTH_REDIRECT_URI=os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:5001/oauth2callback"),
)

# データベースの初期化（sqlite:///gizirou.db を作成/接続）
init_db(app)

ALLOWED_EXTENSIONS = {".txt"}
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    GMAIL_SEND_SCOPE,
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
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(Path(app.instance_path) / "oauth_tokens.sqlite3")
    connection.execute("CREATE TABLE IF NOT EXISTS oauth_tokens (session_id TEXT PRIMARY KEY, token_json TEXT NOT NULL)")
    return connection


def save_credentials(credentials: Credentials) -> None:
    token_id = session.get("oauth_token_id") or secrets.token_urlsafe(32)
    session["oauth_token_id"] = token_id
    with token_database() as connection:
        connection.execute("INSERT OR REPLACE INTO oauth_tokens (session_id, token_json) VALUES (?, ?)", (token_id, credentials.to_json()))


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
    credentials = Credentials.from_authorized_user_info(json.loads(row[0]), OAUTH_SCOPES)
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
    return get_credentials() is not None


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


# --- 1. 文字起こしファイル/テキスト読み込み処理 ---
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


# --- 2. Google Calendar URL 生成関数 ---
def build_calendar_url(next_meeting: dict) -> str:
    """detected: True, date_confirmed: True かつ日時情報が存在する場合のみカレンダーURLを生成"""
    if not (next_meeting.get("detected") and next_meeting.get("date_confirmed")):
        return ""

    date_str = (next_meeting.get("date") or "").replace("-", "")
    start_time_str = (next_meeting.get("start_time") or "").replace(":", "")
    end_time_str = (next_meeting.get("end_time") or "").replace(":", "")

    # 日付と開始時刻が無い場合は生成不可
    if not date_str or not start_time_str:
        return ""

    # 終了時刻が無い場合は開始時刻から1時間後に設定
    if not end_time_str:
        try:
            start_hour = int(start_time_str[:2])
            end_time_str = f"{(start_hour + 1) % 24:02d}{start_time_str[2:]}"
        except ValueError:
            end_time_str = start_time_str

    start_iso = f"{date_str}T{start_time_str}00"
    end_iso = f"{date_str}T{end_time_str}00"

    query = urlencode(
        {
            "action": "TEMPLATE",
            "text": next_meeting.get("title") or "次回会議",
            "dates": f"{start_iso}/{end_iso}",
            "ctz": "Asia/Tokyo",
        }
    )
    return f"https://calendar.google.com/calendar/render?{query}"


# --- 3. AIによる実解析処理 ---
def run_ai_analysis(transcript: str) -> dict:
    """ai.analyze_transcript() を呼び出して議事録を解析する"""
    result = analyze_transcript(transcript)

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

    # UI用補助データ
    result["source_excerpt"] = " ".join(transcript.split())[:120]
    result["is_demo"] = False

    return result


# --- 4. Flask ルーティング ---
@app.before_request
def use_oauth_redirect_host():
    """OAuthセッション保持のため、ローカル開発時のホスト名を統一する。"""
    redirect_uri = urlparse(app.config["GOOGLE_OAUTH_REDIRECT_URI"])
    request_host = urlparse(request.host_url).hostname
    if redirect_uri.hostname == "localhost" and request_host != "localhost":
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


@app.get("/")
def index():
    if not is_logged_in():
        return render_template("login.html")
    return render_template("index.html", error=None, transcript="")


@app.post("/analyze")
@login_required
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

    try:
        analysis_result = run_ai_analysis(transcript)

        # ★ SQLite データベースに解析結果と文字起こし原文を自動保存
        saved_meeting = save_meeting(analysis_result, transcript)
        analysis_result["id"] = saved_meeting.id

        return render_template("review.html", result=analysis_result, gmail_connected=is_gmail_connected())
    except Exception as e:
        return (
            render_template(
                "index.html",
                error=f"解析エラーが発生しました: {str(e)}",
                transcript=transcript,
            ),
            500,
        )


@app.get("/google/connect")
def google_connect():
    try:
        flow = create_oauth_flow()
        authorization_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
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
    recipients = [address.strip() for address in request.form.getlist("email_to") if address.strip()]
    cc_recipients = [address.strip() for address in request.form.getlist("email_cc") if address.strip()]
    subject = request.form.get("email_subject", "").strip()
    body = request.form.get("email_body", "").strip()
    all_recipients = recipients + cc_recipients
    normalized_recipients = [address.casefold() for address in all_recipients]
    has_duplicate = len(normalized_recipients) != len(set(normalized_recipients))
    has_invalid_subject = "\r" in subject or "\n" in subject
    if not recipients or not all(is_valid_email(address) for address in all_recipients) or has_duplicate or not subject or has_invalid_subject or not body:
        return render_template(
            "email_status.html",
            success=False,
            message="Toを1件以上追加し、重複のない正しい宛先、件名、メール本文を入力してください。",
        ), 400
    if not is_gmail_connected():
        return render_template("email_status.html", success=False, message="メール送信にはGoogleアカウントの連携が必要です。", connect_url=url_for("google_connect")), 401
    try:
        send_follow_up_email(recipients, subject, body, cc_recipients)
    except HttpError:
        app.logger.exception("Gmail API rejected send request")
        return render_template("email_status.html", success=False, message="Gmail APIでメールを送信できませんでした。連携をやり直してください。"), 502
    except (OSError, RuntimeError, ValueError):
        app.logger.exception("Unable to send follow-up email")
        return render_template("email_status.html", success=False, message="メールを送信できませんでした。ネットワーク接続を確認してください。"), 502
    recipient_summary = f"To: {', '.join(recipients)}"
    if cc_recipients:
        recipient_summary += f" / CC: {', '.join(cc_recipients)}"
    return render_template("email_status.html", success=True, recipient=recipient_summary)

# =====================================================================
# SQLite データベース用 (履歴一覧・詳細表示・削除) ルーティング
# =====================================================================

@app.get("/meetings")
@login_required
def list_meetings():
    """履歴一覧を取得（JSON返却 または HTML描画）"""
    meetings = get_all_meetings()

    # API(JSON)リクエスト、またはクエリパラメータ format=json の場合
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("format") == "json":
        return jsonify([m.to_dict() for m in meetings])

    return render_template("history.html", meetings=[m.to_dict() for m in meetings])


@app.get("/meetings/<int:meeting_id>")
@login_required
def get_meeting(meeting_id: int):
    """特定の会議詳細を取得"""
    meeting = get_meeting_by_id(meeting_id)
    if not meeting:
        return jsonify({"error": "指定された議事録が見つかりません"}), 404

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("format") == "json":
        return jsonify(meeting.to_dict())

    return render_template("review.html", result=meeting.to_dict(), gmail_connected=is_gmail_connected())


@app.delete("/api/meetings/<int:meeting_id>")
@login_required
def api_delete_meeting(meeting_id: int):
    """特定会議データの削除API"""
    success = delete_meeting(meeting_id)
    if not success:
        return jsonify({"error": "削除対象が見つかりませんでした"}), 404

    return jsonify({"message": "正常に削除されました", "id": meeting_id}), 200


if __name__ == "__main__":
    # macOSの localhost は IPv6 (::1) を優先することがあるため、
    # Google OAuth のコールバックを IPv4 / IPv6 の両方で受け付ける。
    app.run(host="::", port=5001, debug=True)
