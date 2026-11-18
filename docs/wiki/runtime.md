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

**OCI 운영안(아직 미배포):** 운영 키를 VM의 저장소 밖 파일(예: `/etc/ragcoon/production.env`)에 두고 배포 계정 소유·권한 `600`으로 제한한다. VM의 프로젝트 디렉터리에서 `docker compose --env-file /etc/ragcoon/production.env up -d --build`로 실행한다. 이 방식은 GitHub Actions가 OpenAI 키를 보관할 필요가 없다. 교체 시에는 같은 `--env-file`로 `up -d api`를 실행한다. 새 이미지가 필요한 게 아니라 컨테이너 환경이 바뀌어야 하므로 `restart`만으로는 부족하다. Docker 관리 권한으로는 환경 변수를 볼 수 있다. 설정 전체 출력이나 로그를 공유하지 않는다.

## 편집기에도 패키지가 필요한 이유

Docker 빌드가 설치한 React 타입은 호스트 편집기에 보이지 않는다. 처음 편집할 때 `apps/web`에서 `corepack pnpm install --frozen-lockfile`과 `corepack pnpm hooks:install`을 실행한다. `corepack pnpm check`는 Biome 검사, `corepack pnpm format`은 포맷, `corepack pnpm exec tsc --noEmit`은 타입 검사다. 커밋 훅은 스테이징한 프론트 변경을 검사할 뿐 자동 수정하지 않는다. 근거: [패키지 스크립트](../../apps/web/package.json), [커밋 훅](../../.husky/pre-commit).
