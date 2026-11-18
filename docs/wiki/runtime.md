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

**OCI 운영안(아직 미배포):** 운영 키는 VM의 저장소 밖 파일(예: `/etc/ragcoon/production.env`)에 두고 배포 계정 소유·권한 `600`으로 제한한다. CI는 운영 키 없이 이미지를 빌드하고 VM은 검증된 digest를 내려받는다. 키 교체 때는 같은 운영 환경 파일을 지정해 API 컨테이너를 다시 생성한다. 이미지 재빌드는 필요 없고 `restart`만으로는 새 값이 반영되지 않는다. Docker 관리 권한으로는 환경 변수를 볼 수 있다. 설정 전체 출력이나 로그를 공유하지 않는다.

## DuckDNS와 Telegram이 보장하지 않는 것

무료 DuckDNS 이름은 **주소를 찾는 수단**이지 인증이나 HTTPS 자체가 아니다. 같은 VM의 같은 IP로 이름 두 개가 해석되어도, 공개 443을 받는 프록시가 호스트명별로 서비스를 구분하고 올바른 인증서를 제공해야 한다. RAGcoon은 여기에 접근 통제까지 필요하다. 화면만 보호하고 `/api/`를 열어 두면 외부에서 모델 비용이 발생하는 요청을 직접 보낼 수 있다.

두 앱을 **동시에 공개하지 않아도 된다면** 같은 VM에서 프록시를 공유할 필요가 없다. 먼저 RAGcoon을 루프백에 띄워 기존 서비스와 독립적으로 검증하고, 공개 전환 때에만 기존 컨테이너를 중지해 443을 넘긴다. 중지는 볼륨 삭제나 VM 종료가 아니다. 기존 인증서·설정·볼륨을 보존해야 전환 실패 시 이전 서비스를 같은 상태로 다시 띄울 수 있다. 이 방식에서는 중지한 서비스의 공개 주소가 응답하지 않는 것이 의도한 동작이다.

Telegram은 **배포 결과의 통보 수단**이지 성공의 판정자가 아니다. 성공 여부는 승인한 리비전이 실제로 서비스되고 외부 HTTPS·인증 검사가 통과했는지로 결정한다. 메시지가 오지 않았다고 배포를 다시 돌리지 말고 Actions 실행 결과부터 확인한다. 기존 Picksy와 함께 운영할 때 확인할 항목과 단계별 승인 기준은 [OCI 작업 플랜](../work-plan.md#oci--구현량보다-승인공개-범위가-달라지는-지점에서-나눈다)을 따른다.

## DB가 두 개인데 볼륨은 왜 하나일까?

[Compose 설정](../../compose.yaml)의 `documents:/data`는 이름 있는 볼륨 하나를 API 컨테이너의 `/data`에 연결한다. SQLite는 `/data/ragcoon.sqlite3` 파일에 원문·청크·희소 검색 색인·채팅 기록을 보관하고, ChromaDB는 `/data/chroma/` 디렉터리에 청크 ID에 대응하는 벡터와 메타데이터를 보관한다. DB의 역할과 저장 경로는 다르지만 컨테이너 교체 후에도 보존해야 한다는 조건이 같아 볼륨을 공유한다.

`docker compose down`은 이 볼륨을 남기므로 같은 프로젝트로 다시 올리면 두 DB를 다시 사용한다. 반면 **`docker compose down -v`는 볼륨까지 삭제해 두 DB가 함께 사라진다.** 삭제하지 않고 실제 연결만 확인하려면 다음 명령으로 마운트 이름과 경로를 본다.

```sh
docker inspect --format '{{range .Mounts}}{{println .Name .Destination}}{{end}}' "$(docker compose ps -q api)"
```

[실제 임베딩 테스트](evaluation.md)는 예외다. `CHROMA_PATH`를 해당 테스트 프로세스에서만 `/data` 밖의 임시 디렉터리로 바꿨고 SQLite 문서 등록은 하지 않았다. 따라서 실제 OpenAI로 벡터화한 합성 문단 3개는 앱 볼륨이나 문서 목록에 들어가지 않았다.

## 편집기에도 패키지가 필요한 이유

Docker 빌드가 설치한 React 타입은 호스트 편집기에 보이지 않는다. 처음 편집할 때 `apps/web`에서 `corepack pnpm install --frozen-lockfile`과 `corepack pnpm hooks:install`을 실행한다. `corepack pnpm check`는 Biome 검사, `corepack pnpm format`은 포맷, `corepack pnpm exec tsc --noEmit`은 타입 검사다. 커밋 훅은 스테이징한 프론트 변경을 검사할 뿐 자동 수정하지 않는다. 근거: [패키지 스크립트](../../apps/web/package.json), [커밋 훅](../../.husky/pre-commit).
