# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import io
import json
import os
import sys
from datetime import date
from urllib.parse import parse_qs, urlparse

import openpyxl
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import raw_to_report  # noqa: E402
from naver_ads import gfa, raw_builder, searchad  # noqa: E402


def test_signature_matches_official_sample():
    # 공식 signaturehelper.py 와 동일한 계산
    ts, method, uri, secret = '1700000000000', 'GET', '/ncc/campaigns', 'dummy-secret=='
    expected = base64.b64encode(hmac.new(bytes(secret, 'utf-8'), bytes(f'{ts}.{method}.{uri}', 'utf-8'),
                                         hashlib.sha256).digest()).decode()
    assert searchad.make_signature(ts, method, uri, secret) == expected


class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json.dumps(data, ensure_ascii=False)

    def json(self):
        return self._d


class FakeSession:
    """네이버 검색광고 API 흉내: 캠페인 2개(Pet 브랜드검색, 파워링크), 광고그룹 3개."""
    def __init__(self, reject_conv=False):
        self.calls, self.reject_conv = [], reject_conv

    def get(self, url, params=None, headers=None, timeout=None):
        path = urlparse(url).path
        self.calls.append((path, params, headers))
        for h in ('X-Timestamp', 'X-API-KEY', 'X-Customer', 'X-Signature'):
            assert h in headers
        if path == '/ncc/campaigns':
            return FakeResp([
                {'nccCampaignId': 'cmp-1', 'name': 'MARS_PET_브랜드검색', 'campaignTp': 'BRAND_SEARCH'},
                {'nccCampaignId': 'cmp-2', 'name': 'PET_파워링크', 'campaignTp': 'WEB_SITE'},
            ])
        if path == '/ncc/adgroups':
            cid = params['nccCampaignId']
            groups = {'cmp-1': [('grp-1', '그리니즈_PC'), ('grp-2', '시저_MO')], 'cmp-2': [('grp-3', '일반')]}[cid]
            return FakeResp([{'nccAdgroupId': g, 'nccCampaignId': cid, 'name': n} for g, n in groups])
        if path == '/stats':
            fields = json.loads(params['fields'])
            if self.reject_conv and 'convAmt' in fields:
                return FakeResp({'code': 11001, 'title': 'invalid field'}, 400)
            tr = json.loads(params['timeRange'])
            assert tr['since'] == tr['until']
            day = int(tr['since'][-2:])
            data = [{'id': i, 'impCnt': 1000 + day, 'clkCnt': 10, 'salesAmt': 5000, 'ccnt': 2,
                     **({'convAmt': 30000} if 'convAmt' in fields else {})} for i in params['ids']]
            return FakeResp({'data': data})
        raise AssertionError(path)


def make_client(**kw):
    return searchad.SearchAdClient('1', 'k', 's', session=FakeSession(**kw), sleep=0)


def test_fetch_daily_rows_and_media_mapping():
    rows = searchad.fetch_daily_raw_rows(make_client(), date(2026, 11, 2), date(2026, 11, 8))
    assert len(rows) == 7 * 3
    bs = [r for r in rows if r['Media'] == 'NaverBS']
    assert len(bs) == 14 and all(r['매출액'] == 30000 for r in bs)
    assert {r['Media'] for r in rows} == {'NaverBS', 'NaverSA'}


def test_conv_amt_fallback():
    c = make_client(reject_conv=True)
    rows = searchad.fetch_daily_raw_rows(c, date(2026, 11, 2), date(2026, 11, 2))
    assert len(rows) == 3 and all(r['매출액'] == 0 for r in rows)


def test_raw_xlsx_feeds_existing_report_pipeline():
    # 검색광고(API) + GFA(파일) → 로우데이터 엑셀 → 기존 build_report
    sa = raw_builder.to_dataframe(searchad.fetch_daily_raw_rows(make_client(), date(2026, 11, 2), date(2026, 11, 15)))
    export = pd.DataFrame({
        '기간': [f'2026.11.{d:02d}' for d in range(2, 16)],
        '캠페인 이름': ['(Mars Pet) ADVoost_시저'] * 14,
        '광고 그룹 이름': ['AG1'] * 14,
        '노출': ['1,000'] * 14, '클릭': [20] * 14, '총 비용': [11000] * 14,
        '구매완료 수': [1] * 14, '구매완료 전환 매출액': [50000] * 14,
    })
    mapping = gfa.guess_mapping(export.columns)
    assert mapping['광고비'] == '총 비용' and mapping['매출액'] == '구매완료 전환 매출액'
    g = gfa.normalize_export(export, mapping)
    merged = pd.concat([sa, g], ignore_index=True)
    raw = raw_builder.to_raw_xlsx_bytes(merged)

    wb = raw_to_report.build_report(io.BytesIO(raw))
    ws = wb['NAVER GFA_Pet']
    labels = {ws.cell(row=r, column=2).value: r for r in range(1, ws.max_row + 1)}
    wk = '11월 1주차 (11/2~11/8)'
    assert wk in labels
    r = next(r for v, r in labels.items() if v == wk)  # 첫 번째 표(TOTAL)
    assert ws.cell(row=r, column=3).value == 7000          # 노출
    assert abs(ws.cell(row=r, column=7).value - 70000) < 1  # 광고비 77000 / 1.1
    # BS_Pet: 브랜드(그리니즈/시저) 분류 + 월예산 환산 광고비
    bs = wb['NAVER BS_Pet']
    vals = [bs.cell(row=r, column=c).value for r in range(1, bs.max_row + 1) for c in (2, 3)]
    assert '그리니즈' in vals and '시저' in vals


def test_today_is_kst():
    assert isinstance(searchad.today(), date)
