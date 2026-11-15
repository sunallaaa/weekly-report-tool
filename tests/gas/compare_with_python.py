# GAS(Code.gs) 결과 ↔ 기존 파이썬 raw_to_report 결과 대조
import io, json, os, sys
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import pandas as pd
import raw_to_report
from naver_ads import raw_builder, searchad

out = json.load(open(sys.argv[1]))

# 1) 주차 라벨 3년치 전부
bad = [(d, l) for d, l in out['weekLabels'] if raw_to_report.week_label(date(*map(int, d.split('-'))))[0] != l]
assert not bad, bad[:5]
print(f"✓ 주차 라벨 {len(out['weekLabels'])}일 전부 일치")

# 2) 서명
assert out['signature'] == searchad.make_signature('1700000000000', 'GET', '/ncc/campaigns', 'dummy-secret==')
print('✓ 서명 일치')

# 3) 주간 리포트 수치: GAS 로우데이터 → 파이썬 build_report
df = pd.DataFrame(out['raw'], columns=raw_builder.RAW_COLS)
wb = raw_to_report.build_report(io.BytesIO(raw_builder.to_raw_xlsx_bytes(df)))
checked = 0
for sheet, groups in out['report'].items():
    ws = wb[sheet]
    tables = raw_to_report._find_group_tables_in_workbook_sheet(ws)
    for grp, rows in groups.items():
        gname = next(g for g in tables if g and grp in g)
        py = {ws.cell(row=r, column=2).value: [ws.cell(row=r, column=c).value for c in range(3, 13)] for r in tables[gname]}
        for w in rows:
            p = py[w['week']]
            for a, b in zip(w['v'], p):
                assert abs(a - b) < 1e-4 * max(1, abs(b)), (sheet, grp, w['week'], w['v'], p)
            checked += 1
print(f'✓ 주간리포트 {checked}개 주차행 × 10개 지표 파이썬과 일치 ({", ".join(out["report"])})')
