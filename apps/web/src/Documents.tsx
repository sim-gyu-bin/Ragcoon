import { useEffect, useRef, useState, type FormEvent } from 'react';

type DocumentEntry = {
  id: number;
  filename: string;
  status: 'registered';
  chunk_count: number;
};

type Chunk = { id: number; start_line: number; end_line: number; text: string };

async function errorDetail(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => null);
  return typeof body?.detail === 'string' ? body.detail : `${fallback} (HTTP ${response.status})`;
}

function Chunks({ documentId }: { documentId: number }) {
  const [chunks, setChunks] = useState<Chunk[] | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    // 구간을 접거나 다시 요청하면 이전 응답을 화면에 반영하지 않는다.
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(`/api/documents/${documentId}/chunks`, { signal: controller.signal, cache: 'no-store' });
        if (!response.ok) throw new Error(await errorDetail(response, '구간을 불러오지 못했습니다.'));
        const rows: Chunk[] = await response.json();
        if (!controller.signal.aborted) setChunks(rows);
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error && !(cause instanceof TypeError) ? cause.message : '구간을 불러오지 못했습니다. 서버 연결을 확인해 주세요.');
      }
    }
    void load();
    return () => controller.abort();
  }, [documentId, attempt]);

  if (error) return <div className="feedback">
    <p role="alert">{error}</p>
    <button type="button" onClick={() => { setError(''); setAttempt(n => n + 1); }}>구간 다시 불러오기</button>
  </div>;
  if (!chunks) return <p role="status">구간을 불러오는 중입니다.</p>;
  if (!chunks.length) return <p>이 문서에는 표시할 구간이 없습니다.</p>;
  return <ol className="chunks">{chunks.map(chunk => <li key={chunk.id}>
    <details>
      <summary>청크 #{chunk.id} · {chunk.start_line}–{chunk.end_line}줄 · 원문</summary>
      <pre>{chunk.text}</pre>
    </details>
  </li>)}</ol>;
}

function DocumentRow({ entry }: { entry: DocumentEntry }) {
  const [open, setOpen] = useState(false);
  return <li className="document-row">
    <div className="row-heading">
      <h3>{entry.filename}</h3>
      <span className="badge">등록됨 · 검색 준비 아님</span>
    </div>
    <p>문서 #{entry.id} · 구간 {entry.chunk_count}개</p>
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
      {open ? '구간 접기' : '구간 펼쳐보기'}
    </button>
    {open && <Chunks documentId={entry.id} />}
  </li>;
}

export function Documents() {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [notice, setNotice] = useState('');
  const [entries, setEntries] = useState<DocumentEntry[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const listRequest = useRef<AbortController | null>(null);

  async function reload() {
    // 수동 새로고침과 등록 직후 조회가 겹쳐도 가장 최근 목록을 유지한다.
    listRequest.current?.abort();
    const controller = new AbortController();
    listRequest.current = controller;
    setLoading(true);
    setListError('');
    try {
      const response = await fetch('/api/documents', { signal: controller.signal, cache: 'no-store' });
      if (!response.ok) throw new Error(await errorDetail(response, '목록을 불러오지 못했습니다.'));
      const rows: DocumentEntry[] = await response.json();
      if (!controller.signal.aborted) setEntries(rows);
    } catch (cause) {
      if (!controller.signal.aborted) setListError(cause instanceof Error && !(cause instanceof TypeError) ? cause.message : '목록을 불러오지 못했습니다. 서버 연결을 확인해 주세요.');
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
    return () => listRequest.current?.abort();
  }, []);

  async function register(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || uploading) return;
    const form = event.currentTarget;
    setUploadError('');
    setNotice('');
    if (!/\.(md|markdown)$/i.test(file.name) || file.size > 1024 * 1024) {
      setUploadError('1 MiB 이하의 .md 또는 .markdown 파일을 선택해 주세요.');
      return;
    }
    setUploading(true);
    try {
      // 파일을 문자열로 변환하거나 폼으로 감싸지 않고 원본 바이트를 보낸다.
      const query = new URLSearchParams({ filename: file.name });
      const response = await fetch(`/api/documents?${query}`, {
        method: 'POST', headers: { 'Content-Type': 'text/markdown; charset=utf-8' }, body: file,
      });
      if (!response.ok) throw new Error(await errorDetail(response, '문서를 등록하지 못했습니다.'));
      const entry: DocumentEntry = await response.json();
      setEntries(previous => [entry, ...(previous ?? []).filter(row => row.id !== entry.id)]);
      setNotice(`${entry.filename} 파일을 문서 #${entry.id}(으)로 등록했습니다.`);
      form.reset();
      setFile(null);
      void reload();
    } catch (cause) {
      setUploadError(cause instanceof Error && !(cause instanceof TypeError) ? cause.message : '문서를 등록하지 못했습니다. 서버 연결을 확인해 주세요.');
    } finally {
      setUploading(false);
    }
  }

  return <>
    <section aria-labelledby="upload-heading">
      <h2 id="upload-heading">문서 등록</h2>
      <form onSubmit={register}>
        <label htmlFor="markdown-file">마크다운 파일</label>
        <p id="file-hint">UTF-8 · .md 또는 .markdown · 최대 1 MiB</p>
        <div className="file-actions">
          <input id="markdown-file" type="file" accept=".md,.markdown" disabled={uploading} aria-describedby="file-hint"
            onChange={event => { setFile(event.target.files?.[0] ?? null); setUploadError(''); setNotice(''); }} />
          <button className="primary" type="submit" disabled={!file || uploading}>{uploading ? '등록 중…' : '파일 등록'}</button>
        </div>
      </form>
      {uploadError && <p role="alert">{uploadError}</p>}
      {notice && <p role="status">{notice}</p>}
    </section>
    <section aria-labelledby="list-heading">
      <div className="row-heading">
        <h2 id="list-heading">등록된 문서</h2>
        <button type="button" onClick={() => void reload()}>목록 새로고침</button>
      </div>
      <p>최근 등록한 문서부터 표시합니다. 같은 이름의 파일도 문서 번호로 구분합니다.</p>
      {loading && <p role="status">목록을 불러오는 중입니다.</p>}
      {listError && <p role="alert">{listError}</p>}
      {!loading && !listError && entries?.length === 0 && <p className="empty">아직 등록된 문서가 없습니다.</p>}
      {!!entries?.length && <ul className="documents">{entries.map(entry => <DocumentRow key={entry.id} entry={entry} />)}</ul>}
    </section>
  </>;
}
