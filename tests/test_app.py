import io
import unittest
from unittest.mock import patch

from main import app


class FrontendFlowTest(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_input_page_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("文字起こしを追加", response.get_data(as_text=True))

    def test_empty_submission_returns_validation_error(self):
        response = self.client.post("/analyze", data={"transcript": ""})
        self.assertEqual(response.status_code, 400)
        self.assertIn("文字起こしを貼り付けるか", response.get_data(as_text=True))

    def test_pasted_text_renders_review_page(self):
        response = self.client.post("/analyze", data={"transcript": "次回は9月3日14時から進捗確認会を実施します。"})
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("送信前確認", body)
        self.assertNotIn('id="todos-title"', body)
        self.assertIn("Google Calendarに追加", body)
        self.assertIn("メール本文をコピー", body)

    def test_txt_upload_renders_review_page(self):
        response = self.client.post(
            "/analyze",
            data={"transcript_file": (io.BytesIO("会議の文字起こし".encode()), "meeting.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("議事録", response.get_data(as_text=True))

    def test_non_txt_upload_is_rejected(self):
        response = self.client.post(
            "/analyze",
            data={"transcript_file": (io.BytesIO(b"content"), "meeting.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(".txt形式のみ", response.get_data(as_text=True))

    def test_ambiguous_next_meeting_shows_warning_without_calendar_link(self):
        result = {
            "external_minutes": "議事録",
            "decisions": [],
            "action_items": [],
            "warnings": [],
            "next_meeting": {
                "detected": True,
                "date_confirmed": False,
                "title": "次回会議",
                "date": None,
                "start_time": None,
                "end_time": None,
                "calendar_url": None,
            },
            "email": {"to": "", "subject": "件名", "body": "本文"},
            "is_demo": False,
        }
        with patch("main.build_demo_analysis", return_value=result):
            response = self.client.post("/analyze", data={"transcript": "来週またやりましょう"})
        body = response.get_data(as_text=True)
        self.assertIn("日時が確定していません", body)
        self.assertNotIn("Google Calendarに追加", body)

    def test_absent_next_meeting_hides_meeting_section(self):
        result = {
            "external_minutes": "議事録",
            "decisions": [],
            "action_items": [],
            "warnings": [],
            "next_meeting": {"detected": False},
            "email": {"to": "", "subject": "件名", "body": "本文"},
            "is_demo": False,
        }
        with patch("main.build_demo_analysis", return_value=result):
            response = self.client.post("/analyze", data={"transcript": "本日はありがとうございました"})
        body = response.get_data(as_text=True)
        self.assertNotIn("次回会議</h2>", body)
        self.assertNotIn("Google Calendarに追加", body)


if __name__ == "__main__":
    unittest.main()
