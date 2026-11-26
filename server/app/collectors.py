# -*- coding: utf-8 -*-
"""데이터 수집 — 네이버 검색광고 API + GFA(사내 게이트웨이).

GFA 성과 조회 경로·응답 필드는 게이트웨이 연결 후 '관리 > GFA 탐색'에서 실제 응답을 보고
.env 의 GFA_REPORT_PATH / GFA_REPORT_PARAMS / GFA_FIELD_MAP 으로 지정한다(코드 수정 불필요).
"""
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from . import db
from . import settings as S
from .rules import SA_MEDIA, daterange
from .sa_client import SearchAdClient, conversion_by_group, fetch_daily_raw_rows, parse_conversion_tsv

KST = timezone(timedelta(hours=9))


def today():
    return datetime.now(KST).date()


# ───────────────────────── 검색광고 ─────────────────────────
def sa_client():
    st = S.settings
    return SearchAdClient(st.sa_customer_id, st.sa_api_key, st.sa_secret_key)


SA_NOTES = []   # 마지막 수집에서 생긴 안내(보고서 실패로 전체전환을 쓴 날 등)


def _purchase_override(client, since, until):
    """구매완료만 쓰도록 날짜별 {그룹ID: (구매수, 구매매출)} 을 미리 받아 둔다.
    보고서가 실패한 날은 None → 그날만 전체전환(/stats)으로 두고 안내에 남긴다."""
    st = S.settings
    if st.sa_conv_source != 'report':
        return None
    try:
        tsv = client.stat_reports_tsv('AD_CONVERSION_DETAIL', list(daterange(since, until)))
    except Exception as e:
        SA_NOTES.append('전환보고서 요청 실패 → 전체전환 사용: {}'.format(str(e)[:150]))
        return None

    def conv(day):
        v = tsv.get(day)
        if isinstance(v, Exception) or v is None:
            SA_NOTES.append('{} 전환보고서 실패 → 전체전환 사용: {}'.format(day, str(v)[:120]))
            return None
        return conversion_by_group(v, st.sa_conv_types, st.sa_conv_methods)
    return conv


def sa_rows(since, until, client=None):
    client = client or sa_client()
    del SA_NOTES[:]
    rows = fetch_daily_raw_rows(client, since, until, conv_override=_purchase_override(client, since, until))
    return [{'date': r['Date'], 'media': r['Media'], 'device': r.get('Device') or 'ALL',
             'campaign': r['캠페인'], 'adgroup': r['그룹'], 'creative': '', 'creative_id': '',
             'imps': r['노출'], 'clicks': r['클릭'], 'cost': r['광고비'], 'conv': r['구매완료수'],
             'revenue': r['매출액']} for r in rows]


def collect_sa(since, until, client=None):
    rows = sa_rows(since, until, client)
    return db.replace_raw(S.settings.advertiser, SA_MEDIA, since, until, rows)


def sa_conversion_breakdown(since, until, client=None):
    """진단용: 기간 내 전환을 '전환유형 × 직접/간접 × 매체'로 합계 (저장 안 함).
    Mars_Daily 구매완료 수·매출과 어느 조합이 맞는지 확인하는 데 쓴다."""
    from .sa_client import media_of
    client = client or sa_client()
    media = {c['nccCampaignId']: media_of(c) for c in client.list_campaigns() if c.get('nccCampaignId')}
    agg, errors = {}, []
    tsv = client.stat_reports_tsv('AD_CONVERSION_DETAIL', list(daterange(since, until)))
    for d in sorted(tsv):
        if isinstance(tsv[d], Exception):
            errors.append('{}: {}'.format(d, str(tsv[d])[:150]))
            continue
        for r in parse_conversion_tsv(tsv[d]):
            k = (media.get(r['campaign_id'], '기타'), r['type'], r['method'])
            a = agg.setdefault(k, [0.0, 0.0])
            a[0] += r['count']
            a[1] += r['sales']
    rows = [{'media': k[0], 'type': k[1], 'method': {'1': '직접', '2': '간접'}.get(k[2], k[2]),
             'count': v[0], 'sales': v[1]} for k, v in sorted(agg.items())]
    return {'since': str(since), 'until': str(until), 'rows': rows, 'errors': errors,
            'current': {'source': S.settings.sa_conv_source, 'types': S.settings.sa_conv_types,
                        'methods': S.settings.sa_conv_methods}}


# ───────────────────────── GFA (사내 게이트웨이) ─────────────────────────
GFA_STATUS_TEXT = {
    400: '필수 파라미터 누락(account_id 등)', 401: 'API 키 누락 또는 불일치 (GFA_API_KEY 확인)',
    403: '등록되지 않은 팀 / 허용되지 않은 IP / 권한 없는 계정 — 팀 이름이 맞다면 IP 제한 가능성',
    502: '네이버 GFA API 통신 오류', 504: '네이버 GFA API 응답 시간 초과',
}

# 응답 필드명 자동 추정용 후보 (GFA_FIELD_MAP 에 없을 때)
GFA_FIELD_CANDIDATES = {
    'date': ['date', 'statDate', 'stat_date', 'day', 'ymd', 'period', '기간', '일자'],
    'campaign': ['campaignName', 'campaign_name', 'campaign', '캠페인 이름'],
    'adgroup': ['adSetName', 'adsetName', 'adGroupName', 'adgroup_name', 'adSet', '광고 그룹 이름'],
    'creative': ['creativeName', 'creative_name', 'adName', 'ad_name', '광고 소재 이름'],
    'creative_id': ['creativeNo', 'creativeId', 'creative_id', 'adNo', 'adId', '광고 소재 ID'],
    'imps': ['impCount', 'impressions', 'imp', 'impCnt', 'imps', '노출'],
    'clicks': ['clickCount', 'clicks', 'click', 'clkCnt', '클릭'],
    'cost': ['totalCost', 'cost', 'spend', 'salesAmt', '총 비용'],
    'conv': ['purchaseCount', 'purchases', 'convCount', 'conversions', '구매완료 수'],
    'revenue': ['purchaseSales', 'purchaseAmount', 'purchaseRevenue', 'convAmount', 'revenue',
                '구매완료 전환 매출액'],
}


class GfaError(Exception):
    pass


def gfa_get(path, params=None, session=None):
    st = S.settings
    if not st.gfa_ready:
        raise GfaError('.env 에 GFA_API_KEY / GFA_TEAM_NAME 이 필요합니다.')
    q = dict(params or {})
    q['team_name'] = st.gfa_team
    # 게이트웨이는 모든 요청에 account_id 가 필요 → .env 의 GFA_AD_ACCOUNT_NO 를 자동으로 붙인다
    if st.gfa_account_id and st.gfa_account_param and st.gfa_account_param not in q:
        q[st.gfa_account_param] = st.gfa_account_id
    url = st.gfa_base.rstrip('/') + '/' + str(path).lstrip('/')
    r = (session or requests).get(url, params=q, headers={'X-API-Key': st.gfa_key}, timeout=60)
    if r.status_code != 200:
        raise GfaError('GFA HTTP {} — {} / {}'.format(r.status_code, GFA_STATUS_TEXT.get(r.status_code, '알 수 없는 오류'),
                                                       r.text[:300]))
    try:
        return r.json()
    except ValueError:
        raise GfaError('GFA 응답이 JSON이 아닙니다: ' + r.text[:300])


def _records(payload):
    """응답에서 행 목록을 찾는다 (리스트 그대로, 또는 data/list/content/rows/results/items 키)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ('data', 'list', 'content', 'rows', 'results', 'items', 'records', 'reports'):
            v = payload.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                inner = _records(v)
                if inner:
                    return inner
    return []


def _num(v):
    try:
        return float(str(v).replace(',', '')) if v not in (None, '') else 0.0
    except ValueError:
        return 0.0


def _get(rec, key):
    """'a.b' 형태의 중첩 키 허용."""
    cur = rec
    for part in str(key).split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def resolve_field_map(sample):
    fm = json.loads(S.settings.gfa_field_map or '{}')
    out = {}
    for std, cands in GFA_FIELD_CANDIDATES.items():
        if fm.get(std):
            out[std] = fm[std]
        elif isinstance(sample, dict):
            out[std] = next((c for c in cands if c in sample), None)
    return out


def normalize_gfa(records, fallback_date=None):
    if not records:
        return [], {}
    fmap = resolve_field_map(records[0])
    rows = []
    for rec in records:
        d = _get(rec, fmap['date']) if fmap.get('date') else None
        d = str(d or fallback_date or '')[:10].replace('.', '-').replace('/', '-')
        if len(d) == 8 and d.isdigit():
            d = '{}-{}-{}'.format(d[:4], d[4:6], d[6:])
        try:
            dd = datetime.strptime(d, '%Y-%m-%d').date()
        except ValueError:
            continue
        rows.append({
            'date': dd, 'media': 'GFA', 'device': 'ALL',
            'campaign': str(_get(rec, fmap['campaign']) or '') if fmap.get('campaign') else '',
            'adgroup': str(_get(rec, fmap['adgroup']) or '') if fmap.get('adgroup') else '',
            'creative': str(_get(rec, fmap['creative']) or '') if fmap.get('creative') else '',
            'creative_id': str(_get(rec, fmap['creative_id']) or '') if fmap.get('creative_id') else '',
            **{k: _num(_get(rec, fmap[k])) if fmap.get(k) else 0.0
               for k in ('imps', 'clicks', 'cost', 'conv', 'revenue')},
        })
    return rows, fmap


def gfa_report_params(day):
    st = S.settings
    raw = st.gfa_report_params or '{}'
    for k, v in {'{since}': str(day), '{until}': str(day), '{account}': st.gfa_account_id}.items():
        raw = raw.replace(k, v)
    return json.loads(raw)


# ── 네이버 GFA 공식 API 경로로 수집 (공식 문서 naver-ad-api.github.io/developers 기준) ──
#   성과:  adAccounts/{계정}/performance/past/{creatives|assetGroups}?startDate&endDate&timeUnit=daily  → rows[], next
#   전환:  adAccounts/{계정}/performance/conversion/past/{…}  → rows[].convType (PURCHASE=구매완료)
#   이름:  adAccounts/{계정}/campaigns | adSets | creatives  (page/size, content[], last)
GFA_CHUNK_DAYS = 31
GFA_NOTES = []


def _gfa_list(path, extra=None, session=None):
    """페이지로 나뉜 목록(content/last)을 끝까지 읽는다."""
    out, page = [], 0
    while page < 200:
        payload = gfa_get(path, dict(extra or {}, page=page, size=100), session=session)
        out.extend(_records(payload))
        if not isinstance(payload, dict) or payload.get('last', True) or not _records(payload):
            break
        page += 1
    return out


def _gfa_rows(path, params, session=None):
    """성과 응답(rows/next)을 다음 페이지 토큰이 없을 때까지 읽는다."""
    out, q = [], dict(params)
    for _ in range(500):
        payload = gfa_get(path, q, session=session)
        out.extend(_records(payload))
        nxt = payload.get('next') if isinstance(payload, dict) else None
        if not nxt:
            break
        q['next'] = nxt
    return out


def gfa_names(acc, session=None):
    base = 'adAccounts/{}'.format(acc)
    names = {'campaign': {}, 'adset': {}, 'creative': {}}
    for extra in ({}, {'deleteOnly': 'true'}):   # 삭제된 소재도 과거 성과가 있으니 이름을 함께 받는다
        try:
            for c in _gfa_list(base + '/campaigns', extra, session):
                names['campaign'][str(c.get('no'))] = c.get('name') or ''
            for g in _gfa_list(base + '/adSets', extra, session):
                names['adset'][str(g.get('no'))] = (g.get('name') or '', str(g.get('campaignNo') or ''),
                                                    g.get('campaignName') or '')
            for c in _gfa_list(base + '/creatives', extra, session):
                names['creative'][str(c.get('no'))] = (c.get('name') or '', str(c.get('adSetNo') or ''))
        except GfaError as e:
            if not extra:
                raise
            GFA_NOTES.append('삭제 항목 이름 조회 생략: {}'.format(str(e)[:100]))
    return names


def _gfa_date(v):
    d = str(v or '')[:10].replace('.', '-').replace('/', '-')
    if len(d) >= 8 and d[:8].isdigit():
        d = '{}-{}-{}'.format(d[:4], d[4:6], d[6:8])
    try:
        return datetime.strptime(d, '%Y-%m-%d').date()
    except ValueError:
        return None


def gfa_native_rows(since, until, session=None):
    st = S.settings
    acc = st.gfa_account_id
    base = 'adAccounts/{}/performance'.format(acc)
    names = gfa_names(acc, session)
    agg = {}

    def key(r, level):
        d = _gfa_date(r.get('targetDate'))
        if not d or not (since <= d <= until):
            return None
        if level == 'creatives':
            return (d, 'c', str(r.get('creativeNo') or ''), str(r.get('adSetNo') or ''), str(r.get('campaignNo') or ''))
        return (d, 'a', str(r.get('assetGroupNo') or ''), '', str(r.get('campaignNo') or ''))

    s = since
    while s <= until:
        e = min(until, s + timedelta(days=GFA_CHUNK_DAYS - 1))
        q = {'startDate': str(s), 'endDate': str(e), 'timeUnit': 'daily'}
        for level in ('creatives', 'assetGroups'):
            try:
                perf = _gfa_rows('{}/past/{}'.format(base, level), q, session)
                conv = _gfa_rows('{}/conversion/past/{}'.format(base, level), q, session)
            except GfaError as ex:
                if level == 'creatives':
                    raise
                GFA_NOTES.append('애셋그룹(ADV쇼핑) 성과 조회 실패: {}'.format(str(ex)[:120]))
                continue
            for r in perf:
                k = key(r, level)
                if k:
                    a = agg.setdefault(k, [0.0] * 5)
                    a[0] += _num(r.get('impCount'))
                    a[1] += _num(r.get('clickCount'))
                    a[2] += _num(r.get('sales'))          # 광고비 (네이버 응답 필드명 sales)
            for r in conv:
                if str(r.get('convType') or '').upper() not in st.gfa_conv_types:
                    continue
                k = key(r, level)
                if k:
                    a = agg.setdefault(k, [0.0] * 5)
                    a[3] += _num(r.get('convCount'))
                    a[4] += _num(r.get('convSales'))
        s = e + timedelta(days=1)

    rows = []
    for (d, kind, no, adset_no, camp_no), v in agg.items():
        if not any(v):
            continue
        if kind == 'c':
            cname, cadset = names['creative'].get(no, ('', ''))
            adset_no = adset_no or cadset
            gname, gcamp, gcampname = names['adset'].get(adset_no, ('', '', ''))
            camp_no = camp_no or gcamp
            campaign = names['campaign'].get(camp_no) or gcampname or '캠페인 {}'.format(camp_no)
            adgroup, creative, cid = gname or '광고그룹 {}'.format(adset_no), cname or '소재 {}'.format(no), no
        else:
            campaign = names['campaign'].get(camp_no) or '캠페인 {}'.format(camp_no)
            adgroup, creative, cid = '애셋그룹 {}'.format(no), '', ''
        rows.append({'date': d, 'media': 'GFA', 'device': 'ALL', 'campaign': campaign, 'adgroup': adgroup,
                     'creative': creative, 'creative_id': cid, 'imps': v[0], 'clicks': v[1], 'cost': v[2],
                     'conv': v[3], 'revenue': v[4]})
    return rows


def collect_gfa(since, until, session=None):
    """반환: 저장 행 수. GFA_REPORT_PATH 가 비어 있으면 네이버 공식 경로로 수집한다."""
    st = S.settings
    del GFA_NOTES[:]
    if not st.gfa_report_path:
        if not st.gfa_account_id:
            raise GfaError('.env 에 GFA_AD_ACCOUNT_NO(광고계정 번호)가 필요합니다.')
        rows = gfa_native_rows(since, until, session)
        return db.replace_raw(st.advertiser, ['GFA'], since, until, rows)
    rows = []
    for d in daterange(since, until):
        path = st.gfa_report_path.replace('{account}', quote(st.gfa_account_id or ''))
        recs = _records(gfa_get(path, gfa_report_params(d), session=session))
        got, _ = normalize_gfa(recs, fallback_date=d)
        rows.extend(r for r in got if since <= r['date'] <= until)
    return db.replace_raw(st.advertiser, ['GFA'], since, until, rows)


# ───────────────────────── 실행 단위 ─────────────────────────
def run_collect(since=None, until=None, sources=('sa', 'gfa')):
    """기본: 최근 N일 재수집 (늦게 잡히는 전환 반영). 반환: {source: 행수 또는 오류문구}"""
    st = S.settings
    until = until or today() - timedelta(days=1)
    since = since or until - timedelta(days=st.recollect_days - 1)
    result = {}
    if 'sa' in sources:
        try:
            result['검색광고'] = collect_sa(since, until) if st.sa_ready else '키 미설정'
            if SA_NOTES:
                result['검색광고 안내'] = SA_NOTES[:5] + (['외 {}건'.format(len(SA_NOTES) - 5)] if len(SA_NOTES) > 5 else [])
        except Exception as e:   # 한 매체 실패가 다른 매체 수집을 막지 않게
            result['검색광고'] = '오류: {}'.format(e)
    if 'gfa' in sources:
        try:
            result['GFA'] = collect_gfa(since, until) if st.gfa_collect_ready else '설정 대기'
            if GFA_NOTES:
                result['GFA 안내'] = GFA_NOTES[:5]
        except Exception as e:
            result['GFA'] = '오류: {}'.format(e)
    return {'since': str(since), 'until': str(until), 'result': result}
