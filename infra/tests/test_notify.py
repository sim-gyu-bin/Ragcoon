import io
import json
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from infra import notify


ENV = {
    "GITHUB_SHA": "a123" * 10, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2",
    **{f"{stage.upper()}_RESULT": "success" for stage in notify.STAGES},
}
TOKEN = "1234:" + "x" * 25
CHAT = "123456789"


class MessageTests(unittest.TestCase):
    def test_success_contains_only_release_metadata(self):
        message = notify.build_message({**ENV, "GITHUB_EVENT_PATH": "not-for-telegram", "TELEGRAM_BOT_TOKEN": TOKEN})
        self.assertEqual(message.splitlines(), [
            "RAGcoon 배포 시험: 성공", "gate: 성공, check: 성공, build: 성공, deploy: 성공",
            "SHA: a123a123a123", "https://github.com/sim-gyu-bin/Ragcoon/actions/runs/123/attempts/2",
        ])

    def test_skipped_jobs_do_not_hide_check_failure_or_cancellation(self):
        for state, label in (("failure", "실패"), ("cancelled", "취소"), ("skipped", "미실행")):
            with self.subTest(state=state):
                message = notify.build_message({**ENV, "CHECK_RESULT": state, "BUILD_RESULT": "skipped", "DEPLOY_RESULT": "skipped"})
                self.assertEqual(message.splitlines()[0], f"RAGcoon 배포 시험: {label}")
                self.assertIn(f"check: {label}, build: 미실행, deploy: 미실행", message)

    def test_recovery_failure_is_not_deployment_success(self):
        message = notify.build_message({**ENV, "DEPLOY_RESULT": "failure"})
        self.assertEqual(message.splitlines()[0], "RAGcoon 배포 시험: 실패")
        self.assertIn("deploy: 실패", message)

    def test_rejects_untrusted_values_in_message_fields(self):
        for key in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "DEPLOY_RESULT"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                notify.build_message({**ENV, key: "untrusted\ncontent"})


class DeliveryTests(unittest.TestCase):
    @patch("infra.notify.http.client.HTTPSConnection")
    def test_only_sends_to_fixed_endpoint_with_previews_disabled(self, connect):
        client = connect.return_value
        client.getresponse.return_value.status = 200
        client.getresponse.return_value.read.return_value = b'{"ok":true}'
        notify.send_message(TOKEN, CHAT, "전송 내용")
        connect.assert_called_once_with("api.telegram.org", timeout=10)
        method, path, body, _ = client.request.call_args.args
        self.assertEqual((method, path), ("POST", f"/bot{TOKEN}/sendMessage"))
        self.assertEqual(json.loads(body), {"chat_id": CHAT, "text": "전송 내용", "link_preview_options": {"is_disabled": True}})
        client.close.assert_called_once()

    @patch("infra.notify.http.client.HTTPSConnection")
    def test_rejects_non_private_chat_and_unsafe_token_before_connect(self, connect):
        for token, chat in ((TOKEN, "-123"), (TOKEN, "@group"), (TOKEN, str(2**52)), ("123:../../redirect", CHAT)):
            with self.subTest(chat=chat), self.assertRaises(ValueError):
                notify.send_message(token, chat, "전송 내용")
        connect.assert_not_called()

    @patch("infra.notify.http.client.HTTPSConnection")
    def test_rejects_redirect_bad_receipt_and_large_response(self, connect):
        client = connect.return_value
        for status, raw in ((302, b""), (200, b'{"ok":false}'), (200, b"not json"), (200, b" " * (notify.RESPONSE_LIMIT + 1))):
            with self.subTest(status=status, size=len(raw)):
                client.getresponse.return_value.status = status
                client.getresponse.return_value.read.return_value = raw
                with self.assertRaises(ValueError):
                    notify.send_message(TOKEN, CHAT, "전송 내용")
                client.close.assert_called_once()
                client.reset_mock()

    def test_missing_credentials_and_connection_errors_emit_only_safe_warning(self):
        for credentials in ({}, {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT}):
            with self.subTest(configured=bool(credentials)), patch.dict(notify.os.environ, {**ENV, **credentials}, clear=True), patch(
                "infra.notify.http.client.HTTPSConnection", side_effect=OSError(f"URL contains {TOKEN}, chat {CHAT}")
            ):
                errors = io.StringIO()
                with redirect_stderr(errors):
                    self.assertEqual(notify.main(), 1)
                self.assertTrue(errors.getvalue().startswith("::warning::"))
                self.assertNotIn(TOKEN, errors.getvalue())
                self.assertNotIn(CHAT, errors.getvalue())
