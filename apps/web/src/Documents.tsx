import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { sourceLocation } from './sourceLocation';

type DocumentEntry = {
  id: number;
  filename: string;
  status: 'registered' | 'indexing' | 'failed' | 'ready' | 'delete_pending';
  chunk_count: number;
};

type Chunk = {
  id: number;
  page_number: number | null;
  start_line: number;
  end_line: number;
  text: string;
};

const statusLabels: Record<DocumentEntry['status'], string> = {
  registered: '등록됨 · 색인 전',
  indexing: '색인 중',
  failed: '색인 실패 · 검색 불가',
  ready: '색인 완료 · 검색 가능',
  delete_pending: '삭제 미완료 · 재시도 필요',
};

function Chunks({ documentId }: { documentId: number }) {
  const [chunks, setChunks] = useState<Chunk[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (error) return;
    // 구간을 접거나 다시 요청하면 이전 응답을 화면에 반영하지 않는다.
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(`/api/documents/${documentId}/chunks`, {
          signal: controller.signal,
          cache: 'no-store',
        });
        if (!response.ok) {
          if (!controller.signal.aborted)
            setError(`구간을 불러오지 못했습니다. (HTTP ${response.status})`);
          return;
        }
        const rows: Chunk[] = await response.json();
        if (!controller.signal.aborted) setChunks(rows);
      } catch {
        if (!controller.signal.aborted)
          setError('구간을 불러오지 못했습니다. 서버 연결을 확인해 주세요.');
      }
    }
    void load();
    return () => controller.abort();
  }, [documentId, error]);

  if (error)
    return (
      <div className="feedback">
        <p role="alert">{error}</p>
        <button type="button" onClick={() => setError('')}>
          구간 다시 불러오기
        </button>
      </div>
    );
  if (!chunks) return <p role="status">구간을 불러오는 중입니다.</p>;
  if (!chunks.length) return <p>이 문서에는 표시할 구간이 없습니다.</p>;
  return (
    <ol className="chunks">
      {chunks.map((chunk) => (
        <li key={chunk.id}>
          <details>
            <summary>
              청크 #{chunk.id} · {sourceLocation(chunk)} · 원문
            </summary>
            <pre>{chunk.text}</pre>
          </details>
        </li>
      ))}
    </ol>
  );
}

function DocumentRow({
  entry,
  onIndexed,
  onDeleted,
  reload,
}: {
  entry: DocumentEntry;
  onIndexed: (id: number) => void;
  onDeleted: (entry: DocumentEntry) => void;
  reload: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const [indexError, setIndexError] = useState('');
  const [confirmation, setConfirmation] = useState<
    'closed' | 'open' | 'deleting'
  >('closed');
  const [deleteError, setDeleteError] = useState('');
  const status =
    indexing && entry.status !== 'ready' ? 'indexing' : entry.status;

  async function indexDocument() {
    if (
      confirmation !== 'closed' ||
      (status !== 'registered' && status !== 'failed')
    )
      return;
    setIndexing(true);
    setIndexError('');
    try {
      const response = await fetch(`/api/documents/${entry.id}/index`, {
        method: 'POST',
      });
      if (response.ok && (await response.json()).status === 'ready') {
        onIndexed(entry.id);
        return;
      }
      setIndexError(
        response.status === 409
          ? '다른 요청이 먼저 처리됐습니다. 문서 상태를 확인해 주세요.'
          : `색인을 완료하지 못했습니다. (HTTP ${response.status})`,
      );
    } catch {
      setIndexError(
        '색인 결과를 확인하지 못했습니다. 서버 연결을 확인해 주세요.',
      );
    } finally {
      await reload();
      setIndexing(false);
    }
  }

  async function deleteDocument() {
    if (confirmation !== 'open' || status === 'indexing') return;
    setConfirmation('deleting');
    setDeleteError('');
    try {
      const response = await fetch(`/api/documents/${entry.id}`, {
        method: 'DELETE',
      });
      if (response.status === 204) {
        onDeleted(entry);
      } else {
        setDeleteError(
          response.status === 409
            ? '색인 중에는 삭제할 수 없습니다. 색인이 끝난 뒤 다시 시도해 주세요.'
            : `삭제를 완료하지 못했습니다. 목록의 상태를 확인하고 다시 시도해 주세요. (HTTP ${response.status})`,
        );
      }
    } catch {
      setDeleteError(
        '삭제 결과를 확인하지 못했습니다. 연결과 목록의 상태를 확인한 뒤 다시 시도해 주세요.',
      );
    } finally {
      // 양쪽 색인 중 하나만 정리됐을 수도 있으므로 오류일 때도 서버 상태를 다시 읽는다.
      await reload();
      setConfirmation('closed');
    }
  }

  return (
    <li className="document-row">
      <div className="row-heading">
        <h3>{entry.filename}</h3>
        <span className="badge" role="status">
          {statusLabels[status]}
        </span>
      </div>
      <p>
        문서 #{entry.id} · 구간 {entry.chunk_count}개
      </p>
      <div className="document-actions">
        {(status === 'registered' || status === 'failed') && (
          <button
            type="button"
            aria-describedby="index-hint"
            disabled={confirmation !== 'closed'}
            onClick={() => void indexDocument()}
          >
            {status === 'failed' || indexError ? '색인 다시 시도' : '색인 시작'}
          </button>
        )}
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {open ? '구간 접기' : '구간 펼쳐보기'}
        </button>
        {confirmation === 'closed' && (
          <button
            type="button"
            disabled={status === 'indexing'}
            onClick={() => {
              setDeleteError('');
              setConfirmation('open');
            }}
          >
            {status === 'delete_pending' ? '삭제 재시도' : '문서 삭제'}
          </button>
        )}
      </div>
      {confirmation !== 'closed' && (
        <fieldset className="delete-confirmation">
          <legend>
            {entry.filename} (문서 #{entry.id}) 삭제 확인
          </legend>
          <p>
            저장된 원문과 구간, 문서 정보, 희소(BM25)·밀집(벡터) 색인을
            삭제합니다. 삭제한 문서는 새 검색이나 답변에 쓰이지 않으며 되돌릴 수
            없습니다.
          </p>
          <p>
            과거 대화와 답변은 삭제되지 않습니다. 당시 저장한 출처의 파일명,
            위치와 인용 원문도 그대로 남습니다.
          </p>
          <div className="document-actions">
            <button
              type="button"
              disabled={confirmation === 'deleting'}
              onClick={() => setConfirmation('closed')}
            >
              취소
            </button>
            <button
              type="button"
              className="danger"
              disabled={confirmation === 'deleting' || status === 'indexing'}
              onClick={() => void deleteDocument()}
            >
              {confirmation === 'deleting'
                ? '삭제 중…'
                : '이 문서 영구 삭제 승인'}
            </button>
          </div>
        </fieldset>
      )}
      {deleteError && <p role="alert">{deleteError}</p>}
      {indexError && status !== 'ready' && <p role="alert">{indexError}</p>}
      {open && <Chunks documentId={entry.id} />}
    </li>
  );
}

export function Documents() {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [notice, setNotice] = useState('');
  const [entries, setEntries] = useState<DocumentEntry[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [deleteNotice, setDeleteNotice] = useState('');
  const listRequest = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    // 수동 새로고침과 등록 직후 조회가 겹쳐도 가장 최근 목록을 유지한다.
    listRequest.current?.abort();
    const controller = new AbortController();
    listRequest.current = controller;
    setLoading(true);
    setListError('');
    try {
      const response = await fetch('/api/documents', {
        signal: controller.signal,
        cache: 'no-store',
      });
      if (!response.ok) {
        if (!controller.signal.aborted)
          setListError(`목록을 불러오지 못했습니다. (HTTP ${response.status})`);
        return;
      }
      const rows: DocumentEntry[] = await response.json();
      if (!controller.signal.aborted) setEntries(rows);
    } catch {
      if (!controller.signal.aborted)
        setListError('목록을 불러오지 못했습니다. 서버 연결을 확인해 주세요.');
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    return () => listRequest.current?.abort();
  }, [reload]);

  function markReady(id: number) {
    setEntries((previous) =>
      previous
        ? previous.map<DocumentEntry>((row) =>
            row.id === id ? { ...row, status: 'ready' } : row,
          )
        : null,
    );
  }

  function markDeleted(entry: DocumentEntry) {
    setEntries(
      (previous) => previous?.filter((row) => row.id !== entry.id) ?? null,
    );
    setDeleteNotice(`${entry.filename} (문서 #${entry.id})을 삭제했습니다.`);
  }

  async function register(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || uploading) return;
    const form = event.currentTarget;
    setUploadError('');
    setNotice('');
    const isPdf = /\.pdf$/i.test(file.name);
    if (!isPdf && !/\.(md|markdown)$/i.test(file.name)) {
      setUploadError('.md, .markdown 또는 .pdf 파일을 선택해 주세요.');
      return;
    }
    if (file.size > (isPdf ? 5 : 1) * 1024 * 1024) {
      setUploadError(
        isPdf
          ? 'PDF 파일은 5 MiB 이하여야 합니다.'
          : 'Markdown 파일은 1 MiB 이하여야 합니다.',
      );
      return;
    }
    setUploading(true);
    try {
      // 파일을 문자열로 변환하거나 폼으로 감싸지 않고 원본 바이트를 보낸다.
      const query = new URLSearchParams({ filename: file.name });
      const response = await fetch(`/api/documents?${query}`, {
        method: 'POST',
        headers: {
          'Content-Type': isPdf
            ? 'application/pdf'
            : 'text/markdown; charset=utf-8',
        },
        body: file,
      });
      if (!response.ok) {
        // 페이지 수와 텍스트 추출 가능 여부는 파일을 읽은 서버가 판별한다.
        const error = await response.json().catch(() => null);
        setUploadError(
          typeof error?.detail === 'string'
            ? error.detail
            : `문서를 등록하지 못했습니다. (HTTP ${response.status})`,
        );
        return;
      }
      const entry: DocumentEntry = await response.json();
      setEntries((previous) => [
        entry,
        ...(previous ?? []).filter((row) => row.id !== entry.id),
      ]);
      setNotice(
        `${entry.filename} 파일을 문서 #${entry.id}(으)로 등록했습니다. 검색하려면 목록에서 색인을 시작하세요.`,
      );
      form.reset();
      setFile(null);
      void reload();
    } catch {
      setUploadError('문서를 등록하지 못했습니다. 서버 연결을 확인해 주세요.');
    } finally {
      setUploading(false);
    }
  }

  return (
    <>
      <section aria-labelledby="upload-heading">
        <h2 id="upload-heading">문서 등록</h2>
        <form onSubmit={register}>
          <label htmlFor="document-file">Markdown 또는 PDF 파일</label>
          <p id="file-hint">
            Markdown: UTF-8 .md 또는 .markdown, 최대 1 MiB. PDF: .pdf, 최대 5
            MiB·100페이지. PDF는 파일 자체를 보관하지 않고 추출한 텍스트와 문서
            정보를 저장합니다. OCR을 지원하지 않아 글자가 이미지로만 들어 있는
            PDF는 등록할 수 없습니다.
          </p>
          <div className="file-actions">
            <input
              id="document-file"
              type="file"
              accept=".md,.markdown,.pdf"
              disabled={uploading}
              aria-describedby="file-hint"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setUploadError('');
                setNotice('');
              }}
            />
            <button
              className="primary"
              type="submit"
              disabled={!file || uploading}
            >
              {uploading ? '등록 중…' : '파일 등록'}
            </button>
          </div>
        </form>
        {uploadError && <p role="alert">{uploadError}</p>}
        {notice && <p role="status">{notice}</p>}
      </section>
      <section aria-labelledby="list-heading">
        <div className="row-heading">
          <h2 id="list-heading">등록된 문서</h2>
          <button type="button" onClick={() => void reload()}>
            목록 새로고침
          </button>
        </div>
        <p>
          최근 등록한 문서부터 표시합니다. 같은 이름의 파일도 문서 번호로
          구분합니다.
        </p>
        <p id="index-hint" className="cost-hint">
          등록만 해서는 OpenAI에 문서를 보내지 않습니다. 색인을 시작하거나
          재시도하면 서버가 문서 구간의 원문을 OpenAI 임베딩 API로 보내며 비용이
          발생할 수 있습니다. 외부 전송이 허용된 문서만 색인하세요.
        </p>
        {loading && <p role="status">목록을 불러오는 중입니다.</p>}
        {deleteNotice && <p role="status">{deleteNotice}</p>}
        {listError && <p role="alert">{listError}</p>}
        {!loading && !listError && entries?.length === 0 && (
          <p className="empty">아직 등록된 문서가 없습니다.</p>
        )}
        {!!entries?.length && (
          <ul className="documents">
            {entries.map((entry) => (
              <DocumentRow
                key={entry.id}
                entry={entry}
                onIndexed={markReady}
                onDeleted={markDeleted}
                reload={reload}
              />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
