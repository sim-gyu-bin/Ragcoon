"""평가 CLI 전용: 원문이나 식별자 없이 실행 요약만 명시적으로 전송한다."""

import os
from contextlib import closing
from datetime import datetime, timezone
from uuid import UUID, uuid4

from langsmith import Client

from .agent import RunResult


_STEPS = {
    'search_sparse', 'search_dense', 'search_hybrid', 'assess_found', 'assess_empty',
    'generate', 'validate_completed', 'validate_insufficient', 'validate_failed', 'retry_search',
}
_REASONS = {
    'answered', 'no_results', 'model_insufficient', 'retry_limit', 'search_failed',
    'generation_failed', 'invalid_citation',
}


def record_synthetic_trace(result: RunResult, mode: str, started_at: datetime) -> UUID:
    if (mode not in {'sparse', 'dense', 'hybrid'} or result.strategy not in {'baseline', 'agent'}
            or result.status not in {'completed', 'insufficient', 'failed'}
            or result.stop_reason not in _REASONS or not _STEPS.issuperset(result.steps)):
        raise ValueError('허용하지 않은 추적 필드입니다.')
    key = os.environ.get('LANGSMITH_API_KEY')
    if not key:
        raise ValueError('합성 추적에는 서버의 LANGSMITH_API_KEY가 필요합니다.')
    ended_at = datetime.now(timezone.utc)
    run_id = uuid4()
    with closing(Client(api_url='https://api.smith.langchain.com', api_key=key,
                        auto_batch_tracing=False, omit_traced_runtime_info=True)) as client:
        client.create_run('synthetic-rag-comparison', {'mode': mode, 'strategy': result.strategy},
                          'chain', project_name='ragcoon-synthetic', id=run_id,
                          start_time=started_at, end_time=ended_at, outputs={
                              'steps': result.steps, 'status': result.status,
                              'stop_reason': result.stop_reason, 'citation_count': len(result.sources),
                          })
    return run_id
