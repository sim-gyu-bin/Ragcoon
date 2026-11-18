# O03 단회 공개 창

이 디렉터리는 VM root가 설치하는 실행 코드다. CI는 여기의 파일, Compose 변수, 명령이나 설치 경로를 SSH로 넘길 수 없다. Ubuntu 24.04 ARM64, systemd, Docker Engine/Compose, Python 3.12와 bcrypt 지원 `crypt`, curl, OpenSSH, sudo가 필요하다. DNS·443 승인, Picksy 배포 정지 협의, 사설 SSH와 호스트 키 검증은 이 스크립트가 대신하지 않는다.

**승인/준비.** 관리자가 승인한 `main` 리비전에서 이 디렉터리만 추출해 VM의 root 전용 staging에 둔다. 실행 전 `/etc/ragcoon-o03`(root:root 0700)에 아래 세 파일(root:root 0600)을 배치한다. 실행 중에는 설정을 바꾸지 않는다. 새 소스는 새 `main` SHA 승인·staging·준비가 필요하다.

| 파일 | 내용 |
| --- | --- |
| `approved-revision` | 소문자 전체 Git SHA 40자리 한 줄 |
| `deploy.pub` | 전용 Ed25519 OpenSSH 공개키 한 줄(LF 포함). 개인키 아님 |
| `site.json` | `host`(승인된 별도 DuckDNS), `picksy_health_url`(기존 HTTPS 200의 절대 URL), `picksy_project`, `picksy_compose_file`(현행 컨테이너 라벨의 단일 절대 경로), `api_repository`, `caddy_repository`(각각 태그 없는 정확한 GHCR 저장소), `ssh_source`(전용 승인 사설 CIDR) 문자열 7개만 |

Root가 staging에서 `./prepare`를 실행한다. Picksy/O02를 정지하거나 공개하지 않는다. root 설치본, 제한된 로그인 셸과 `ragcoon-o03` 계정, 강제 키, 인자 없는 receiver에 한정한 sudo 규칙, systemd 복구 유닛을 배치한다. 기존 SSH 전역 설정은 바꾸지 않는다. `access.netrc`(임시 `smoke` 암호), `access.hash`(bcrypt)는 0600으로 생성한다. 관리자는 외부 검사에 쓸 netrc만 **별도 검증된 사설 관리 채널**로 로컬 0600 임시 파일에 전달한다. 화면·채팅·Actions 비밀·명령줄에 암호를 넣지 않는다.

**실행.** 배포 계정으로 TTY·원격 명령 없이 연결하고 stdin에 UTF-8 JSON 하나(`revision,api,caddy,username,token`)를 보낸 후 EOF. 타임아웃 15초, 최대 8192바이트, 중복·추가 키 금지. VM은 root 승인 SHA와 `<허용 저장소>@sha256:<소문자 64자리>`를 대조하고 로컬 Docker 소켓의 단건 GHCR pull에만 인증을 보낸다. Ubuntu ARM64용 두 이미지의 실제 digest·리비전 라벨을 확인한다. SSH stdout에는 로컬 검사가 통과했을 때 `LOCAL_SMOKE_OK`만 쓴다. **이 한 줄은 배포 성공이 아니다.**

1. O02의 기존 API·루프백 Caddy·외부 볼륨, Picksy의 Compose 경로·정확한 healthy 컨테이너 4개·443·TLS 200을 확인한다. 프로젝트 충돌이면 중지하지 않는다.
2. root 전용 영속 저널과 부팅 복구/5분 타이머를 먼저 준비한다. 전용 16MiB tmpfs 볼륨의 새 API에 **별개 합성 FTS fixture**를 시드한다. API에는 외부망·호스트 게시 포트·모델 키가 없다. 벡터 색인 성공을 주장하지 않는다.
3. 확인한 Picksy ID만 중지한다. Caddy만 IPv4 TCP 443을 게시한다. TLS-ALPN-01을 사용하므로 포트 80이 필요 없다. 정적 파일과 직접 API 모두 임시 Basic 인증을 요구한다. 인덱스·채팅 등은 프록시에서 막고 본문은 64KB로 제한한다.
4. VM은 CA/호스트명 검증으로 루프백 HTTPS의 익명 401, 인증 UI/health, 합성 업로드 201·원문 읽기, 유료 색인 경로 거부, 합성 문단의 희소 반환과 포트 제한을 확인한다. 외부에서 관리자는 독립적으로 실제 공개 DNS·인증서·접근 제어·업로드·검색·포트 제한을 확인한다. 검색 본문 `{"mode":"sparse","query":"o03sparseproof"}`의 첫 문단은 `o03-fixture.md`/`o03sparseproof`다. 앞서 별도로 업로드한 문서가 벡터화된 것이 아니다.
5. **외부 성공 직후** 사설 관리 채널에서 `sudo /etc/ragcoon-o03/external-pass`. 실패·취소 시 `sudo /etc/ragcoon-o03/restore`. 둘 다 인자가 없으며 배포 계정에는 허용하지 않는다. `external-pass`는 현재 로컬 검사를 통과한 창에만 신호를 전달한다. receiver는 그 신호와 O03 제거, Picksy 네 컨테이너 healthy·기존 인증서 HTTPS 200을 모두 확인해야 0으로 끝난다. 신호 없이 시간이 지나면 실패다. 운영자는 외부에서도 기존 Picksy HTTPS 복구와 O03 종료를 확인하고 로컬 netrc를 즉시 삭제한다.

**실패/정리 경계.** SSH 종료 경로, systemd 종료 후처리, 서비스 시간 제한과 별도 5분 타이머, 재부팅 시의 복구 서비스가 같은 root 저널을 사용한다. 복구 실패는 성공으로 보고하지 않으며 `ragcoon-o03-restore.service`가 재시도한다. 기존 ID가 수동 삭제되거나 기존 서비스가 고장 났으면 운영자가 해결하고 `restore`를 다시 실행한다. 원래 Picksy를 `compose up`으로 재생성하거나 O02의 DB를 고치지 않는다. 아직 공개하지 않은 준비를 취소할 때도 `restore`가 임시 접근 파일을 폐기한다.

- 삭제: `ragcoon-o03` 전용 컨테이너·네트워크·smoke 볼륨, Caddy 임시 인증서/설정 tmpfs, `access.{netrc,hash}`, 복구 완료 후 `/var/lib/ragcoon-o03/session.json`.
- 유지: O02 `ragcoon-private_documents`와 기존 서비스, Picksy 설정·볼륨·인증서·컨테이너 ID, Docker 이미지 캐시, root 승인 설정·수신기·부팅 복구 유닛, `/run/ragcoon-o03`의 잠금/결과 nonce. 전역 prune, DNS/방화벽/sshd 변경, 앱 계정 생성은 하지 않는다. 다음 창은 root가 새 임시 암호를 준비하기 전에는 열리지 않는다.
