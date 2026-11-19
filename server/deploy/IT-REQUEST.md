# IT 요청 게시판에 올릴 내용

아래 내용을 그대로 복사해서 인트라넷 IT 요청 게시판에 올려주세요.
첨부파일 2개도 함께 올리시면 IT팀에서 바로 적용할 수 있습니다.

**첨부할 파일**
1. `IT-docker-compose-snippet.yml`
2. `IT-nginx-snippet.conf`

---

## 제목

[AI 배포서버] Python 앱 배포 요청 - seonhwa_choi / 네이버 광고 대시보드

---

## 본문

안녕하세요, PD 1팀 최선화입니다.

AI 배포서버(ai.adef.co.kr)에 광고주 성과 대시보드(네이버 검색광고·GFA)를 추가로 배포하려고 합니다.
기존에 배포해 주신 PD 1팀 관리 페이지와 같은 방식이며, 이번에는 Python(FastAPI) 앱입니다.

### 1. 요청 사항

| 구분 | 내용 |
|---|---|
| 유저 ID | `seonhwa_choi` |
| 앱 이름 | 네이버 광고 대시보드 |
| 소스 위치 | `/home/seonhwa_choi/naver-dashboard` (업로드 완료 상태입니다) |
| 실행 환경 | Python 3.11 (Dockerfile 포함) — **컨테이너 추가 필요** |
| 희망 접속 주소 | `https://ai.adef.co.kr/seonhwa_choi/naver/` |
| 컨테이너 포트 | 8080 |
| DB | 제 홈의 기존 `seonhwa_choi-mariadb` 컨테이너 사용 (새 DB 불필요) |

**요청 드리는 3가지**

1. **컨테이너 추가** — 첨부한 `IT-docker-compose-snippet.yml` 내용을 제 홈의 `docker-compose.yml` 에 추가 부탁드립니다.
2. **외부 접속 라우팅 추가** — 첨부한 `IT-nginx-snippet.conf` 내용을 `nginx.conf` 에 추가 부탁드립니다.
3. **상시 실행** — compose 설정에 `restart: unless-stopped` 와 헬스체크(`GET /healthz` → `{"ok":true}`)가 포함되어 있습니다.
   앱 안에 매일 07:00 데이터 수집 예약이 들어 있어 **컨테이너가 계속 켜져 있어야** 합니다.

### 2. 참고 사항

- **하위 경로 대응 완료**: `BASE_PATH` 환경변수로 `/seonhwa_choi/naver` 경로를 인식합니다. nginx에서 경로를 떼고 넘기셔도, 그대로 넘기셔도 동작합니다.
- **환경변수**: API 키·DB 접속 정보는 `/home/seonhwa_choi/naver-dashboard/.env` 에 있고 compose 의 `env_file` 로 읽습니다.
- **접근 제어**: 앱 자체에 등록된 이메일만 들어올 수 있는 로그인(이메일 인증코드)과 권한 관리가 있습니다.
- **이후 코드 수정**: 파일 업로드 후 `docker compose up -d --build naver-dashboard` 를 제가 직접 실행하는 방식으로 운영할 예정입니다. 권한상 어렵다면 알려주세요.

### 3. 서버 아웃바운드(외부로 나가는 통신) 허용 확인 요청

| 대상 | 포트 | 용도 | 막히면 생기는 일 |
|---|---|---|---|
| `api.searchad.naver.com` | 443 | 네이버 검색광고 API | 검색광고 데이터 수집 불가 |
| `openapi.naver.com` | 443 | 네이버 데이터랩(검색어 트렌드) | 트렌드 화면만 안 보임 |
| `ai.adef.co.kr` (윤성현님 GFA 게이트웨이) | 443 | GFA 데이터 수집 | GFA 데이터 수집 불가 |
| `oauth2.googleapis.com`, `www.googleapis.com`, `sheets.googleapis.com`, `gmail.googleapis.com` | 443 | 구글 드라이브 저장, 로그인 인증 메일, 예산 시트 읽기 | 로그인·리포트 저장 불가 |
| `pypi.org`, `files.pythonhosted.org` | 443 | 컨테이너 빌드 시 라이브러리 설치 | 컨테이너 생성 불가 |
| Docker Hub (`python:3.11-slim` 이미지) | 443 | 컨테이너 이미지 | 컨테이너 생성 불가 |

### 4. 확인 요청 (2가지)

1. **GFA API 고정 IP**: 네이버 GFA API는 회사 고정 IP에서만 호출됩니다(윤성현님 안내).
   이 서버에서 외부로 나갈 때 **회사 고정 IP로 나가는지** 확인 부탁드립니다.
2. **로그인 사용자 정보 헤더 / 외부 접속**
   - 사내 SSO를 통과한 사용자의 이메일을 앱으로 넘겨주는 헤더가 있다면 **헤더 이름**을 알려주세요. 있으면 팀원은 별도 로그인 없이 바로 쓸 수 있습니다.
   - 추후 **광고주(외부 이메일)** 에게도 이 대시보드를 공유할 예정입니다. 앱 자체 로그인이 있으니, 이 경로(`/seonhwa_choi/naver/`)만 SSO 예외로 둘 수 있는지 검토 부탁드립니다. (지금 당장은 필요 없고, 공유 시점에 다시 요청드리겠습니다.)

설정 완료 후 `https://ai.adef.co.kr/seonhwa_choi/naver/healthz` 접속이 되는지 회신 부탁드립니다.

감사합니다.

PD 1팀 최선화 드림
