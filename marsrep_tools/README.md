# marsrep 보조 도구 (마즈 위클리 자동화)

데이터팀 `marsrep` 패키지(드라이브 `마즈_리포트_자동화.zip` 문서에서 `restore.py`로 복원)에 없는 단계를 보완하는 스크립트.
패키지 원본은 수정하지 않는다. 데이터(로우·보고서)는 저장소에 넣지 않는다.

| 순서 | 스크립트 | 역할 |
|---|---|---|
| 0 | `restore.py <출력> <소스문서.md...>` | 문서의 `## 파일:` 블록 → marsrep 패키지 복원 |
| 1 | `prep.py <기준.xlsx> <Mars_Daily.xlsx> <작업폴더>` | 명칭 치환(쇼핑프로모션→쇼핑고객확장) + 로우 매체별 분리 |
| 2 | `split_index_swap.py <marsrep> <xlsx> --apply --out` | (1회성) 2주차 이전 구조의 소재 인덱스 분리 |
| 3 | `index_add.py <marsrep> <in> <out> <ID> <소재명> <구분> <월> <비고>` | 신규 소재 인덱스 등록 (담당자 지정값) |
| 4 | `monthly_creative.py <result.csv> <out.csv>` | 일자별 소재 export → 소재별 월 누적 |
| 5 | marsrep `merge.py` → `weekly.py --comments --apply` | 적재 · 주차 전환 · 코멘트 |
| 6 | `cachefill.py <marsrep> <in> <out>` | 브랜드검색 CPT(기간 구매) 광고비 수식 + 당주차·노출창 캐시 기록 |
| 7 | `progress.py <marsrep> <in> <out>` | 헤더 진척률 캐시 갱신 |

브랜드검색 광고비 규칙: CPT 상품 → 계약 시작일(뉴트로 9/30, 그 외 10/2)부터 노출과 무관하게 매일 (월예산/30) 적용. 계약 이전 기간은 원본 수식 유지 (2026-11-19 담당자 확정).
