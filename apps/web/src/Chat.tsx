import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';

const sessionsUrl = '/api/chat/sessions';
const selectionKey = 'ragcoon.chat.session';
const insufficient = '문서에서 답변 근거를 확인할 수 없습니다.';
const statusLabels = {
  streaming: '답변 수신 중',
  completed: '답변 완료',
  insufficient: '근거 없음',
  failed: '답변 실패',
};
type Status = keyof typeof statusLabels;
type Source = {
  document_id: number;
  chunk_id: number;
  filename: string;
  start_line: number;
  end_line: number;
  text: string;
};
type Message = {
  id: number;
  role: 'user' | 'assistant';
  status: Status;
  text: string;
  sources: Source[];
};
type Session = { id: number };
type End = {
  type: 'end';
  status: Exclude<Status, 'streaming'>;
  sources: Source[];
};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
function isSession(value: unknown): value is Session {
  return record(value) && Number.isSafeInteger(value.id);
}
function isSources(value: unknown): value is Source[] {
  return (
    Array.isArray(value) &&
    value.every(
      (s: unknown) =>
        record(s) &&
        Number.isSafeInteger(s.document_id) &&
        Number.isSafeInteger(s.chunk_id) &&
        Number.isSafeInteger(s.start_line) &&
        Number.isSafeInteger(s.end_line) &&
        typeof s.filename === 'string' &&
        typeof s.text === 'string',
    )
  );
}
function isFinalStatus(value: unknown): value is End['status'] {
  return (
    value === 'completed' || value === 'insufficient' || value === 'failed'
  );
}
function isMessage(value: unknown): value is Message {
  return (
    record(value) &&
    Number.isSafeInteger(value.id) &&
    (value.role === 'user' || value.role === 'assistant') &&
    typeof value.text === 'string' &&
    (value.status === 'streaming' || isFinalStatus(value.status)) &&
    isSources(value.sources)
  );
}
class HttpError extends Error {
  constructor(readonly status: number) {
    super();
  }
}
async function request(url: string, options: RequestInit) {
  const response = await fetch(url, { cache: 'no-store', ...options });
  if (!response.ok) throw new HttpError(response.status);
  return response;
}
function notice(message: string, error: unknown) {
  return error instanceof HttpError
    ? `${message} (HTTP ${error.status})`
    : message;
}

async function readAnswer(
  response: Response,
  onDelta: (text: string) => void,
): Promise<End> {
  const invalid = '답변 스트림의 형식이 올바르지 않습니다.';
  if (
    response.headers.get('content-type')?.split(';')[0].trim() !==
      'application/x-ndjson' ||
    !response.body
  )
    throw new Error(invalid);
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let buffer = '';
  let ended: End | undefined;
  function accept(line: string) {
    if (!line.trim()) return;
    const event: unknown = JSON.parse(line);
    if (ended || !record(event)) throw new Error(invalid);
    if (event.type === 'delta' && typeof event.text === 'string')
      onDelta(event.text);
    else if (
      event.type === 'end' &&
      isFinalStatus(event.status) &&
      isSources(event.sources)
    )
      ended = { type: 'end', status: event.status, sources: event.sources };
    else throw new Error(invalid);
  }
  try {
    while (true) {
      const { done, value } = await reader.read();
      // 한글 문자와 JSON 한 줄 모두 네트워크 조각 사이에서 나뉠 수 있다.
      buffer += decoder.decode(value, { stream: !done });
      let start = 0;
      while (true) {
        const newline = buffer.indexOf('\n', start);
        if (newline === -1) break;
        accept(buffer.slice(start, newline));
        start = newline + 1;
      }
      buffer = buffer.slice(start);
      if (done) break;
    }
    accept(buffer);
    if (!ended) throw new Error('답변 종료 이벤트가 없습니다.');
    return ended;
  } finally {
    reader.releaseLock();
  }
}

function Answer({ message }: { message: Message }) {
  const { text, sources, status, id } = message;
  const cited = new Set<number>();
  const sourceId = (n: number) => `chat-source-${id}-${n}`;
  let offset = 0;
  const answer =
    status !== 'completed'
      ? text
      : text.split(/(\[[1-9]\d*\])/g).map((part, i) => {
          offset += part.length;
          if (i % 2 === 0) return part;
          const n = Number(part.slice(1, -1));
          if (!sources[n - 1]) return part;
          cited.add(n);
          return (
            <a
              key={offset}
              href={`#${sourceId(n)}`}
              aria-label={`출처 ${n} 원문 보기`}
              onClick={() => {
                const detail = document.getElementById(sourceId(n));
                if (detail instanceof HTMLDetailsElement) detail.open = true;
              }}
            >
              {part}
            </a>
          );
        });
  return (
    <>
      <strong>답변 · {statusLabels[status]}</strong>
      <p className="chat-text">
        {status === 'insufficient' ? insufficient : answer}
      </p>
      {status === 'failed' && (
        <p>완료된 답변이 아닙니다. 일부만 표시될 수 있습니다.</p>
      )}
      {[...cited].map((n) => {
        const s = sources[n - 1];
        return (
          <details className="chat-source" key={n} id={sourceId(n)}>
            <summary>
              [{n}] {s.filename} · {s.start_line}–{s.end_line}줄
            </summary>
            <p>
              문서 #{s.document_id} · 청크 #{s.chunk_id}
            </p>
            <pre>{s.text}</pre>
          </details>
        );
      })}
    </>
  );
}

function Conversation({ id }: { id: number }) {
  const url = `${sessionsUrl}/${id}/messages`;
  const [history, setHistory] = useState<Message[] | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState('hybrid');
  const [draft, setDraft] = useState<{ question: string; text: string } | null>(
    null,
  );
  const sending = useRef<AbortController | null>(null);
  const nextId = useRef(-1);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await request(url, { signal: controller.signal });
        const messages: unknown = await response.json();
        if (!Array.isArray(messages) || !messages.every(isMessage))
          throw new Error();
        if (!controller.signal.aborted) setHistory(messages);
      } catch (e) {
        if (!controller.signal.aborted)
          setError(
            notice('대화 기록을 불러오지 못했습니다. 새로고침해 주세요.', e),
          );
      }
    }
    void load();
    return () => {
      controller.abort();
      sending.current?.abort();
    };
  }, [url]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = query.trim();
    if (!question || history === null || sending.current) return;
    const controller = new AbortController();
    sending.current = controller;
    setError('');
    setDraft({ question, text: '' });
    let text = '';
    let status: End['status'] = 'failed';
    let sources: Source[] = [];
    try {
      const response = await request(url, {
        method: 'POST',
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/x-ndjson',
        },
        body: JSON.stringify({ query: question, mode }),
      });
      const end = await readAnswer(response, (delta) => {
        text += delta;
        if (!controller.signal.aborted) setDraft({ question, text });
      });
      status = end.status;
      sources = end.sources;
    } catch (e) {
      if (!controller.signal.aborted)
        setError(
          notice(
            '답변을 끝까지 받지 못했습니다. 세션 기록은 새로고침하여 확인하세요.',
            e,
          ),
        );
    } finally {
      if (!controller.signal.aborted) {
        const user: Message = {
          id: nextId.current--,
          role: 'user',
          status: 'completed',
          text: question,
          sources: [],
        };
        const assistant: Message = {
          id: nextId.current--,
          role: 'assistant',
          status,
          text,
          sources,
        };
        setHistory((previous) => [...(previous ?? []), user, assistant]);
        setDraft(null);
      }
      controller.abort();
      sending.current = null;
    }
  }
  return (
    <div className="chat-conversation">
      <h3>세션 #{id} 대화</h3>
      {history === null && !error && (
        <p role="status">대화 기록을 불러오는 중…</p>
      )}
      {history?.length === 0 && !draft && (
        <p className="empty">아직 질문이 없습니다.</p>
      )}
      <ol className="chat-messages" aria-label="대화 기록">
        {history?.map((m) => (
          <li key={m.id}>
            {m.role === 'user' ? (
              <>
                <strong>질문</strong>
                <p className="chat-text">{m.text}</p>
              </>
            ) : (
              <Answer message={m} />
            )}
          </li>
        ))}
        {draft && (
          <>
            <li>
              <strong>질문</strong>
              <p className="chat-text">{draft.question}</p>
            </li>
            <li>
              <strong role="status">답변 · {statusLabels.streaming}</strong>
              <p className="chat-text">{draft.text}</p>
            </li>
          </>
        )}
      </ol>
      {error && <p role="alert">{error}</p>}
      <form onSubmit={send}>
        <label htmlFor="chat-query">채팅 질문</label>
        <input
          id="chat-query"
          type="text"
          value={query}
          required
          onChange={(e) => setQuery(e.target.value)}
        />
        <label htmlFor="chat-mode">채팅 검색 방식</label>
        <select
          id="chat-mode"
          value={mode}
          onChange={(e) => setMode(e.target.value)}
        >
          <option value="hybrid">하이브리드 (RRF)</option>
          <option value="sparse">희소 (BM25)</option>
          <option value="dense">밀집 (벡터 유사도)</option>
        </select>
        <p className="cost-hint" id="chat-cost">
          {mode === 'sparse'
            ? '희소 검색 자체에는 임베딩 비용이 없습니다. '
            : '이 검색 방식은 질문을 OpenAI에 보내므로 임베딩 비용이 발생할 수 있습니다. '}
          근거 구간을 찾으면 질문과 해당 원문을 OpenAI 답변 모델에 보내므로 답변
          생성 비용이 발생할 수 있습니다. 검색 결과가 없으면 답변 모델을
          호출하지 않습니다. 외부 전송이 허용된 내용만 사용하세요.
        </p>
        <button
          className="primary"
          type="submit"
          disabled={history === null || !query.trim() || !!draft}
          aria-describedby="chat-cost"
        >
          {draft ? '답변 수신 중…' : '질문 보내기'}
        </button>
      </form>
    </div>
  );
}

export function Chat() {
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');
  const creation = useRef<AbortController | null>(null);
  const select = useCallback((id: number | null) => {
    setSelected(id);
    try {
      if (id === null) localStorage.removeItem(selectionKey);
      else localStorage.setItem(selectionKey, String(id));
    } catch {
      /* 저장소를 사용할 수 없어도 현재 화면에서는 세션을 선택한다. */
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await request(sessionsUrl, {
          signal: controller.signal,
        });
        const list: unknown = await response.json();
        if (!Array.isArray(list) || !list.every(isSession)) throw new Error();
        if (controller.signal.aborted) return;
        let saved: string | null = null;
        try {
          saved = localStorage.getItem(selectionKey);
        } catch {
          /* 기본 세션을 사용한다. */
        }
        setSessions(list);
        select(
          list.find((s) => String(s.id) === saved)?.id ?? list[0]?.id ?? null,
        );
      } catch (e) {
        if (!controller.signal.aborted)
          setError(
            notice('세션 목록을 불러오지 못했습니다. 새로고침해 주세요.', e),
          );
      }
    }
    void load();
    return () => {
      controller.abort();
      creation.current?.abort();
    };
  }, [select]);
  async function create() {
    if (creation.current || sessions === null) return;
    const controller = new AbortController();
    creation.current = controller;
    setCreating(true);
    setError('');
    try {
      const response = await request(sessionsUrl, {
        method: 'POST',
        signal: controller.signal,
      });
      const session: unknown = await response.json();
      if (!isSession(session)) throw new Error();
      if (!controller.signal.aborted) {
        setSessions((previous) => [session, ...(previous ?? [])]);
        select(session.id);
      }
    } catch (e) {
      if (!controller.signal.aborted)
        setError(notice('새 세션을 만들지 못했습니다.', e));
    } finally {
      if (!controller.signal.aborted) setCreating(false);
      creation.current = null;
    }
  }
  return (
    <section aria-labelledby="chat-heading">
      <h2 id="chat-heading">문서 근거 채팅</h2>
      <p>
        세션에 질문과 답변을 보관합니다. 각 답변은 이번 질문으로 찾은 구간만
        사용합니다.
      </p>
      <div className="chat-sessions">
        <label htmlFor="chat-session">대화 세션</label>
        <select
          id="chat-session"
          value={selected ?? ''}
          disabled={!sessions?.length || creating}
          onChange={(e) => select(Number(e.target.value))}
        >
          {selected === null && <option value="">선택된 세션 없음</option>}
          {sessions?.map((s) => (
            <option key={s.id} value={s.id}>
              세션 #{s.id}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={sessions === null || creating}
          onClick={create}
        >
          {creating ? '생성 중…' : '새 세션 만들기'}
        </button>
      </div>
      {sessions === null && !error && (
        <p role="status">세션 목록을 불러오는 중…</p>
      )}
      {sessions?.length === 0 && (
        <p className="empty">새 세션을 만들어 질문을 시작하세요.</p>
      )}
      {error && <p role="alert">{error}</p>}
      {selected !== null && <Conversation key={selected} id={selected} />}
    </section>
  );
}
