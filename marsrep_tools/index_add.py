# -*- coding: utf-8 -*-
"""GFA소재_index 에 신규 소재 행 추가 (사용자 지정 구분). split_index 와 같은 셀 형식."""
import sys, re
sys.path.insert(0, sys.argv[1])
from core.xlsxpatch import Workbook
from core.sheetmap import sheet_map
import split_index as S
src, dst, cid, name, cat, mon, note = sys.argv[2:9]
wb = Workbook(src); smap = sheet_map(wb); x = smap['GFA소재_index']
t = wb.text(x)
if re.search(r'<c r="A\d+"[^>]*><v>%s</v>' % re.escape(cid), t): sys.exit('이미 등록된 ID')
r = wb.last_row(x) + 1
row = '<row r="%d">%s%s%s%s%s</row>' % (r, S.num('A%d' % r, float(cid)), S.istr('B%d' % r, name),
                                        S.istr('C%d' % r, cat), S.istr('D%d' % r, mon), S.istr('E%d' % r, note))
wb.append_rows(x, row)
wb.set_dimension(x, 'A1:E%d' % r)
wb.save(dst); print('추가 r%d' % r, cid, name, '→', cat)
