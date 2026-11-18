import io
import json
import signal
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryFile
from threading import BoundedSemaphore

MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_PAGES = 100
MAX_TEXT_BYTES = 2 * 1024 * 1024
# JSON에서 제어 문자 한 바이트는 최대 여섯 바이트(\u0000)가 된다.
MAX_OUTPUT_BYTES = MAX_TEXT_BYTES * 6 + 3 * MAX_PAGES + 2
MEMORY_BYTES = 256 * 1024 * 1024
CPU_SECONDS = 5
TIMEOUT_SECONDS = 10
_WORKER_SLOT = BoundedSemaphore(1)

_INVALID, _ENCRYPTED, _PAGES, _LIMIT, _UNAVAILABLE = range(10, 15)


def _worker() -> int:
    # API의 스레드에서 preexec_fn을 실행하지 않는다. 새 인터프리터에서 파서 import 전부터 제한한다.
    if sys.platform != "linux":
        return _UNAVAILABLE
    try:
        import resource

        for kind, ceiling in (
            (resource.RLIMIT_CORE, 0),
            (resource.RLIMIT_AS, MEMORY_BYTES),
            (resource.RLIMIT_CPU, CPU_SECONDS),
            (resource.RLIMIT_FSIZE, MAX_OUTPUT_BYTES),
        ):
            soft, _ = resource.getrlimit(kind)
            limit = ceiling if soft == resource.RLIM_INFINITY else min(ceiling, soft)
            resource.setrlimit(kind, (limit, limit))
        from pypdf import PdfReader
    except (ImportError, ValueError, OSError):
        return _UNAVAILABLE

    try:
        payload = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
        if len(payload) > MAX_PDF_BYTES:
            return _LIMIT
        reader = PdfReader(io.BytesIO(payload), strict=True)
        if reader.is_encrypted:
            return _ENCRYPTED
        if len(reader.pages) > MAX_PAGES:
            return _PAGES
        pages = []
        total = 0
        for page in reader.pages:
            text = page.extract_text()
            if "\x00" in text:
                return _INVALID
            total += len(text.encode("utf-8"))
            if total > MAX_TEXT_BYTES:
                return _LIMIT
            pages.append(text)
        sys.stdout.reconfigure(encoding="utf-8")
        json.dump(pages, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.flush()
    except (MemoryError, RecursionError):
        return _LIMIT
    except Exception:
        # 파서 예외에는 PDF 조각이 담길 수 있으므로 고정된 종료 코드만 전달한다.
        return _INVALID
    return 0


def extract_pdf(body: bytearray) -> tuple[str, list[str]]:
    from fastapi import HTTPException

    if not body.startswith(b"%PDF-"):
        raise HTTPException(400, "PDF 형식의 파일이 아닙니다.")
    if sys.platform != "linux":
        raise HTTPException(503, "안전한 PDF 처리를 위해 Linux Docker API 환경을 사용해 주세요.")
    if not _WORKER_SLOT.acquire(blocking=False):
        raise HTTPException(503, "다른 PDF를 처리 중입니다. 잠시 뒤 다시 시도해 주세요.")
    try:
        # 업로드 바이트는 stdin으로만 보낸다. stdout은 파이프 대신 파일 크기 제한이 적용되는
        # 익명 임시 파일에 받아, 워커 출력이 부모 메모리로 무제한 수집되지 않게 한다.
        with TemporaryFile() as output:
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(Path(__file__).resolve())],
                input=body, stdout=output, stderr=subprocess.DEVNULL, env={}, timeout=TIMEOUT_SECONDS,
            )
            if result.returncode == _ENCRYPTED:
                raise HTTPException(400, "암호화된 PDF는 등록할 수 없습니다.")
            if result.returncode == _PAGES:
                raise HTTPException(413, "PDF는 100페이지 이하여야 합니다.")
            if result.returncode in (_LIMIT, -signal.SIGXCPU, -signal.SIGKILL, -signal.SIGXFSZ):
                raise HTTPException(413, "PDF가 텍스트 추출 자원 한도를 초과했습니다.")
            if result.returncode == _UNAVAILABLE:
                raise HTTPException(503, "PDF 추출 환경을 사용할 수 없습니다.")
            if result.returncode:
                raise HTTPException(400, "손상되었거나 읽을 수 없는 PDF입니다.")
            if output.seek(0, 2) > MAX_OUTPUT_BYTES:
                raise HTTPException(413, "PDF 추출 원문이 너무 큽니다.")
            output.seek(0)
            data = output.read()
        # 압축 파일 자체가 아니라 빈 페이지까지 구분되는 추출 원문을 그대로 보관한다.
        source = data.decode("utf-8")
        return source, json.loads(source)
    except subprocess.TimeoutExpired:
        raise HTTPException(413, "PDF 텍스트 추출 시간 한도를 초과했습니다.") from None
    except OSError:
        raise HTTPException(503, "PDF 추출 환경을 사용할 수 없습니다.") from None
    finally:
        _WORKER_SLOT.release()


if __name__ == "__main__":
    raise SystemExit(_worker())
