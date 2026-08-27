import stat
from pathlib import Path
from tempfile import TemporaryDirectory

from flask import Flask

from database import (
    delete_meeting,
    get_all_meetings,
    get_meeting_by_id,
    init_db,
    save_meeting,
    update_next_meeting,
)


def test_meeting_crud_is_scoped_to_owner():
    with TemporaryDirectory() as instance_path:
        test_app = Flask(__name__, instance_path=instance_path)
        init_db(test_app)

        with test_app.app_context():
            first = save_meeting({}, "ユーザーAの会議", "user-a@example.com")
            second = save_meeting({}, "ユーザーBの会議", "user-b@example.com")

            assert [meeting.id for meeting in get_all_meetings("user-a@example.com")] == [first.id]
            assert get_meeting_by_id(second.id, "user-a@example.com") is None
            assert update_next_meeting(second.id, {"detected": False}, "user-a@example.com") is None
            assert delete_meeting(second.id, "user-a@example.com") is False
            assert get_meeting_by_id(second.id, "user-b@example.com") is not None


def test_meeting_database_is_owner_readable_only():
    with TemporaryDirectory() as instance_path:
        test_app = Flask(__name__, instance_path=instance_path)
        init_db(test_app)

        database_mode = stat.S_IMODE((Path(test_app.instance_path) / "gizirou.db").stat().st_mode)
        assert database_mode == 0o600
