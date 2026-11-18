# 화면은 Caddy에 접속하고, 키는 FastAPI만 읽는다

브라우저는 컨테이너 이름 `api`를 알 필요가 없다. `127.0.0.1:18730`에서 화면을 받은 뒤 **같은 주소**의 `/api/health`를 요청한다. React가 요청을 백엔드로 중계하는 것이 아니다. Caddy가 정적 파일은 직접 응답하고 `/api/` 요청은 FastAPI로 넘긴다. [화면의 연결 확인](../../apps/web/src/main.tsx), [Caddy 경로 분기](../../infra/Caddyfile), [포트와 준비 상태](../../compose.yaml)를 차례로 보면 연결된다.

**직접 확인:** 브라우저 개발자 도구의 네트워크 탭을 열고 새로고침한다. `/`와 `/api/health`의 호스트가 같고, 후자는 HTML이 아니라 `{"status":"ok"}`를 돌려준다. Compose는 첫 기동 때 API 준비를 기다리지만, 이후 API가 멈추면 정적 화면이 열려도 연결 확인은 실패할 수 있다.

## 이미지와 환경 변수는 다르다

이미지는 프로그램을 포장한 결과물이고, 컨테이너는 그 이미지를 설정값과 함께 실행한 인스턴스다. 이 프로젝트의 빌드 대상은 `apps/api`와 `apps/web`이다. 그 **바깥**에 있는 루트 `.env`는 이미지에 복사되지 않는다. 실행할 때 Compose가 `${OPENAI_API_KEY:-}`를 해석해 `api` 서비스에 **값만** 전달한다. Caddy에는 이 항목이 없다. `VITE_`로 시작하는 변수는 브라우저에 공개될 수 있으므로 키에 쓰지 않는다.

로컬에서는 `compose.yaml` 옆의 Git 제외 파일 `.env`에 `OPENAI_API_KEY=발급받은_키` 형식으로 둔다. 셸에서 이미 같은 이름을 export했다면 셸 값이 우선한다. 아래 명령은 키를 출력하거나 모델을 호출하지 않는다. ‘키 설정됨’은 값이 비어 있지 않다는 뜻이지, 인증이 성공했다는 뜻은 아니다.

```sh
docker compose up -d api
docker compose exec -T api sh -c 'test -n "$OPENAI_API_KEY" && echo "키 설정됨" || echo "키 없음"'
```

**OCI 비공개 실행:** VM 전용 키는 릴리스 소스 밖 `~/ragcoon-private/production.env`에 권한 `600`으로 둔다. 권한이 맞아도 빈 파일일 수 있다. 키 값을 출력하지 않고 컨테이너 안에서 `test -n "$OPENAI_API_KEY"`로 주입 여부를 확인한다. 이 검사는 인증·모델 권한까지 검증하지 않으므로 실제 임베딩 검사는 별도 승인 후 실행한다. 키 교체 때는 같은 `--env-file`로 API 컨테이너를 다시 생성한다. 이미지 재빌드는 필요 없고 `restart`만으로 새 값이 반영되지는 않는다. O02에서는 VM에서 직접 빌드했고, O03에서는 CI가 빌드한 이미지를 비공개 GHCR에서 digest로 지정해 가져왔다.

## DuckDNS와 Telegram이 보장하지 않는 것

무료 DuckDNS 이름은 **주소를 찾는 수단**이지 인증이나 HTTPS 자체가 아니다. 같은 VM의 같은 IP로 이름 두 개가 해석되어도, 공개 443을 받는 프록시가 호스트명별로 서비스를 구분하고 올바른 인증서를 제공해야 한다. RAGcoon은 여기에 접근 통제까지 필요하다. 화면만 보호하고 `/api/`를 열어 두면 외부에서 모델 비용이 발생하는 요청을 직접 보낼 수 있다.

### 같은 VM에서 Picksy와 RAGcoon이 함께 실행될 수 있나?

**가능하다.** 충돌하는 것은 두 앱이 아니라 **같은 호스트 IP의 TCP `443`을 두 Caddy가 각각 직접 사용하려는 설정**이다. 마지막으로 확인한 상태에서는 Picksy가 공개 `443`을 받고 RAGcoon은 중지돼 있다. O02 비공개 RAGcoon을 다시 켜더라도 VM 내부의 `127.0.0.1:18730`만 사용하므로 Picksy를 내릴 필요가 없다.

**둘 다 공개하려면(미적용):** Caddy 하나가 `443`에서 두 도메인의 HTTPS를 받고, `picksy-api.duckdns.org` 요청은 Picksy로, `ragcoon-lab.duckdns.org` 요청은 RAGcoon으로 전달하면 된다. 두 번째 공인 IP나 별도 HTTPS 포트는 필요 없다. DNS 이름만 추가하면 되는 것은 아니다. 공용 Caddy가 각 서비스에 도달할 경로와 두 호스트의 인증서, RAGcoon의 접근 제어가 필요하다.

**O03에서 Picksy를 잠깐 내린 이유:** 공존이 불가능해서가 아니라, 기존 Picksy 프록시 설정을 바꾸지 않고 RAGcoon의 Caddy가 직접 인증서를 받고 HTTPS를 제공하는지 확인하기 위해서였다. 두 Caddy가 같은 `443`을 써야 하는 그 시험 동안만 교대했고, 끝난 뒤 Picksy를 복구했다.

### `main`에 병합하면 Oracle 컨테이너가 시작되나?

Picksy는 `main` push를 배포 승인으로 사용한다. 반면 [RAGcoon 배포 워크플로](../../.github/workflows/deploy.yml)는 `main`의 **수동 실행(`workflow_dispatch`)**만 허용한다. `develop` push와 PR은 [코드 검사](../../.github/workflows/check.yml)를 실행한다. 이 구분 덕분에 문서만 `main`에 병합해도 공개 시험 때문에 Picksy가 중단되거나, 꺼 둔 RAGcoon이 다시 켜지지 않는다. 향후 **비공개 서비스 업데이트는 자동, 공개 전환은 수동**으로 분리할 수도 있지만 현재 적용된 동작은 아니다.

Telegram은 **배포 결과의 통보 수단**이지 성공의 판정자가 아니다. 성공 여부는 승인한 리비전이 실제로 서비스되고 외부 HTTPS·인증 검사가 통과했는지로 결정한다. 메시지가 오지 않았다고 배포를 다시 돌리지 말고 Actions 실행 결과부터 확인한다. 기존 Picksy와 함께 운영할 때 확인할 항목과 단계별 승인 기준은 [OCI 작업 플랜](../work-plan.md#oci--구현량보다-승인공개-범위가-달라지는-지점에서-나눈다)을 따른다.

**O03 공개 시험은 상시 운영이 아니다.** [단회 배포 절차](../../infra/o03/README.md)에 따라 `ragcoon-lab.duckdns.org`에서 최대 5분 동안 임시 Basic 인증으로 화면과 제한된 API를 확인한다. 시험 API에는 O02의 `/data`나 OpenAI 키를 연결하지 않는다. 따라서 업로드·희소 검색은 검증할 수 있지만 실제 임베딩·채팅 검증을 대신하지 않는다. 외부 검사가 끝나면 시험 컨테이너를 내리고 기존 Picksy HTTPS를 복구한다. DNS 이름이 남아 있어도 RAGcoon이 공개 실행 중이라는 뜻은 아니다.

**사설 배포와 공개 검증은 경로가 다르다.** GitHub Actions는 승인한 `main` 실행에 한해 OIDC로 `tag:ragcoon-ci`를 발급받고, Tailscale을 통해 VM의 SSH에 접속한다. Actions는 고정한 SSH 호스트 키로 VM을 확인하고, VM은 전용 배포 키·승인 SHA·이미지 digest를 확인한다. 반면 외부 시험은 Tailscale이 아니라 공인 DNS와 HTTPS를 사용한다. 이 창에서는 요청 본문 64KB, 격리 DB 16MiB, 유료 모델 호출 0으로 제한하며 **사용자별 초당 요청 수 제한은 구현하지 않았다.** 상시 공개로 바꿀 때는 별도로 정해야 한다.

**공개 배포 로그:** `DEPLOY_HOST`, `TS_OIDC_CLIENT_ID`, `TS_OIDC_AUDIENCE`는 저장소의 Actions **Secrets**에 둔다. 이 값들은 단독으로 접속 권한을 주지는 않지만, Variables에 두면 워크플로가 직접 출력하지 않아도 단계의 환경 변수나 액션 입력에 평문으로 나타날 수 있다. 마스킹은 값의 변형까지 보장하지 않으므로 진단용으로 출력하지 않는다.

## DB가 두 개인데 볼륨은 왜 하나일까?

[Compose 설정](../../compose.yaml)의 `documents:/data`는 이름 있는 볼륨 하나를 API 컨테이너의 `/data`에 연결한다. SQLite는 `/data/ragcoon.sqlite3` 파일에 원문·청크·희소 검색 색인·채팅 기록을 보관하고, ChromaDB는 `/data/chroma/` 디렉터리에 청크 ID에 대응하는 벡터와 메타데이터를 보관한다. DB의 역할과 저장 경로는 다르지만 컨테이너 교체 후에도 보존해야 한다는 조건이 같아 볼륨을 공유한다.

`docker compose down`은 이 볼륨을 남기므로 같은 프로젝트로 다시 올리면 두 DB를 다시 사용한다. 반면 **`docker compose down -v`는 볼륨까지 삭제해 두 DB가 함께 사라진다.** 삭제하지 않고 실제 연결만 확인하려면 다음 명령으로 마운트 이름과 경로를 본다.

```sh
docker inspect --format '{{range .Mounts}}{{println .Name .Destination}}{{end}}' "$(docker compose ps -q api)"
```

**영구 볼륨은 백업이 아니다.** 현재는 개인 스터디라 별도 백업이나 복원 훈련을 하지 않는다. 볼륨이나 VM이 유실되면 원본이 남은 문서는 다시 등록·색인할 수 있지만, 채팅 기록은 되살릴 수 없다. 대체할 수 없는 문서나 보존해야 할 대화가 생기면 백업을 다시 검토한다.

[실제 임베딩 테스트](evaluation.md)는 예외다. `CHROMA_PATH`를 해당 테스트 프로세스에서만 `/data` 밖의 임시 디렉터리로 바꿨고 SQLite 문서 등록은 하지 않았다. 따라서 실제 OpenAI로 벡터화한 합성 문단 3개는 앱 볼륨이나 문서 목록에 들어가지 않았다.

## 편집기에도 패키지가 필요한 이유

Docker 빌드가 설치한 React 타입은 호스트 편집기에 보이지 않는다. 처음 편집할 때 `apps/web`에서 `corepack pnpm install --frozen-lockfile`과 `corepack pnpm hooks:install`을 실행한다. `corepack pnpm check`는 Biome 검사, `corepack pnpm format`은 포맷, `corepack pnpm exec tsc --noEmit`은 타입 검사다. 커밋 훅은 스테이징한 프론트 변경을 검사할 뿐 자동 수정하지 않는다. 근거: [패키지 스크립트](../../apps/web/package.json), [커밋 훅](../../.husky/pre-commit).
