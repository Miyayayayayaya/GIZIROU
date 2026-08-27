import json
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# --- 1. テーブル定義 (モデル) ---
class Meeting(db.Model):
    __tablename__ = "meetings"

    id = db.Column(db.Integer, primary_key=True)  # ※primary_keyに修正
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    title = db.Column(db.String(200), default="名称未設定の会議")
    transcript = db.Column(db.Text)
    external_minutes = db.Column(db.Text)
    decisions = db.Column(db.JSON)
    action_items = db.Column(db.JSON)
    warnings = db.Column(db.JSON)
    next_meeting = db.Column(db.JSON)
    email = db.Column(db.JSON)

    def to_dict(self):
        """フロントエンドへ返却するための辞書形式変換"""
        return {
            "id": self.id,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "title": self.title,
            "transcript": self.transcript,
            "external_minutes": self.external_minutes,
            "decisions": self.decisions,
            "action_items": self.action_items,
            "warnings": self.warnings,
            "next_meeting": self.next_meeting,
            "email": self.email,
        }


# --- 2. データベース操作用関数 (CRUD) ---
def init_db(app):
    """データベースの初期化"""
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///gizirou.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()


def save_meeting(data: dict, transcript: str) -> Meeting:
    """AI解析結果をDBに保存"""
    meeting = Meeting(
        title=data.get("next_meeting", {}).get("title") or "会議議事録",
        transcript=transcript,
        external_minutes=data.get("external_minutes", ""),
        decisions=data.get("decisions", []),
        action_items=data.get("action_items", []),
        warnings=data.get("warnings", []),
        next_meeting=data.get("next_meeting", {}),
        email=data.get("email", {}),
    )
    db.session.add(meeting)
    db.session.commit()
    return meeting


def get_all_meetings():
    """履歴一覧を取得（作成日時の降順）"""
    return Meeting.query.order_by(Meeting.created_at.desc()).all()


def get_meeting_by_id(meeting_id: int):
    """IDで特定の会議詳細を取得"""
    return Meeting.query.get(meeting_id)


def update_next_meeting(meeting_id: int, next_meeting: dict) -> Meeting | None:
    """次回会議情報を更新する。Calendar APIの作成結果保存に使用する。"""
    meeting = Meeting.query.get(meeting_id)
    if not meeting:
        return None
    meeting.next_meeting = dict(next_meeting)
    db.session.commit()
    return meeting


def delete_meeting(meeting_id: int) -> bool:
    """会議データを削除"""
    meeting = Meeting.query.get(meeting_id)
    if meeting:
        db.session.delete(meeting)
        db.session.commit()
        return True
    return False
