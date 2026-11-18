import re
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, StringConstraints

from .documents import connection
from .vectors import VectorStore

router = APIRouter(prefix="/api")
Mode = Literal["sparse", "dense", "hybrid"]
TOP_K = 3
RRF_K = 60


class SearchRequest(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    mode: Mode = "hybrid"


class SearchResult(BaseModel):
    chunk_id: int
    document_id: int
    filename: str
    # 이전 채팅 스냅샷에는 페이지 필드가 없고 Markdown은 줄 번호만 갖는다.
    page_number: int | None = None
    start_line: int
    end_line: int
    text: str


class SearchResponse(BaseModel):
    mode: Mode
    results: list[SearchResult]


def sparse_ids(db: sqlite3.Connection, query: str) -> list[int]:
    # MATCH는 바인딩한 문자열도 수식으로 해석한다. 단어만 인용해 입력의 연산자를 무력화한다.
    # 한글 단어는 뒤에 조사가 붙어도 찾고, 밑줄이 든 식별자는 토큰 전체를 맞춘다.
    terms = dict.fromkeys(re.findall(r"\w+", query))
    if not terms:
        return []
    expression = " OR ".join(
        f'"{term}"*' if re.fullmatch(r"[가-힣]+", term) else f'"{term}"' for term in terms
    )
    # 희소 검색은 원문에 있는 단어로 후보를 좁힌다. FTS5의 bm25()는 작은 값이
    # 더 좋은 일치이므로 오름차순이다. 이 경로에서는 질문 임베딩을 만들지 않는다.
    rows = db.execute(
        """SELECT chunks_fts.rowid FROM chunks_fts
           JOIN chunks AS c ON c.id = chunks_fts.rowid
           JOIN documents AS d ON d.id = c.document_id
           WHERE chunks_fts MATCH ? AND d.status = 'ready'
           ORDER BY bm25(chunks_fts), chunks_fts.rowid LIMIT ?""",
        (expression, TOP_K),
    )
    return [row[0] for row in rows]


def reciprocal_rank_fusion(sparse: list[int], dense: list[int]) -> list[int]:
    # BM25 점수와 벡터 거리는 척도가 달라 그대로 더하지 않는다. RRF는 각 목록의
    # 순위만 사용한다. 두 목록에서 모두 2위인 청크는 1/62 + 1/62를 얻어
    # 한 목록에서만 1위인 청크(1/61)보다 앞선다. 합산 기준은 문서가 아니라 청크 ID다.
    scores: dict[int, float] = {}
    for ranking in (sparse, dense):
        for rank, chunk_id in enumerate(dict.fromkeys(ranking), start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (RRF_K + rank)
    return sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))


def read_results(db: sqlite3.Connection, ranked_ids: list[int]) -> list[SearchResult]:
    if not ranked_ids:
        return []
    ids = list(dict.fromkeys(ranked_ids))
    placeholders = ", ".join("?" for _ in ids)
    # 벡터 저장소는 순위만 제공한다. 출처와 공개 여부는 항상 현재 SQLite 원본으로 확인한다.
    rows = db.execute(
        f"""SELECT c.id AS chunk_id, c.document_id, d.filename, c.page_number, c.start_line, c.end_line, c.text
            FROM chunks AS c JOIN documents AS d ON d.id = c.document_id
            WHERE d.status = 'ready' AND c.id IN ({placeholders})""",
        ids,
    )
    by_id = {row["chunk_id"]: row for row in rows}
    return [SearchResult(**by_id[chunk_id]) for chunk_id in ids if chunk_id in by_id][:TOP_K]


def retrieve(path: str, store: VectorStore, body: SearchRequest) -> list[SearchResult]:
    with connection(path) as db:
        # 하이브리드의 두 후보군은 같은 시점의 준비 문서를 사용한다. 네트워크 대기 중에는 락을 풀어 둔다.
        db.execute("BEGIN")
        sparse = sparse_ids(db, body.query) if body.mode != "dense" else []
        if body.mode == "sparse":
            return read_results(db, sparse)
        ready = [row[0] for row in db.execute("SELECT id FROM documents WHERE status = 'ready' ORDER BY id")]
    if not ready:
        return []
    dense = store.search(body.query, ready, TOP_K)
    ranked = dense if body.mode == "dense" else reciprocal_rank_fusion(sparse, dense)
    with connection(path) as db:
        return read_results(db, ranked)


@router.post("/search")
def search(request: Request, body: SearchRequest) -> SearchResponse:
    results = retrieve(request.app.state.db_path, request.app.state.vector_store, body)
    return SearchResponse(mode=body.mode, results=results)
