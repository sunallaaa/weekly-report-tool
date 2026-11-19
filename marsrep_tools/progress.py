# -*- coding: utf-8 -*-
"""헤더 진척률 캐시 갱신: 기준일(TODAY()-1 → 최종적재일+1 로 캐시), 기간 진척률, 월 누적 광고비·현재 진척률."""
import sys, re, datetime as dt
sys.path.insert(0, sys.argv[1])
sys.argv = sys.argv[:1] + sys.argv[2:]
from core import config_mars as M, raw as R
from core.xlsxpatch import Workbook
from core.sheetmap import sheet_map
sys.path.insert(0, sys.argv[0].rsplit('/', 1)[0])
from cachefill import set_cache
src, dst = sys.argv[1:3]
wb = Workbook(src); smap = sheet_map(wb); rows = R.parse(wb, smap[M.RAW_SHEET])
maxd = max(r['date'] for r in rows); month = maxd[:7]
base = dt.date.fromisoformat(maxd) + dt.timedelta(days=1)
serial = (base - dt.date(1899, 12, 30)).days
elapsed = (base - dt.date.fromisoformat(month + '-01')).days / 30
mtd = lambda pref: sum(r['cost'] for r in rows if r['date'][:7] == month and r['camp'].startswith(pref)) / 1.1
days_in = int(maxd[8:10])
bs = M.BS_DAY_TOTAL * days_in
spec = [('NAVER GFA_Conf', 'P3', 'O4', 'O5', mtd('(Mars Conf)')),
        ('NAVER GFA_Pet', 'P3', 'O4', 'O5', mtd('(Mars Pet)')),
        ('NAVER BS_Pet', 'S3', 'R4', 'R5', bs)]
for sh, dref, eref, pref, cost in spec:
    x = smap[sh]; t = wb.text(x)
    fm = re.search(r'<c r="%s"[^>]*><f>\$?G\$?(\d+)/\$?M\$?(\d+)</f>' % pref, t)   # 현재 진척률 = 월누적 광고비 / 예산
    gref, bref = 'G' + fm.group(1), 'M' + fm.group(2)
    from core import cells as CC
    from core.sheetmap import shared_strings
    sm_ = re.search(r'<c r="%s"[^>]*><f>SUM\(M(\d+):M(\d+)\)</f>' % bref, t)
    budget = sum((CC.row_cells(t, rr).get('M') or 0) for rr in range(int(sm_.group(1)), int(sm_.group(2)) + 1)
                 if isinstance(CC.row_cells(t, rr).get('M'), float))
    set_cache(wb, x, bref, budget)
    set_cache(wb, x, dref, serial); set_cache(wb, x, eref, elapsed)
    set_cache(wb, x, gref, cost); set_cache(wb, x, pref, cost / budget)
    print('%s 기준일 %s · 기간경과 %.1f%% · 월누적 광고비 %s / 예산 %s = %.1f%%'
          % (sh[6:], base, elapsed * 100, format(cost, ',.0f'), format(budget, ',.0f'), cost / budget * 100))
wb.set_fullcalc(); wb.save(dst)
