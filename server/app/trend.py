# -*- coding: utf-8 -*-
"""키워드 트렌드 — 네이버 데이터랩(상대지표) + 검색광고 키워드도구(최근 30일 실제 검색수).

추정 검색량 계산 (그룹별)
  1) 데이터랩을 '일' 단위로 조회 (요청 기간 + 최근 30일을 모두 포함하도록 넓혀서)
  2) 키워드도구에서 그룹 키워드들의 최근 30일 검색수 합계 K 를 구한다 (기기 선택 반영)
  3) 최근 30일 일별 지표 합 R 로 나눠 배율 c = K / R  → 하루 추정 검색량 = 일별 지표 × c
  4) 주/월 단위는 일별 추정치를 더한다. 상대지표(최대=100)도 같은 일별 값에서 다시 계산한다.
키워드도구 숫자는 '최근 30일' 기준이고 10 미만은 '< 10' 으로만 나와 0 으로 계산한다 → 결과는 추정치.
"""
from datetime import date, timedelta

import requests

from . import settings as S

DATALAB_URL = 'https://openapi.naver.com/v1/datalab/search'
MAX_GROUPS, MAX_KEYWORDS = 5, 20


class TrendError(Exception):
    pass


def datalab(groups, since, until, unit='date', device=''):
    st = S.settings
    if not (st.naver_client_id and st.naver_client_secret):
        raise TrendError('NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 미설정')
    payload = {'startDate': str(since), 'endDate': str(until), 'timeUnit': unit,
               'keywordGroups': [{'groupName': g['name'], 'keywords': g['keywords'][:MAX_KEYWORDS]}
                                 for g in groups[:MAX_GROUPS]]}
    if device:
        payload['device'] = device
    r = requests.post(DATALAB_URL, json=payload, timeout=30, headers={
        'X-Naver-Client-Id': st.naver_client_id, 'X-Naver-Client-Secret': st.naver_client_secret})
    if r.status_code != 200:
        raise TrendError('데이터랩 오류 HTTP {}: {}'.format(r.status_code, r.text[:200]))
    return r.json().get('results') or []


def _norm(k):
    return str(k or '').replace(' ', '').upper()


def _cnt(v):
    """키워드도구 값: 숫자 또는 '< 10' → (숫자, 10미만여부)"""
    if isinstance(v, (int, float)):
        return float(v), False
    s = str(v or '').replace(',', '').strip()
    if s.startswith('<'):
        return 0.0, True
    try:
        return float(s), False
    except ValueError:
        return 0.0, False


def keyword_volumes(keywords, client):
    """{키워드: {'pc','mo','total','lt10'}} — 키워드도구 최근 30일 검색수 (띄어쓰기는 붙여서 조회)"""
    want = {}
    for k in keywords:
        want.setdefault(_norm(k), []).append(k)
    out, keys = {}, list(want)
    for i in range(0, len(keys), 5):          # 한 번에 5개까지
        chunk = keys[i:i + 5]
        res = client.get('/keywordstool', params={'hintKeywords': ','.join(chunk), 'showDetail': 1}) or {}
        for row in res.get('keywordList') or []:
            nk = _norm(row.get('relKeyword'))
            if nk in want:
                pc, l1 = _cnt(row.get('monthlyPcQcCnt'))
                mo, l2 = _cnt(row.get('monthlyMobileQcCnt'))
                for orig in want[nk]:
                    out[orig] = {'pc': pc, 'mo': mo, 'total': pc + mo, 'lt10': l1 or l2}
    for k in keywords:
        out.setdefault(k, {'pc': 0.0, 'mo': 0.0, 'total': 0.0, 'lt10': False, 'missing': True})
    return out


def period_of(d, unit):
    if unit == 'week':
        return d - timedelta(days=d.weekday())       # 월요일 시작 (데이터랩과 같음)
    if unit == 'month':
        return d.replace(day=1)
    return d


def period_end(p, unit):
    if unit == 'week':
        return p + timedelta(days=6)
    if unit == 'month':
        nxt = (p.replace(day=28) + timedelta(days=4)).replace(day=1)
        return nxt - timedelta(days=1)
    return p


def build(groups, since, until, unit, device, today, sa=None):
    """반환: {'results': [{title, keywords, data:[{period, ratio, estimate, partial}]}],
             'volumes': {키워드: …}, 'basis': {...}, 'estimate': bool}"""
    yday = today - timedelta(days=1)
    until = min(until, yday)                         # 데이터랩은 어제까지
    if since > until:
        raise TrendError('기간을 확인해 주세요 (종료일은 어제까지).')
    win_s, win_e = yday - timedelta(days=29), yday
    fs, fe = min(since, win_s), max(until, win_e)
    daily = datalab(groups, fs, fe, 'date', device)

    vols, scale = {}, {}
    if sa is not None:
        allk = [k for g in groups[:MAX_GROUPS] for k in g['keywords'][:MAX_KEYWORDS]]
        vols = keyword_volumes(allk, sa)
    field = {'pc': 'pc', 'mo': 'mo'}.get(device, 'total')

    series = []
    for g, res in zip(groups[:MAX_GROUPS], daily):
        pts = {date.fromisoformat(p['period']): float(p.get('ratio') or 0) for p in res.get('data') or []}
        c = None
        if vols:
            k_sum = sum(vols[k][field] for k in g['keywords'][:MAX_KEYWORDS] if k in vols)
            r30 = sum(v for d, v in pts.items() if win_s <= d <= win_e)
            c = k_sum / r30 if r30 > 0 else None
            scale[g['name']] = {'monthly': k_sum, 'ratioSum30': r30}
        agg = {}
        for d, v in pts.items():
            if since <= d <= until:
                p = period_of(d, unit)
                agg[p] = agg.get(p, 0.0) + v
        series.append((g, agg, c))

    peak = max([v for _, agg, _ in series for v in agg.values()] or [0])
    periods = sorted({p for _, agg, _ in series for p in agg})
    out = []
    for g, agg, c in series:
        out.append({'title': g['name'], 'keywords': g['keywords'][:MAX_KEYWORDS], 'data': [{
            'period': str(p),
            'ratio': round(agg.get(p, 0) / peak * 100, 2) if peak else 0,
            'estimate': round(agg.get(p, 0) * c) if c is not None else None,
            'partial': period_of(since, unit) == p and since > p or period_end(p, unit) > until,
        } for p in periods]})
    return {'results': out, 'volumes': vols, 'estimate': bool(vols),
            'basis': {'from': str(win_s), 'to': str(win_e), 'device': device or '전체', 'groups': scale},
            'dropped': {'groups': max(0, len(groups) - MAX_GROUPS),
                        'keywords': sum(max(0, len(g['keywords']) - MAX_KEYWORDS) for g in groups[:MAX_GROUPS])}}
