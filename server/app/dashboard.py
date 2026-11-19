# -*- coding: utf-8 -*-
"""대시보드 집계 — gas/Code.gs 의 apiDashboard 와 같은 결과 구조."""
import time
from datetime import timedelta

from sqlalchemy import select

from . import collectors, db
from . import settings as S
from .rules import (MEDIA_LABEL, SA_MEDIA, Contract, apply_bs_cpt, brand_of, classify_gfa, group_by, kpi, total,
                    vat_exclude)

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
    rows = collectors.sa_rows(t, t) if S.settings.sa_ready else []
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
    return {
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
