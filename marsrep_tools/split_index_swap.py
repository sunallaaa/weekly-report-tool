# -*- coding: utf-8 -*-
"""1회성: 2주차 이전 구조(R_GFA(월) P=소재 이름, Q=소재 ID, R=구분)를 split_index 가 기대하는
(P=ID, Q=이름, R=구분) 순서로 읽도록 read_index 만 교체하고, 나머지는 데이터팀 split_index.py 그대로 실행."""
import os, re, sys
sys.path.insert(0, sys.argv.pop(1))   # marsrep 경로
import split_index as S
from core import cells as C

def _cell(body, col, r):
    cm = re.search(r'<c r="%s%d"([^>]*?)(?:/>|>(.*?)</c>)' % (col, r), body, re.S)
    if not cm: return None
    v = re.search(r'<v>([^<]*)</v>', cm.group(2) or '')
    it = re.search(r'<is>.*?<t[^>]*>(.*?)</t>', cm.group(2) or '', re.S)
    if it: return ('t', it.group(1))
    if v and 't="s"' in cm.group(1): return ('s', int(v.group(1)))
    if v: return ('t', v.group(1))
    return None

def read_index_swapped(wb, smap, ss):
    t = wb.text(smap[S.SRC_SHEET]); rows = C.rows(t); out = []
    for r in sorted(rows):
        if r < 4: continue
        body = rows[r][1]
        q = _cell(body, 'Q', r)
        if not q: continue
        idv = ss[q[1]] if q[0] == 's' else q[1]
        try: pid = float(str(idv).replace(',', ''))
        except ValueError: continue
        mon = C.row_cells(t, r, ss).get('B')
        out.append((pid, _cell(body, 'P', r), _cell(body, 'R', r), str(mon) if isinstance(mon, str) else ''))
    return out

S.read_index = read_index_swapped
sys.exit(S.main(sys.argv[1:]))
