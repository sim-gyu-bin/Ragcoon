# 🦝 RAGcoon

**Dig into knowledge, one chunk at a time.**

RAGcoon은 RAG(Retrieval-Augmented Generation, 검색 증강 생성)와 AI Agent를 직접 구현하고 실험하는 풀스택 스터디 프로젝트다. 문서와 질문이 주어졌을 때 **어떤 문단이 검색됐고, 답변이 그 문단의 어떤 내용에 근거하는지** 확인할 수 있는 서비스를 만드는 것이 목표다.

**현재 상태: 기획 단계.** 아래 기능과 배포 구성은 개발 계획이며, 아직 실행 가능한 애플리케이션은 없다.

기능별 인수 기준과 단계별 완료 조건은 [제품 요구사항 문서(PRD)](docs/prd.md)를 참고한다.
한 단계씩 구현·검증하는 순서와 로컬 완료 후 OCI 배포 절차는 [단계별 작업 플랜](docs/work-plan.md)을 따른다.

## 모델 선택

- **임베딩: OpenAI `text-embedding-3-small`.** 별도 모델 서버를 운영하지 않기 위해 OpenAI API를 사용한다. 업로드한 문서 조각과 사용자 질문을 같은 모델로 벡터화한다. 문서 벡터는 ChromaDB에 저장하고 질문 벡터는 관련 문단을 찾는 데 사용한다.
- **답변 생성: 구체적인 채팅 모델은 미정.** 임베딩 모델은 문단을 찾는 역할이고, 검색된 문단을 읽고 답변을 작성하는 일은 별도 LLM이 맡는다.
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
