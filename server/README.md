# 네이버 광고 대시보드 — 회사 서버 버전

회사 AI 배포서버(`ai.adef.co.kr`)에서 돌아가는 대시보드입니다. 검색광고·GFA 데이터를 매일 자동으로 수집하고, 월요일에 주간 리포트용 로우 파일을 만들어 드라이브에 올립니다.
본부 관리페이지와 **같은 방식**(내 홈 폴더의 docker-compose·nginx 설정에 추가)으로 배포합니다.

```
[매일 07:00] 검색광고 API ─┐
             GFA 게이트웨이 ─┼→ 서버 DB(MariaDB) ─→ 대시보드  https://ai.adef.co.kr/seonhwa_choi/naver/
[월 13:30]   당월 1일~전일 재수집 → Mars_Daily·result.csv 형식으로 내보내기 → 드라이브 '로우업로드'
                                   (병행 검증 후) → 엑셀 리포트 작성 → 드라이브 '위클리'
```

## 화면

| 주소 | 누가 | 내용 |
|---|---|---|
| `/seonhwa_choi/naver/` | 로그인한 사용자 | 실시간 성과(매체·브랜드검색 브랜드·GFA 채널·소재 TOP3), 키워드 트렌드 |
| `/seonhwa_choi/naver/admin` | 관리자 | **사용자 추가·제거**, 브랜드검색 계약, 수동 수집, 월예산 확인, GFA 탐색, 변경 기록 |
| `/seonhwa_choi/naver/login` | 누구나 | 이메일 인증코드 로그인 |

### 권한

| 역할 | 볼 수 있는 것 | 용도 |
|---|---|---|
| 관리자 | 전체 + 관리 화면 | 팀장 |
| 팀원 | 지정 광고주(기본 전체) | PD-1팀 |
| 광고주 | 지정 광고주만, 관리 메뉴 없음 | 추후 광고주 공유 |

- 등록된 이메일만 로그인할 수 있습니다. 미등록 이메일에는 인증 메일이 가지 않습니다.
- **비활성**으로 바꾸거나 삭제하면 바로 접속이 막힙니다. 모든 변경은 '변경 기록'에 남습니다.
- `.env` 의 `BOOTSTRAP_ADMIN_EMAILS` 에 적은 이메일은 목록이 비어 있어도 관리자로 들어올 수 있습니다(첫 로그인용).

## 설치 순서 (내 서버 계정에서 직접 — IT 요청 불필요)

PD 관리페이지(`pd-dashboard`)를 올린 것과 같은 방법입니다. 내 홈 폴더의 `docker-compose.yml` 과 `nginx.conf` 에 한 블록씩 추가합니다.

### 1) 구글 토큰 발급 (1회)
`docs/구글토큰_발급가이드.md` 참고 → `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`

### 2) 파일 올리기
1. `naver-dashboard.zip` 을 서버 `/home/seonhwa_choi/naver-dashboard/` 에 풀기 (`Dockerfile` 이 이 폴더 바로 아래에 오도록)
2. 같은 폴더에 `.env` 만들기 (`.env.example` 복사 후 값 채우기). **키 값은 메신저·메일·깃에 올리지 마세요.**
3. `data` 폴더를 만들고 `contracts.json`(브랜드검색 계약)을 넣기

### 3) 설정 두 곳에 블록 추가
- `/home/seonhwa_choi/docker-compose.yml` → `deploy/compose-snippet.yml` 내용을 `services:` 아래에 추가
- `/home/seonhwa_choi/nginx.conf` → `deploy/nginx-snippet.conf` 내용을 기존 `/seonhwa_choi/dashboard/` 블록 아래에 추가

### 4) 실행
```bash
cd /home/seonhwa_choi
docker compose up -d --build naver-dashboard          # 처음 1~3분 (라이브러리 설치)
docker compose restart web                             # nginx 설정 다시 읽기
docker logs --tail 30 seonhwa_choi-naver-dashboard     # 오류 확인
docker exec seonhwa_choi-naver-dashboard python -m app.seed /app/data/contracts.json
```
브라우저에서 `https://ai.adef.co.kr/seonhwa_choi/naver/healthz` → `{"ok":true}` 가 보이면 성공.

### 5) 처음 켠 뒤
1. `https://ai.adef.co.kr/seonhwa_choi/naver/` → 내 이메일로 인증코드 로그인
2. **관리 → 접근 권한**에서 PD-1팀 팀원 이메일 추가
3. **관리 → 브랜드검색 계약** 금액·기간 확인
4. **관리 → 데이터 수집**: 10/1~어제 (62일씩 나눠서)
5. **관리 → GFA 탐색**으로 게이트웨이 응답 확인 → 성과 경로·필드를 `.env` 에 넣고 `docker compose up -d naver-dashboard`
6. 병행 검증 동안 `REPORT_ENABLED=1` → 월요일 로우 내보내기를 기존 Mars_Daily·result.csv 와 비교

### 문제가 생기면
| 증상 | 확인 |
|---|---|
| `healthz` 가 502 | `docker ps` 로 컨테이너가 떠 있는지, `docker logs` 오류 |
| 빌드 중 pip 오류 | 서버에서 pypi.org 접속이 막힌 경우 → 이때만 IT 문의 |
| 수집 시 검색광고/GFA 연결 오류 | 관리 화면 작업 기록의 오류 문구를 캡처해 전달 |

### 코드 수정 후 반영
```bash
cd /home/seonhwa_choi && docker compose up -d --build naver-dashboard
```

## 개발·테스트 (저장소)
```bash
cd server && pip install -r requirements.txt pytest httpx && python -m pytest -q tests
```
테스트는 외부 API를 가짜로 대체하고 SQLite 임시 DB를 씁니다. 데이터팀 `marsrep` 패키지·광고주 데이터는 저장소에 넣지 않습니다.
