# Agent의 재검색과 테스트 결과 읽기

## 한 번만 다시 검색하는 이유

기본 RAG는 선택한 방식으로 한 번 검색한다. Agent의 차이는 더 강한 모델을 쓰는 것이 아니라 **첫 결과가 부족할 때 다음 행동을 선택하는 것**이다. [LangGraph 실행 코드](../../apps/api/app/agent.py)의 `run_strategy`에서 두 전략을 비교할 수 있다.

후보가 없거나 모델이 근거 부족을 명시하면 Agent는 다른 방식으로 한 번만 재검색한다. 희소·밀집에서 시작했다면 하이브리드, 하이브리드에서 시작했다면 희소로 바꾼다. 그래도 근거가 없으면 `retry_limit`으로 끝낸다. 잘못된 인용이나 공급자 오류는 검색 방식을 바꾼다고 해결되지 않으므로 재시도하지 않는다. 한 번의 비교에서 두 전략을 합쳐 검색과 생성은 각각 최대 3회다.

**화면에서 읽는 법:** 기본 RAG가 `no_results`인데 Agent가 `answered`라면 먼저 실행 순서에서 `retry_search`를 찾고, 마지막에 인용한 원문이 질문에 답하는지 확인한다. [비교 화면](../../apps/web/src/Compare.tsx)은 이런 차이를 보여 주며 일반 채팅과 달리 대화 기록을 만들지 않는다.

## 오프라인 검증의 범위

| 실험 | 실제로 확인한 것 | 확인할 수 없는 것 |
|---|---|---|
| Caddy 브라우저 실습 | 로컬 공급자가 고정 벡터와 정해진 답변을 돌려줄 때 등록·색인·검색·인용이 연결되고, 컨테이너 교체 뒤 같은 세션과 출처가 복원됐다. | 모델이 동의어를 이해했는지, 답을 스스로 만들었는지 |
| 한국어 합성 평가 | 실제 SQLite·Chroma로 검색하고 LangGraph를 실행했다. 외부 모델 대신 글자를 두 개씩 나눈 해시 벡터와 원문 발췌 규칙을 썼다. | `text-embedding-3-small`의 의미 검색 품질과 실제 LLM의 인용 정확성 |
| 실제 OpenAI 임베딩 | [VectorStore](../../apps/api/app/vectors.py)의 `upsert`로 합성 문단 3개를 `text-embedding-3-small`의 1,536차원 벡터로 격리 Chroma에 저장했다. `search`에서 ‘파일 복사본’ 질문은 백업 시각 문단을, ‘서적을 돌려줄 때’ 질문은 도서 대출 기한 문단을 각각 1위로 반환했다. | 두 질문의 밀집 검색 연결 검사다. 모드별 품질, 실제 LLM 답변·인용, 청구액은 측정하지 않았다. |

### 모델 키 없이 평가하기

로컬 앱이 켜진 상태에서 저장소 루트에서 실행한다. 서비스 DB 대신 별도 합성 자료를 쓴다.

```sh
docker compose exec api .venv/bin/python -m evaluation
```

[질문과 정답표](../../apps/api/evaluation/dataset.py)에는 답이 있는 질문 8개와 없는 질문 2개가 있다. 세 검색 방식과 두 전략으로 총 60번 실행한다. JSON에서 `hit_at_3`는 **답이 있는 질문의 최종 검색 후보에 정답 문단이 있었는지**, `citation_accuracy`는 **완료한 답변이 기대 답을 포함하고 모든 인용이 그 답을 지지하는지** 본다. 근거 없는 질문을 거절했는지와 실행 실패 수도 별도로 본다. 두 지표는 분모가 다르다. [채점 코드](../../apps/api/evaluation/scoring.py)는 정답 표기로 검사하므로 문장 전체의 의미를 판정하는 심판은 아니다.

예를 들어 희소 검색으로 시작한 합성 평가에서 정답 문단 포함은 기본 4/8, Agent 7/8이었다. 이는 이 자료에서 재검색이 누락을 일부 보완했다는 뜻이지 실제 임베딩보다 우수하다는 뜻이 아니다. [오프라인 모델](../../apps/api/evaluation/offline.py)과 [전체 검증 결과](../work-plan.md)를 함께 본다.

실제 품질을 확인할 때만 서버 키와 전송·비용 승인 후 같은 명령에 `--live --allow-external`을 붙인다. LangSmith는 별도 선택이다. `--trace-synthetic`은 첫 합성 질문·첫 모드의 두 전략에 대해 모드·전략·시각·단계·상태·종료 사유·인용 수를 보낸다. 질문·원문·답변은 보내지 않는다. [추적 필드](../../apps/api/app/experiment_trace.py)는 로컬에서 확인했고 원격 기록은 미검증이다.

### 로컬 인수 검사 재현

저장소 루트에서 실행한다. 첫 빌드와 의존성 설치에는 네트워크가 필요하지만 테스트 컨테이너는 키·외부망·서비스 볼륨 없이 실행한다.

```sh
docker build --target test -t ragcoon-api-test apps/api
docker run --rm --network none ragcoon-api-test
(cd apps/web && corepack pnpm install --frozen-lockfile && corepack pnpm test && corepack pnpm check && corepack pnpm build)
```

자동 검사와 별도로 브라우저에서는 **등록한 청크 → 검색된 원문 → 답변이 인용한 문단 → 컨테이너 교체 뒤 복원된 인용**이 같은지를 대조한다. 단순히 화면에 답변이 나타나는 것만으로 저장·검색·복원이 모두 정상이라고 판단할 수 없다.
