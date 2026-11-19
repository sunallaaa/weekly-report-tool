# 네이버 광고 대시보드 — 회사 서버 버전

회사 AI 배포서버(`ai.adef.co.kr`)에서 돌아가는 대시보드입니다. 검색광고·GFA 데이터를 매일 자동으로 수집하고, 월요일에 주간 리포트용 로우 파일을 만들어 드라이브에 올립니다.
본부 관리페이지와 **같은 방식**(내 홈 폴더에 올림 → IT팀이 컨테이너·주소 연결)으로 배포합니다.

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

## 설치 순서

### 1) 구글 토큰 발급 (내 PC, 1회)
1. [구글 클라우드 콘솔](https://console.cloud.google.com/) → 프로젝트 선택(본부 관리페이지 캘린더 연동에 쓴 프로젝트 재사용 가능)
2. **API 및 서비스 → 라이브러리**: `Google Drive API`, `Gmail API`, `Google Sheets API` 사용 설정
3. **사용자 인증 정보 → OAuth 클라이언트 ID 만들기 → 데스크톱 앱** → 클라이언트 ID·보안 비밀번호 복사
4. 내 PC에서 `python tools/get_google_token.py <클라이언트ID> <보안비밀번호>` 실행 → 브라우저에서 회사 계정으로 허용
5. 화면에 나온 `GOOGLE_REFRESH_TOKEN=...` 값을 `.env` 에 넣기

### 2) `.env` 작성
`.env.example` 을 `.env` 로 복사해 값을 채웁니다. **키 값은 메신저·메일·깃에 올리지 마세요.**
- 검색광고·데이터랩 키: 지금 구글 Apps Script '스크립트 속성'에 넣어둔 값과 같습니다.
- GFA: `GFA_API_KEY`, `GFA_TEAM_NAME` (윤성현님 안내서). 성과 경로 3개는 배포 후 GFA 탐색으로 채웁니다.
- DB 비밀번호: 내 홈 `docker-compose.yml` 의 `MYSQL_ROOT_PASSWORD` 값.
- `APP_SECRET_KEY`: 아무 무작위 문자열 40자 이상.
- 드라이브 폴더·시트 ID, 최초 관리자 이메일: 담당자에게 따로 전달받은 값 (광고주 관련 정보라 저장소에 두지 않음).

### 3) 서버에 올리기
`server` 폴더 전체를 서버의 `/home/seonhwa_choi/naver-dashboard` 로 올리고 `.env` 를 같은 폴더에 둡니다. (`Dockerfile` 이 이 폴더 바로 아래에 있어야 합니다.)

### 4) IT 요청
`deploy/IT-REQUEST.md` 내용을 게시판에 올리고 `deploy/IT-docker-compose-snippet.yml`, `deploy/IT-nginx-snippet.conf` 를 첨부합니다.

### 5) 처음 켠 뒤
```bash
docker exec seonhwa_choi-naver-dashboard python -m app.seed /app/data/contracts.json   # 브랜드검색 계약 일괄 등록 (파일은 data 폴더에만)
```
1. `https://ai.adef.co.kr/seonhwa_choi/naver/login` → 내 이메일로 인증코드 로그인
2. **관리 → 접근 권한**에서 PD-1팀 팀원 이메일 추가
3. **관리 → 브랜드검색 계약** 금액·기간 확인
4. **관리 → 데이터 수집**: 10/1~어제 기간 수집(62일씩 나눠서)
5. **관리 → GFA 탐색**으로 게이트웨이 응답 확인 → 성과 경로·필드를 `.env` 에 넣고 `docker restart seonhwa_choi-naver-dashboard`
6. 병행 검증(2~3주) 동안 `REPORT_ENABLED=1` 로 월요일 로우 내보내기 켜기 → 기존 Mars_Daily·result.csv 와 비교

### 코드 수정 후 반영
```bash
cd /home/seonhwa_choi && docker compose up -d --build naver-dashboard
```

## 개발·테스트 (저장소)
```bash
cd server && pip install -r requirements.txt pytest httpx && python -m pytest -q tests
```
테스트는 외부 API를 가짜로 대체하고 SQLite 임시 DB를 씁니다. 데이터팀 `marsrep` 패키지·광고주 데이터는 저장소에 넣지 않습니다.
