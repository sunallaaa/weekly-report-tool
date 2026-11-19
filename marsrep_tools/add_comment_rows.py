# -*- coding: utf-8 -*-
"""코멘트 영역에 신규 채널 섹션 행 삽입 (데이터팀 xlsxrows 사용, 수식·sqref 이동 포함).
사용: add_comment_rows.py <marsrep> <in> <out> <시트> <삽입행> <라벨1> [<라벨2> ...]
각 라벨마다 [빈행, 라벨행, 코멘트행, 코멘트행] 4행 + 마지막 빈행 1행 삽입. 서식은 직전 섹션(빈행·라벨·코멘트) 행 복사."""
import sys, re
sys.path.insert(0, sys.argv[1])
import xlsxrows as X
from core.xlsxpatch import Workbook
from core.sheetmap import sheet_map
pkg, src, dst, sheet, at = sys.argv[1:6]; labels = sys.argv[6:]; at = int(at)
wb = Workbook(src); sm = sheet_map(wb); xml = sm[sheet]
head, rows, tail = X.split_sheet(wb.text(xml))
n = len(labels) * 4 + 1
mp = lambda r: r if r < at else r + n
tpl_blank, tpl_label, tpl_c1, tpl_c2 = rows[at - 5], rows[at - 4], rows[at - 3], rows[at - 2]
new = {}
for r, x in rows.items():
    new[mp(r)] = X.renumber_row(X.map_row_formulas(x, sheet, mp, 'insert'), mp(r))
def mk(tpl, r, text):
    x = X.strip_cache(X.renumber_row(tpl, r))
    x = re.sub(r'<f>.*?</f>', '', x)
    return X.set_cell(x, 'B', text=text)[0] if text is not None else X.set_cell(x, 'B')[0]
r = at
for lab in labels:
    new[r] = mk(tpl_blank, r, None); new[r + 1] = mk(tpl_label, r + 1, lab)
    new[r + 2] = mk(tpl_c1, r + 2, '-'); new[r + 3] = mk(tpl_c2, r + 3, '-'); r += 4
new[r] = mk(tpl_blank, r, None)
body = X.join_sheet(head, new, tail)
i = body.index('<sheetData>'); j = body.index('</sheetData>')
out = X.map_sqref(body[:i], mp) + body[i:j] + X.map_sqref(body[j:], mp)
wb.set_text(xml, out); wb.save(dst)
print('%s %d행 삽입 (%d행부터): %s' % (sheet, n, at, ', '.join(labels)))
