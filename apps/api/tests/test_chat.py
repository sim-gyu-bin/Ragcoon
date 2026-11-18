import asyncio
import json
import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from app.chat import reserve
from app.chat_model import INSUFFICIENT, GroundedChat
from app.documents import connection
from app.main import app
from app.vectors import VectorStore


def tokens(*texts):
    async def reply(query, sources):
        for text in texts:
            yield text

    return reply


class ChatApiTest(unittest.TestCase):
    def setUp(self):
        self.path = str(Path(self.enterContext(TemporaryDirectory())) / "chat.sqlite3")
        self.enterContext(patch.dict(os.environ, RAGCOON_DB=self.path))
        self.store = Mock(spec=VectorStore)
        self.store.search.return_value = []
        self.model = Mock(spec=GroundedChat)
        self.enterContext(patch.object(app.state, "vector_store", self.store, create=True))
        self.enterContext(patch.object(app.state, "chat_model", self.model, create=True))
        self.clients = self.enterContext(ExitStack())
        self.client = self.clients.enter_context(TestClient(app))

    def session(self):
        response = self.client.post("/api/chat/sessions")
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def document(self, text="합성 자료"):
        response = self.client.post("/api/documents", params={"filename": "안내.md"}, content=text.encode())
        self.assertEqual(response.status_code, 201)
        doc = response.json()["id"]
        self.assertEqual(self.client.post(f"/api/documents/{doc}/index").status_code, 200)
        return doc

    def history(self, session):
        response = self.client.get(f"/api/chat/sessions/{session}/messages")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def ask(self, session, query="합성", **options):
        return self.client.post(f"/api/chat/sessions/{session}/messages", json={"query": query, **options})

    def events(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/x-ndjson")
        self.assertTrue(response.content.endswith(b"\n"))
        return [json.loads(line) for line in response.iter_lines()]

    def test_history_preserves_order_and_original_evidence_after_restart(self):
        first, other = self.session(), self.session()
        doc = self.document("합성 자료\r\n첫 줄 다음\r\n\r\n합성 출처 둘")
        found = self.client.post("/api/search", json={"query": "합성"}).json()["results"]
        self.assertEqual(len(found), 2)
        self.store.search.reset_mock()
        self.model.stream.side_effect = tokens("첫 줄\n", "다음 [", "2]")
        events = self.events(self.ask(first, "  합성  "))
        self.assertEqual(events, [
            {"type": "delta", "text": "첫 줄\n"}, {"type": "delta", "text": "다음 ["},
            {"type": "delta", "text": "2]"}, {"type": "end", "status": "completed", "sources": found},
        ])
        self.store.search.assert_called_once()
        self.model.stream.assert_called_once()
        query, sources = self.model.stream.call_args.args
        self.assertEqual((query, [s.model_dump() for s in sources]), ("합성", found))
        messages = self.history(first)
        self.assertEqual([(m["role"], m["text"], m["status"], m["sources"]) for m in messages], [
            ("user", "합성", "completed", []), ("assistant", "첫 줄\n다음 [2]", "completed", found),
        ])
        self.assertLess(messages[0]["id"], messages[1]["id"])
        self.clients.close()
        with connection(self.path) as db:
            db.execute("UPDATE documents SET filename = '변경.md', status = 'failed' WHERE id = ?", (doc,))
            db.execute("UPDATE chunks SET text = '변경된 원문' WHERE document_id = ?", (doc,))
        self.client = self.clients.enter_context(TestClient(app))
        self.assertEqual(self.history(first), messages)
        self.assertEqual(self.history(other), [])
        self.assertEqual(self.client.get("/api/chat/sessions").json(), [{"id": other}, {"id": first}])

    def test_insufficient_does_not_reuse_history_or_invent_sources(self):
        session = self.session()
        self.document()
        self.model.stream.side_effect = tokens("이전 답변 [1]")
        self.events(self.ask(session, mode="sparse"))
        self.model.stream.reset_mock()
        events = self.events(self.ask(session, "없는질문", mode="sparse"))
        self.assertEqual(events, [
            {"type": "delta", "text": INSUFFICIENT}, {"type": "end", "status": "insufficient", "sources": []},
        ])
        self.model.stream.assert_not_called()
        self.model.stream.side_effect = tokens(INSUFFICIENT[:3], INSUFFICIENT[3:])
        end = self.events(self.ask(session, "자료", mode="sparse"))[-1]
        self.assertEqual(end, {"type": "end", "status": "insufficient", "sources": []})
        query, sources = self.model.stream.call_args.args
        self.assertEqual(query, "자료")
        self.assertEqual([s.text for s in sources], ["합성 자료"])
        answers = self.history(session)[1::2]
        self.assertEqual([m["status"] for m in answers], ["completed", "insufficient", "insufficient"])
        self.assertEqual([(m["text"], m["sources"]) for m in answers[1:]], [(INSUFFICIENT, [])] * 2)
        self.store.search.assert_not_called()

    def test_invalid_citations_never_complete_or_link_sources(self):
        session = self.session()
        self.document()
        for text in ("", "출처 없음", "범위 밖 [2]", "잘못된 인용 [1] [0]", "비표준 인용 [01]"):
            with self.subTest(text=text):
                self.model.stream.side_effect = tokens(text)
                self.assertEqual(self.events(self.ask(session, mode="sparse"))[-1],
                                 {"type": "end", "status": "failed", "sources": []})
                last = self.history(session)[-1]
                self.assertEqual((last["status"], last["sources"]), ("failed", []))

    def test_search_and_model_failures_do_not_disclose_exception_or_retry(self):
        session = self.session()
        self.document()
        secret = "가상의비밀키와원문"

        async def broken(query, sources):
            yield "미완성 [1]"
            raise RuntimeError(secret)

        self.store.search.side_effect = RuntimeError(secret)
        self.model.stream.side_effect = broken
        with self.assertNoLogs(level="WARNING"):
            search = self.events(self.ask(session, mode="dense"))
            model = self.events(self.ask(session, mode="sparse"))
        failure = {"type": "end", "status": "failed", "sources": []}
        self.assertEqual(search, [failure])
        self.assertEqual(model, [{"type": "delta", "text": "미완성 [1]"}, failure])
        self.store.search.assert_called_once()
        self.model.stream.assert_called_once()
        messages = self.history(session)
        self.assertNotIn(secret, str(messages))
        self.assertEqual([(m["text"], m["status"]) for m in messages[1::2]], [("", "failed"), ("미완성 [1]", "failed")])

    def test_invalid_or_unknown_session_request_creates_no_messages(self):
        session = self.session()
        self.assertEqual(self.ask(100).status_code, 404)
        self.assertEqual(self.client.get("/api/chat/sessions/100/messages").status_code, 404)
        response = self.ask(session, mode="가상의민감입력")
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("가상의민감입력", response.text)
        self.assertEqual(self.ask(session, "\n  ").status_code, 422)
        self.assertEqual(self.ask(2**63).status_code, 422)
        self.assertEqual(self.history(session), [])
        self.model.stream.assert_not_called()

    async def asgi_post(self, session, send, disconnected, version="2.3"):
        # TestClient는 본문을 모아서 반환한다. 실제 ASGI 프레임과 연결 해제를 직접 관찰한다.
        path = f"/api/chat/sessions/{session}/messages"
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": version}, "http_version": "1.1",
            "method": "POST", "path": path, "raw_path": path.encode(), "scheme": "http", "root_path": "",
            "query_string": b"", "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
        }
        body = json.dumps({"query": "합성", "mode": "sparse"}).encode()

        async def receive():
            nonlocal body
            if body is not None:
                chunk, body = body, None
                return {"type": "http.request", "body": chunk, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        await app(scope, receive, send)

    def test_disconnect_after_first_delta_releases_only_its_session(self):
        first, other = self.session(), self.session()
        self.document()

        async def scenario():
            arrived, disconnected, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
            frames = []

            async def waiting(query, sources):
                try:
                    yield "미완성 [1]"
                    await asyncio.Future()
                finally:
                    closed.set()

            async def send(message):
                if message.get("body"):
                    frames.append(json.loads(message["body"]))
                    arrived.set()

            self.model.stream.side_effect = waiting
            pending = asyncio.create_task(self.asgi_post(first, send, disconnected))
            try:
                await asyncio.wait_for(arrived.wait(), timeout=5)
                self.assertEqual(frames, [{"type": "delta", "text": "미완성 [1]"}])
                self.assertEqual(self.history(first)[-1]["status"], "streaming")
                self.assertEqual(self.ask(first, "중복 질문").status_code, 409)
                self.assertEqual(self.events(self.ask(other, "다른질문", mode="sparse"))[-1]["status"], "insufficient")
                self.model.stream.assert_called_once()
            finally:
                disconnected.set()
                await asyncio.wait_for(pending, timeout=5)
            self.assertTrue(closed.is_set())
            self.assertEqual(len(frames), 1)

        asyncio.run(scenario())
        history = self.history(first)
        self.assertEqual([m["text"] for m in history], ["합성", "미완성 [1]"])
        self.assertEqual(history[-1]["status"], "failed")
        self.assertEqual(self.history(other)[0]["text"], "다른질문")
        self.model.stream.side_effect = tokens("재질문 [1]")
        self.assertEqual(self.events(self.ask(first, mode="sparse"))[-1]["status"], "completed")

    def test_transport_failure_on_start_or_end_never_marks_answer_complete(self):
        self.document()
        self.model.stream.side_effect = tokens("전송할 답변 [1]")
        for point in ("start", "end"):
            with self.subTest(point=point):
                session = self.session()

                async def send(message):
                    if point == "start" and message["type"] == "http.response.start":
                        raise OSError("연결 종료")
                    if message.get("body") and json.loads(message["body"])["type"] == "end":
                        raise OSError("연결 종료")

                async def scenario():
                    with self.assertRaises(ClientDisconnect):
                        await self.asgi_post(session, send, asyncio.Event(), version="2.4")

                asyncio.run(scenario())
                self.assertEqual(self.history(session)[-1]["status"], "failed")
                self.assertEqual(self.events(self.ask(session, "없는질문", mode="sparse"))[-1]["status"], "insufficient")
        self.model.stream.assert_called_once()

    def test_restart_recovers_unfinished_turn_without_search_or_model_replay(self):
        session = self.session()
        self.clients.close()
        reserve(self.path, session, "중단된 질문")
        self.client = self.clients.enter_context(TestClient(app))
        history = self.history(session)
        self.assertEqual([(m["text"], m["status"]) for m in history], [("중단된 질문", "completed"), ("", "failed")])
        self.assertEqual(history[-1]["sources"], [])
        self.assertEqual(self.store.mock_calls, [])
        self.model.stream.assert_not_called()
        self.assertEqual(self.events(self.ask(session))[-1]["status"], "insufficient")
