import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .pdf import MAX_PDF_BYTES, extract_pdf

router = APIRouter(prefix="/api/documents")
MAX_BYTES = 1024 * 1024
MAX_CHARS = 1000
MAX_PDF_CHUNKS = 10_000
# 줄바꿈을 정규화하지 않고 원문에서 잘라야 CRLF 문서도 그대로 인용할 수 있다.
LINE = re.compile(r"([^\r\n]*)(?:\r\n?|\n|\Z)")


class Document(BaseModel):
    id: int
    filename: str
    status: Literal["registered", "indexing", "failed", "ready", "delete_pending"]
    chunk_count: int


class Chunk(BaseModel):
    id: int
    page_number: int | None
    start_line: int
    end_line: int
    text: str


@contextmanager
def connection(path: str) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        with db:
            yield db


def initialize(path: str) -> None:
    with connection(path) as db:
        db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'registered',
                source TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL REFERENCES documents(id),
                page_number INTEGER CHECK(page_number >= 1),
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chunks_by_document ON chunks(document_id);
            CREATE TABLE IF NOT EXISTS ingest_sequence (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                document_id INTEGER NOT NULL,
                chunk_id INTEGER NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                text, tokenize = "unicode61 tokenchars '_'"
            );
            -- 로컬 API는 단일 워커다. 중단된 작업은 기동 시 유료 재호출하지 않는다.
            UPDATE documents SET status = 'failed' WHERE status = 'indexing';
        """)
        if "page_number" not in {row["name"] for row in db.execute("PRAGMA table_info(chunks)")}:
            # 기존 Markdown 행의 ID, 줄 범위, FTS rowid를 바꾸지 않고 NULL 열만 추가한다.
            db.execute("ALTER TABLE chunks ADD COLUMN page_number INTEGER CHECK(page_number >= 1)")
        if not db.execute("SELECT 1 FROM ingest_sequence").fetchone():
            document_id, chunk_id = db.execute("""
                SELECT COALESCE((SELECT MAX(id) FROM documents), 0),
                       MAX(COALESCE((SELECT MAX(id) FROM chunks), 0),
                           COALESCE((SELECT MAX(rowid) FROM chunks_fts), 0))
            """).fetchone()
            # 삭제된 행보다 오래 남는 인용 ID도 예약해야 옛 답변이 다른 문서를 가리키지 않는다.
            if db.execute("SELECT 1 FROM sqlite_schema WHERE name = 'chat_messages'").fetchone():
                past_document, past_chunk = db.execute("""
                    SELECT COALESCE(MAX(json_extract(s.value, '$.document_id')), 0),
                           COALESCE(MAX(json_extract(s.value, '$.chunk_id')), 0)
                    FROM chat_messages AS m, json_each(m.sources) AS s
                    WHERE m.sources != '[]'
                """).fetchone()
                document_id = max(document_id, past_document)
                chunk_id = max(chunk_id, past_chunk)
            db.execute("INSERT INTO ingest_sequence VALUES (1, ?, ?)", (document_id, chunk_id))


def validate_filename(filename: str) -> None:
    stem, _, ext = filename.rpartition(".")
    if (
        not stem.strip(" .")
        or filename != filename.strip()
        or not filename.isprintable()
        or any(char in filename for char in ("/", "\\", ":"))
        or len(filename.encode("utf-8")) > 255
    ):
        raise HTTPException(400, "경로를 제외한 올바른 파일 이름이 필요합니다.")
    if ext.lower() not in {"md", "markdown", "pdf"}:
        raise HTTPException(415, "Markdown(.md, .markdown) 또는 PDF(.pdf) 파일을 등록해 주세요.")


def split_chunks(source: str) -> Iterator[tuple[int, int, str]]:
    start: int | None = None
    end = first_line = last_line = 0
    # UTF-8 서명은 원문에 보관하되 첫 문단의 내용으로 세지 않는다.
    lines = LINE.finditer(source, int(source.startswith("\ufeff")))
    for line_no, match in enumerate(lines, 1):
        left, right = match.span(1)
        blank = left == right or match[1].isspace()
        if start is not None and (blank or right - start > MAX_CHARS):
            yield first_line, last_line, source[start:end]
            start = None
        if blank:
            continue
        if start is None:
            # 한 줄 자체가 길 때만 줄 안에서 나눈다. 조각들의 출처는 같은 줄이다.
            while right - left > MAX_CHARS:
                yield line_no, line_no, source[left : left + MAX_CHARS]
                left += MAX_CHARS
            start, first_line = left, line_no
        end, last_line = right, line_no
    if start is not None:
        yield first_line, last_line, source[start:end]


def register(path: str, filename: str, body: bytearray) -> Document:
    pdf = filename.lower().endswith(".pdf")
    if pdf:
        source, pages = extract_pdf(body)
    else:
        try:
            source = body.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(400, "파일을 UTF-8 텍스트로 읽을 수 없습니다.") from None
        if "\x00" in source:
            raise HTTPException(400, "NUL 문자가 포함된 파일은 등록할 수 없습니다.")
        pages = [source]

    # 삭제와 등록이 경합해도 발급 기록과 실제 행은 한 트랜잭션으로만 커밋한다.
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        last_document, last_chunk = db.execute(
            "SELECT document_id, chunk_id FROM ingest_sequence WHERE singleton = 1"
        ).fetchone()
        document_id = last_document + 1
        if document_id > 2**63 - 1:
            raise HTTPException(503, "문서 식별자를 더 이상 발급할 수 없습니다.")
        db.execute(
            "INSERT INTO documents (id, filename, source) VALUES (?, ?, ?)", (document_id, filename, source)
        )

        def rows() -> Iterator[tuple[int, int, int | None, int, int, str]]:
            chunk_id = last_chunk
            for number, text in enumerate(pages, 1):
                for start, end, part in split_chunks(text):
                    chunk_id += 1
                    if pdf and chunk_id - last_chunk > MAX_PDF_CHUNKS:
                        raise HTTPException(413, "PDF에서 나뉜 문단 수가 너무 많습니다.")
                    if chunk_id > 2**63 - 1:
                        raise HTTPException(503, "문단 식별자를 더 이상 발급할 수 없습니다.")
                    yield chunk_id, document_id, number if pdf else None, start, end, part

        count = db.executemany(
            "INSERT INTO chunks (id, document_id, page_number, start_line, end_line, text) VALUES (?, ?, ?, ?, ?, ?)",
            rows(),
        ).rowcount
        if not count:
            if pdf:
                raise HTTPException(400, "추출 가능한 텍스트가 없습니다. 이미지 OCR은 지원하지 않습니다.")
            raise HTTPException(400, "내용이 없는 문서는 등록할 수 없습니다.")
        db.execute(
            "UPDATE ingest_sequence SET document_id = ?, chunk_id = ? WHERE singleton = 1",
            (document_id, last_chunk + count),
        )
        return Document(id=document_id, filename=filename, status="registered", chunk_count=count)


@router.post("", status_code=201)
async def create_document(request: Request, filename: str) -> Document:
    validate_filename(filename)
    body = bytearray()
    limit = MAX_PDF_BYTES if filename.lower().endswith(".pdf") else MAX_BYTES
    # Content-Length가 없거나 실제 크기와 달라도 본문을 제한 이상 모으지 않는다.
    async for part in request.stream():
        if len(part) > limit - len(body):
            raise HTTPException(413, f"파일은 {limit // (1024 * 1024)} MiB 이하여야 합니다.")
        body.extend(part)
    return await run_in_threadpool(register, request.app.state.db_path, filename, body)


@router.get("")
def list_documents(request: Request) -> list[Document]:
    with connection(request.app.state.db_path) as db:
        rows = db.execute("""
            SELECT id, filename, status,
                   (SELECT COUNT(*) FROM chunks WHERE document_id = documents.id) AS chunk_count
            FROM documents ORDER BY id DESC
        """)
        return [Document(**row) for row in rows]


def claim_index(path: str, document_id: int) -> list[tuple[int, str]] | None:
    with connection(path) as db:
        # 조건부 갱신으로 같은 문서를 동시에 전송하지 않는다. 외부 호출 전에 커밋한다.
        claimed = db.execute(
            "UPDATE documents SET status = 'indexing' WHERE id = ? AND status IN ('registered', 'failed')",
            (document_id,),
        ).rowcount
        if not claimed:
            row = db.execute("SELECT status FROM documents WHERE id = ?", (document_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "문서를 찾을 수 없습니다.")
            if row["status"] == "ready":
                return None
            if row["status"] == "delete_pending":
                raise HTTPException(409, "삭제 대기 중인 문서는 색인할 수 없습니다.")
            raise HTTPException(409, "이 문서는 이미 색인 중입니다.")
        return [
            (row["id"], row["text"])
            for row in db.execute(
                "SELECT id, text FROM chunks WHERE document_id = ? ORDER BY id", (document_id,)
            )
        ]


@router.post("/{document_id}/index")
def index_document(
    request: Request, document_id: Annotated[int, Path(ge=1, le=2**63 - 1)]
) -> dict[str, Literal["ready"]]:
    path = request.app.state.db_path
    chunks = claim_index(path, document_id)
    if chunks is not None:
        try:
            request.app.state.vector_store.upsert(document_id, chunks)
            # 벡터 쓰기 후 FTS와 상태를 함께 커밋해야 실패 문서가 BM25 통계에도 섞이지 않는다.
            with connection(path) as db:
                db.execute(
                    """INSERT OR REPLACE INTO chunks_fts (rowid, text)
                       SELECT id, text FROM chunks WHERE document_id = ?""",
                    (document_id,),
                )
                db.execute("UPDATE documents SET status = 'ready' WHERE id = ?", (document_id,))
        except Exception:
            with connection(path) as db:
                db.execute("UPDATE documents SET status = 'failed' WHERE id = ?", (document_id,))
            raise HTTPException(
                503, "색인을 완료하지 못했습니다. 저장소와 임베딩 설정을 확인한 뒤 다시 시도해 주세요."
            ) from None
    return {"status": "ready"}


@router.get("/{document_id}/chunks")
def list_chunks(
    request: Request, document_id: Annotated[int, Path(ge=1, le=2**63 - 1)]
) -> list[Chunk]:
    with connection(request.app.state.db_path) as db:
        # 삭제 선점과 청크 조회가 경합해도 한 시점의 공개 상태만 보인다.
        db.execute("BEGIN")
        exists = db.execute(
            "SELECT 1 FROM documents WHERE id = ? AND status != 'delete_pending'", (document_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        # ID는 한 트랜잭션에서 원문 순서대로 발급되므로 같은 줄에서 나눈 조각도 정렬된다.
        rows = db.execute(
            "SELECT id, page_number, start_line, end_line, text FROM chunks WHERE document_id = ? ORDER BY id",
            (document_id,),
        )
        return [Chunk(**row) for row in rows]
