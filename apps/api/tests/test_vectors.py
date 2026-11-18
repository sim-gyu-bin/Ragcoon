import os
import unittest
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from app.vectors import COLLECTION, MODEL, VectorError, VectorStore


def response(vectors):
    return SimpleNamespace(data=[
        SimpleNamespace(index=i, embedding=vector)
        for i, vector in reversed(list(enumerate(vectors)))
    ])


class VectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"OPENAI_API_KEY": "test-only", "CHROMA_PATH": "test-path"}))
        self.sdk = self.enterContext(patch("app.vectors.OpenAI"))
        self.chroma = self.enterContext(patch("app.vectors.chromadb.PersistentClient"))
        self.create = self.sdk.return_value.embeddings.create
        self.create.side_effect = lambda *, model, input: response([[float(i)] for i in range(len(input))])
        self.collection = self.chroma.return_value.get_or_create_collection.return_value
        self.store = VectorStore()

    def test_missing_key_and_empty_candidates_do_not_open_clients(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(self.store.search("질문", [], 3), [])
            with self.assertRaisesRegex(VectorError, "API 키"):
                self.store.upsert(1, [(1, "자료")])
            with self.assertRaisesRegex(VectorError, "API 키"):
                self.store.search("질문", [1], 3)
        self.sdk.assert_not_called()
        self.chroma.assert_not_called()

    def test_reuses_clients_and_explicit_vectors_without_storing_text(self):
        self.store.upsert(12, [(31, "첫 조각"), (35, "둘째 조각")])
        self.create.assert_called_once_with(model=MODEL, input=["첫 조각", "둘째 조각"])
        self.collection.upsert.assert_called_once_with(
            ids=["31", "35"], embeddings=[[0.0], [1.0]], metadatas=[{"document_id": 12}] * 2,
        )
        self.collection.query.return_value = {"ids": [["35", "31"]]}
        self.assertEqual(self.store.search("질문", [12, 34], 2), [35, 31])
        self.create.assert_called_with(model="text-embedding-3-small", input=["질문"])
        self.collection.query.assert_called_once_with(
            query_embeddings=[[0.0]], where={"document_id": {"$in": [12, 34]}}, n_results=2, include=[],
        )
        self.sdk.assert_called_once_with(api_key="test-only", timeout=30.0, max_retries=0)
        self.chroma.assert_called_once()
        self.assertEqual(self.chroma.call_args.kwargs["path"], "test-path")
        self.chroma.return_value.get_or_create_collection.assert_called_once_with(
            name=COLLECTION, embedding_function=None,
        )

    def test_batches_limit_item_count_and_utf8_bytes(self):
        for text, count, sizes in [("한", 65, [64, 1]), ("한" * 1000, 43, [42, 1])]:
            with self.subTest(count=count):
                self.create.reset_mock()
                self.collection.upsert.reset_mock()
                self.store.upsert(2, [(i, text) for i in range(count)])
                self.assertEqual([len(c.kwargs["input"]) for c in self.create.call_args_list], sizes)
                ids = [key for c in self.collection.upsert.call_args_list for key in c.kwargs["ids"]]
                self.assertEqual(ids, [str(i) for i in range(count)])
        with self.assertRaises(VectorError):
            self.store.search("가" * 3000, [2], 3)
        self.assertEqual(self.create.call_count, 2)

    def test_invalid_embedding_indices_never_reach_storage(self):
        self.create.side_effect = None
        self.create.return_value = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.0])] * 2)
        with self.assertRaises(VectorError):
            self.store.upsert(4, [(9, "앞"), (10, "뒤")])
        self.collection.upsert.assert_not_called()

    def test_dependency_errors_do_not_echo_private_details(self):
        private = "합성-민감-내용"
        steps = [
            (self.chroma.return_value.get_or_create_collection, lambda: self.store.search(private, [1], 1)),
            (self.collection.upsert, lambda: self.store.upsert(1, [(7, private)])),
            (self.collection.query, lambda: self.store.search(private, [1], 1)),
            (self.create, lambda: self.store.search(private, [1], 1)),
        ]
        for method, operation in steps:
            with self.subTest(method=method):
                previous, calls = method.side_effect, method.call_count
                method.side_effect = RuntimeError(private)
                try:
                    with self.assertRaises(VectorError) as raised:
                        operation()
                finally:
                    method.side_effect = previous
                self.assertEqual(method.call_count, calls + 1)
                self.assertNotIn(private, str(raised.exception))
                self.assertTrue(raised.exception.__suppress_context__)


class LocalChromaTests(unittest.TestCase):
    def test_reopened_store_ranks_only_ready_chunks_after_upsert(self):
        # 실제 로컬 저장소를 사용하되 모델 호출은 고정된 합성 벡터로 대체한다.
        vectors = {"질문": [1.0, 0.0], "근접": [0.9, 0.1], "먼쪽": [0.0, 1.0]}
        with TemporaryDirectory() as path, patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-only", "CHROMA_PATH": path,
        }), patch("app.vectors.OpenAI") as sdk:
            sdk.return_value.embeddings.create.side_effect = (
                lambda *, model, input: response([vectors[text] for text in input])
            )
            store = VectorStore()
            store.upsert(10, [(1, "질문")])
            store.upsert(20, [(2, "먼쪽"), (3, "근접")])
            store.upsert(20, [(2, "근접"), (3, "먼쪽")])
            self.assertEqual(VectorStore().search("질문", [20], 2), [2, 3])
