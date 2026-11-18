import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

function ConnectionStatus() {
  const [status, setStatus] = useState('연결 확인 중');

  useEffect(() => {
    const controller = new AbortController();
    async function checkHealth() {
      try {
        // 컨테이너 내부 주소 대신 화면을 제공한 Caddy를 통해 요청한다.
        const response = await fetch('/api/health', { signal: controller.signal, cache: 'no-store' });
        const healthy = response.ok && (await response.json()).status === 'ok';
        setStatus(healthy ? '연결 정상' : '연결 실패');
      } catch {
        if (!controller.signal.aborted) setStatus('연결 실패');
      }
    }
    void checkHealth();
    return () => controller.abort();
  }, []);

  return <section>
    <p>RAGcoon / 로컬 개발</p>
    <h1>백엔드 연결 상태</h1>
    <strong role="status">{status}</strong>
    <p>페이지를 새로고침하면 서버 연결을 다시 확인합니다.</p>
  </section>;
}

createRoot(document.getElementById('root')!).render(<ConnectionStatus />);
