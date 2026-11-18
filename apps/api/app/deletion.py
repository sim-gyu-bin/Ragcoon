from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response

from .documents import connection
from .vectors import VectorError

router = APIRouter(prefix="/api/documents")


class ActiveDeletions:
    def __init__(self) -> None:
        self._lock = Lock()
        self._ids: set[int] = set()

    @contextmanager
    def claim(self, document_id: int) -> Iterator[None]:
        # 단일 워커 안에서 같은 ID의 정리만 막는다. 프로세스가 끊기면 SQLite에 남은
        # delete_pending은 새 워커가 다시 선점할 수 있고 다른 문서 작업은 기다리지 않는다.
        with self._lock:
            if document_id in self._ids:
                raise HTTPException(409, "이 문서는 이미 삭제 중입니다.")
            self._ids.add(document_id)
        try:
            yield
        finally:
            with self._lock:
                self._ids.remove(document_id)


@router.delete("/{document_id}", status_code=204, response_class=Response)
def delete_document(
    request: Request, document_id: Annotated[int, Path(ge=1, le=2**63 - 1)]
) -> Response:
    state = request.app.state
    with state.active_deletions.claim(document_id):
        # 외부 저장소와 원자적으로 커밋할 수 없다. 먼저 검색·색인에서 내리고,
        # 어느 정리 단계가 실패하더라도 이 상태로 남겨 같은 ID로 재시도하게 한다.
        with connection(state.db_path) as db:
            changed = db.execute(
                """UPDATE documents SET status = 'delete_pending'
                   WHERE id = ? AND status IN ('registered', 'failed', 'ready', 'delete_pending')""",
                (document_id,),
            ).rowcount
            if not changed:
                if db.execute("SELECT 1 FROM documents WHERE id = ?", (document_id,)).fetchone() is None:
                    raise HTTPException(404, "문서를 찾을 수 없습니다.")
                raise HTTPException(409, "색인 중인 문서는 삭제할 수 없습니다.")

        # Chroma 복구를 기다리는 동안에도 이 문서의 단어가 BM25 통계에 남지 않게 한다.
        with connection(state.db_path) as db:
            db.execute(
                "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE document_id = ?)",
                (document_id,),
            )
        try:
            state.vector_store.delete_document(document_id)
        except VectorError:
            raise HTTPException(503, "문서 벡터를 삭제하지 못했습니다. 목록에서 삭제를 다시 시도해 주세요.") from None

        with connection(state.db_path) as db:
            db.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    return Response(status_code=204)
