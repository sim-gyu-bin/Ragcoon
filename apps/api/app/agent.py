from dataclasses import dataclass
from operator import add
from typing import Annotated, Literal, TypedDict

from anyio import CancelScope
from fastapi import APIRouter, Request
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .chat import CITATION, Outcome, outcome
from .chat_model import INSUFFICIENT, GroundedChat
from .search import Mode, SearchRequest, SearchResult, retrieve
from .vectors import VectorStore

router = APIRouter(prefix="/api/experiments")
Strategy = Literal["baseline", "agent"]
StopReason = Literal[
    "answered", "no_results", "model_insufficient", "retry_limit",
    "search_failed", "generation_failed", "invalid_citation",
]


class RunResult(BaseModel):
    strategy: Strategy
    status: Outcome
    answer: str
    sources: list[SearchResult]
    retrieved: list[SearchResult]
    steps: list[str]
    stop_reason: StopReason


class Comparison(BaseModel):
    baseline: RunResult
    agent: RunResult


@dataclass(frozen=True)
class _Inputs:
    path: str
    store: VectorStore
    model: GroundedChat
    query: str
    strategy: Strategy


class _State(TypedDict):
    mode: Mode
    retried: bool
    sources: list[SearchResult]
    answer: str
    steps: Annotated[list[str], add]
    reason: StopReason | None


async def _search(state: _State, runtime: Runtime[_Inputs]) -> dict:
    # 유일한 읽기 도구. 모델/문서에서 도구 이름이나 호출 인자를 해석하지 않는다.
    ctx = runtime.context
    body = SearchRequest(query=ctx.query, mode=state["mode"])
    step = f"search_{body.mode}"
    try:
        sources = await run_in_threadpool(retrieve, ctx.path, ctx.store, body)
    except Exception:
        return {"sources": [], "reason": "search_failed", "steps": [step]}
    return {"sources": sources, "steps": [step]}


def _assess(state: _State) -> dict:
    if state["sources"]:
        return {"steps": ["assess_found"]}
    reason = "retry_limit" if state["retried"] else "no_results"
    return {"reason": reason, "answer": INSUFFICIENT, "steps": ["assess_empty"]}


async def _generate(state: _State, runtime: Runtime[_Inputs]) -> dict:
    ctx = runtime.context
    try:
        parts: list[str] = []
        reply = ctx.model.stream(ctx.query, state["sources"])
        try:
            async for text in reply:
                parts.append(text)
        finally:
            with CancelScope(shield=True):
                await reply.aclose()
        answer = "".join(parts)
    except Exception:
        # SDK 예외나 부분 답변을 진단에 싣거나 재시도하지 않는다.
        return {"reason": "generation_failed", "steps": ["generate"]}
    return {"answer": answer, "steps": ["generate"]}


def _validate(state: _State) -> dict:
    status = outcome(state["answer"], len(state["sources"]))
    if status == "completed":
        reason = "answered"
    elif status == "insufficient":
        reason = "retry_limit" if state["retried"] else "model_insufficient"
    else:
        reason = "invalid_citation"
    return {"reason": reason, "steps": [f"validate_{status}"]}


def _next(state: _State, runtime: Runtime[_Inputs]) -> str:
    reason = state["reason"]
    if reason is None:  # 후보가 있으면 실제 답변과 그 인용까지 판정한다.
        return "generate"
    if runtime.context.strategy == "agent" and reason in ("no_results", "model_insufficient"):
        return "retry"
    return END


def _retry(state: _State) -> dict:
    mode = "sparse" if state["mode"] == "hybrid" else "hybrid"
    return {"mode": mode, "retried": True, "reason": None, "answer": "", "sources": [],
            "steps": ["retry_search"]}


def _build_graph():
    graph = StateGraph(_State, context_schema=_Inputs)
    graph.add_node("search", _search)
    graph.add_node("assess", _assess)
    graph.add_node("generate", _generate)
    graph.add_node("validate", _validate)
    graph.add_node("retry", _retry)
    graph.add_edge(START, "search")
    graph.add_conditional_edges("search", lambda s: END if s["reason"] else "assess", [END, "assess"])
    graph.add_conditional_edges("assess", _next, ["generate", "retry", END])
    graph.add_conditional_edges("generate", lambda s: END if s["reason"] else "validate", [END, "validate"])
    graph.add_conditional_edges("validate", _next, ["retry", END])
    graph.add_edge("retry", "search")
    return graph.compile()


_GRAPH = _build_graph()


def _cited_sources(answer: str, retrieved: list[SearchResult]) -> tuple[str, list[SearchResult]]:
    # outcome이 검증한 번호만 있다. 후보를 줄인 뒤에도 [n]과 sources[n-1]이 대응해야 한다.
    cited = set(CITATION.findall(answer))
    sources = []
    numbers = {}
    for n, source in enumerate(retrieved, 1):
        if str(n) in cited:
            sources.append(source)
            numbers[str(n)] = f"[{len(sources)}]"
    return CITATION.sub(lambda match: numbers[match[1]], answer), sources


async def run_strategy(path: str, store: VectorStore, model: GroundedChat, query: str,
                       mode: Mode, strategy: Strategy) -> RunResult:
    initial: _State = {"mode": mode, "retried": False, "sources": [], "answer": "",
                       "steps": [], "reason": None}
    # 상위 앱/환경에서 추적을 켰더라도 질문·원문을 가진 그래프는 전송하지 않는다.
    with tracing_context(enabled=False):
        result = await _GRAPH.ainvoke(initial, context=_Inputs(path, store, model, query, strategy))
    reason = result["reason"]
    status: Outcome
    sources = []
    answer = ""
    if reason == "answered":
        status = "completed"
        answer, sources = _cited_sources(result["answer"], result["sources"])
    elif reason in ("no_results", "model_insufficient", "retry_limit"):
        status = "insufficient"
        answer = INSUFFICIENT
    else:
        status = "failed"
    return RunResult(strategy=strategy, status=status, stop_reason=reason, steps=result["steps"],
                     answer=answer, sources=sources, retrieved=result["sources"])


@router.post("/compare")
async def compare(request: Request, body: SearchRequest) -> Comparison:
    app = request.app.state
    args = (app.db_path, app.vector_store, app.chat_model, body.query, body.mode)
    baseline = await run_strategy(*args, strategy="baseline")
    agent = await run_strategy(*args, strategy="agent")
    return Comparison(baseline=baseline, agent=agent)
