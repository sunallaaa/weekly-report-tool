# 한국마즈 네이버 광고 리포트 자동화

| 페이지 | 기능 |
|---|---|
| **Report Automation Program** (app.py) | 로우데이터/리포트 엑셀 업로드 → 주간 리포트 업데이트 · 코멘트 자동 작성 · 성과 대시보드 |
| **📡 네이버 API 대시보드** (pages/) | 검색광고 API 실시간 조회 · GFA 데이터 연동 · 데이터랩 키워드 트렌드 · 로우데이터/주간 리포트 엑셀 출력 |

## 1. API 키 등록 (깃허브에는 절대 올리지 않기)
- **로컬 실행**: `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml`로 복사 후 값 입력 (`.gitignore` 처리됨)
- **Streamlit Cloud**: 앱 → Settings → Secrets에 같은 내용 붙여넣기

| 키 | 용도 | 상태 |
|---|---|---|
| `NAVER_SA_CUSTOMER_ID / API_KEY / SECRET_KEY` | 검색광고 API (캠페인·광고그룹 일별 통계) | ✅ 바로 사용 |
| `NAVER_CLIENT_ID / SECRET` | 네이버 개발자센터 → 데이터랩 검색어트렌드 | ✅ 개발자센터 앱의 '사용 API'에 데이터랩(검색어트렌드) 추가 필요 |
| `GFA_API_KEY` (+ `GFA_API_BASE_URL`, `GFA_KEY_HEADER`, `GFA_STATS_PATH`) | GFA 디스플레이 API | ⏳ 규격 미공개 → agency_cs@navercorp.com 에 기술 가이드 요청. 받기 전엔 GFA 리포트 파일 업로드로 사용 |

## 2. 실행
```bash
pip install -r requirements.txt
streamlit run app.py      # 왼쪽 메뉴에서 '네이버 API 대시보드' 선택
python -m pytest tests    # API 연동부 테스트 (네이버 서버 흉내 낸 가짜 응답으로 검증)
```

## 3. 주간 리포트 흐름
1. 검색광고 탭 → 기간 '지난주 (월~일)' 조회
2. GFA 탭 → GFA 광고관리자 일별 리포트(CSV/XLSX) 업로드 (컬럼 자동 매칭, 필요시 직접 지정)
3. 리포트 출력 탭 → 로우데이터 엑셀 다운로드, 또는 지난 리포트를 올려 서식 그대로 숫자 업데이트
4. 메인 페이지 ③ 모드에서 코멘트 자동 작성

## 4. 확인이 필요한 사항
- 검색광고 API 광고비(`salesAmt`)의 VAT 포함 여부 → 광고관리 화면 '총비용'과 1회 대조 (사이드바의 ÷1.1 옵션)
- BS_Pet 분류 규칙: 캠페인명에 `PET`·`브랜드검색`, 광고그룹명에 브랜드명(그리니즈/시저/템테이션/쉬바/위스카스)이 들어가야 함
- GFA 캠페인명은 `(Mars Conf)` / `(Mars Pet)`으로 시작해야 해당 시트에 반영됨
