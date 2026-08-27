import io
import unittest
import base64
from email import message_from_bytes
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ai.errors import TranscriptionError
from main import app, send_follow_up_email


class FrontendFlowTest(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()
        with self.client.session_transaction() as login_session:
            login_session["user_email"] = "tester@example.com"
            login_session["user_name"] = "テストユーザー"
        self.analysis_patcher = patch(
            "main.run_ai_analysis",
            return_value={
                "external_minutes": "議事録",
                "decisions": [],
                "action_items": [],
                "warnings": [],
                "next_meeting": {"detected": True, "date_confirmed": True, "title": "進捗確認会", "date": "2026-09-03", "start_time": "14:00", "end_time": "15:00", "calendar_url": "https://calendar.google.com"},
                "email": {"to": "", "subject": "件名", "body": "本文"},
                "is_demo": False,
            },
        )
        self.analysis_patcher.start()
        self.addCleanup(self.analysis_patcher.stop)
        self.save_patcher = patch("main.save_meeting", return_value=SimpleNamespace(id=1))
        self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)

    def test_input_page_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("議事郎", response.get_data(as_text=True))
        self.assertIn("文字起こしを追加", response.get_data(as_text=True))

    def test_logged_out_user_sees_login_page(self):
        response = app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("議事郎", response.get_data(as_text=True))
        self.assertIn("Googleアカウントでログイン", response.get_data(as_text=True))

    def test_127_host_redirects_to_oauth_host_before_login(self):
        response = app.test_client().get("/", base_url="http://127.0.0.1:5001", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost:5001/")

    def test_logged_out_user_cannot_analyze(self):
        response = app.test_client().post("/analyze", data={"transcript": "会議内容"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_logged_out_user_cannot_read_history_json(self):
        response = app.test_client().get("/meetings?format=json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "ログインが必要です"})

    def test_logged_out_user_cannot_delete_meeting(self):
        response = app.test_client().delete("/api/meetings/1")
        self.assertEqual(response.status_code, 401)

    @patch("main.clear_credentials")
    def test_logout_clears_user_session(self, clear_credentials_mock):
        response = self.client.post("/google/disconnect")
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as login_session:
            self.assertNotIn("user_email", login_session)
            self.assertNotIn("user_name", login_session)
        clear_credentials_mock.assert_called_once()

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
        self.assertIn("音声ファイル", response.get_data(as_text=True))

    def test_audio_upload_is_transcribed_and_renders_review_page(self):
        with patch("main.transcribe_audio", return_value="会議の文字起こし結果") as mock_transcribe:
            response = self.client.post(
                "/analyze",
                data={"transcript_file": (io.BytesIO(b"fake-audio-bytes"), "meeting.mp3")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("議事録", response.get_data(as_text=True))
        mock_transcribe.assert_called_once()
        self.assertEqual(mock_transcribe.call_args.args[1], "meeting.mp3")

    def test_oversized_audio_upload_is_rejected(self):
        oversized = io.BytesIO(b"0" * (25 * 1024 * 1024 + 1))
        response = self.client.post(
            "/analyze",
            data={"transcript_file": (oversized, "meeting.wav")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("25MB", response.get_data(as_text=True))

    def test_failed_audio_transcription_shows_friendly_error(self):
        with patch("main.transcribe_audio", side_effect=TranscriptionError("boom")):
            response = self.client.post(
                "/analyze",
                data={"transcript_file": (io.BytesIO(b"fake-audio-bytes"), "meeting.mp3")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("文字起こしに失敗しました", response.get_data(as_text=True))

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
        with patch("main.run_ai_analysis", return_value=result):
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
        with patch("main.run_ai_analysis", return_value=result):
            response = self.client.post("/analyze", data={"transcript": "本日はありがとうございました"})
        body = response.get_data(as_text=True)
        self.assertNotIn("次回会議</h2>", body)
        self.assertNotIn("Google Calendarに追加", body)

    def test_review_page_has_multiple_recipient_editor(self):
        response = self.client.post("/analyze", data={"transcript": "会議の文字起こし"})
        body = response.get_data(as_text=True)
        self.assertIn('id="email-to-input"', body)
        self.assertIn('id="email-cc-input"', body)
        self.assertIn('id="email-to-list"', body)
        self.assertIn('id="email-cc-list"', body)

    @patch("main.send_follow_up_email")
    @patch("main.is_gmail_connected", return_value=True)
    def test_send_email_accepts_multiple_to_and_cc(self, _connected, send_mock):
        response = self.client.post(
            "/send-email",
            data={
                "email_to": ["user1@example.com", "user2@example.com"],
                "email_cc": ["manager@example.com"],
                "email_subject": "件名",
                "email_body": "本文",
            },
        )
        self.assertEqual(response.status_code, 200)
        send_mock.assert_called_once_with(
            ["user1@example.com", "user2@example.com"],
            "件名",
            "本文",
            ["manager@example.com"],
        )

    @patch("main.is_gmail_connected", return_value=True)
    def test_send_email_rejects_duplicate_recipient(self, _connected):
        response = self.client.post(
            "/send-email",
            data={
                "email_to": ["user@example.com"],
                "email_cc": ["USER@example.com"],
                "email_subject": "件名",
                "email_body": "本文",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("重複のない正しい宛先", response.get_data(as_text=True))

    @patch("main.is_gmail_connected", return_value=True)
    def test_send_email_requires_at_least_one_to_recipient(self, _connected):
        response = self.client.post(
            "/send-email",
            data={
                "email_cc": ["manager@example.com"],
                "email_subject": "件名",
                "email_body": "本文",
            },
        )
        self.assertEqual(response.status_code, 400)

    @patch("main.is_gmail_connected", return_value=True)
    def test_send_email_rejects_subject_with_line_break(self, _connected):
        response = self.client.post(
            "/send-email",
            data={
                "email_to": ["user@example.com"],
                "email_subject": "件名\nBcc: other@example.com",
                "email_body": "本文",
            },
        )
        self.assertEqual(response.status_code, 400)

    @patch("main.build")
    @patch("main.get_credentials", return_value=object())
    def test_gmail_message_contains_multiple_to_and_cc_headers(self, _credentials, build_mock):
        send_follow_up_email(
            ["user1@example.com", "user2@example.com"],
            "件名",
            "本文",
            ["manager@example.com"],
        )

        send_call = build_mock.return_value.users.return_value.messages.return_value.send
        raw_message = send_call.call_args.kwargs["body"]["raw"]
        decoded = base64.urlsafe_b64decode(raw_message.encode("ascii"))
        message = message_from_bytes(decoded)
        self.assertEqual(message["To"], "user1@example.com, user2@example.com")
        self.assertEqual(message["Cc"], "manager@example.com")
    @patch("main.get_all_meetings")
    def test_history_page_renders(self, get_all_meetings_mock):
        get_all_meetings_mock.return_value = [
            SimpleNamespace(
                to_dict=lambda: {
                    "id": 1,
                    "created_at": "2026-08-27 10:00:00",
                    "title": "定例会議",
                    "transcript": "文字起こし",
                    "external_minutes": "議事録本文",
                    "decisions": ["決定事項"],
                    "action_items": [],
                    "warnings": [],
                    "next_meeting": {"detected": False},
                    "email": {"to": "", "subject": "件名", "body": "本文"},
                }
            )
        ]
        response = self.client.get("/meetings")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("会議履歴", body)
        self.assertIn("定例会議", body)
        self.assertIn("詳細を確認", body)

    @patch("main.save_credentials")
    @patch("main.google_id_token.verify_oauth2_token")
    @patch("main.create_oauth_flow")
    @patch("main.oauth_client_config", return_value={"web": {"client_id": "client-id"}})
    def test_oauth_callback_stores_verified_user(
        self,
        _oauth_config_mock,
        create_flow_mock,
        verify_token_mock,
        save_credentials_mock,
    ):
        flow = MagicMock()
        flow.credentials.id_token = "signed-id-token"
        create_flow_mock.return_value = flow
        verify_token_mock.return_value = {
            "email": "user@example.com",
            "email_verified": True,
            "name": "利用者",
        }
        with self.client.session_transaction() as login_session:
            login_session.clear()
            login_session["oauth_state"] = "valid-state"
            login_session["oauth_code_verifier"] = "verifier"

        response = self.client.get("/oauth2callback?state=valid-state&code=code")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        with self.client.session_transaction() as login_session:
            self.assertEqual(login_session["user_email"], "user@example.com")
            self.assertEqual(login_session["user_name"], "利用者")
        save_credentials_mock.assert_called_once_with(flow.credentials)


if __name__ == "__main__":
    unittest.main()
