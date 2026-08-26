from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

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
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

# データベースの初期化（sqlite:///gizirou.db を作成/接続）
init_db(app)

ALLOWED_EXTENSIONS = {".txt"}


# --- 1. LLM出力用 Pydantic スキーマ定義 ---
class ActionItem(BaseModel):
    task: str = Field(description="タスク内容")
    assignee: str = Field(description="担当者。不明な場合は'未確定'")
    deadline: str = Field(description="期限。不明な場合は'未確定'")


class NextMeeting(BaseModel):
    detected: bool = Field(description="次回会議の記述や話題が存在するかどうか")
    date_confirmed: bool = Field(description="次回会議の「日付」と「開始時間」が確定しているかどうか")
    title: str = Field(default="次回会議", description="次回会議のタイトル")
    date: str = Field(default="", description="YYYY-MM-DD形式。不明なら空文字")
    start_time: str = Field(default="", description="HH:MM形式（24時間表記）。不明なら空文字")
    end_time: str = Field(default="", description="HH:MM形式（24時間表記）。不明なら空文字")


class Email(BaseModel):
    to: str = Field(default="", description="送信先メールアドレス。不明なら空文字")
    subject: str = Field(description="件名")
    body: str = Field(description="メール本文")


class AnalysisOutput(BaseModel):
    external_minutes: str = Field(description="社外・チーム共有用の要約・議事録テキスト")
    decisions: list[str] = Field(description="会議で決定した事項のリスト")
    action_items: list[ActionItem] = Field(description="アクションアイテムのリスト")
    warnings: list[str] = Field(description="期限の曖昧さ、未確定要素、機密情報等の警告・注意事項")
    next_meeting: NextMeeting
    email: Email


# --- 2. 文字起こしファイル/テキスト読み込み処理 ---
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


# --- 3. Google Calendar URL 生成関数 ---
def build_calendar_url(next_meeting: dict) -> str:
    """detected: True, date_confirmed: True かつ日時情報が存在する場合のみカレンダーURLを生成"""
    if not (next_meeting.get("detected") and next_meeting.get("date_confirmed")):
        return ""

    date_str = next_meeting.get("date", "").replace("-", "")
    start_time_str = next_meeting.get("start_time", "").replace(":", "")
    end_time_str = next_meeting.get("end_time", "").replace(":", "")

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


# --- 4. AIによる実解析処理 ---
def run_ai_analysis(transcript: str) -> dict:
    """OpenAI API（LangChain Structured Output）を呼び出して議事録を解析"""
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEYが設定されていません。.envファイルを確認してください。")

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    structured_llm = llm.with_structured_output(AnalysisOutput)

    prompt = f"""
あなたは優秀な会議議事録アシスタントです。
以下の文字起こしテキストを解析し、指定された形式で情報を抽出してください。

【重要な制約事項】
- 不明な担当者・期限・日時は決して推測せず、空値または「未確定」で返してください。
- 次回会議の「日付」および「開始時間」が明示的に決まっていない場合は、next_meeting.date_confirmed を false にしてください。
- 次回会議自体についての話題がない場合は、next_meeting.detected を false にしてください。

【文字起こしテキスト】
{transcript}
"""

    parsed_result: AnalysisOutput = structured_llm.invoke(prompt)
    result_dict = parsed_result.model_dump()

    # Backend側で Calendar URL を生成して埋め込み
    calendar_url = build_calendar_url(result_dict["next_meeting"])
    result_dict["next_meeting"]["calendar_url"] = calendar_url

    # メール本文にカレンダーURLが含まれていれば反映補助
    if calendar_url and "{calendar_url}" in result_dict["email"]["body"]:
        result_dict["email"]["body"] = result_dict["email"]["body"].replace("{calendar_url}", calendar_url)

    # UI用補助データ
    excerpt = " ".join(transcript.split())[:120]
    result_dict["source_excerpt"] = excerpt
    result_dict["is_demo"] = False

    return result_dict


# --- 5. Flask ルーティング ---
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

    try:
        # AI実処理の呼び出し
        analysis_result = run_ai_analysis(transcript)
        
        # ★ SQLite データベースに解析結果と文字起こし原文を自動保存
        saved_meeting = save_meeting(analysis_result, transcript)
        analysis_result["id"] = saved_meeting.id

        return render_template("review.html", result=analysis_result)
    except Exception as e:
        return (
            render_template(
                "index.html",
                error=f"解析エラーが発生しました: {str(e)}",
                transcript=transcript,
            ),
            500,
        )


# =====================================================================
# SQLite データベース用 (履歴一覧・詳細表示・削除) ルーティング
# =====================================================================

@app.get("/meetings")
def list_meetings():
    """履歴一覧を取得（JSON返却 または HTML描画）"""
    meetings = get_all_meetings()
    
    # API(JSON)リクエスト、またはクエリパラメータ format=json の場合
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("format") == "json":
        return jsonify([m.to_dict() for m in meetings])
        
    return render_template("history.html", meetings=[m.to_dict() for m in meetings])


@app.get("/meetings/<int:meeting_id>")
def get_meeting(meeting_id: int):
    """特定の会議詳細を取得"""
    meeting = get_meeting_by_id(meeting_id)
    if not meeting:
        return jsonify({"error": "指定された議事録が見つかりません"}), 404

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("format") == "json":
        return jsonify(meeting.to_dict())

    return render_template("review.html", result=meeting.to_dict())


@app.delete("/api/meetings/<int:meeting_id>")
def api_delete_meeting(meeting_id: int):
    """特定会議データの削除API"""
    success = delete_meeting(meeting_id)
    if not success:
        return jsonify({"error": "削除対象が見つかりませんでした"}), 404
        
    return jsonify({"message": "正常に削除されました", "id": meeting_id}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)