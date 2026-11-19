# -*- coding: utf-8 -*-
"""RAW_all 재적재 준비: 로우 시작일(D) 이후 행을 시트 끝에서 제거 → merge.py 가 D부터 새 로우를 다시 적재.
(담당자 규칙: 매주 '당월 1일~전일' 전체를 다시 반영 — 늦게 잡히는 이월 전환 보정)
RAW_all 은 일자 오름차순으로 누적되므로 D 이후 행은 항상 시트 끝에 모여 있다(아니면 중단)."""
import sys, re, datetime as dt
sys.path.insert(0, sys.argv[1])
import xlsxrows as X
from core.xlsxpatch import Workbook
from core.sheetmap import sheet_map
from core import raw as R, config_mars as M
pkg, src, dst, start = sys.argv[1:5]
wb = Workbook(src); sm = sheet_map(wb); x = sm[M.RAW_SHEET]
head, rows, tail = X.split_sheet(wb.text(x))
from core import cells as C
from core.sheetmap import shared_strings
ss = shared_strings(wb); full = wb.text(x)
def rdate(r):
    d = R._to_date(C.row_cells(full, r, ss).get('F'))
    return d.isoformat() if d else None
data = sorted(r for r in rows if r >= 5)
dates = {r: rdate(r) for r in data}
cut = next((r for r in data if dates[r] and dates[r] >= start), None)
assert cut, '제거할 행 없음'
assert all((dates[r] or '9') >= start for r in data if r >= cut), 'D 이후 행이 시트 끝에 모여 있지 않음 — 중단'
removed = [r for r in data if r >= cut]
for r in removed: del rows[r]
body = X.join_sheet(head, rows, tail)
body = re.sub(r'<dimension ref="([A-Z]+\d+):([A-Z]+)\d+"/>', lambda m: '<dimension ref="%s:%s%d"/>' % (m.group(1), m.group(2), cut - 1), body, count=1)
wb.set_text(x, body); wb.save(dst)
print('RAW_all %s 이후 %d행 제거 (행 %d~) → %s 부터 재적재 준비' % (start, len(removed), cut, start))
