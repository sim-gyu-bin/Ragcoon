import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

from langsmith import tracing_context
from starlette.applications import Starlette
from starlette.requests import Request

from app.agent import run_strategy
from app.chat_model import GroundedChat
from app.documents import index_document, initialize, register
from app.vectors import VectorStore

from .dataset import CASES, DOCUMENTS
from .offline import ExtractiveModel, OfflineStore
from .scoring import score, summarize

MODES = ("sparse", "dense", "hybrid")
STRATEGIES = ("baseline", "agent")


def build_index(path: str, store) -> None:
    initialize(path)
    # 실제 등록·색인 함수를 사용하되 서버 기동이나 기본 DB의 lifespan은 실행하지 않는다.
    app = Starlette()
    app.state.db_path, app.state.vector_store = path, store
    request = Request({"type": "http", "app": app})
    for filename, lines in DOCUMENTS.items():
        document = register(path, filename, bytearray("\n\n".join(lines), "utf-8"))
        index_document(request, document.id)


async def evaluate(live: bool, trace_synthetic: bool) -> dict:
    profile = "live" if live else "offline"
    directory = Path(mkdtemp(prefix=f"ragcoon-eval-{profile}-"))
    path, chroma = str(directory / "documents.sqlite3"), directory / "chroma"
    rows = []
    report = {
        "schema_version": 1, "profile": profile, "workspace": str(directory),
        "trace_requested": trace_synthetic, "setup_error": None,
        "notice": ("실제 임베딩·생성 모델로 합성 자료만 평가했습니다. 소규모 결과는 운영 품질을 보장하지 않습니다."
                   if live else "SQLite FTS5/BM25·Chroma 검색과 두 전략의 실행 경로를 측정합니다. "
                   "고정 문자 2-gram 해시 임베딩과 항목 유사도 기반 원문 발췌는 의미 임베딩이나 LLM이 아니므로 "
                   "실제 모델 품질·비용·지연의 측정값이 아닙니다."),
        "metric_definitions": {
            "hit_at_3": "실패하지 않은 유근거 질문 중 최종 검색 상위 3개에 기대 파일·줄·정답이 있는 비율",
            "citation_accuracy": "완료 답변 중 기대답을 포함하고 모든 인용이 기대 파일·줄과 답을 지지하는 답변 비율. "
                                 "유한한 표기 변형으로 채점하며 문장 전체의 의미나 부정 표현을 판별하지 않음",
            "insufficient_rate": "실패를 제외한 모든 질문 중 근거 부족 응답 비율",
            "unanswerable_abstention_rate": "실패하지 않은 무근거 질문 중 근거 부족 응답 비율",
        },
        "cases": [{"id": case.id, "kind": case.kind, "query": case.query,
                   "expected_evidence": ({"filename": case.evidence[0], "line": case.evidence[1]}
                                         if case.evidence else None),
                   "accepted_answers": case.answers} for case in CASES],
        "runs": rows, "metrics": {},
    }
    try:
        if live:
            os.environ["CHROMA_PATH"] = str(chroma)
            store, model = VectorStore(), GroundedChat()
        else:
            store, model = OfflineStore(chroma), ExtractiveModel()
        build_index(path, store)
    except Exception as exc:
        # 기존 DB로 우회하지 않고 설정 실패를 질문별 실패와 구분한다. 공급자 응답은 내보내지 않는다.
        report["setup_error"] = type(exc).__name__
        return report

    for strategy in STRATEGIES:
        for mode in MODES:
            batch = []
            for case in CASES:
                trace_this = trace_synthetic and mode == MODES[0] and case == CASES[0]
                # 환경에 전역 추적이 켜져 있어도 그래프의 원문·질문은 자동 전송하지 않는다.
                with tracing_context(enabled=False):
                    started_at = datetime.now(timezone.utc) if trace_this else None
                    result = await run_strategy(path, store, model, case.query, mode, strategy)
                trace_status = "not_requested"
                if started_at is not None:
                    from app.experiment_trace import record_synthetic_trace
                    try:
                        record_synthetic_trace(result, mode, started_at)
                        trace_status = "sent"
                    except Exception:
                        # 추적 실패는 전략 실패로 세지 않고 비교는 같은 데이터 끝까지 진행한다.
                        trace_status = "failed"
                row = {"case_id": case.id, "mode": mode, **result.model_dump(mode="json"),
                       "trace_status": trace_status,
                       "scores": score(case, result.status, result.answer, result.sources, result.retrieved)}
                batch.append(row)
            rows.extend(batch)
            report["metrics"].setdefault(mode, {})[strategy] = summarize(batch)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="새 임시 폴더를 보존하며 합성 한국어 문서·질문을 비교 평가합니다.")
    parser.add_argument("--live", action="store_true", help="실제 임베딩과 답변 모델 사용 (--allow-external 필요)")
    parser.add_argument("--allow-external", action="store_true",
                        help="합성 문서·질문·검색 발췌를 모델 API에 전송하며 비용이 발생할 수 있음에 동의")
    parser.add_argument("--trace-synthetic", action="store_true",
                        help="두 전략의 첫 합성 질문·모드 단계 메타데이터를 LangSmith로 전송하는 데 동의")
    args = parser.parse_args()
    if args.live != args.allow_external:
        parser.error("실제 모델 평가에는 --live와 --allow-external을 함께 지정해야 합니다.")
    if args.live and not os.environ.get("OPENAI_API_KEY", "").strip():
        parser.error("실제 모델 평가에는 OPENAI_API_KEY가 필요합니다.")
    if args.trace_synthetic and not os.environ.get("LANGSMITH_API_KEY", "").strip():
        parser.error("합성 단계 추적에는 LANGSMITH_API_KEY가 필요합니다.")
    report = asyncio.run(evaluate(args.live, args.trace_synthetic))
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return int(report["setup_error"] is not None or any(
        row["status"] == "failed" or row["trace_status"] == "failed" for row in report["runs"]
    ))


if __name__ == "__main__":
    sys.exit(main())
