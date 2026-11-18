import { type FormEvent, useEffect, useRef, useState } from 'react';
import { sourceLocation } from './sourceLocation';

const modes = {
  hybrid: '하이브리드 (RRF)',
  sparse: '희소 (BM25)',
  dense: '밀집 (벡터 유사도)',
};
type Mode = keyof typeof modes;
const statuses = {
  completed: '완료',
  insufficient: '근거 부족',
  failed: '실패',
};
type RunResult = {
  strategy: 'baseline' | 'agent';
  status: keyof typeof statuses;
  answer: string;
  steps: string[];
  stop_reason: string;
  sources: {
    filename: string;
    chunk_id: number;
    start_line: number;
    end_line: number;
    page_number: number | null;
    text: string;
  }[];
};
type Comparison = { baseline: RunResult; agent: RunResult };
type Attempt = { query: string; mode: Mode } & (
  | { status: 'loading' }
  | { status: 'done'; result: Comparison }
  | { status: 'failed'; message: string }
);

function Result({ run }: { run: RunResult }) {
  return (
    <article className="compare-run">
      <h3>{run.strategy === 'baseline' ? '기본 RAG' : 'Agent'}</h3>
      <p>상태: {statuses[run.status]}</p>
      <p>
        종료 사유: <code>{run.stop_reason}</code>
      </p>
      <p>실행 순서: {run.steps.join(' → ') || '실행된 단계 없음'}</p>
      <strong>답변</strong>
      <p className="chat-text">{run.answer || '반환된 답변이 없습니다.'}</p>
      <strong>근거 원문</strong>
      {run.sources.length === 0 ? (
        <p className="empty">이 실행에서 반환된 근거가 없습니다.</p>
      ) : (
        <ol className="chunks">
          {run.sources.map((source, index) => (
            <li key={source.chunk_id}>
              <details>
                <summary>
                  [{index + 1}] {source.filename} · {sourceLocation(source)} ·
                  원문 보기
                </summary>
                <pre>{source.text}</pre>
              </details>
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}

export function Compare() {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<Mode>('hybrid');
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  const pending = attempt?.status === 'loading';

  async function compare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const request = { query: query.trim(), mode };
    if (!request.query || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setAttempt({ ...request, status: 'loading' });
    try {
      const response = await fetch('/api/experiments/compare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result: Comparison = await response.json();
      if (!controller.signal.aborted)
        setAttempt({ ...request, status: 'done', result });
    } catch (error) {
      if (!controller.signal.aborted)
        setAttempt({
          ...request,
          status: 'failed',
          message: `비교 결과를 받지 못했습니다. 서버 연결을 확인해 주세요. ${error instanceof Error ? error.message : ''}`,
        });
    } finally {
      if (active.current === controller) active.current = null;
    }
  }

  return (
    <section aria-labelledby="compare-heading">
      <h2 id="compare-heading">기본 RAG와 Agent 비교</h2>
      <p>같은 질문과 검색 방식으로 두 전략을 각각 실행합니다.</p>
      <form onSubmit={compare}>
        <label htmlFor="compare-query">비교할 질문</label>
        <input
          id="compare-query"
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          disabled={pending}
          required
        />
        <label htmlFor="compare-mode">첫 검색 방식</label>
        <select
          id="compare-mode"
          value={mode}
          onChange={(event) => setMode(event.target.value as Mode)}
          disabled={pending}
        >
          {Object.entries(modes).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <p id="compare-limits">
          기본 RAG는 검색 1회, 답변 생성 최대 1회입니다. Agent는 검색과 생성을
          각각 최대 2회 수행합니다. 검색 결과가 없거나 모델이 근거 부족을
          명시하면
          {` ${modes[mode === 'hybrid' ? 'sparse' : 'hybrid']} 방식으로 한 번 더 검색할 수 있습니다.`}
        </p>
        <p id="compare-cost" className="cost-hint">
          밀집·하이브리드 검색은 질문을 OpenAI 임베딩 API로 보냅니다. 희소
          검색으로 시작해도 Agent가 하이브리드로 재검색할 수 있습니다. 답변 생성
          시 질문과 검색된 문서 원문이 모델 제공자에게 전송될 수 있으며 비용이
          발생할 수 있습니다. 외부 전송이 허용된 질문과 문서만 사용하세요.
        </p>
        <button
          type="submit"
          className="primary"
          aria-describedby="compare-limits compare-cost"
          disabled={!query.trim() || pending}
        >
          {pending ? '비교 중…' : '비교 실행'}
        </button>
      </form>
      {attempt && (
        <div className="search-output">
          <h3>
            “{attempt.query}” · {modes[attempt.mode]}
          </h3>
          {attempt.status === 'loading' && (
            <p role="status">두 전략을 실행 중입니다.</p>
          )}
          {attempt.status === 'failed' && <p role="alert">{attempt.message}</p>}
          {attempt.status === 'done' && (
            <div className="compare-results">
              <Result run={attempt.result.baseline} />
              <Result run={attempt.result.agent} />
            </div>
          )}
        </div>
      )}
    </section>
  );
}
