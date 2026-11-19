# -*- coding: utf-8 -*-
"""소재 export 의 ID 중 대상 파일 GFA소재_index 에 없는 것을, 참조 파일(이전 보고서)의 GFA소재_index 값으로 등록.
참조에도 없으면 등록하지 않고 목록만 출력(담당자 지정 필요)."""
import sys, re, csv
sys.path.insert(0, sys.argv[1])
from core.xlsxpatch import Workbook
from core.sheetmap import sheet_map, shared_strings
from core import cells as C
import split_index as S
pkg, src, ref, csvf, dst = sys.argv[1:6]
def index_of(path):
    wb = Workbook(path); sm = sheet_map(wb); ss = shared_strings(wb); t = wb.text(sm['GFA소재_index']); out = {}
    for r in sorted(C.rows(t)):
        if r < 4: continue
        g = C.row_cells(t, r, ss)
        if isinstance(g.get('A'), float): out['%d' % g['A']] = (g.get('B') or '', g.get('C') or '', g.get('D') or '')
    return wb, sm, out
wb, sm, have = index_of(src); _, _, refidx = index_of(ref)
ids = {}
for row in list(csv.reader(open(csvf, encoding='utf-8-sig')))[1:]:
    ids[row[1].strip()] = row[0].strip()
miss = [i for i in ids if i not in have]
x = sm['GFA소재_index']; r = wb.last_row(x); add = []; unknown = []
for i in miss:
    if i in refidx:
        r += 1; nm, cat, mon = refidx[i]
        add.append('<row r="%d">%s%s%s%s%s</row>' % (r, S.num('A%d' % r, float(i)), S.istr('B%d' % r, nm or ids[i]),
                   S.istr('C%d' % r, cat), S.istr('D%d' % r, mon), S.istr('E%d' % r, '이전 보고서 인덱스에서 승계')))
    else: unknown.append((i, ids[i]))
if add: wb.append_rows(x, add); wb.set_dimension(x, 'A1:E%d' % r)
wb.save(dst)
print('미등록 %d건 → 이전 보고서 값으로 등록 %d건 · 담당자 지정 필요 %d건' % (len(miss), len(add), len(unknown)))
for u in unknown: print('  지정 필요', u)
