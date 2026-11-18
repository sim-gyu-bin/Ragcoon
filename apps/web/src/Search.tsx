import { type FormEvent, useEffect, useRef, useState } from 'react';

const modeLabels = {
  hybrid: '하이브리드 (RRF)',
  sparse: '희소 (BM25)',
  dense: '밀집 (벡터 유사도)',
};
type Mode = keyof typeof modeLabels;

type Hit = {
  chunk_id: number;
  document_id: number;
  filename: string;
  start_line: number;
  end_line: number;
  text: string;
};

type SearchResponse = { mode: Mode; results: Hit[] };
type Attempt = { query: string; mode: Mode } & (
  | { status: 'loading' }
  | { status: 'failed'; message: string }
  | { status: 'done'; results: Hit[] }
);

export function Search() {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<Mode>('hybrid');
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  const trimmed = query.trim();
  const pending =
    attempt?.status === 'loading' &&
    attempt.query === trimmed &&
    attempt.mode === mode;

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!trimmed || pending) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const request = { query: trimmed, mode };
    setAttempt({ ...request, status: 'loading' });
    try {
      const response = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: controller.signal,
      });
      if (!response.ok) {
        if (!controller.signal.aborted)
          setAttempt({
            ...request,
            status: 'failed',
            message: `검색하지 못했습니다. (HTTP ${response.status})`,
          });
        return;
      }
      const result: SearchResponse = await response.json();
      if (!controller.signal.aborted)
        setAttempt({ query: trimmed, status: 'done', ...result });
    } catch {
      if (!controller.signal.aborted)
        setAttempt({
          ...request,
          status: 'failed',
          message: '검색 결과를 받지 못했습니다. 서버 연결을 확인해 주세요.',
        });
    }
  }

  return (
    <section aria-labelledby="search-heading">
      <h2 id="search-heading">문서 검색</h2>
      <p>
        색인이 끝난 문서에서 관련 구간을 최대 3개 찾습니다. 같은 질문을 두고
        검색 방식만 바꿔 원문과 순위를 비교해 보세요.
      </p>
      <form onSubmit={search}>
        <label htmlFor="search-query">질문</label>
        <input
          id="search-query"
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          required
        />
        <label htmlFor="search-mode">검색 방식</label>
        <select
          id="search-mode"
          value={mode}
          onChange={(event) => setMode(event.target.value as Mode)}
        >
          <option value="hybrid">{modeLabels.hybrid}</option>
          <option value="sparse">{modeLabels.sparse}</option>
          <option value="dense">{modeLabels.dense}</option>
        </select>
        <p id="search-cost" className="cost-hint">
          {mode === 'sparse'
            ? '희소 검색은 서버에서 단어를 찾습니다. 질문을 OpenAI에 보내지 않으며 임베딩 비용이 없습니다.'
            : '밀집·하이브리드 검색은 실행할 때마다 질문을 OpenAI 임베딩 API로 보내며 비용이 발생할 수 있습니다. 외부 전송이 허용된 질문만 입력하세요.'}
        </p>
        <button
          type="submit"
          className="primary"
          aria-describedby="search-cost"
          disabled={!trimmed || pending}
        >
          {pending ? '검색 중…' : '검색 실행'}
        </button>
      </form>
      {attempt && (
        <div className="search-output">
          <h3>
            {modeLabels[attempt.mode]} · “{attempt.query}”
          </h3>
          {attempt.status === 'loading' && <p role="status">검색 중입니다.</p>}
          {attempt.status === 'failed' && (
            <p role="alert">
              {attempt.message} 같은 질문으로 다시 시도할 수 있습니다.
            </p>
          )}
          {attempt.status === 'done' && attempt.results.length === 0 && (
            <p className="empty" role="status">
              찾은 구간이 없습니다. 문서의 색인 상태를 확인하거나 질문 또는 검색
              방식을 바꿔 보세요.
            </p>
          )}
          {attempt.status === 'done' && attempt.results.length > 0 && (
            <>
              <p role="status">검색된 구간 {attempt.results.length}개</p>
              <ol className="search-results">
                {attempt.results.map((hit, index) => (
                  <li key={hit.chunk_id}>
                    <strong>
                      {index + 1}위 · {hit.filename}
                    </strong>
                    <p>
                      문서 #{hit.document_id} · 청크 #{hit.chunk_id} ·{' '}
                      {hit.start_line}–{hit.end_line}줄
                    </p>
                    <pre>{hit.text}</pre>
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
      )}
    </section>
  );
}
