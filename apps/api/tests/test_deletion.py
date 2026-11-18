import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import chromadb
from chromadb.config import Settings
from fastapi.testclient import TestClient

from app.chat_model import GroundedChat
from app.documents import connection
from app.main import app
from app.vectors import COLLECTION, VectorError, VectorStore

MODES = ("sparse", "dense", "hybrid")


class DocumentDeletionTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.path = str(root / "deletion.sqlite3")
        self.chroma_path = str(root / "chroma")
        self.enterContext(patch.dict(os.environ, {
            "RAGCOON_DB": self.path, "CHROMA_PATH": self.chroma_path, "OPENAI_API_KEY": "test-only",
        }))
        self.store = Mock(spec=VectorStore)
        self.model = Mock(spec=GroundedChat)
        self.enterContext(patch.object(app.state, "vector_store", self.store, create=True))
        self.enterContext(patch.object(app.state, "chat_model", self.model, create=True))
        self.clients = self.enterContext(ExitStack())
        self.client = self.clients.enter_context(TestClient(app))

    def register(self, source):
        response = self.client.post("/api/documents", params={"filename": "안내.md"}, content=source.encode())
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def index(self, doc):
        return self.client.post(f"/api/documents/{doc}/index")

    def chunks(self, doc):
        return self.client.get(f"/api/documents/{doc}/chunks")

    def delete(self, doc):
        return self.client.delete(f"/api/documents/{doc}")

    def statuses(self):
        response = self.client.get("/api/documents")
        self.assertEqual(response.status_code, 200)
        return {d["id"]: d["status"] for d in response.json()}

    def assert_search_documents(self, *ids):
        for mode in MODES:
            with self.subTest(mode=mode):
                response = self.client.post("/api/search", json={"query": "공통", "mode": mode})
                self.assertEqual(response.status_code, 200)
                self.assertCountEqual([r["document_id"] for r in response.json()["results"]], ids)

    def test_same_name_removes_only_requested_source_and_indexes_not_chat(self):
        # 실제 Chroma는 임시 경로만 사용하고 임베딩과 답변은 네트워크에 보내지 않는다.
        sdk = self.enterContext(patch("app.vectors.OpenAI"))
        sdk.return_value.embeddings.create.side_effect = lambda *, model, input: SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[1.0, 0.0]) for i in range(len(input))]
        )
        app.state.vector_store = VectorStore()
        original = "공통 안내\r\n인용보존 원문"
        keep, target = self.register("공통 유지"), self.register(original)
        keep_chunk, target_chunk = (self.chunks(doc).json()[0]["id"] for doc in (keep, target))
        for doc in (keep, target):
            self.assertEqual(self.index(doc).status_code, 200)
        self.assert_search_documents(keep, target)
        local = chromadb.PersistentClient(path=self.chroma_path, settings=Settings(anonymized_telemetry=False))
        collection = local.get_collection(name=COLLECTION, embedding_function=None)
        self.assertCountEqual(collection.get()["ids"], [str(keep_chunk), str(target_chunk)])

        async def answer(query, sources):
            yield "원문을 인용합니다 [1]"

        self.model.stream.side_effect = answer
        session = self.client.post("/api/chat/sessions").json()["id"]
        messages = f"/api/chat/sessions/{session}/messages"
        self.assertEqual(self.client.post(messages, json={"query": "인용보존", "mode": "sparse"}).status_code, 200)
        history = self.client.get(messages).json()
        self.assertEqual(history[-1]["sources"], [{
            "chunk_id": target_chunk, "document_id": target, "filename": "안내.md", "page_number": None,
            "start_line": 1, "end_line": 2, "text": original,
        }])

        # SQLite에 대응하는 청크가 없는 잔여 벡터도 메타데이터의 문서 ID로 정리해야 한다.
        collection.upsert(ids=[str(target_chunk + 1)], embeddings=[[1.0, 0.0]], metadatas=[{"document_id": target}])
        # 저장소를 새로 열어 캐시된 OpenAI 클라이언트 없이도 삭제할 수 있는지 확인한다.
        app.state.vector_store = VectorStore()
        sdk.reset_mock()
        with patch.dict(os.environ, OPENAI_API_KEY=""):
            response = self.delete(target)
        self.assertEqual((response.status_code, response.content), (204, b""))
        self.assertEqual(sdk.mock_calls, [])
        self.assertEqual(self.statuses(), {keep: "ready"})
        self.assertEqual(collection.get()["ids"], [str(keep_chunk)])
        with connection(self.path) as db:
            self.assertEqual([tuple(r) for r in db.execute("SELECT id, source FROM documents")], [(keep, "공통 유지")])
            self.assertEqual([r[0] for r in db.execute("SELECT id FROM chunks")], [keep_chunk])
            self.assertEqual([r[0] for r in db.execute("SELECT rowid FROM chunks_fts")], [keep_chunk])
        self.assertEqual(self.chunks(target).status_code, 404)
        self.assert_search_documents(keep)

        replacement = self.register("공통 새 문서")
        self.assertGreater(replacement, target)
        self.assertGreater(self.chunks(replacement).json()[0]["id"], target_chunk)
        self.assertEqual(self.delete(target).status_code, 404)
        self.assertEqual(self.statuses(), {keep: "ready", replacement: "registered"})
        self.clients.close()
        self.client = self.clients.enter_context(TestClient(app))
        self.assertEqual(self.client.get(messages).json(), history)

    def test_vector_failure_stays_hidden_and_retryable_across_restart(self):
        keep, target = self.register("공통 유지"), self.register("공통 재시도")
        for doc in (keep, target):
            self.assertEqual(self.index(doc).status_code, 200)
        # 고장난 벡터 저장소가 비공개 ID를 후보로 반환해도 최종 원문은 SQLite가 판정한다.
        self.store.search.return_value = [self.chunks(doc).json()[0]["id"] for doc in (target, keep)]
        self.store.delete_document.side_effect = VectorError("비공개 원인")
        response = self.delete(target)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("비공개", response.json()["detail"])
        self.assertEqual(self.statuses(), {keep: "ready", target: "delete_pending"})
        self.assertEqual(self.index(target).status_code, 409)
        self.assertEqual(self.chunks(target).status_code, 404)
        self.assert_search_documents(keep)
        self.store.search.assert_called_with("공통", [keep], 3)

        self.clients.close()
        self.store.reset_mock()
        self.client = self.clients.enter_context(TestClient(app))
        self.assertEqual(self.statuses()[target], "delete_pending")
        self.assertEqual(self.store.mock_calls, [])
        self.store.delete_document.side_effect = None
        self.assertEqual(self.delete(target).status_code, 204)
        self.store.delete_document.assert_called_once_with(target)
        self.assertEqual(self.statuses(), {keep: "ready"})

    def test_sql_failure_after_vector_cleanup_keeps_pending_row_for_retry(self):
        target = self.register("공통 저장소")
        self.assertEqual(self.index(target).status_code, 200)
        with connection(self.path) as db:
            db.execute("""CREATE TRIGGER fail_purge BEFORE DELETE ON documents
                          BEGIN SELECT RAISE(ABORT, '비공개 원인'); END""")
        response = self.delete(target)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("비공개", response.json()["detail"])
        self.assertEqual(self.statuses(), {target: "delete_pending"})
        self.assert_search_documents()
        with connection(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM chunks WHERE document_id = ?", (target,)).fetchone()[0], 1)
            db.execute("DROP TRIGGER fail_purge")
        self.assertEqual(self.delete(target).status_code, 204)
        self.assertEqual(self.store.delete_document.call_args_list, [call(target), call(target)])
        self.assertEqual(self.statuses(), {})

    def test_unpublished_documents_still_clean_vectors_by_id(self):
        registered, failed = self.register("미색인"), self.register("부분 색인")
        self.store.upsert.side_effect = VectorError("부분 색인 실패")
        self.assertEqual(self.index(failed).status_code, 503)
        self.assertEqual(self.statuses(), {registered: "registered", failed: "failed"})
        for doc in (registered, failed):
            self.assertEqual(self.delete(doc).status_code, 204)
        self.assertEqual(self.store.delete_document.call_args_list, [call(registered), call(failed)])
        self.assertEqual(self.statuses(), {})

    def test_index_claim_rejects_delete_before_touching_vectors(self):
        target = self.register("공통 색인")
        entered, release = Event(), Event()

        def hold(doc, chunks):
            entered.set()
            self.assertTrue(release.wait(timeout=10))

        self.store.upsert.side_effect = hold
        with ThreadPoolExecutor(max_workers=1) as pool:
            indexing = pool.submit(self.index, target)
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.assertEqual(self.delete(target).status_code, 409)
                self.store.delete_document.assert_not_called()
            finally:
                release.set()
            self.assertEqual(indexing.result(timeout=5).status_code, 200)
        self.assertEqual(self.statuses(), {target: "ready"})

    def test_delete_claim_rejects_duplicate_and_index_without_blocking_new_registration(self):
        target = self.register("공통 삭제")
        entered, release = Event(), Event()

        def hold(doc):
            entered.set()
            self.assertTrue(release.wait(timeout=10))

        self.store.delete_document.side_effect = hold
        with ThreadPoolExecutor(max_workers=1) as pool:
            deleting = pool.submit(self.delete, target)
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.assertEqual(self.delete(target).status_code, 409)
                self.assertEqual(self.index(target).status_code, 409)
                replacement = self.register("공통 새 문서")
            finally:
                release.set()
            self.assertEqual(deleting.result(timeout=5).status_code, 204)
        self.assertEqual(self.statuses(), {replacement: "registered"})
        self.store.delete_document.assert_called_once_with(target)
        self.store.upsert.assert_not_called()
