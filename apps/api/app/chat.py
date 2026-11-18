import json
import re
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from anyio import CancelScope
from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send

from .chat_model import INSUFFICIENT, GroundedChat
from .documents import connection
from .search import SearchRequest, SearchResult, retrieve
from .vectors import VectorStore

router = APIRouter(prefix="/api/chat/sessions")
SessionId = Annotated[int, Path(ge=1, le=2**63 - 1)]
Outcome = Literal["completed", "insufficient", "failed"]
CITATION = re.compile(r"\[([+-]?\d+)\]")


class Session(BaseModel):
    id: int


class Message(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    text: str
    status: Literal["streaming", "completed", "insufficient", "failed"]
    sources: list[SearchResult]


def initialize(path: str) -> None:
    with connection(path) as db:
        db.executescript("""
            BEGIN;
            CREATE TABLE IF NOT EXISTS chat_sessions (id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL REFERENCES chat_sessions(id),
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                text TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK(status IN ('streaming', 'completed', 'insufficient', 'failed')),
                sources TEXT NOT NULL DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS messages_by_session ON chat_messages(session_id, id);
            CREATE UNIQUE INDEX IF NOT EXISTS one_stream_per_session
                ON chat_messages(session_id) WHERE status = 'streaming';
            -- 단일 워커 재기동 시 끊긴 답변을 재전송하거나 완료로 승격하지 않는다.
            UPDATE chat_messages SET status = 'failed' WHERE status = 'streaming';
        """)


@router.get("")
def list_sessions(request: Request) -> list[Session]:
    with connection(request.app.state.db_path) as db:
        return [Session(id=row[0]) for row in db.execute("SELECT id FROM chat_sessions ORDER BY id DESC")]


@router.post("", status_code=201)
def create_session(request: Request) -> Session:
    with connection(request.app.state.db_path) as db:
        return Session(id=db.execute("INSERT INTO chat_sessions DEFAULT VALUES").lastrowid)


@router.get("/{session_id}/messages")
def list_messages(request: Request, session_id: SessionId) -> list[Message]:
    with connection(request.app.state.db_path) as db:
        if not db.execute("SELECT 1 FROM chat_sessions WHERE id = ?", (session_id,)).fetchone():
            raise HTTPException(404, "채팅 세션을 찾을 수 없습니다.")
        rows = db.execute(
            "SELECT id, role, text, status, sources FROM chat_messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        )
        return [Message(id=r[0], role=r[1], text=r[2], status=r[3], sources=json.loads(r[4])) for r in rows]


def reserve(path: str, session_id: int, query: str) -> int:
    with connection(path) as db:
        # 검색 전 질문과 빈 답변을 한 번에 확보한다. 충돌한 요청은 어느 쪽도 남기지 않는다.
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM chat_sessions WHERE id = ?", (session_id,)).fetchone():
            raise HTTPException(404, "채팅 세션을 찾을 수 없습니다.")
        if db.execute(
            "SELECT 1 FROM chat_messages WHERE session_id = ? AND status = 'streaming'", (session_id,)
        ).fetchone():
            raise HTTPException(409, "이 세션에서 답변을 생성하고 있습니다.")
        db.execute(
            "INSERT INTO chat_messages(session_id, role, status, text) VALUES (?, 'user', 'completed', ?)",
            (session_id, query),
        )
        return db.execute(
            "INSERT INTO chat_messages(session_id, role, status) VALUES (?, 'assistant', 'streaming')",
            (session_id,),
        ).lastrowid


def save(path: str, message_id: int, text: str, status: Outcome, sources: list[dict]) -> None:
    with connection(path) as db:
        # 청크를 다시 조회하지 않는다. 답변 생성 시점의 순서·파일명·원문이 함께 남아야 한다.
        db.execute(
            "UPDATE chat_messages SET text = ?, status = ?, sources = ? WHERE id = ? AND status = 'streaming'",
            (text, status, json.dumps(sources, ensure_ascii=False), message_id),
        )


def outcome(answer: str, source_count: int) -> Outcome:
    if answer == INSUFFICIENT:
        return "insufficient"
    citations = CITATION.findall(answer)
    allowed = {str(n) for n in range(1, source_count + 1)}
    return "completed" if citations and all(n in allowed for n in citations) else "failed"


def frame(**fields: object) -> bytes:
    return (json.dumps(fields, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class ChatStream(StreamingResponse):
    media_type = "application/x-ndjson"

    def __init__(self, path: str, session_id: int, body: SearchRequest, store: VectorStore, model: GroundedChat):
        self.path = path
        self.session_id = session_id
        self.body_request = body
        self.store = store
        self.model = model
        self.parts: list[str] = []
        self.sources: list[dict] = []
        self.status: Outcome = "failed"
        self.events = self.generate()
        super().__init__(self.events)

    async def generate(self) -> AsyncIterator[bytes]:
        status: Outcome = "failed"
        try:
            # 검색 화면과 같은 retrieve()를 쓴다. 검색은 관련 문단을 찾는 데서 끝나지만,
            # 채팅은 그 문단을 근거로 답변까지 만든다. 후보가 없으면 모델에 추측을 맡기지 않는다.
            results = await run_in_threadpool(retrieve, self.path, self.store, self.body_request)
            if not results:
                self.parts.append(INSUFFICIENT)
                yield frame(type="delta", text=INSUFFICIENT)
                status = "insufficient"
            else:
                self.sources = [r.model_dump() for r in results]
                reply = self.model.stream(self.body_request.query, results)
                try:
                    async for text in reply:
                        if not isinstance(text, str):
                            raise ValueError("모델 응답 형식이 올바르지 않습니다.")
                        if text:
                            delta = frame(type="delta", text=text)
                            self.parts.append(text)
                            yield delta
                finally:
                    with CancelScope(shield=True):
                        await reply.aclose()
                # 스트리밍 조각 하나에는 인용 번호의 절반만 들어올 수도 있다.
                # 화면에는 즉시 보내되 인용 검사는 답변이 완성된 뒤에 한다.
                status = outcome("".join(self.parts), len(results))
        except Exception:
            # SDK 예외에는 질문·원문·인증 정보가 들어갈 수 있으므로 전송하거나 기록하지 않는다.
            status = "failed"
        yield frame(type="end", status=status, sources=self.sources if status == "completed" else [])
        # 마지막 줄까지 ASGI가 받아들인 뒤에만 정상 종료를 기록한다.
        self.status = status

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        message_id = await run_in_threadpool(reserve, self.path, self.session_id, self.body_request.query)
        try:
            await super().__call__(scope, receive, send)
        except BaseException:
            self.status = "failed"
            raise
        finally:
            # 전송 시작 또는 delta 전송에서 끊기면 반복자 내부의 다음 줄은 실행되지 않을 수 있다.
            with CancelScope(shield=True):
                try:
                    await self.events.aclose()
                finally:
                    sources = self.sources if self.status == "completed" else []
                    await run_in_threadpool(save, self.path, message_id, "".join(self.parts), self.status, sources)


@router.post("/{session_id}/messages", response_class=ChatStream)
async def post_message(request: Request, session_id: SessionId, body: SearchRequest) -> ChatStream:
    state = request.app.state
    return ChatStream(state.db_path, session_id, body, state.vector_store, state.chat_model)
