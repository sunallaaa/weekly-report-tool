# -*- coding: utf-8 -*-
"""대시보드 집계 — gas/Code.gs 의 apiDashboard 와 같은 결과 구조."""
import time
from datetime import timedelta

from sqlalchemy import select

from . import collectors, db
from . import settings as S
from .rules import (MEDIA_LABEL, SA_MEDIA, Contract, apply_bs_cpt, brand_of, category_of, classify_gfa, device_of,
                    group_by, kpi, media_key, total, vat_exclude)

LIVE_CACHE_SEC = 300
_live = {}


def load_contracts(advertisers):
    q = select(db.contracts)
    if advertisers != ['*']:
        q = q.where(db.contracts.c.advertiser.in_(advertisers))
    with db.engine().connect() as c:
        return [Contract(r.brand, r.start, r.end, r.amount) for r in c.execute(q)]


def live_today(force=False):
    """오늘 검색광고 실시간 (5분 캐시). GFA는 당일 데이터 확정 전이라 제외."""
    t = collectors.today()
    hit = _live.get(t)
    if hit and not force and time.time() - hit['ts'] < LIVE_CACHE_SEC:
        return hit
    # 오늘 구매완료 보고서는 다음날 확정 → 오늘 실시간은 /stats 전체전환으로 가볍게 조회
    rows = collectors.sa_rows(t, t, tsv=None, fetch_tsv=False) if S.settings.sa_ready else []
    for r in rows:
        r['advertiser'] = S.settings.advertiser
    hit = {'rows': rows, 'ts': time.time(), 'at': time.strftime('%H:%M:%S', time.localtime())}
    _live.clear()
    _live[t] = hit
    return hit


def build(advertisers, since, until, media='ALL', name_filter='', vat=True, force=False, live=True):
    t = collectors.today()
    rows = [r for r in db.read_raw(advertisers, since, min(until, t - timedelta(days=1)))]
    live_at, notice = None, []
    if live and until >= t:
        try:
            lv = live_today(force)
            if advertisers == ['*'] or S.settings.advertiser in advertisers:
                rows += lv['rows']
            live_at = lv['at']
        except Exception as e:
            notice.append('오늘 실시간 조회 실패: {}'.format(e))
    if vat:
        rows = vat_exclude(rows)
    contracts = load_contracts(advertisers)
    rows, missing = apply_bs_cpt(rows, contracts, since, min(until, t))
    if missing:
        notice.append('브랜드검색 계약 미등록: {} → 관리 > 브랜드검색 계약에 입력하면 광고비·ROAS가 계산됩니다.'
                      .format(', '.join(missing)))
    if media == 'SA':
        rows = [r for r in rows if r['media'] in SA_MEDIA]
    elif media == 'GFA':
        rows = [r for r in rows if r['media'] == 'GFA']
    if name_filter:
        f = name_filter.lower()
        rows = [r for r in rows if f in str(r['campaign']).lower()]

    def ch(r):
        c = classify_gfa(r['campaign'], r['adgroup'], r['creative']) if r['media'] == 'GFA' else None
        return c[0].replace('NAVER ', '') + '|' + c[1] if c else None

    def by_cost(lst, n=None):
        lst = sorted(lst, key=lambda x: -x['cost'])
        return lst[:n] if n else lst

    creatives = []
    for g in group_by([r for r in rows if r['media'] == 'GFA' and r['creative'] and ch(r)],
                      lambda r: ch(r) + '|' + r['creative'] + '|' + (r['creative_id'] or '')):
        sheet, channel, cr, cid = g['key'].split('|', 3)
        creatives.append(dict(g, sheet=sheet, channel=channel, creative=cr, id=cid))

    data_until = max((r['date'] for r in rows if r['date'] < t), default=None)

    # ── 개편 화면용: 매체 › 카테고리 › 캠페인 › 광고그룹 (BS 는 브랜드 › 디바이스) ──
    def mk(r):
        return media_key(r['media']), category_of(r['media'], r['campaign'], r['adgroup'])

    def leaf_key(r):
        m, c = mk(r)
        if m == 'BS':
            b = brand_of(r['adgroup'], contracts) or str(r['adgroup']).split('_')[0]
            return '|'.join([m, c, r['campaign'], b, device_of(r['adgroup'])])
        return '|'.join([m, c, r['campaign'], r['adgroup'], ''])

    leaves = []
    for g in group_by(rows, leaf_key):
        m, c, camp, grp, dev = g['key'].split('|', 4)
        leaves.append(dict(g, media=m, cat=c, campaign=camp, group=grp, device=dev))
    daily_by = []
    for g in group_by(rows, lambda r: '|'.join([str(r['date'])] + list(mk(r)))):
        d, m, c = g['key'].split('|')
        daily_by.append(dict(g, date=d, media=m, cat=c))
    today_by = []
    if live_at:
        for g in group_by([r for r in rows if r['date'] == t], lambda r: '|'.join(mk(r))):
            m, c = g['key'].split('|')
            today_by.append(dict(g, media=m, cat=c))
    meta = db.read_creative_meta(advertisers, 'GFA')
    gfa_creatives = []
    for g in group_by([r for r in rows if r['media'] == 'GFA' and (r['creative'] or r['creative_id'])],
                      lambda r: '|'.join([mk(r)[1], r['campaign'], r['adgroup'], r['creative'], r['creative_id'] or '',
                                          (ch(r) or '|').split('|', 1)[1]])):
        c, camp, grp, cr, cid, chn = g['key'].split('|', 5)
        gfa_creatives.append(dict(g, cat=c, campaign=camp, group=grp, creative=cr, id=cid, channel=chn,
                                  image=(meta.get(cid) or {}).get('image', '')))
    gfa_creatives = by_cost(gfa_creatives, 1000)
    return {
        'leaves': leaves, 'dailyBy': daily_by, 'todayBy': today_by, 'gfaCreatives': gfa_creatives,
        'channels': [dict(x, cat='Conf' if x['key'].startswith('GFA_Conf') else 'Pet') for x in by_cost(group_by(rows, ch))],
        'budget': budget_status(advertisers, since, until, vat, contracts),
        'since': str(since), 'until': str(until), 'liveAt': live_at, 'notice': ' '.join(notice),
        'dataUntil': str(data_until) if data_until else None,
        'total': total(rows),
        'today': total([r for r in rows if r['date'] == t]) if live_at else None,
        'daily': sorted(group_by(rows, lambda r: str(r['date'])), key=lambda x: x['key']),
        'byMedia': by_cost(group_by(rows, lambda r: MEDIA_LABEL.get(r['media'], r['media']))),
        'byBrand': by_cost(group_by([r for r in rows if r['media'] == 'NaverBS'],
                                    lambda r: brand_of(r['adgroup'], contracts) or r['adgroup'])),
        'byChannel': by_cost(group_by(rows, ch)),
        'byCampaign': by_cost(group_by(rows, lambda r: r['media'] + '|' + r['campaign'])),
        'byGroup': by_cost(group_by(rows, lambda r: r['media'] + '|' + r['campaign'] + '|' + r['adgroup']), 300),
        'byCreative': by_cost(group_by([r for r in rows if r['creative']],
                                       lambda r: r['media'] + '|' + r['campaign'] + '|' + r['adgroup'] + '|' +
                                       r['creative']), 300),
        'creatives': creatives,
    }


def kpi_empty():
    return kpi({'imps': 0, 'clicks': 0, 'cost': 0, 'conv': 0, 'revenue': 0})


_budget_cache = {}


def budget_status(advertisers, since, until, vat, contracts):
    """조회 기간이 한 달 안이면 그 달 '월예산'(구글시트)과 1일~어제 광고비(매체·카테고리별) 비교"""
    if (since.year, since.month) != (until.year, until.month) or not S.settings.google_ready \
            or not S.settings.budget_sheet_id:
        return None
    from . import google
    month = since.strftime('%Y-%m')
    hit = _budget_cache.get(month)
    if not hit or time.time() - hit[0] > 600:
        try:
            hit = (time.time(), google.monthly_budgets(month))
        except Exception as e:
            return {'month': month, 'error': str(e)[:150], 'items': []}
        _budget_cache[month] = hit
    t = collectors.today()
    first = since.replace(day=1)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    last = min(nxt - timedelta(days=1), t - timedelta(days=1))
    used = {}
    if last >= first:
        rows = db.read_raw(advertisers, first, last)
        if vat:
            rows = vat_exclude(rows)
        rows, _ = apply_bs_cpt(rows, contracts, first, last)
        for r in rows:
            k = (media_key(r['media']), category_of(r['media'], r['campaign'], r['adgroup']))
            used[k] = used.get(k, 0) + float(r['cost'] or 0)
    items = []
    for b in hit[1]:
        sh = (b['sheet'] + ' ' + b['name']).upper()
        m = 'BS' if ('BS' in sh or '브랜드' in sh) else 'GFA' if 'GFA' in sh else 'SA'
        c = 'Conf' if 'CONF' in sh else 'Pet' if 'PET' in sh else '기타'
        items.append({'media': m, 'cat': c, 'sheet': b['sheet'], 'name': b['name'], 'budget': b['budget']})
    merged = {}
    for it in items:
        k = (it['media'], it['cat'])
        mg = merged.setdefault(k, {'media': k[0], 'cat': k[1], 'budget': 0.0, 'names': []})
        mg['budget'] += it['budget']
        mg['names'].append(it['name'] or it['sheet'])
    for k, mg in merged.items():
        mg['used'] = round(used.get(k, 0))
    days = (nxt - first).days
    return {'month': month, 'items': list(merged.values()), 'until': str(last),
            'elapsed': round(max(0, (last - first).days + 1) / days * 100)}
