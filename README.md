# 🦝 RAGcoon

**Dig into knowledge, one chunk at a time.**

RAGcoon은 RAG(Retrieval-Augmented Generation, 검색 증강 생성)와 AI Agent를 직접 구현하고 실험하는 풀스택 스터디 프로젝트다. 문서와 질문이 주어졌을 때 **어떤 문단이 검색됐고, 답변이 그 문단의 어떤 내용에 근거하는지** 확인할 수 있는 서비스를 만드는 것이 목표다.

**현재 구현 범위: 등록·검색·출처가 있는 문서 채팅(L01~L04).** Agent와 OCI 배포는 이후 단계다.

기능별 인수 기준과 단계별 완료 조건은 [제품 요구사항 문서(PRD)](docs/prd.md)를 참고한다.
한 단계씩 구현·검증하는 순서와 로컬 완료 후 OCI 배포 절차는 [단계별 작업 플랜](docs/work-plan.md)을 따른다.

## 로컬 실행

**처음 편집할 때:** `cd apps/web` 후 `corepack pnpm install --frozen-lockfile`을 실행한다. Corepack이 이 디렉터리의 `package.json`에서 지정한 pnpm 버전을 선택한다. Docker 이미지에 설치된 패키지는 호스트 편집기에 보이지 않으므로 로컬에도 설치한다. 같은 디렉터리에서 `corepack pnpm exec tsc --noEmit`으로 타입을 검사할 수 있다.

같은 `apps/web`에서 `corepack pnpm hooks:install`을 한 번 실행하면 이 체크아웃에 Husky가 연결된다. Docker 빌드는 Git 훅을 설치하지 않는다. VS Code에서는 권장 확장 **Biome**을 설치하면 프론트 파일을 저장할 때 포맷한다.

- `corepack pnpm check`: Biome 포맷·린트·import 정렬 검사. 타입 검사는 별도다.
- `corepack pnpm format`: 작업 파일의 포맷만 수정한다.
- 커밋 전에는 추가·수정한 프론트 소스와 설정 JSON의 **스테이징된 내용만** 검사한다. 실패하면 진단을 수정한 뒤 원하는 변경분만 다시 스테이징한다. 훅은 파일이나 Git 인덱스를 수정하지 않는다.

Docker Compose가 실행되는 환경에서 저장소 루트에서 `docker compose up -d --build`를 실행하고 [로컬 화면](http://127.0.0.1:18730/)을 연다. 별도 DB 서버는 필요 없다. API 키 없이도 등록과 원문 확인은 가능하다.

- **정상:** 브라우저의 React 화면이 같은 주소의 `/api/health`를 요청한다. Caddy가 FastAPI로 전달하고 `{"status":"ok"}`를 받으면 ‘연결 정상’으로 표시한다.
- **실패 실습:** `docker compose stop api` 후 새로고침한다. Caddy가 정적 화면은 계속 제공하지만 ‘연결 실패’로 바뀐다. `docker compose start api` 후 다시 새로고침하면 복구된다.
- **읽는 순서:** [React 요청](apps/web/src/main.tsx) → [Caddy 경로 분기](infra/Caddyfile) → [FastAPI 응답](apps/api/app/main.py). 실습을 마치면 `docker compose stop`으로 중지한다.

### Markdown 등록 실습

1. 빈 줄로 문단을 구분한 한국어 `.md` 또는 `.markdown` 파일을 선택해 등록한다. UTF-8 텍스트만 허용하며 파일당 최대 1 MiB(1,048,576바이트)다.
2. 목록에서 문서를 열고 **청크 ID, 원문 줄 번호, 텍스트**를 대조한다. 같은 이름으로 다시 등록하면 기존 문서를 덮어쓰지 않고 별도의 문서 ID가 생긴다.
3. 새로고침 후에도 목록이 유지된다. 원문과 청크는 API 컨테이너의 `/data/ragcoon.sqlite3`에 보관하며, Compose의 `documents` 볼륨이 컨테이너보다 오래 유지된다. 자동 만료는 없으므로 실습에는 민감하지 않은 파일을 사용한다.

이 단계의 ‘등록’은 ‘검색 준비 완료’가 아니다. 외부로 본문을 전송하거나 임베딩을 생성하지 않는다. [문서 API](apps/api/app/documents.py)에서 등록한 원문이 어떻게 청크로 나뉘고 SQLite에 보관되는지 살펴볼 수 있다.

### 같은 질문으로 검색 방식 비교

1. 실제 임베딩·답변 생성 실습을 할 때만 서버의 `OPENAI_API_KEY`를 설정한다. 셸 환경 또는 Git이 무시하는 루트 `.env`를 사용하고, 키 값을 코드·채팅·화면에 넣지 않는다. 설정 후 루트에서 `docker compose up -d api`로 적용한다.
2. 외부 전송 가능한 Markdown을 등록하고 그 문서의 **색인**을 눌러야 청크가 OpenAI로 전송된다. SQLite FTS5와 영구 Chroma 색인이 모두 완료된 문서만 검색에 참여한다. 중간 실패는 준비 완료가 아니며, 재시도하면 임베딩 비용이 다시 발생할 수 있다.
3. `OPENAI_API_KEY` 같은 식별자(키 값이 아님) 또는 `백업은 언제 해?` 같은 질문으로 검색한다. 기본 **하이브리드**는 BM25와 벡터 순위를 RRF로 합친다. **희소**는 준비된 문서를 로컬에서만 검색하고, **밀집**과 **하이브리드**는 질문 임베딩을 사용할 수 있다. 같은 질문에서 모드만 바꿔 상위 3개 원문·줄 범위를 비교한다. 어떤 모드도 채팅 모델을 호출하지 않는다.

구현 흐름: [검색 화면](apps/web/src/Search.tsx) → [색인 상태·검색 모드·RRF](apps/api/app/search.py) → [OpenAI 임베딩과 Chroma](apps/api/app/vectors.py). API에서 `POST /api/search`에 `{"query":"백업"}`을 보내면 `hybrid`가 기본이고, `mode`에 `sparse` 또는 `dense`를 지정하면 해당 경로만 실행한다.

### 출처가 있는 문서 채팅

새 채팅을 열고 검색 방식과 질문을 선택한다. 검색 화면은 원문 후보만 반환하지만, **채팅**은 그 후보를 근거로 답변을 생성한다. `[1]` 같은 인용을 선택해 실제 문서명·줄 범위·원문을 대조한다. 인용 번호가 존재한다는 것만으로 답변 내용까지 사실로 검증된 것은 아니다.

질문·답변 상태와 인용 출처는 세션별로 SQLite에 보관한다. 새로고침해도 대화를 다시 읽을 수 있다. 대화 기록을 저장하는 것과 전체 기록을 모델에 전송하는 것은 다르다. L04에서는 매 질문과 그때 검색한 근거만 전송한다. 검색 후보가 없으면 생성 모델은 호출하지 않으며, 생성 중 실패는 완료된 답변과 구분한다.

학습 순서: [채팅 API와 기록](apps/api/app/chat.py) → [LangChain 생성 스트림](apps/api/app/chat_model.py) → [토큰과 인용을 표시하는 화면](apps/web/src/Chat.tsx).

## 모델 선택

- **임베딩: OpenAI `text-embedding-3-small`.** 별도 모델 서버를 운영하지 않기 위해 OpenAI API를 사용한다. 업로드한 문서 조각과 사용자 질문을 같은 모델로 벡터화한다. 문서 벡터는 ChromaDB에 저장하고 질문 벡터는 관련 문단을 찾는 데 사용한다.
- **답변 생성: OpenAI `gpt-4.1-mini` + LangChain.** 한 질문에 최대 한 번 생성하고, 최대 3개 청크와 출력 상한 512토큰을 사용한다. SDK 자동 재시도는 하지 않는다. 요청 단위 제한이지 월 예산 제한은 아니다. 희소 검색을 선택해도 **답변 생성**에는 외부 전송과 비용이 발생할 수 있다.
- 임베딩 호출에는 비용이 발생하며 문서 조각과 질문이 OpenAI로 전송된다. 외부 전송이 가능한 문서로 실습하고, `OPENAI_API_KEY`는 백엔드 환경 변수로만 전달한다. 프론트엔드 번들이나 저장소에는 넣지 않는다.

## 기술 구성

| 영역 | 기술 | 역할 |
|---|---|---|
| 프론트엔드 | React, TypeScript, Vite, pnpm | 문서 업로드, 채팅, 검색 근거 표시 |
| 백엔드 | Python, FastAPI, Pydantic, uv | 요청 검증, 문서 처리, 응답 스트리밍 |
| RAG·Agent | LangChain, LangGraph | 검색과 생성 연결, 도구 호출, 상태 관리와 조건부 재검색 |
| 관측·평가 | LangSmith | 실행 추적, 프롬프트와 검색·답변 품질 비교 |
| 저장소 | SQLite, ChromaDB | 문서·대화 메타데이터와 벡터 검색 데이터 관리 |
| 테스트 | Pytest, Vitest | 백엔드와 프론트엔드 동작 검증 |
| 배포 | Docker Compose, Caddy, GitHub Actions | 컨테이너 운영, HTTPS, CI/CD |

## 문서에서 답변까지

1. **색인:** PDF·Markdown을 청크로 나누고 동일한 청크 ID와 출처를 ChromaDB 벡터 색인과 SQLite FTS5 전문 색인에 사용한다.
2. **검색:** 기본은 밀집·희소 검색의 순위를 RRF로 합치는 하이브리드 검색이다. 희소 또는 밀집만 선택해 같은 질문의 결과를 비교할 수 있다. 예시는 [검색 개념 스터디](docs/search-concepts.md)를 참고한다.
3. **답변:** 질문과 검색된 청크를 LLM에 전달한다. 프론트엔드는 답변을 스트리밍으로 표시하고 참고 문서를 함께 보여 준다. 채팅 세션과 메시지는 SQLite에 저장한다.
4. **Agent 실험:** 기본 검색이 동작한 뒤 LangGraph로 검색 결과 평가, 조건부 재검색, 도구 호출을 구현한다. LangSmith로 각 단계의 실행을 추적하고 기본 RAG와 비교한다.

## 프로젝트 구조 (예정)

```text
ragcoon/
├── apps/
│   ├── web/src/          # React UI
│   └── api/
│       ├── app/          # FastAPI 진입점, 라우터, 모델, 서비스, RAG, Agent
│       ├── tests/
│       └── pyproject.toml
├── infra/                # Caddyfile, Docker 관련 설정
├── compose.yaml
├── .github/workflows/    # CI/CD
├── .omp/                 # 스터디 작업 규칙과 스킬
└── README.md
```

## 개발 로드맵

- [ ] **1. 풀스택 기반:** 모노레포, React + Vite, FastAPI, 프론트엔드 ↔ 백엔드 API 연동, Docker Compose 개발 환경
- [ ] **2. RAG 파이프라인:** 문서 업로드·목록·삭제, 청크 분할, 밀집·희소·하이브리드 검색, 출처가 있는 문서 채팅 UI
- [ ] **3. AI Agent:** LangGraph StateGraph, 조건부 분기와 재검색, 도구 호출, 실행 상태 시각화
- [ ] **4. 관측과 평가:** LangSmith 추적, 평가용 질문·근거 문단 데이터셋, 검색 품질과 답변 품질 평가, 프롬프트 개선
- [ ] **5. 배포와 운영:** Oracle Cloud ARM64 배포, Caddy HTTPS, GitHub Actions CI/CD, 영구 데이터 백업과 모니터링

## 저장과 배포 계획

초기에는 SQLite와 ChromaDB를 임베디드 방식으로 사용한다. SQLite는 문서 메타데이터·채팅 기록·설정을, ChromaDB는 청크·벡터·검색용 메타데이터를 맡는다. 필요하면 LangGraph 체크포인트도 SQLite에 저장한다. 데이터는 Docker 볼륨 또는 호스트 바인드 마운트에 보관해 컨테이너를 재생성해도 유지하도록 한다.

배포 대상은 Oracle Cloud 오사카 리전의 Ampere A1 VM(2 OCPU, 메모리 12GB, Ubuntu 24.04 ARM64)이다. Always Free 범위 운영을 목표로 하며, Caddy가 HTTPS 요청을 받아 React 정적 파일을 제공하고 API 요청을 FastAPI로 전달한다. 외부 모델 API와 LangSmith 사용 비용은 VM 비용과 별도로 관리한다.
