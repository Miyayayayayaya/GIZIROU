from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for
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
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

# データベースの初期化（sqlite:///gizirou.db を作成/接続）
init_db(app)

ALLOWED_EXTENSIONS = {".txt"}


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

    # メール本文にカレンダーURLが含まれていれば反映補助
    if calendar_url and result.get("email") and "{calendar_url}" in result["email"].get("body", ""):
        result["email"]["body"] = result["email"]["body"].replace("{calendar_url}", calendar_url)

    # UI用補助データ
    result["source_excerpt"] = " ".join(transcript.split())[:120]
    result["is_demo"] = False

    return result


# --- 4. Flask ルーティング ---
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
