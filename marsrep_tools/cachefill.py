# -*- coding: utf-8 -*-
"""당주차 캐시 기록 + 브랜드검색 CPT(기간 구매) 광고비 규칙 적용.

데이터팀 marsrep 패키지(블록 탐지·RAW 파싱·집계·검증)를 그대로 쓰고, 빠져 있던 두 가지만 보탠다.
 1) BS_Pet 일자별 표(당월 행)의 브랜드 광고비 수식
      IF(노출>0, 일할, )  →  IF(날짜<=MAX(RAW_all!F:F), 일할, )   (활성 6개 브랜드만, 종료 브랜드 제외)
    → 노출이 없어도 계약 기간(=최종 적재일까지) 매일 광고비 적용. 당월 행 캐시도 함께 갱신.
 2) 활성 블록의 노출창(최근 5주) C~L 및 WoW 행 캐시를 RAW 재집계값으로 기록.
    BS 광고비: 당월 일자는 CPT(매일), 전월 일자는 원본 규칙(노출일) 유지.
사용: python3 cachefill.py <marsrep경로> <입력.xlsx> <출력.xlsx>
"""
import sys, re, datetime as dt
sys.path.insert(0, sys.argv[1])
from core import config_mars as M, verify as V, raw as R, aggregate as A
from core.xlsxpatch import Workbook
from core.sheetmap import shared_strings, sheet_map
import weekly as W

M.CAMP_OF_LABEL.setdefault('쇼핑고객확장', {'NAVER GFA_Conf': '(Mars Conf) 쇼핑고객확장',
                                          'NAVER GFA_Pet': '(Mars Pet) 쇼핑고객확장'})
ACTIVE_IMP = {'M': '그리니즈', 'W': '시저', 'AG': '템테이션', 'AQ': '쉬바', 'BA': '위스카스', 'BK': '뉴트로'}
# 브랜드검색 계약 시작일 (CPT: 이 날부터 노출과 무관하게 매일 일할 광고비). 이전 기간은 원본 규칙 유지.
CONTRACT_START = {'그리니즈': '2026-10-02', '시저': '2026-10-02', '템테이션': '2026-10-02',
                  '쉬바': '2026-10-02', '위스카스': '2026-10-02', '뉴트로': '2026-09-30'}
EPOCH = dt.date(1899, 12, 30)
d2s = lambda d: (EPOCH + dt.timedelta(days=int(d))).isoformat()

def set_cache(wb, xml, ref, val):
    t = wb.text(xml)
    m = (re.search(r'<c r="%s"([^/>]*)/>' % ref, t) or re.search(r'<c r="%s"([^>]*)>(.*?)</c>' % ref, t, re.S))
    if not m: return False
    attr = re.sub(r'\s*t="[^"]*"', '', m.group(1)); body = m.group(2) if m.lastindex and m.lastindex >= 2 else ''
    f = re.search(r'<f[^>]*/>|<f[^>]*>.*?</f>', body or '', re.S)
    v = ('%d' % val) if float(val).is_integer() else repr(float(val))
    new = '<c r="%s"%s>%s<v>%s</v></c>' % (ref, attr, f.group(0) if f else '', v)
    wb.set_text(xml, t[:m.start()] + new + t[m.end():]); return True

def main(pkg, src, dst):
    wb = Workbook(src); smap = sheet_map(wb); ss = shared_strings(wb)
    rows = R.parse(wb, smap[M.RAW_SHEET]); maxd = max(r['date'] for r in rows)
    cur_month = maxd[:7]
    print('최종 적재일', maxd)
    # ── 1) BS 일자별 표: 당월 행 CPT 수식 ──
    bx = smap['NAVER BS_Pet']; t = wb.text(bx)
    pat = re.compile(r'(<c r="([A-Z]+)(\d+)"[^>]*><f t="shared" ref="([A-Z]+)(\d+):[A-Z]+(\d+)" si="\d+">)'
                     r'IF\(\$([A-Z]+)(\d+)&gt;0,\$([A-Z]+)\$(\d+),\)(</f>)')
    changes = []
    for m in pat.finditer(t):
        col, r0, r1, impc, bcol, brow = m.group(2), int(m.group(3)), int(m.group(6)), m.group(7), m.group(9), m.group(10)
        bm = re.search(r'<c r="B%d"[^>]*><v>([^<]+)</v>' % r0, t)
        if impc not in ACTIVE_IMP: continue
        em = re.search(r'<c r="B%d"[^>]*><v>([^<]+)</v>' % r1, t)
        if not em or d2s(float(em.group(1))) < CONTRACT_START[ACTIVE_IMP[impc]]: continue
        changes.append((m, col, r0, r1, bcol, brow))
    daily_by = {}
    for m, col, r0, r1, bcol, brow in reversed(changes):
        sd = dt.date.fromisoformat(CONTRACT_START[ACTIVE_IMP[m.group(7)]])
        newf = m.group(1) + ('IF(AND($B%d&gt;=DATE(%d,%d,%d),$B%d&lt;=MAX(RAW_all!$F:$F)),$%s$%s,IF($%s%d&gt;0,$%s$%s,))'
                             % (r0, sd.year, sd.month, sd.day, r0, bcol, brow, m.group(7), r0, bcol, brow)) + m.group(11)
        t = t[:m.start()] + newf + t[m.end():]
    wb.set_text(bx, t)
    for m, col, r0, r1, bcol, brow in changes:
        daily = float(re.search(r'<c r="%s%s"[^>]*><v>([^<]+)</v>' % (bcol, brow), t).group(1))
        daily_by[ACTIVE_IMP[m.group(7)]] = daily
        st = CONTRACT_START[ACTIVE_IMP[m.group(7)]]; nset = 0
        for r in range(r0, r1 + 1):
            bm = re.search(r'<c r="B%d"[^>]*><v>([^<]+)</v>' % r, wb.text(bx))
            if not bm: continue
            ds = d2s(float(bm.group(1)))
            if ds < st: continue                     # 계약 이전: 원본 수식·캐시 유지
            set_cache(wb, bx, '%s%d' % (col, r), daily if ds <= maxd else 0); nset += 1
        print('  CPT 수식 %s%d:%s%d (%s, %s~, 일 %s원, 캐시 %d행)' % (col, r0, col, r1, ACTIVE_IMP[m.group(7)], st, format(daily, ',.0f'), nset))
    # 일자별 G(일 합계) 캐시 재계산
    for gm in re.finditer(r'<c r="G(\d+)"[^>]*><f t="shared" ref="G(\d+):G(\d+)"[^>]*>Q\d+\+AA', wb.text(bx)):
        for r in range(int(gm.group(2)), int(gm.group(3)) + 1):
            bm = re.search(r'<c r="B%d"[^>]*><v>([^<]+)</v>' % r, wb.text(bx))
            if not bm or d2s(float(bm.group(1))) < min(CONTRACT_START.values()): continue
            s = 0.0
            for c in ('Q', 'AA', 'AK', 'AU', 'BE', 'BO', 'BY'):
                v = re.search(r'<c r="%s%d"[^>]*>(?:<f[^>]*/>|<f[^>]*>.*?</f>)?<v>([^<]+)</v>' % (c, r), wb.text(bx), re.S)
                s += float(v.group(1)) if v else 0.0
            set_cache(wb, bx, 'G%d' % r, s)
    # ── 2) 노출창 캐시 ──
    imp_by_day = {}
    for r in rows:
        if r['camp'].startswith('M_브랜드검색_'):
            k = (r['camp'].replace('M_브랜드검색_', ''), r['date']); imp_by_day[k] = imp_by_day.get(k, 0) + r['imp']
    def bs_cost(brands, lo, hi):
        tot, d = 0.0, dt.date.fromisoformat(lo)
        while d.isoformat() <= hi:
            ds = d.isoformat()
            for b in brands:
                daily = M.BS_BRANDS[b] / M.BS_DAILY_DIVISOR
                if ds >= CONTRACT_START[b]: tot += daily if ds <= maxd else 0
                elif imp_by_day.get((b, ds), 0) > 0: tot += daily
            d += dt.timedelta(days=1)
        return tot
    blocks = W.discover(wb, smap, ss)
    cur = W.current_week(blocks, rows) if hasattr(W, 'current_week') else None
    n = 0
    for b in blocks:
        if not b.active: continue
        pred, fixed, who = V._resolve(b, 7)
        if pred is None: print('  미매핑', b.label); continue
        rs = sorted(r for r in b.weeks if b.weeks[r]['start'].isoformat() <= maxd)[-5:]
        vals = {}
        for r in rs:
            lo, hi = b.weeks[r]['start'].isoformat(), b.weeks[r]['end'].isoformat()
            fc = None
            if b.sheet == 'NAVER BS_Pet':
                fc = bs_cost([who] if who in M.BS_BRANDS else list(M.BS_BRANDS), lo, hi)
            e = A.agg(rows, pred, lo, hi, fc)
            row = dict(C=e['imp'], D=e['clk'], E=e['ctr'], F=e['cpc'], G=e['cost'], H=e['cv'], I=e['rev'],
                       J=e['cpa'], K=e['cvr'], L=e['roas'])
            vals[r] = row
            for c, v in row.items(): n += set_cache(wb, b.sheet_xml, '%s%d' % (c, r), v)
        crow = max(rs)
        if crow - 1 in vals:
            for c in b.cols:
                p, q = vals[crow - 1][c], vals[crow][c]
                set_cache(wb, b.sheet_xml, '%s%d' % (c, b.wow), (q / p - 1) if p else 0)
    print('  노출창 캐시 %d셀 기록' % n)
    wb.set_fullcalc(); wb.save(dst); print('OUT', dst)

if __name__ == '__main__':
    main(*sys.argv[1:4])
