import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.documents import connection
from app.main import app
from app.vectors import VectorError, VectorStore


class SearchApiTest(unittest.TestCase):
    def setUp(self):
        self.path = str(Path(self.enterContext(TemporaryDirectory())) / "test.sqlite3")
        self.enterContext(patch.dict(os.environ, RAGCOON_DB=self.path))
        self.store = Mock(spec=VectorStore)
        self.enterContext(patch.object(app.state, "vector_store", self.store, create=True))
        self.clients = self.enterContext(ExitStack())
        self.client = self.clients.enter_context(TestClient(app))

    def register(self, source):
        response = self.client.post("/api/documents", params={"filename": "안내.md"}, content=source.encode())
        self.assertEqual(response.status_code, 201)
        document = response.json()
        self.assertEqual(document["status"], "registered")
        return document["id"]

    def chunks(self, doc):
        return self.client.get(f"/api/documents/{doc}/chunks").json()

    def index(self, doc):
        return self.client.post(f"/api/documents/{doc}/index")

    def status(self, doc):
        return next(d["status"] for d in self.client.get("/api/documents").json() if d["id"] == doc)

    def search(self, query, mode=None):
        body = {"query": query}
        if mode is not None:
            body["mode"] = mode
        response = self.client.post("/api/search", json=body)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["mode"], mode or "hybrid")
        return data["results"]

    def test_sparse_query_is_read_only_and_returns_original_lines(self):
        doc = self.register("백업은 OPENAI_API_KEY 항목을 먼저 확인한다.\r\n담당자 확인\r\n\r\n백업은 백업은\r\n\r\nOPENAI API KEY")
        first, second, _ = self.chunks(doc)
        for mode in ("sparse", "dense", "hybrid"):
            self.assertEqual(self.search("백업", mode), [])
        self.assertEqual(self.store.mock_calls, [])
        self.assertEqual(self.index(doc).json(), {"status": "ready"})
        self.store.reset_mock()

        @contextmanager
        def read_only(path):
            with connection(path) as db:
                db.execute("PRAGMA query_only = ON")
                yield db

        with patch("app.search.connection", read_only):
            ranked = self.search("백업", "sparse")
            self.assertEqual([r["chunk_id"] for r in ranked], [second["id"], first["id"]])
            self.assertEqual(self.search("* :- (", "sparse"), [])
            self.assertEqual(self.search('OPENAI_API_KEY" NOT ((+*', "sparse"), [{
                "chunk_id": first["id"], "document_id": doc, "filename": "안내.md",
                "start_line": 1, "end_line": 2,
                "text": "백업은 OPENAI_API_KEY 항목을 먼저 확인한다.\r\n담당자 확인",
            }])
        self.assertEqual(self.store.mock_calls, [])

    def test_modes_and_default_fusion_keep_single_list_candidates(self):
        doc = self.register("새벽\n\n새벽\n\n새벽\n\n새벽")
        other = self.register("데이터 복사본을 만드는 시각")
        a, b, c, _ = [chunk["id"] for chunk in self.chunks(doc)]
        d = self.chunks(other)[0]["id"]
        for document in (doc, other):
            self.assertEqual(self.index(document).status_code, 200)
        self.store.search.return_value = [d, a, a]

        self.assertEqual([r["chunk_id"] for r in self.search("새벽", "sparse")], [a, b, c])
        self.store.search.assert_not_called()
        with patch("app.search.sparse_ids", side_effect=AssertionError("밀집 단독 검색에서 FTS 호출")):
            dense = self.search("새벽", "dense")
        self.assertEqual([r["chunk_id"] for r in dense], [d, a])
        self.assertEqual(dense[0]["document_id"], other)
        self.store.search.assert_called_once_with("새벽", [doc, other], 3)
        for mode in (None, "hybrid"):
            with self.subTest(mode=mode):
                self.assertEqual([r["chunk_id"] for r in self.search("새벽", mode)], [a, d, b])

    def test_failed_document_is_filtered_in_all_modes_and_can_retry(self):
        ready = self.register("점검")
        failed = self.register("점검 합성문단")
        valid_id = self.chunks(ready)[0]["id"]
        orphan_id = self.chunks(failed)[0]["id"]
        self.assertEqual(self.index(ready).status_code, 200)
        self.store.upsert.side_effect = VectorError("합성문단 가상비밀")
        error = self.index(failed)
        self.assertEqual(error.status_code, 503)
        self.assertNotIn("합성문단", error.json()["detail"])
        self.assertNotIn("가상비밀", error.json()["detail"])
        self.assertEqual(self.status(failed), "failed")

        # 일부 벡터 쓰기 후 실패한 저장소가 잘못된 ID까지 주더라도 SQLite에서 한 번 더 가린다.
        self.store.search.return_value = [orphan_id, valid_id]
        for mode in ("sparse", "dense", "hybrid"):
            self.assertEqual([r["chunk_id"] for r in self.search("점검", mode)], [valid_id])
        self.store.search.assert_called_with("점검", [ready], 3)
        self.store.upsert.side_effect = None
        self.assertEqual(self.index(failed).json(), {"status": "ready"})
        self.store.upsert.assert_called_with(failed, [(orphan_id, "점검 합성문단")])
        self.assertEqual(self.search("합성문단", "sparse")[0]["chunk_id"], orphan_id)
        self.assertEqual(self.index(failed).json(), {"status": "ready"})
        self.assertEqual(self.store.upsert.call_count, 3)

    def test_sql_publication_failure_rolls_back_fts_after_vector_write(self):
        doc = self.register("합성문단")
        with connection(self.path) as db:
            db.execute("""CREATE TRIGGER fail_ready BEFORE UPDATE OF status ON documents
                          WHEN NEW.status = 'ready' BEGIN SELECT RAISE(ABORT, '합성문단'); END""")
        error = self.index(doc)
        self.assertEqual(error.status_code, 503)
        self.assertNotIn("합성문단", error.json()["detail"])
        self.store.upsert.assert_called_once()
        self.assertEqual(self.status(doc), "failed")
        with connection(self.path) as db:
            self.assertEqual(db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH '합성문단'").fetchall(), [])

    def test_concurrent_index_request_never_sends_document_twice(self):
        doc = self.register("동시 색인")
        claimed, release = Event(), Event()

        def hold(document_id, chunks):
            claimed.set()
            self.assertTrue(release.wait(timeout=10))

        self.store.upsert.side_effect = hold
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.index, doc)
            try:
                self.assertTrue(claimed.wait(timeout=5))
                self.assertEqual(self.status(doc), "indexing")
                self.assertEqual(self.index(doc).status_code, 409)
                self.assertEqual(self.search("동시"), [])
                self.store.search.assert_not_called()
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5).json(), {"status": "ready"})
        self.store.upsert.assert_called_once()

    def test_restart_marks_interrupted_work_failed_without_paid_replay(self):
        ready = self.register("운영 안내")
        interrupted = self.register("복구 문서")
        self.assertEqual(self.index(ready).status_code, 200)
        self.clients.close()
        with connection(self.path) as db:
            db.execute("UPDATE documents SET status = 'indexing' WHERE id = ?", (interrupted,))
        self.store.reset_mock()
        self.client = self.clients.enter_context(TestClient(app))
        self.assertEqual(self.status(ready), "ready")
        self.assertEqual(self.status(interrupted), "failed")
        self.assertEqual(self.search("복구", "sparse"), [])
        self.assertEqual(self.store.mock_calls, [])
        self.assertEqual(self.index(interrupted).json(), {"status": "ready"})
        self.assertEqual(self.search("복구", "sparse")[0]["document_id"], interrupted)

    def test_errors_do_not_echo_queries_or_vector_exceptions(self):
        self.assertEqual(self.index(123).status_code, 404)
        for payload in ({"query": " "}, {"query": "합성질문", "mode": "unknown"}):
            response = self.client.post("/api/search", json=payload)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("합성질문", response.json()["detail"])
        doc = self.register("공개 예시")
        self.assertEqual(self.index(doc).status_code, 200)
        self.store.search.side_effect = VectorError("합성질문 가상비밀")
        for mode in ("dense", "hybrid"):
            response = self.client.post("/api/search", json={"query": "합성질문", "mode": mode})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("합성질문", response.json()["detail"])
            self.assertNotIn("가상비밀", response.json()["detail"])
