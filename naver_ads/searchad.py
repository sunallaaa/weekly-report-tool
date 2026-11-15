# -*- coding: utf-8 -*-
"""네이버 검색광고 API 클라이언트.

인증/서명 방식은 네이버 공식 샘플(github.com/naver/searchad-apidoc python-sample)과 동일:
  message   = "{timestamp}.{METHOD}.{uri}"   (uri = 쿼리스트링 제외 경로)
  signature = base64(HMAC-SHA256(secret_key, message))
  headers   = X-Timestamp, X-API-KEY(액세스 라이선스), X-Customer(CUSTOMER_ID), X-Signature

통계는 GET /stats (ids, fields=JSON 배열 문자열, timeRange=JSON 문자열) 사용.
일별 데이터는 하루 단위(since=until)로 반복 조회해서 만든다 (공식 샘플에서 확인된 파라미터만 사용).
"""
import base64
import hashlib
import hmac
import json
import time
from datetime import date, datetime, timedelta, timezone

import requests

BASE_URL = 'https://api.searchad.naver.com'

# 공식 샘플에서 확인된 필드 + 전환매출(convAmt)
BASE_FIELDS = ['impCnt', 'clkCnt', 'salesAmt', 'ctr', 'cpc', 'avgRnk', 'ccnt']
OPTIONAL_FIELDS = ['convAmt']

ID_CHUNK = 50  # 한 번에 조회할 id 개수 (보수적으로 설정)


class SearchAdError(Exception):
    pass


def make_signature(timestamp, method, uri, secret_key):
    message = '{}.{}.{}'.format(timestamp, method, uri)
    digest = hmac.new(secret_key.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).digest()
    return base64.b64encode(digest).decode('utf-8')


class SearchAdClient:
    def __init__(self, customer_id, api_key, secret_key, base_url=BASE_URL, session=None, sleep=0.15):
        if not (customer_id and api_key and secret_key):
            raise SearchAdError('검색광고 API 키(CUSTOMER_ID / API_KEY / SECRET_KEY)가 설정되지 않았습니다.')
        self.customer_id = str(customer_id)
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url
        self.session = session or requests.Session()
        self.sleep = sleep
        self._conv_amt_supported = True

    # ── 저수준 ────────────────────────────────────────────
    def _headers(self, method, uri):
        ts = str(round(time.time() * 1000))
        return {
            'Content-Type': 'application/json; charset=UTF-8',
            'X-Timestamp': ts,
            'X-API-KEY': self.api_key,
            'X-Customer': self.customer_id,
            'X-Signature': make_signature(ts, method, uri, self.secret_key),
        }

    def get(self, uri, params=None, retries=3):
        last = None
        for attempt in range(retries):
            r = self.session.get(self.base_url + uri, params=params, headers=self._headers('GET', uri), timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                last = r
                time.sleep(1.0 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise SearchAdError('{} {} → HTTP {}: {}'.format('GET', uri, r.status_code, r.text[:300]))
            if self.sleep:
                time.sleep(self.sleep)
            return r.json()
        raise SearchAdError('GET {} 재시도 실패 (HTTP {}): {}'.format(
            uri, getattr(last, 'status_code', '?'), getattr(last, 'text', '')[:300]))

    # ── 구조 조회 ─────────────────────────────────────────
    def list_campaigns(self):
        return self.get('/ncc/campaigns') or []

    def list_adgroups(self, campaign_id=None):
        params = {'nccCampaignId': campaign_id} if campaign_id else None
        return self.get('/ncc/adgroups', params=params) or []

    # ── 통계 ─────────────────────────────────────────────
    def stats(self, ids, since, until):
        """ids 목록의 [since~until] 합계 통계. 반환: {id: {field: value}}"""
        out = {}
        ids = list(ids)
        for i in range(0, len(ids), ID_CHUNK):
            chunk = ids[i:i + ID_CHUNK]
            fields = BASE_FIELDS + (OPTIONAL_FIELDS if self._conv_amt_supported else [])
            params = {
                'ids': chunk,
                'fields': json.dumps(fields),
                'timeRange': json.dumps({'since': str(since), 'until': str(until)}),
            }
            try:
                res = self.get('/stats', params=params)
            except SearchAdError:
                if not self._conv_amt_supported:
                    raise
                # convAmt를 지원하지 않는 경우 기본 필드로 재시도
                self._conv_amt_supported = False
                params['fields'] = json.dumps(BASE_FIELDS)
                res = self.get('/stats', params=params)
            for row in _stat_rows(res):
                rid = row.get('id')
                if rid:
                    out[rid] = row
        return out


def _stat_rows(res):
    if isinstance(res, dict):
        return res.get('data') or []
    if isinstance(res, list):
        return res
    return []


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def media_of(campaign):
    """캠페인 유형/이름으로 로우데이터 Media 값을 정한다 (raw_to_report와 동일한 값 사용)."""
    tp = (campaign.get('campaignTp') or '').upper()
    name = campaign.get('name') or ''
    if tp == 'BRAND_SEARCH' or '브랜드검색' in name:
        return 'NaverBS'
    if tp == 'SHOPPING':
        return 'NaverShopping'
    return 'NaverSA'


def daterange(since, until):
    d = since
    while d <= until:
        yield d
        d += timedelta(days=1)


def fetch_daily_raw_rows(client, since, until, campaign_filter=None, progress=None):
    """광고그룹 단위 일별 통계를 로우데이터 행(dict) 목록으로 반환.

    컬럼: Date, Media, Device, 캠페인, 그룹, 노출, 클릭, 광고비, 구매완료수, 매출액
    (기존 raw_to_report.parse_raw가 읽는 형식과 동일)
    """
    campaigns = client.list_campaigns()
    if campaign_filter:
        campaigns = [c for c in campaigns if campaign_filter(c)]
    camp_by_id = {c['nccCampaignId']: c for c in campaigns if c.get('nccCampaignId')}

    adgroups = []
    for cid in camp_by_id:
        adgroups.extend(client.list_adgroups(cid))
    group_by_id = {g['nccAdgroupId']: g for g in adgroups if g.get('nccAdgroupId')}
    if not group_by_id:
        return []

    days = list(daterange(since, until))
    rows = []
    for n, d in enumerate(days, start=1):
        if progress:
            progress(n, len(days), d)
        stats = client.stats(group_by_id.keys(), d, d)
        for gid, s in stats.items():
            g = group_by_id[gid]
            c = camp_by_id.get(g.get('nccCampaignId'), {})
            imps, clicks = _num(s.get('impCnt')), _num(s.get('clkCnt'))
            if imps == 0 and clicks == 0 and _num(s.get('salesAmt')) == 0:
                continue
            rows.append({
                'Date': d,
                'Media': media_of(c),
                'Device': 'ALL',
                '캠페인': c.get('name', ''),
                '그룹': g.get('name', ''),
                '노출': int(imps),
                '클릭': int(clicks),
                '광고비': _num(s.get('salesAmt')),
                '구매완료수': int(_num(s.get('ccnt'))),
                '매출액': _num(s.get('convAmt')),
                '평균순위': _num(s.get('avgRnk')),
            })
    return rows


KST = timezone(timedelta(hours=9))


def today():
    """한국시간 기준 오늘 (Streamlit Cloud 서버는 UTC라 date.today()를 쓰면 하루 밀릴 수 있음)."""
    return datetime.now(KST).date()
