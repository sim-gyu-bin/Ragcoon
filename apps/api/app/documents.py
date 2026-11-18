import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/documents")
MAX_BYTES = 1024 * 1024
MAX_CHARS = 1000
# 줄바꿈을 정규화하지 않고 원문에서 잘라야 CRLF 문서도 그대로 인용할 수 있다.
LINE = re.compile(r"([^\r\n]*)(?:\r\n?|\n|\Z)")


class Document(BaseModel):
    id: int
    filename: str
    status: Literal["registered"]
    chunk_count: int


class Chunk(BaseModel):
    id: int
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
            BEGIN;
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'registered',
                source TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL REFERENCES documents(id),
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chunks_by_document ON chunks(document_id);
        """)


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
    if ext.lower() not in {"md", "markdown"}:
        raise HTTPException(415, "Markdown 파일(.md, .markdown)만 등록할 수 있습니다.")


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
    try:
        source = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "파일을 UTF-8 텍스트로 읽을 수 없습니다.") from None
    if "\x00" in source:
        raise HTTPException(400, "NUL 문자가 포함된 파일은 등록할 수 없습니다.")

    # 같은 이름도 새 ID를 받는다. 청크가 없거나 저장에 실패하면 원문도 롤백한다.
    with connection(path) as db:
        document_id = db.execute(
            "INSERT INTO documents (filename, source) VALUES (?, ?)", (filename, source)
        ).lastrowid
        count = db.executemany(
            "INSERT INTO chunks (document_id, start_line, end_line, text) VALUES (?, ?, ?, ?)",
            ((document_id, *chunk) for chunk in split_chunks(source)),
        ).rowcount
        if not count:
            raise HTTPException(400, "내용이 없는 문서는 등록할 수 없습니다.")
        return Document(id=document_id, filename=filename, status="registered", chunk_count=count)


@router.post("", status_code=201)
async def create_document(request: Request, filename: str) -> Document:
    validate_filename(filename)
    body = bytearray()
    # Content-Length가 없거나 실제 크기와 달라도 본문을 제한 이상 모으지 않는다.
    async for part in request.stream():
        if len(part) > MAX_BYTES - len(body):
            raise HTTPException(413, "파일은 1 MiB 이하여야 합니다.")
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


@router.get("/{document_id}/chunks")
def list_chunks(
    request: Request, document_id: Annotated[int, Path(ge=1, le=2**63 - 1)]
) -> list[Chunk]:
    with connection(request.app.state.db_path) as db:
        exists = db.execute("SELECT 1 FROM documents WHERE id = ?", (document_id,)).fetchone()
        if exists is None:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        # ID는 한 트랜잭션에서 원문 순서대로 발급되므로 같은 줄에서 나눈 조각도 정렬된다.
        rows = db.execute(
            "SELECT id, start_line, end_line, text FROM chunks WHERE document_id = ? ORDER BY id",
            (document_id,),
        )
        return [Chunk(**row) for row in rows]
