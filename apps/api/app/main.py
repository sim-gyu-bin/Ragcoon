import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import documents

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.db_path = os.environ.get("RAGCOON_DB", "ragcoon.sqlite3")
    documents.initialize(app.state.db_path)
    yield


app = FastAPI(title="RAGcoon API", lifespan=lifespan)
app.include_router(documents.router)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    # 기본 검증 응답에는 사용자가 보낸 값이 담길 수 있다.
    return JSONResponse(status_code=422, content={"detail": "요청 매개변수를 확인해 주세요."})


@app.exception_handler(sqlite3.Error)
async def storage_error(request: Request, exc: sqlite3.Error) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "문서 저장소를 사용할 수 없습니다."})


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
