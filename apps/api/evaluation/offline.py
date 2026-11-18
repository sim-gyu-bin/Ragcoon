"""정답표와 무관한 합성 임베딩·발췌 대역. 실제 Chroma에 벡터를 쓰고 질의한다."""

import hashlib
import math
import re
import unicodedata
from collections.abc import AsyncIterator
from pathlib import Path

import chromadb
from chromadb.config import Settings

from app.chat_model import INSUFFICIENT
from app.search import SearchResult


def _features(text: str) -> set[str]:
    terms = re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())
    return {gram for term in terms for gram in (
        [term] if len(term) == 1 else [term[i:i + 2] for i in range(len(term) - 1)]
    )}


def embedding(text: str) -> list[float]:
    # 내장 hash의 프로세스별 시드 대신 고정 해시로 문자 2-gram을 투영한다.
    vector = [0.0] * 512
    for gram in _features(text):
        digest = hashlib.blake2s(gram.encode(), digest_size=4).digest()
        vector[int.from_bytes(digest, "little") % len(vector)] += 1.0
    length = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / length for v in vector]


class OfflineStore:
    def __init__(self, path: Path):
        self.client = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))
        self.collection = self.client.create_collection(name="synthetic-bigrams", embedding_function=None)

    def upsert(self, document_id: int, chunks: list[tuple[int, str]]) -> None:
        self.collection.add(ids=[str(i) for i, _ in chunks], embeddings=[embedding(text) for _, text in chunks],
                            metadatas=[{"document_id": document_id} for _ in chunks])

    def search(self, query: str, ready_ids: list[int], limit: int) -> list[int]:
        if not ready_ids or limit <= 0:
            return []
        result = self.collection.query(query_embeddings=[embedding(query)],
                                       where={"document_id": {"$in": ready_ids}}, n_results=limit, include=[])
        return [int(i) for i in result["ids"][0]]


class ExtractiveModel:
    async def stream(self, query: str, sources: list[SearchResult]) -> AsyncIterator[str]:
        # 모든 문서에 같은 규칙: 검색된 '항목: 값' 줄만 보고 질문에 가까운 항목의 원문을 인용한다.
        # 항목을 구별할 수 없거나 어휘 유사도가 낮으면 답을 만들지 않는다.
        query_terms = _features(query)
        best = second = 0.0
        answer = INSUFFICIENT
        for i, source in enumerate(sources, 1):
            for line in source.text.splitlines():
                label, separator, value = line.partition(":")
                terms = _features(label)
                if not separator or not value.strip() or not terms or not query_terms:
                    continue
                similarity = len(terms & query_terms) / math.sqrt(len(terms) * len(query_terms))
                if similarity > best:
                    second, best = best, similarity
                    answer = f"{line} [{i}]"
                else:
                    second = max(second, similarity)
        yield answer if best >= 0.30 and best - second >= 0.04 else INSUFFICIENT
