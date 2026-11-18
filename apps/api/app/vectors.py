import os
from collections.abc import Iterator
from threading import Lock

import chromadb
from chromadb.config import Settings
from openai import OpenAI

MODEL = "text-embedding-3-small"
COLLECTION = "ragcoon-chunks-text-embedding-3-small"
# BPE 토큰 수는 UTF-8 바이트 수를 넘지 않는다. 별도 토크나이저 없이
# 입력 하나(8192토큰)와 요청 전체(30만 토큰·2048개 입력) 한도에 여유를 둔다.
MAX_INPUT_BYTES = 8_000
MAX_BATCH_BYTES = 128_000
MAX_BATCH_INPUTS = 64


class VectorError(RuntimeError):
    pass


def _input_size(text: str) -> int:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise VectorError("임베딩 입력을 UTF-8로 변환할 수 없습니다.") from None
    if not size or size > MAX_INPUT_BYTES:
        raise VectorError("임베딩 입력 길이가 허용 범위를 벗어났습니다.")
    return size


def _batches(chunks: list[tuple[int, str]]) -> Iterator[list[tuple[int, str]]]:
    start = total = 0
    for i, (_, text) in enumerate(chunks):
        size = _input_size(text)
        if i - start == MAX_BATCH_INPUTS or total + size > MAX_BATCH_BYTES:
            yield chunks[start:i]
            start, total = i, 0
        total += size
    if start < len(chunks):
        yield chunks[start:]


def _embed(client: OpenAI, inputs: list[str]) -> list[list[float]]:
    try:
        response = client.embeddings.create(model=MODEL, input=inputs)
        # 결과가 요청 순서와 다르게 와도 다른 청크의 벡터를 저장하지 않는다.
        rows = sorted(response.data, key=lambda row: row.index)
        if len(rows) != len(inputs) or any(row.index != i for i, row in enumerate(rows)):
            raise ValueError
        return [row.embedding for row in rows]
    except Exception:
        # SDK 예외에는 요청 본문, 서비스 응답 등의 민감한 정보가 포함될 수 있다.
        raise VectorError("임베딩을 가져올 수 없습니다.") from None


class VectorStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._services: tuple[OpenAI, chromadb.Collection] | None = None

    def _clients(self) -> tuple[OpenAI, chromadb.Collection]:
        with self._lock:
            if self._services is None:
                key = os.environ.get("OPENAI_API_KEY")
                if not key or not key.strip():
                    raise VectorError("서버에 OpenAI API 키가 설정되지 않았습니다.")
                try:
                    self._chroma = chromadb.PersistentClient(
                        path=os.environ.get("CHROMA_PATH", "./chroma"),
                        settings=Settings(anonymized_telemetry=False),
                    )
                    collection = self._chroma.get_or_create_collection(
                        name=COLLECTION, embedding_function=None,
                    )
                    # 타임아웃 뒤에도 과금될 수 있어 SDK가 요청을 자동 재전송하지 않게 한다.
                    client = OpenAI(api_key=key, timeout=30.0, max_retries=0)
                    self._services = client, collection
                except Exception:
                    raise VectorError("벡터 검색 서비스를 초기화할 수 없습니다.") from None
            return self._services

    def upsert(self, document_id: int, chunks: list[tuple[int, str]]) -> None:
        if not chunks:
            return
        client, collection = self._clients()
        for batch in _batches(chunks):
            embeddings = _embed(client, [text for _, text in batch])
            try:
                # 재시도 시 이전 배치에서 성공한 청크도 SQLite의 같은 ID로 덮어쓴다.
                collection.upsert(
                    ids=[str(chunk_id) for chunk_id, _ in batch],
                    embeddings=embeddings,
                    metadatas=[{"document_id": document_id} for _ in batch],
                )
            except Exception:
                raise VectorError("벡터 색인을 저장할 수 없습니다.") from None

    def search(self, query: str, ready_ids: list[int], limit: int) -> list[int]:
        if not ready_ids or limit <= 0:
            return []
        _input_size(query)
        client, collection = self._clients()
        embeddings = _embed(client, [query])
        try:
            # 순위 계산 전에 범위를 좁혀야 부분 색인된 문서가 상위 후보를 밀어내지 않는다.
            result = collection.query(
                query_embeddings=embeddings, where={"document_id": {"$in": ready_ids}},
                n_results=limit, include=[],
            )
            return [int(chunk_id) for chunk_id in result["ids"][0]]
        except Exception:
            raise VectorError("벡터 색인을 검색할 수 없습니다.") from None
