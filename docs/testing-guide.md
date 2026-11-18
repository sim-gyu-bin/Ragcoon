# RAGcoon 화면·API 테스트 가이드

[README](../README.md) · [학습 위키](wiki/README.md) · [모델 평가 결과](wiki/evaluation.md)

문서 한 줄을 올리고, 검색 결과와 AI 답변의 인용이 그 줄을 가리키는지 확인하는 실습이다. **API 키 없이 시작하려면 1~3절의 원문 확인까지만** 진행한다. 모든 명령은 별도 표시가 없으면 저장소 루트에서 실행한다.

## 1. 테스트 환경 선택

| 환경 | 접속 경로 | 용도 |
|---|---|---|
| 내 Mac의 Docker Desktop | `http://127.0.0.1:18730` | 기본 실습 환경. 화면과 API 모두 로컬 Caddy를 통과하며 데이터는 `ragcoon_documents`에 남는다. |
| Oracle VM의 비공개 Docker | VM 안에서 `http://127.0.0.1:18730` | Mac과 다른 데이터다. 현재 중지한 서비스이므로 이 실습을 위해 켤 필요가 없다. |
| 공개 HTTPS | O03 승인 시험 창에서만 사용 | 상시 테스트 주소가 아니다. 공개 시험은 종료됐고 Picksy가 443을 사용한다. |

## 2. 로컬 실행과 준비 확인

Docker Desktop을 켜고 실행한다. 이미 실행 중이라면 `up`을 생략한다.

```sh
docker compose up -d --build
docker compose ps
BASE_URL=http://127.0.0.1:18730
curl -fsS "$BASE_URL/api/health"
```

API가 `healthy`, Caddy가 실행 중이고 응답이 `{"status":"ok"}`면 화면을 열 수 있다. 이는 Caddy → API 연결 확인이지, OpenAI 키나 색인이 정상이라는 뜻은 아니다.

## 3. 화면에서 문서 한 줄 따라가기

1. **등록 — 무료.** [로컬 화면](http://127.0.0.1:18730/)을 연다. 파일 선택 창에서 `study.md`를 선택해 등록한다. 파일이 없다면 텍스트 편집기에서 `도서관에서 빌린 책은 14일 안에 반납해야 합니다.` 한 줄을 쓰고 UTF-8 Markdown 파일로 저장한다. 질문 입력란에 이 문장을 붙이는 게 아니다.
2. **원문 확인 — 무료.** 방금 등록한 문서의 구간을 펼친다. 파일명, 1줄, `14일`이 들어간 원문을 확인한다. 같은 이름을 다시 올리면 다른 문서 ID가 생기므로 한 번만 등록한다. 아직 검색되지 않아도 정상이다.
3. **색인 — 여기부터 실모델 선택.** 합성 문서를 외부로 전송해도 될 때만 진행한다. 저장소 루트의 `.env`에 `OPENAI_API_KEY`를 설정하고 `docker compose up -d api`로 적용한다. 화면에서 해당 문서의 색인을 한 번 실행하고 완료를 기다린다. 이때 `text-embedding-3-small`이 청크를 벡터화한다. 키를 프론트엔드나 터미널 출력에 넣지 않는다.
4. **검색 — 답변이 아닌 근거 확인.** 검색 영역에서 `희소 (BM25)`를 선택하고 `14일`을 검색한다. 방금 올린 문단과 파일명이 후보에 있는지 본다. 이미 색인된 문서의 희소 검색 자체는 모델을 부르지 않는다. 기본값인 하이브리드와 밀집 검색은 질문 임베딩을 호출한다.
5. **채팅 — 생성 비용 발생 가능.** 채팅 영역에서 `도서관에서 빌린 책은 며칠 안에 반납해야 하나요?`라고 묻는다. 근거가 있으면 `gpt-4.1-mini`가 답변을 작성한다. **합격 기준은 답변에 ‘14일’이 있는 것만이 아니다.** 인용 번호를 눌렀을 때 `study.md`의 해당 문장이 열려야 한다. 새로고침한 뒤 같은 세션을 선택해 답변과 인용이 남아 있는지도 확인한다.
6. **선택: Agent 비교.** 비교 화면에서 같은 질문과 시작 검색 방식을 사용한다. 최종 문구보다 기본 RAG와 Agent가 가져온 근거, 재검색 여부와 종료 상태를 비교한다. 양쪽 실행이 모델을 호출할 수 있다. [평가 지표 해설](wiki/evaluation.md)

## 4. 터미널에서 API만 확인

브라우저 대신 Caddy에 직접 요청한다. 첫 블록은 읽기 전용이다. 문서 목록의 `id`는 파일명이 아니라 등록 건의 식별자다.

```sh
BASE_URL=http://127.0.0.1:18730
curl -fsS "$BASE_URL/api/health"
curl -fsS "$BASE_URL/api/documents" | python3 -m json.tool --no-ensure-ascii
```

이미 색인을 마쳤다면 아래 요청으로 화면과 같은 희소 검색을 확인한다. `mode`가 `sparse`이고 `results`에 예상 문장이 있는지 본다. 문서가 없거나 일치하지 않으면 빈 결과가 정상이다.

```sh
curl -fsS "$BASE_URL/api/search" -H 'Content-Type: application/json' \
  -d '{"mode":"sparse","query":"14일"}' | python3 -m json.tool --no-ensure-ascii
```

## 5. 결과가 예상과 다를 때

| 증상 | 먼저 확인할 것 |
|---|---|
| 연결 거절 또는 502 | Mac에서 `docker compose ps`로 Caddy와 API 상태를 구분한다. 시작 직후면 API health를 기다린다. |
| 문서는 보이는데 검색되지 않음 | 등록만 했는지, 색인이 완료됐는지 확인한다. 희소 검색은 문장 전체 대신 원문에 있는 단어 `14일`로 먼저 시험한다. |
| 색인·생성 실패 | 연결 확인과 모델 인증은 별개다. `.env` 변경 후 API에 반영했는지, OpenAI 프로젝트의 키 권한·한도를 확인한다. 무작정 재색인하지 않는다. |
| 답은 맞는데 인용이 다름 | 최근에 같은 파일명을 여러 번 등록했는지 확인하고, 인용된 원문과 줄을 직접 비교한다. 검색 품질과 답변 품질은 따로 판정한다. |

## 6. 코드 변경 후 자동 검사

[GitHub Actions 검사](../.github/workflows/check.yml)와 같은 명령이다. Python 3, Docker, 프론트 검사용 Node.js와 Corepack이 필요하다. 의존성 설치와 이미지 **빌드**에는 네트워크가 필요할 수 있지만, API 테스트 **실행**에는 외부망이나 모델 키가 필요 없다. 로컬 앱 볼륨은 마운트하지 않는다.

```sh
python3 -B -m unittest discover -s infra/tests
docker build --target test -t ragcoon-api-test apps/api
docker run --rm --network none ragcoon-api-test
(cd apps/web && corepack pnpm install --frozen-lockfile && corepack pnpm check && corepack pnpm test && corepack pnpm build)
```

이 검사가 통과해도 실모델의 답변 품질까지 검증한 것은 아니다. 실습을 끝낼 때 로컬 앱만 중지하려면 `docker compose stop`을 사용한다. **`down -v`는 쓰지 않는다.** 문서와 채팅이 든 볼륨까지 삭제하며 스터디 데이터에는 별도 백업이 없다.

## 7. Oracle VM 상태 확인 (선택·읽기 전용)

Tailscale과 기존 관리용 SSH로 **VM에 접속한 셸에서만** 아래를 실행한다. Mac에서 실행하면 로컬 Docker 상태가 나온다.

```sh
sudo docker ps -a --filter label=com.docker.compose.project=ragcoon-private \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

수동 중지 상태에서는 `Exited`가 예상값이다. 비공개 서비스가 이미 실행 중일 때만 VM 안에서 `curl -fsS http://127.0.0.1:18730/api/health`로 확인한다. 공개 DNS 조회나 Telegram의 과거 배포 성공 알림은 현재 RAGcoon이 실행 중이라는 증거가 아니다. [운영 경계와 데이터 보존](wiki/runtime.md)
