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

    def post(self, uri, body):
        r = self.session.post(self.base_url + uri, data=json.dumps(body), headers=self._headers('POST', uri), timeout=30)
        if r.status_code >= 400:
            raise SearchAdError('POST {} → HTTP {}: {}'.format(uri, r.status_code, r.text[:300]))
        return r.json()

    def delete(self, uri):
        try:
            self.session.delete(self.base_url + uri, headers=self._headers('DELETE', uri), timeout=30)
        except Exception:
            pass

    # ── 대용량 보고서(StatReport) ──────────────────────────
    def stat_reports_tsv(self, report_tp, days, wait_sec=300):
        """여러 날의 대용량 보고서를 한꺼번에 요청해 {날짜: TSV문자열} 로 받는다 (공식 샘플과 같은 절차).
        POST /stat-reports → GET /stat-reports/{id} 상태 BUILT → downloadUrl 다운로드(서명 uri=/report-download)
        데이터가 없는 날은 '', 실패한 날은 SearchAdError 객체가 값으로 들어간다."""
        jobs, out = {}, {}
        for d in days:
            try:
                job = self.post('/stat-reports', {'reportTp': report_tp, 'statDt': d.strftime('%Y%m%d')})
                jobs[d] = job.get('reportJobId')
            except SearchAdError as e:
                # 10004 '선택하신 조건에 지표가 확인되지 않습니다' = 그날 해당 지표(전환)가 0건 → 빈 보고서로 처리
                out[d] = '' if '"code":10004' in str(e).replace(' ', '') else e
        deadline = time.time() + wait_sec
        try:
            while jobs:
                for d, jid in list(jobs.items()):
                    st = self.get('/stat-reports/{}'.format(jid))
                    status = st.get('status')
                    if status == 'BUILT':
                        r = self.session.get(st.get('downloadUrl'), headers=self._headers('GET', '/report-download'),
                                             timeout=60)
                        if r.status_code >= 400:
                            out[d] = SearchAdError('보고서 다운로드 HTTP {}: {}'.format(r.status_code, r.text[:200]))
                        else:
                            r.encoding = 'utf-8'
                            out[d] = r.text
                    elif status == 'NONE':
                        out[d] = ''
                    elif status in ('ERROR', 'AGGREGATING_ERROR'):
                        out[d] = SearchAdError('보고서 생성 오류 ({})'.format(status))
                    else:
                        continue
                    self.delete('/stat-reports/{}'.format(jobs.pop(d)))
                if jobs:
                    if time.time() > deadline:
                        for d in jobs:
                            out[d] = SearchAdError('보고서 생성 대기 시간 초과')
                        break
                    time.sleep(2)
        finally:
            for jid in jobs.values():
                self.delete('/stat-reports/{}'.format(jid))
        return out

    def stat_report_tsv(self, report_tp, stat_dt):
        v = self.stat_reports_tsv(report_tp, [stat_dt])[stat_dt]
        if isinstance(v, Exception):
            raise v
        return v

    # ── 구조 조회 ─────────────────────────────────────────
    def list_campaigns(self):
        return self.get('/ncc/campaigns') or []

    def list_adgroups(self, campaign_id=None):
        params = {'nccCampaignId': campaign_id} if campaign_id else None
        return self.get('/ncc/adgroups', params=params) or []

    def list_keywords(self, adgroup_id):
        return self.get('/ncc/keywords', params={'nccAdgroupId': adgroup_id}) or []

    def list_ads(self, adgroup_id):
        return self.get('/ncc/ads', params={'nccAdgroupId': adgroup_id}) or []

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


# 전환 상세 보고서(AD_CONVERSION_DETAIL) — 머리글 없는 TSV.
# 열 순서(네이버 문서): Date, CUSTOMER ID, Campaign ID, AD Group ID, AD keyword ID, AD ID, Business Channel ID,
#   (버전에 따라 Hours/Region 이 더 있을 수 있음), Media code, PC Mobile Type,
#   Conversion Method, Conversion Type, Conversion count, Sales by conversion
# → 앞쪽은 위치, 뒤쪽 4개는 '끝에서부터' 읽어 열 개수 차이에 흔들리지 않게 한다.
def parse_conversion_tsv(text):
    out = []
    for line in (text or '').splitlines():
        f = line.rstrip('\r').split('\t')
        if len(f) < 8 or not f[0][:1].isdigit():
            continue   # 머리글/빈 줄 건너뜀
        out.append({'date': f[0], 'campaign_id': f[2], 'adgroup_id': f[3], 'keyword_id': f[4].strip(),
                    'ad_id': f[5].strip(), 'method': f[-4].strip(),
                    'type': f[-3].strip().lower(), 'count': _num(f[-2]), 'sales': _num(f[-1])})
    return out


def conversion_by_group(tsv_text, types=('purchase',), methods=()):
    """하루치 TSV → {광고그룹ID: (전환수, 전환매출)} — 지정한 전환유형(기본 구매완료)만 합산"""
    recs = parse_conversion_tsv(tsv_text)
    types = {t.lower() for t in types if t}
    agg = {}
    for r in recs:
        if types and r['type'] not in types:
            continue
        if methods and r['method'] not in methods:
            continue
        a = agg.setdefault(r['adgroup_id'], [0.0, 0.0])
        a[0] += r['count']
        a[1] += r['sales']
    return {k: tuple(v) for k, v in agg.items()}


def _ad_name_image(ad):
    """소재(ad) 응답에서 보여줄 이름과 이미지 주소를 찾는다 (소재 유형마다 필드가 달라 넓게 찾음)"""
    body = ad.get('ad') if isinstance(ad.get('ad'), dict) else {}
    name = ''
    for k in ('headline', 'title', 'subject', 'name', 'description'):
        v = body.get(k)
        if isinstance(v, str) and v.strip():
            name = v.strip()
            break
    img = ''

    def walk(x):
        nonlocal img
        if img:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(v, str) and v.startswith('http') and (
                        'image' in k.lower() or 'img' in k.lower() or 'thumb' in k.lower()
                        or v.lower().split('?')[0].endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp'))):
                    img = v
                    return
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(body)
    return name or '{} {}'.format(ad.get('type') or '소재', ad.get('nccAdId') or ''), img


def fetch_bs_detail(client, since, until, conv_tsv=None, types=('purchase',), methods=(), notes=None):
    """브랜드검색 키워드·소재 단위 일별 성과. conv_tsv: {날짜: 전환상세 TSV} (구매완료만 합산)"""
    notes = notes if notes is not None else []
    camps = {c['nccCampaignId']: c for c in client.list_campaigns()
             if c.get('nccCampaignId') and media_of(c) == 'NaverBS'}
    items = {}   # id → (level, name, image, campaign, adgroup)
    for cid, c in camps.items():
        for g in client.list_adgroups(cid):
            gid = g.get('nccAdgroupId')
            if not gid:
                continue
            try:
                for k in client.list_keywords(gid):
                    if k.get('nccKeywordId'):
                        items[k['nccKeywordId']] = ('keyword', k.get('keyword') or '', '', c.get('name', ''), g.get('name', ''))
            except Exception as e:
                notes.append('브랜드검색 키워드 목록 실패({}): {}'.format(g.get('name'), str(e)[:80]))
            try:
                for a in client.list_ads(gid):
                    if a.get('nccAdId'):
                        nm, img = _ad_name_image(a)
                        items[a['nccAdId']] = ('ad', nm, img, c.get('name', ''), g.get('name', ''))
            except Exception as e:
                notes.append('브랜드검색 소재 목록 실패({}): {}'.format(g.get('name'), str(e)[:80]))
    if not items:
        return []
    types = {t.lower() for t in types if t}
    rows = []
    for d in daterange(since, until):
        st = client.stats(items.keys(), d, d)
        conv = {}
        tsv = (conv_tsv or {}).get(d)
        if isinstance(tsv, str):
            for r in parse_conversion_tsv(tsv):
                if (types and r['type'] not in types) or (methods and r['method'] not in methods):
                    continue
                for key in (r['keyword_id'], r['ad_id']):
                    if key in items:
                        a = conv.setdefault(key, [0.0, 0.0])
                        a[0] += r['count']
                        a[1] += r['sales']
        for iid, (level, name, img, camp, grp) in items.items():
            s = st.get(iid, {})
            imps, clk = _num(s.get('impCnt')), _num(s.get('clkCnt'))
            cv, rv = conv.get(iid, (0.0, 0.0))
            if not (imps or clk or cv or rv):
                continue
            rows.append({'date': d, 'campaign': camp, 'adgroup': grp, 'level': level, 'item_id': iid,
                         'item_name': name[:500], 'image_url': img[:1000], 'imps': imps, 'clicks': clk,
                         'conv': cv, 'revenue': rv})
    return rows


def fetch_daily_raw_rows(client, since, until, campaign_filter=None, progress=None, conv_override=None):
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
        conv = conv_override(d) if conv_override else None   # {그룹ID: (구매수, 구매매출)} 또는 None
        if conv:
            for gid in conv:   # 노출·클릭이 없는 날 잡힌 구매도 빠뜨리지 않게
                if gid in group_by_id and gid not in stats:
                    stats[gid] = {'id': gid}
        for gid, s in stats.items():
            if gid not in group_by_id:
                continue
            g = group_by_id[gid]
            c = camp_by_id.get(g.get('nccCampaignId'), {})
            imps, clicks = _num(s.get('impCnt')), _num(s.get('clkCnt'))
            if conv is not None:
                cnt, sales = conv.get(gid, (0.0, 0.0))
                s = dict(s, ccnt=cnt, convAmt=sales)
            if imps == 0 and clicks == 0 and _num(s.get('salesAmt')) == 0 and _num(s.get('ccnt')) == 0:
                continue
            rows.append({
                'Date': d,
                'Media': media_of(c),
                'Device': 'ALL',
                '캠페인': c.get('name', ''),
                '그룹': g.get('name', ''),
                '그룹ID': gid,
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
