# `ai` パッケージ（AI担当スコープ）

会議の文字起こしから、社外向け議事録・決定事項・ToDo・注意情報・次回会議情報・フォローアップメールを生成する。担当チケット: #2, #12, #14, #20。

## セットアップ

```bash
pip install -r requirements.txt
cp .env.example .env
# .env の OPENAI_API_KEY に実際のキーを設定
```

## Backend担当向け: 使い方

`main.py` の `build_demo_analysis()` を置き換える際は、以下の1関数だけ呼べば良い。

```python
from ai import analyze_transcript

result = analyze_transcript(transcript)
```

`result` は `templates/review.html` が前提としている形そのままの dict:

```python
{
    "external_minutes": str,
    "decisions": [str, ...],
    "action_items": [{"task": str, "assignee": str, "deadline": str}, ...],
    "warnings": [str, ...],
    "next_meeting": {
        "detected": bool,
        "date_confirmed": bool,
        "title": str | None,
        "date": str | None,        # "YYYY-MM-DD"
        "start_time": str | None,  # "HH:MM"
        "end_time": str | None,
    },
    "email": {"to": str, "subject": str, "body": str},
}
```

注意点:
- `next_meeting` に `calendar_url` は含まれない。日時が確定している場合のみ、Backend側で `calendar_url` を生成して追加すること。
- 不明な `assignee` / `deadline` は `"未確定"`、不明な `next_meeting` の日時フィールドは `None`。AIが推測することはない。
- `email.to` は常に空文字（送信先アドレスは文字起こしからは特定できないため）。
- 失敗時は `ai.AIModuleError`（またはサブクラス `ExtractionError` / `GenerationError`）を送出する。

個別の処理を呼びたい場合:

```python
from ai import extract_meeting_data, generate_minutes, generate_followup_email

extracted = extract_meeting_data(transcript)   # ExtractedMeetingData
minutes = generate_minutes(extracted)           # MeetingMinutes
email = generate_followup_email(extracted)       # FollowUpEmail
```

すべて pydantic `BaseModel` なので `.model_dump()` / `.model_dump_json()` が使える。詳細な型は [`schemas.py`](schemas.py) を参照。

## テスト

```bash
pytest              # モックのみ、APIキー不要
pytest -m live       # 実際にOpenAI APIを呼ぶ結合テスト（要APIキー、費用発生）
```
