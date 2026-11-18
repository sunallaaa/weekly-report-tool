# -*- coding: utf-8 -*-
"""일자별 소재 export(result.csv) → 소재별 월 누적 1행 (기존 R_GFA(월) 형식). 지표는 단순 합계."""
import csv, sys, collections
src, dst = sys.argv[1:3]
rows = list(csv.reader(open(src, encoding='utf-8-sig')))
h, body = rows[0], rows[1:]
ix = {k: h.index(k) for k in h}
num = ['총비용', '노출수', '클릭수', '구매완료 수', '구매완료 전환매출액']
agg, first, order = {}, {}, []
for r in body:
    k = r[ix['광고 소재 ID']]
    if k not in agg: agg[k] = [0.0] * len(num); first[k] = r; order.append(k)
    for i, n in enumerate(num): agg[k][i] += float(str(r[ix[n]]).replace(',', '') or 0)
dates = sorted(r[ix['기간']] for r in body)
w = csv.writer(open(dst, 'w', encoding='utf-8-sig', newline=''))
w.writerow(h)
for k in order:
    r = list(first[k]); r[ix['기간']] = dates[0]
    for i, n in enumerate(num): r[ix[n]] = ('%d' % agg[k][i]) if agg[k][i].is_integer() else repr(agg[k][i])
    if '구매완료 광고수익률(%)' in ix:
        c, v = agg[k][0], agg[k][4]; r[ix['구매완료 광고수익률(%)']] = '%.2f' % (v / c * 100) if c else '0'
    w.writerow(r)
print('일자별 %d행 → 소재별 %d행 (%s ~ %s)' % (len(body), len(order), dates[0], dates[-1]))
