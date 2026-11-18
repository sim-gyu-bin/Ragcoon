import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Documents } from './Documents';
import './style.css';

function ConnectionStatus() {
  const [status, setStatus] = useState('연결 확인 중');

  useEffect(() => {
    const controller = new AbortController();
    async function checkHealth() {
      try {
        // 컨테이너 내부 주소 대신 화면을 제공한 Caddy를 통해 요청한다.
        const response = await fetch('/api/health', {
          signal: controller.signal,
          cache: 'no-store',
        });
        const healthy = response.ok && (await response.json()).status === 'ok';
        setStatus(healthy ? '연결 정상' : '연결 실패');
      } catch {
        if (!controller.signal.aborted) setStatus('연결 실패');
      }
    }
    void checkHealth();
    return () => controller.abort();
  }, []);

  return (
    <section className="connection" aria-labelledby="connection-heading">
      <h2 id="connection-heading">백엔드 연결 상태</h2>
      <strong role="status">{status}</strong>
      <p>페이지를 새로고침하면 서버 연결을 다시 확인합니다.</p>
    </section>
  );
}

const root = document.getElementById('root');
if (!root) throw new Error('앱을 표시할 루트 요소가 없습니다.');
createRoot(root).render(
  <>
    <header>
      <p className="eyebrow">RAGcoon / 로컬 개발</p>
      <h1>문서 보관함</h1>
      <p>마크다운 문서를 등록하고 줄 단위로 나뉜 원문을 확인하세요.</p>
    </header>
    <Documents />
    <ConnectionStatus />
  </>,
);
