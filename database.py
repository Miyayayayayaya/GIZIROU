import json
import os
from datetime import datetime
from pathlib import Path

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text

db = SQLAlchemy()


# --- 1. テーブル定義 (モデル) ---
class Meeting(db.Model):
    __tablename__ = "meetings"

    id = db.Column(db.Integer, primary_key=True)  # ※primary_keyに修正
    owner_email = db.Column(db.String(320), index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    title = db.Column(db.String(200), default="名称未設定の会議")
    transcript = db.Column(db.Text)
    external_minutes = db.Column(db.Text)
    english = db.Column(db.JSON)
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
            "english": self.english or {},
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
        database_path = Path(app.instance_path) / "gizirou.db"
        try:
            os.chmod(database_path, 0o600)
        except OSError:
            app.logger.warning("SQLite database file permissions could not be restricted")
        columns = {column["name"] for column in inspect(db.engine).get_columns("meetings")}
        if "english" not in columns:
            with db.engine.begin() as connection:
                connection.execute(text("ALTER TABLE meetings ADD COLUMN english JSON"))
        if "owner_email" not in columns:
            # create_all() は既存テーブルへ列を追加しないため、MVP用の最小移行を行う。
            with db.engine.begin() as connection:
                connection.execute(text("ALTER TABLE meetings ADD COLUMN owner_email VARCHAR(320)"))


def save_meeting(data: dict, transcript: str, owner_email: str) -> Meeting:
    """AI解析結果をDBに保存"""
    meeting = Meeting(
        owner_email=owner_email,
        title=data.get("meeting_title") or "会議議事録",
        transcript=transcript,
        external_minutes=data.get("external_minutes", ""),
        english=data.get("english", {}),
        decisions=data.get("decisions", []),
        action_items=data.get("action_items", []),
        warnings=data.get("warnings", []),
        next_meeting=data.get("next_meeting", {}),
        email=data.get("email", {}),
    )
    try:
        db.session.add(meeting)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return meeting


def get_all_meetings(owner_email: str):
    """履歴一覧を取得（作成日時の降順）"""
    return Meeting.query.filter_by(owner_email=owner_email).order_by(Meeting.created_at.desc()).all()


def get_meeting_by_id(meeting_id: int, owner_email: str):
    """IDで特定の会議詳細を取得"""
    return Meeting.query.filter_by(id=meeting_id, owner_email=owner_email).first()


def update_next_meeting(meeting_id: int, next_meeting: dict, owner_email: str) -> Meeting | None:
    """次回会議情報を更新する。Calendar APIの作成結果保存に使用する。"""
    meeting = Meeting.query.filter_by(id=meeting_id, owner_email=owner_email).first()
    if not meeting:
        return None
    meeting.next_meeting = dict(next_meeting)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return meeting


def delete_meeting(meeting_id: int, owner_email: str) -> bool:
    """会議データを削除"""
    meeting = Meeting.query.filter_by(id=meeting_id, owner_email=owner_email).first()
    if meeting:
        try:
            db.session.delete(meeting)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return True
    return False
