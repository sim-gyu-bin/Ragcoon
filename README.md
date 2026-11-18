# 🦝 RAGcoon

**Dig into knowledge, one chunk at a time.**

RAGcoon은 RAG(Retrieval-Augmented Generation, 검색 증강 생성)와 AI Agent를 직접 구현하고 실험하는 풀스택 스터디 프로젝트다. 문서와 질문이 주어졌을 때 **어떤 문단이 검색됐고, 답변이 그 문단의 어떤 내용에 근거하는지** 확인할 수 있는 서비스를 만드는 것이 목표다.

Markdown·텍스트 PDF를 등록하고, 검색된 문단을 바탕으로 답변을 받아 인용 원문까지 확인할 수 있다. 같은 질문으로 기본 RAG와 한 번 더 검색할 수 있는 Agent의 결과도 비교한다.

**구성:** React·TypeScript 화면과 FastAPI를 Caddy 뒤에서 실행한다. SQLite FTS5와 ChromaDB가 검색을, LangChain과 LangGraph가 답변 생성과 조건부 재검색을 맡는다.

**현재 상태:** 로컬 Docker 환경에서 합성 데이터로 등록·검색·채팅·기록 복원을 검증했다. 실제 OpenAI 모델의 품질 평가와 OCI 배포는 아직 진행하지 않았다.

## 빠른 시작

Docker Compose가 설치된 환경에서 저장소 루트에서 실행한다.

```sh
docker compose up -d --build
```

[로컬 화면](http://127.0.0.1:18730/)에서 문서를 등록하고 원문을 확인하는 데에는 API 키가 필요 없다. 중지할 때는 `docker compose stop`을 사용한다.

실제 색인·AI 채팅을 사용하려면 `compose.yaml` 옆의 `.env`에 `OPENAI_API_KEY`를 설정하고 `docker compose up -d api`로 적용한다. 색인을 시작하면 문서 조각이, 답변을 생성하면 질문과 검색 근거가 OpenAI로 전송되며 비용이 발생할 수 있다. 키를 Git에 커밋하거나 프론트엔드에 설정하지 않는다.

## 더 읽기

- [학습 위키 — L01~L07](docs/wiki/README.md): 화면의 요청부터 검색·인용·평가까지, 설계 이유와 직접 확인하는 방법
- [제품 요구사항](docs/prd.md): 기능 범위와 인수 기준
- [작업 플랜](docs/work-plan.md): 단계별 검증 결과, 미확인 항목과 OCI 배포 계획
