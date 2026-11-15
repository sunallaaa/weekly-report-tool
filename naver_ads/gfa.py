# -*- coding: utf-8 -*-
"""네이버 GFA(성과형 디스플레이) 데이터 연동.

현재 상태
- GFA API는 공개 문서가 없고, 대행사 공통키로만 발급되며 기술 규격은 파트너사 지원 메일
  (agency_cs@navercorp.com)로 안내받아야 한다. 따라서 API 호출부는 '설정형'으로 만들어 두고
  (주소/헤더명/경로/필드명을 secrets에서 지정), 규격을 받으면 코드 수정 없이 켤 수 있게 했다.
- 그 전까지는 GFA 광고관리자에서 내려받은 일별 리포트(CSV/XLSX)를 올리면 같은 로우데이터 형식으로 변환한다.
"""
import pandas as pd
import requests

# 로우데이터 표준 컬럼
RAW_COLS = ['Date', 'Media', 'Device', '캠페인', '그룹', '노출', '클릭', '광고비', '구매완료수', '매출액']

# GFA 리포트 다운로드 파일의 컬럼명 후보 (표기 차이를 흡수하기 위한 자동 매칭용)
COLUMN_CANDIDATES = {
    'Date': ['기간', '일', '날짜', '일자', 'Date', 'date'],
    '캠페인': ['캠페인 이름', '캠페인명', '캠페인', 'Campaign', 'campaign'],
    '그룹': ['광고 그룹 이름', '광고그룹 이름', '광고그룹명', '광고 그룹', '광고그룹', 'Ad Set', 'adset'],
    '노출': ['노출', '노출수', 'Impressions', 'impressions'],
    '클릭': ['클릭', '클릭수', 'Clicks', 'clicks'],
    '광고비': ['총 비용', '총비용', '비용', '광고비', '총 비용(VAT 포함)', 'Cost', 'cost', 'spend'],
    '구매완료수': ['구매완료 수', '구매완료수', '구매 완료 수', '전환수', '구매', 'Conversions', 'purchases'],
    '매출액': ['구매완료 전환 매출액', '구매완료 매출액', '구매완료매출액', '전환 매출액', '매출액', 'Revenue', 'revenue'],
}


def guess_mapping(columns):
    cols = [str(c).strip() for c in columns]
    mapping = {}
    for std, cands in COLUMN_CANDIDATES.items():
        hit = next((c for cand in cands for c in cols if c == cand), None)
        if hit is None:  # 부분 일치
            hit = next((c for cand in cands for c in cols if cand in c), None)
        mapping[std] = hit
    return mapping


def _to_num(series):
    return pd.to_numeric(series.astype(str).str.replace(',', '').str.replace('원', '').str.strip(),
                         errors='coerce').fillna(0)


def normalize_export(df, mapping):
    """GFA 리포트 DataFrame → 로우데이터 형식 DataFrame (Media='GFA').

    광고비는 리포트에 있는 값 그대로 둔다(raw_to_report가 GFA 광고비를 /1.1 하여 VAT 제외로 변환).
    """
    missing = [k for k in ('Date', '캠페인', '노출', '클릭', '광고비') if not mapping.get(k)]
    if missing:
        raise ValueError('필수 컬럼 매칭 필요: ' + ', '.join(missing))
    out = pd.DataFrame()
    dates = df[mapping['Date']].astype(str).str.replace('.', '-', regex=False).str.strip().str[:10]
    out['Date'] = pd.to_datetime(dates, errors='coerce').dt.date
    out['Media'] = 'GFA'
    out['Device'] = 'ALL'
    out['캠페인'] = df[mapping['캠페인']].astype(str).str.strip()
    out['그룹'] = df[mapping['그룹']].astype(str).str.strip() if mapping.get('그룹') else ''
    for k in ('노출', '클릭', '광고비', '구매완료수', '매출액'):
        out[k] = _to_num(df[mapping[k]]) if mapping.get(k) else 0
    out = out[out['Date'].notna()]
    return out[RAW_COLS].reset_index(drop=True)


class GFAClient:
    """설정형 GFA API 클라이언트 (규격 확정 전까지는 연결 테스트 용도)."""

    def __init__(self, api_key, base_url, key_header='X-API-KEY', stats_path=None, ad_account_no=None):
        self.api_key = api_key
        self.base_url = (base_url or '').rstrip('/')
        self.key_header = key_header or 'X-API-KEY'
        self.stats_path = stats_path
        self.ad_account_no = ad_account_no

    @property
    def configured(self):
        return bool(self.api_key and self.base_url and self.stats_path)

    def fetch(self, since, until, extra_params=None):
        if not self.configured:
            raise RuntimeError('GFA API 규격(GFA_API_BASE_URL / GFA_STATS_PATH)이 아직 설정되지 않았습니다.')
        params = {'startDate': str(since), 'endDate': str(until)}
        if self.ad_account_no:
            params['adAccountNo'] = self.ad_account_no
        params.update(extra_params or {})
        r = requests.get(self.base_url + self.stats_path, params=params,
                         headers={self.key_header: self.api_key}, timeout=30)
        r.raise_for_status()
        return r.json()
