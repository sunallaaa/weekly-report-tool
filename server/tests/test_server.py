# -*- coding: utf-8 -*-
"""서버 앱 테스트 — 외부 API는 가짜로 대체 (SQLite 임시 DB)."""
import csv
import json
import os
import sys
from datetime import date, timedelta

import openpyxl
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 't.db'))
    monkeypatch.setenv('APP_SECRET_KEY', 'test-secret')
    monkeypatch.setenv('BOOTSTRAP_ADMIN_EMAILS', 'boss@adef.co.kr')
    monkeypatch.setenv('BASE_PATH', '/seonhwa_choi/naver')
    monkeypatch.setenv('SCHEDULER', '0')
    monkeypatch.setenv('WORK_DIR', str(tmp_path / 'work'))
    monkeypatch.setenv('GFA_API_KEY', 'k')
    monkeypatch.setenv('GFA_TEAM_NAME', 'pd1')
    monkeypatch.setenv('GFA_REPORT_PATH', 'adAccounts/{account}/report')
    monkeypatch.setenv('GFA_AD_ACCOUNT_NO', '123')
    monkeypatch.setenv('GFA_REPORT_PARAMS', '{"startDate":"{since}","endDate":"{until}"}')
    from app import db, settings
    settings.reload()
    db.reset_engine()
    db.engine()
    from fastapi.testclient import TestClient
    from app import main
    main._rate.clear()
    yield TestClient(main.app)
    db.reset_engine()


def login(client, email, monkeypatch):
    from app import auth
    sent = {}
    monkeypatch.setattr('app.google.send_mail', lambda to, sub, text: sent.update(to=to, text=text))
    r = client.post('/seonhwa_choi/naver/api/auth/request', json={'email': email})
    assert r.status_code == 200
    if 'text' not in sent:
        return None
    code = sent['text'].split('인증코드: ')[1][:6]
    r = client.post('/api/auth/verify', json={'email': email, 'code': code})
    assert r.status_code == 200, r.text
    return code


# ── 규칙 ──
def test_classify_gfa():
    from app.rules import classify_gfa
    assert classify_gfa('(Mars Pet) ADVoost 쇼핑') == ('NAVER GFA_Pet', 'ADVoost')
    assert classify_gfa('(Mars Pet) 스마트채널&피드', '261023_브데_피드_알림받기') == ('NAVER GFA_Pet', '피드')
    assert classify_gfa('(Mars Pet) 스마트채널&피드', '그룹', 'x_smart_y') == ('NAVER GFA_Pet', '스마트채널')
    assert classify_gfa('(Mars Pet) 스마트채널&피드', '그룹', 'x_feed_y') == ('NAVER GFA_Pet', '피드')
    assert classify_gfa('(Mars Conf) 쇼핑프로모션') == ('NAVER GFA_Conf', '쇼핑고객확장')
    assert classify_gfa('(Mars Conf) 쇼핑고객확장') == ('NAVER GFA_Conf', '쇼핑고객확장')
    assert classify_gfa('기타 캠페인') is None


def test_bs_cpt_fills_days_without_impressions():
    from app.rules import Contract, apply_bs_cpt
    c = [Contract('쉬바', date(2026, 10, 2), date(2026, 12, 30), 900000)]   # 90일 → 하루 10,000
    rows = [{'date': date(2026, 11, 2), 'media': 'NaverBS', 'campaign': 'PET_브랜드검색', 'adgroup': '쉬바_PC',
             'imps': 100, 'clicks': 10, 'cost': 0, 'conv': 1, 'revenue': 10000},
            {'date': date(2026, 11, 2), 'media': 'NaverBS', 'campaign': 'PET_브랜드검색', 'adgroup': '쉬바_MO',
             'imps': 50, 'clicks': 5, 'cost': 0, 'conv': 0, 'revenue': 0}]
    out, missing = apply_bs_cpt(rows, c, date(2026, 11, 1), date(2026, 11, 7))
    assert missing == []
    assert abs(sum(r['cost'] for r in out) - 7 * 900000 / 90) < 0.01   # 노출 없는 날 포함 7일, 같은 날 중복 없음
    assert len(out) == 2 + 6


def test_vat_exclude_keeps_bs():
    from app.rules import vat_exclude
    r = vat_exclude([{'media': 'GFA', 'cost': 110}, {'media': 'NaverBS', 'cost': 100}, {'media': 'NaverSA', 'cost': 11}])
    assert [round(x['cost'], 6) for x in r] == [100, 100, 10]


# ── 로그인·권한 ──
def test_login_and_permissions(app_env, monkeypatch):
    c = app_env
    assert c.get('/api/me').status_code == 401
    assert c.get('/seonhwa_choi/naver/', follow_redirects=False).status_code in (302, 307)
    # 미등록 이메일: 같은 응답, 메일 미발송
    assert login(c, 'stranger@x.com', monkeypatch) is None
    # 최초 관리자
    login(c, 'BOSS@adef.co.kr', monkeypatch)
    assert c.get('/api/me').json()['role'] == 'admin'
    # 팀원·광고주 추가
    assert c.post('/api/admin/users', json={'email': 'mate@adef.co.kr', 'name': '팀원', 'role': 'member'}).status_code == 200
    assert c.post('/api/admin/users', json={'email': 'mars@client.com', 'role': 'client', 'advertisers': '*'}).status_code == 400
    assert c.post('/api/admin/users', json={'email': 'mars@client.com', 'role': 'client', 'advertisers': 'mars'}).status_code == 200
    assert len(c.get('/api/admin/users').json()) == 2
    # 팀원은 관리 API 불가
    c.cookies.clear()
    login(c, 'mate@adef.co.kr', monkeypatch)
    assert c.get('/api/admin/users').status_code == 403
    assert c.get('/api/me').json()['advertisers'] == ['*']
    # 비활성화하면 즉시 차단
    c.cookies.clear()
    login(c, 'boss@adef.co.kr', monkeypatch)
    c.post('/api/admin/users', json={'email': 'mate@adef.co.kr', 'role': 'member', 'active': False})
    c.cookies.clear()
    assert login(c, 'mate@adef.co.kr', monkeypatch) is None
    # 삭제 + 본인 삭제 불가
    login(c, 'boss@adef.co.kr', monkeypatch)
    assert c.delete('/api/admin/users/boss@adef.co.kr').status_code == 400
    assert c.delete('/api/admin/users/mars@client.com').status_code == 200


def test_wrong_code_locks(app_env, monkeypatch):
    c = app_env
    login(c, 'boss@adef.co.kr', monkeypatch)
    c.post('/api/admin/users', json={'email': 'a@adef.co.kr', 'role': 'member'})
    c.cookies.clear()
    monkeypatch.setattr('app.google.send_mail', lambda *a: None)
    c.post('/api/auth/request', json={'email': 'a@adef.co.kr'})
    for _ in range(5):
        assert c.post('/api/auth/verify', json={'email': 'a@adef.co.kr', 'code': '000000'}).status_code == 401


def test_sso_header(app_env, monkeypatch):
    monkeypatch.setenv('SSO_EMAIL_HEADER', 'X-Auth-Email')
    from app import settings
    settings.reload()
    c = app_env
    assert c.get('/api/me', headers={'X-Auth-Email': 'boss@adef.co.kr'}).json()['role'] == 'admin'
    assert c.get('/api/me', headers={'X-Auth-Email': 'nobody@adef.co.kr'}).status_code == 401


# ── 수집 · 대시보드 ──
GFA_PAYLOAD = {'data': [
    {'date': '2026-11-02', 'campaignName': '(Mars Pet) 스마트채널&피드', 'adSetName': '261023_브데_피드_알림',
     'creativeName': 'tem_feed_1', 'creativeNo': '111', 'impCount': 1000, 'clickCount': 50, 'totalCost': 110000,
     'purchaseCount': 5, 'purchaseSales': 500000},
    {'date': '2026-11-02', 'campaignName': '(Mars Pet) 스마트채널&피드', 'adSetName': '261023_브데_피드_알림',
     'creativeName': 'tem_feed_2', 'creativeNo': '112', 'impCount': 500, 'clickCount': 10, 'totalCost': 11000,
     'purchaseCount': 1, 'purchaseSales': 20000},
    {'date': '2026-11-02', 'campaignName': '(Mars Conf) ADVoost', 'adSetName': 'g', 'creativeName': 'c',
     'creativeNo': '9', 'impCount': 10, 'clickCount': 1, 'totalCost': 1100, 'purchaseCount': 0, 'purchaseSales': 0},
]}


def test_gfa_collect_and_dashboard(app_env, monkeypatch):
    from app import collectors, db, report_job
    calls = []

    def fake_get(path, params=None, session=None):
        calls.append((path, params))
        d = params['startDate']
        return {'data': [r for r in GFA_PAYLOAD['data'] if r['date'] == d]}

    monkeypatch.setattr(collectors, 'gfa_get', fake_get)
    n = collectors.collect_gfa(date(2026, 11, 1), date(2026, 11, 3))
    assert n == 3 and calls[0][0] == 'adAccounts/123/report' and calls[1][1]['startDate'] == '2026-11-02'
    # 재수집 시 중복 없이 교체
    collectors.collect_gfa(date(2026, 11, 1), date(2026, 11, 3))
    assert len(db.read_raw(['*'], date(2026, 11, 1), date(2026, 11, 3))) == 3

    c = app_env
    login(c, 'boss@adef.co.kr', monkeypatch)
    monkeypatch.setattr(collectors, 'today', lambda: date(2026, 11, 20))
    d = c.get('/api/dashboard', params={'since': '2026-11-01', 'until': '2026-11-03', 'vat': 1}).json()
    assert round(d['total']['cost']) == 111000   # 122,100 ÷ 1.1
    ch = {x['key']: x for x in d['byChannel']}
    assert set(ch) == {'GFA_Pet|피드', 'GFA_Conf|ADVoost'}
    assert round(ch['GFA_Pet|피드']['roas']) == round(520000 / 110000 * 100)
    assert {x['creative'] for x in d['creatives']} == {'tem_feed_1', 'tem_feed_2', 'c'}

    # 광고주 계정은 지정된 광고주만
    c.post('/api/admin/users', json={'email': 'x@client.com', 'role': 'client', 'advertisers': 'other'})
    c.cookies.clear()
    login(c, 'x@client.com', monkeypatch)
    d2 = c.get('/api/dashboard', params={'since': '2026-11-01', 'until': '2026-11-03'}).json()
    assert d2['total']['cost'] == 0
    assert c.get('/api/admin/status').status_code == 403

    # 로우 내보내기 형식
    rows = db.read_raw(['mars'], date(2026, 11, 1), date(2026, 11, 3))
    p1, p2 = os.environ['WORK_DIR'] + '/d.xlsx', os.environ['WORK_DIR'] + '/r.csv'
    os.makedirs(os.environ['WORK_DIR'], exist_ok=True)
    assert report_job.export_daily(rows, p1) == 2          # 소재 2개 → 광고그룹 1행 + Conf 1행
    ws = openpyxl.load_workbook(p1).active
    assert [c.value for c in ws[1]] == report_job.DAILY_COLS
    pet = [r for r in ws.iter_rows(min_row=2, values_only=True) if 'Pet' in r[3]][0]
    assert pet[0] == '2026-11-02' and pet[1] == 'GFA' and pet[7] == 121000 and pet[9] == 520000
    assert report_job.export_result(rows, p2) == 3
    rr = list(csv.reader(open(p2, encoding='utf-8-sig')))
    assert rr[0] == report_job.RESULT_COLS and rr[1][6] == '2026.11.02.'


def test_sa_collect(app_env, monkeypatch):
    from app import collectors, db

    class FakeSA:
        def list_campaigns(self):
            return [{'nccCampaignId': 'c1', 'name': 'PET_브랜드검색', 'campaignTp': 'BRAND_SEARCH'},
                    {'nccCampaignId': 'c2', 'name': '파워링크', 'campaignTp': 'WEB_SITE'}]

        def list_adgroups(self, cid):
            return [{'nccAdgroupId': cid + 'g', 'nccCampaignId': cid, 'name': '쉬바_PC' if cid == 'c1' else 'g'}]

        def stats(self, ids, s, u):
            return {i: {'id': i, 'impCnt': 10, 'clkCnt': 2, 'salesAmt': 0 if i == 'c1g' else 1100, 'ccnt': 1,
                        'convAmt': 5000} for i in ids}

    n = collectors.collect_sa(date(2026, 11, 1), date(2026, 11, 2), client=FakeSA())
    assert n == 4
    media = sorted({r['media'] for r in db.read_raw(['*'], date(2026, 11, 1), date(2026, 11, 2))})
    assert media == ['NaverBS', 'NaverSA']


def test_contract_admin_and_cpt_in_dashboard(app_env, monkeypatch):
    from app import collectors, db
    c = app_env
    login(c, 'boss@adef.co.kr', monkeypatch)
    assert c.post('/api/admin/contracts', json={'brand': '쉬바', 'start': '2026-10-02', 'end': '2026-12-30',
                                                'amount': 900000}).status_code == 200
    assert c.post('/api/admin/contracts', json={'brand': '쉬바', 'start': '2026-12-30', 'end': '2026-10-02',
                                                'amount': 1}).status_code == 400
    db.replace_raw('mars', ['NaverBS'], date(2026, 11, 2), date(2026, 11, 2), [
        {'date': date(2026, 11, 2), 'media': 'NaverBS', 'campaign': 'PET_브랜드검색', 'adgroup': '쉬바_PC',
         'imps': 100, 'clicks': 10, 'cost': 0, 'conv': 2, 'revenue': 50000}])
    monkeypatch.setattr(collectors, 'today', lambda: date(2026, 11, 20))
    d = c.get('/api/dashboard', params={'since': '2026-11-01', 'until': '2026-11-07'}).json()
    b = {x['key']: x for x in d['byBrand']}
    assert round(b['쉬바']['cost']) == round(7 * 900000 / 90)
    assert d['notice'] == ''


def test_health_and_static(app_env):
    c = app_env
    assert c.get('/healthz').json() == {'ok': True}
    assert c.get('/seonhwa_choi/naver/healthz').json() == {'ok': True}
    assert '인증코드' in c.get('/seonhwa_choi/naver/login').text


CONV_TSV = (
    "20261101\tcus\tc1\tc1g\t-\t-\tbiz\tmedia\tP\t1\tpurchase\t2\t30000\n"
    "20261101\tcus\tc1\tc1g\t-\t-\tbiz\tmedia\tM\t2\tpurchase\t1\t10000\n"
    "20261101\tcus\tc1\tc1g\t-\t-\tbiz\tmedia\tM\t1\tadd_to_cart\t9\t0\n"
    "20261101\tcus\tc2\tc2g\t-\t-\tbiz\t1\t2\tmedia\tM\t1\tpurchase\t1\t5000\n"   # 열이 더 많은 형식
)


class FakeSA2:
    def __init__(self, fail=False):
        self.fail = fail

    def list_campaigns(self):
        return [{'nccCampaignId': 'c1', 'name': 'PET_브랜드검색', 'campaignTp': 'BRAND_SEARCH'},
                {'nccCampaignId': 'c2', 'name': '파워링크', 'campaignTp': 'WEB_SITE'}]

    def list_adgroups(self, cid):
        return [{'nccAdgroupId': cid + 'g', 'nccCampaignId': cid, 'name': 'g' + cid}]

    def stats(self, ids, s, u):   # 전체전환(장바구니 포함)은 12건으로 크게 나옴
        return {'c1g': {'id': 'c1g', 'impCnt': 10, 'clkCnt': 2, 'salesAmt': 0, 'ccnt': 12, 'convAmt': 40000}}

    def stat_reports_tsv(self, tp, days):
        from app.sa_client import SearchAdError
        assert tp == 'AD_CONVERSION_DETAIL'
        if self.fail:
            return {d: SearchAdError('x') for d in days}
        return {d: (CONV_TSV if d == date(2026, 11, 1) else '') for d in days}


def test_sa_purchase_only(app_env, monkeypatch):
    from app import collectors, db
    collectors.collect_sa(date(2026, 11, 1), date(2026, 11, 2), client=FakeSA2())
    rows = {(r['date'], r['adgroup']): r for r in db.read_raw(['*'], date(2026, 11, 1), date(2026, 11, 2))}
    bs = rows[(date(2026, 11, 1), 'gc1')]
    assert (bs['conv'], bs['revenue']) == (3, 40000)            # 구매완료(직접2+간접1)만, 장바구니 제외
    sa = rows[(date(2026, 11, 1), 'gc2')]                        # 노출 없던 그룹의 구매도 포함
    assert (sa['imps'], sa['conv'], sa['revenue']) == (0, 1, 5000)
    assert (date(2026, 11, 2), 'gc1') in rows and rows[(date(2026, 11, 2), 'gc1')]['conv'] == 0
    # 보고서 실패 시 전체전환으로 대체하고 안내
    r = collectors.collect_sa(date(2026, 11, 1), date(2026, 11, 1), client=FakeSA2(fail=True))
    assert r == 1 and collectors.SA_NOTES and db.read_raw(['*'], date(2026, 11, 1), date(2026, 11, 1))[0]['conv'] == 12
    # 진단 합계
    b = collectors.sa_conversion_breakdown(date(2026, 11, 1), date(2026, 11, 2), client=FakeSA2())
    got = {(x['media'], x['type'], x['method']): (x['count'], x['sales']) for x in b['rows']}
    assert got[('NaverBS', 'purchase', '직접')] == (2, 30000) and got[('NaverBS', 'purchase', '간접')] == (1, 10000)
    assert got[('NaverSA', 'purchase', '직접')] == (1, 5000) and ('NaverBS', 'add_to_cart', '직접') in got


def test_stat_reports_flow(monkeypatch):
    from app.sa_client import SearchAdClient

    class R:
        def __init__(self, code, js=None, text=''):
            self.status_code, self._js, self.text, self.encoding = code, js, text, None

        def json(self):
            return self._js

    class Sess:
        def __init__(self):
            self.n, self.calls = 0, []

        def post(self, url, data=None, headers=None, timeout=None):
            self.calls.append(('POST', url, data))
            if '20261103' in data:
                return R(400, text='{"code":10004,"message":"선택하신 조건에 지표가 확인되지 않습니다.","status":"BAD_REQUEST"}')
            if '20261104' in data:
                return R(400, text='{"code":11001,"message":"x"}')
            return R(200, {'reportJobId': 7 if '20261101' in data else 8})

        def get(self, url, params=None, headers=None, timeout=None):
            self.calls.append(('GET', url, headers.get('X-Signature') and headers['X-Timestamp']))
            if url.endswith('/stat-reports/7'):
                self.n += 1
                return R(200, {'status': 'BUILT' if self.n > 1 else 'RUNNING', 'downloadUrl': 'https://dl/report-download?authtoken=a'})
            if url.endswith('/stat-reports/8'):
                return R(200, {'status': 'NONE'})
            return R(200, text='20261101\tx')

        def delete(self, url, headers=None, timeout=None):
            self.calls.append(('DELETE', url, None))

    s = Sess()
    c = SearchAdClient('1', 'k', 's', session=s, sleep=0)
    import app.sa_client as m
    monkeypatch.setattr(m.time, 'sleep', lambda x: None)
    out = c.stat_reports_tsv('AD_CONVERSION_DETAIL', [date(2026, 11, d) for d in (1, 2, 3, 4)])
    assert out[date(2026, 11, 1)] == '20261101\tx' and out[date(2026, 11, 2)] == ''
    assert out[date(2026, 11, 3)] == '' and isinstance(out[date(2026, 11, 4)], Exception)   # 10004=전환 0건
    assert ('GET', 'https://dl/report-download?authtoken=a') in [(a, b) for a, b, _ in s.calls]
    assert sum(1 for a, *_ in s.calls if a == 'DELETE') == 2


def test_gfa_get_adds_account_id(app_env, monkeypatch):
    from app import collectors
    seen = {}

    class S_:
        def get(self, url, params=None, headers=None, timeout=None):
            seen.update(url=url, params=params, headers=headers)

            class R:
                status_code = 200

                def json(self):
                    return [{'adAccountNo': 123}]
            return R()

    collectors.gfa_get('adAccounts', {}, session=S_())
    assert seen['params'] == {'team_name': 'pd1', 'account_id': '123'} and seen['url'].endswith('/gfa-proxy/adAccounts')
    collectors.gfa_get('adAccounts', {'account_id': '9'}, session=S_())
    assert seen['params']['account_id'] == '9'               # 직접 넣으면 그 값 우선


def test_gfa_native_collect(app_env, monkeypatch):
    from app import collectors, db, settings
    monkeypatch.setenv('GFA_REPORT_PATH', '')
    settings.reload()
    calls = []
    perf_c = [{'campaignNo': 1, 'adSetNo': 11, 'creativeNo': 111, 'targetDate': '2026-11-02', 'impCount': 100,
               'clickCount': 5, 'sales': 11000},
              {'campaignNo': 1, 'adSetNo': 11, 'creativeNo': 999, 'targetDate': '2026-11-02', 'impCount': 10,
               'clickCount': 1, 'sales': 1100}]
    conv_c = [{'campaignNo': 1, 'adSetNo': 11, 'creativeNo': 111, 'targetDate': '2026-11-02', 'convType': 'PURCHASE',
               'convCount': 2, 'convSales': 50000},
              {'campaignNo': 1, 'adSetNo': 11, 'creativeNo': 111, 'targetDate': '2026-11-02', 'convType': 'CART',
               'convCount': 9, 'convSales': 0},
              {'campaignNo': 1, 'adSetNo': 11, 'creativeNo': 111, 'targetDate': '2026-11-03', 'convType': 'PURCHASE',
               'convCount': 1, 'convSales': 7000}]
    perf_a = [{'campaignNo': 2, 'assetGroupNo': 5, 'targetDate': '20261102', 'impCount': 50, 'clickCount': 2,
               'sales': 2200}]

    def fake_get(path, params=None, session=None):
        calls.append((path, dict(params or {})))
        p = params or {}
        if '/performance/' not in path and path.endswith('/campaigns'):
            return {'content': [] if p.get('deleteOnly') else [{'no': 1, 'name': '(Mars Pet) 스마트채널&피드'},
                                                               {'no': 2, 'name': '(Mars Pet) ADVoost 쇼핑'}], 'last': True}
        if '/performance/' not in path and path.endswith('/adSets'):
            return {'content': [{'no': 11, 'name': '261023_브데_피드', 'campaignNo': 1}], 'last': True}
        if '/performance/' not in path and path.endswith('/creatives'):
            if p.get('page') == 0 and not p.get('deleteOnly'):
                return {'content': [{'no': 111, 'name': 'tem_feed_1', 'adSetNo': 11}], 'last': False}
            return {'content': [], 'last': True}
        if '/conversion/past/creatives' in path:
            return {'rows': conv_c[:2], 'next': 'T'} if 'next' not in p else {'rows': conv_c[2:]}
        if '/conversion/past/assetGroups' in path:
            return {'rows': []}
        if '/past/creatives' in path:
            return {'rows': perf_c}
        if '/past/assetGroups' in path:
            return {'rows': perf_a}
        raise AssertionError(path)

    monkeypatch.setattr(collectors, 'gfa_get', fake_get)
    n = collectors.collect_gfa(date(2026, 11, 1), date(2026, 11, 3))
    assert n == 4
    assert any(c[0] == 'adAccounts/123/performance/past/creatives' and c[1]['timeUnit'] == 'daily'
               and c[1]['startDate'] == '2026-11-01' and c[1]['endDate'] == '2026-11-03' for c in calls)
    rows = {(r['date'], r['creative_id'] or r['adgroup']): r for r in db.read_raw(['*'], date(2026, 11, 1), date(2026, 11, 3))}
    r = rows[(date(2026, 11, 2), '111')]
    assert (r['campaign'], r['adgroup'], r['creative']) == ('(Mars Pet) 스마트채널&피드', '261023_브데_피드', 'tem_feed_1')
    assert (r['imps'], r['cost'], r['conv'], r['revenue']) == (100, 11000, 2, 50000)   # 장바구니 제외
    assert rows[(date(2026, 11, 3), '111')]['conv'] == 1                               # 다음 페이지(next)까지 읽음
    assert rows[(date(2026, 11, 2), '999')]['creative'] == '소재 999'                  # 이름 모르면 번호로
    a = rows[(date(2026, 11, 2), '애셋그룹 5')]
    assert a['campaign'] == '(Mars Pet) ADVoost 쇼핑' and a['cost'] == 2200
    from app.rules import classify_gfa
    assert classify_gfa(a['campaign'])[1] == 'ADVoost'


def test_admin_collect_runs_in_background(app_env, monkeypatch):
    import threading
    from app import main, report_job
    gate, done = threading.Event(), threading.Event()

    def slow(s, e):
        gate.wait(5)
        done.set()
    monkeypatch.setattr(report_job, 'run_collect_job', slow)
    c = app_env
    login(c, 'boss@adef.co.kr', monkeypatch)
    r = c.post('/api/admin/collect', json={'since': '2026-11-01', 'until': '2026-11-02'})
    assert r.status_code == 200 and r.json()['started'] is True and not done.is_set()   # 바로 응답
    assert c.post('/api/admin/collect', json={}).status_code == 409                    # 중복 실행 막기
    gate.set()
    assert done.wait(5)
    main._bg_running['job'].join(5)
    assert c.post('/api/admin/collect', json={'since': '2026-11-01', 'until': '2026-11-01'}).status_code == 200


def test_trend_estimate(app_env, monkeypatch):
    from app import trend, settings
    monkeypatch.setenv('NAVER_CLIENT_ID', 'id')
    monkeypatch.setenv('NAVER_CLIENT_SECRET', 'sec')
    settings.reload()
    today = date(2026, 11, 27)
    seen = {}

    def fake_datalab(groups, s, e, unit, device):
        seen.update(s=s, e=e, unit=unit)
        out = []
        for gi, g in enumerate(groups):
            data, d = [], s
            while d <= e:
                data.append({'period': str(d), 'ratio': 10.0 * (gi + 1)})
                d += timedelta(days=1)
            out.append({'title': g['name'], 'data': data})
        return out

    class FakeSA:
        def get(self, uri, params=None):
            assert uri == '/keywordstool' and ' ' not in params['hintKeywords']
            return {'keywordList': [{'relKeyword': '시저강아지', 'monthlyPcQcCnt': 300, 'monthlyMobileQcCnt': 2700},
                                    {'relKeyword': '시저', 'monthlyPcQcCnt': '< 10', 'monthlyMobileQcCnt': 3000},
                                    {'relKeyword': '쉬바', 'monthlyPcQcCnt': 100, 'monthlyMobileQcCnt': 500}]}

    monkeypatch.setattr(trend, 'datalab', fake_datalab)
    groups = [{'name': '시저', 'keywords': ['시저', '시저 강아지']}, {'name': '쉬바', 'keywords': ['쉬바', '없는키워드']}]
    r = trend.build(groups, date(2026, 11, 2), date(2026, 11, 26), 'week', '', today, FakeSA())
    assert seen['unit'] == 'date' and seen['s'] == date(2026, 10, 28) and seen['e'] == date(2026, 11, 26)
    g0 = r['results'][0]
    # 시저: 30일 검색수 3000+3000=6000, 일별 지표 10 → 하루 200 → 주(7일) 1400
    assert g0['data'][0] == {'period': '2026-11-02', 'ratio': 50.0, 'estimate': 1400, 'partial': False}
    assert g0['data'][-1]['period'] == '2026-11-23' and g0['data'][-1]['partial'] is True and g0['data'][-1]['estimate'] == 800
    assert r['results'][1]['data'][0]['ratio'] == 100.0 and r['results'][1]['data'][0]['estimate'] == 140   # 600/30*7
    assert r['volumes']['시저']['lt10'] and r['volumes']['없는키워드'].get('missing')
    # 키워드도구 없이도 상대지표는 나온다
    r2 = trend.build(groups, date(2026, 11, 2), date(2026, 11, 26), 'week', '', today, None)
    assert r2['estimate'] is False and r2['results'][0]['data'][0]['estimate'] is None


def test_category_device_rules():
    from app.rules import category_of, device_of, media_key
    assert category_of('GFA', '(Mars Conf) ADVoost 쇼핑') == 'Conf' and category_of('GFA', '(Mars Pet) NDA') == 'Pet'
    assert category_of('NaverBS', 'PET_브랜드검색', '시저_MO') == 'Pet' and category_of('NaverBS', '브랜드검색', '쉬바_PC') == 'Pet'
    assert category_of('NaverBS', 'CONF_브랜드검색', '스니커즈_MO') == 'Conf' and category_of('NaverSA', '파워링크', 'g') == '기타'
    assert (device_of('시저_MO'), device_of('템테이션 PC'), device_of('쉬바')) == ('MO', 'PC', '전체')
    assert device_of('1. 시저', '1. M_마즈코리아_PET_브랜드검색') == 'MO' and device_of('2. 그리니즈', 'P_마즈코리아_PET_브랜드검색') == 'PC'
    assert category_of('NaverBS', '1. M_마즈코리아_PET_브랜드검색', '1. 시저') == 'Pet'
    assert (media_key('GFA'), media_key('NaverBS'), media_key('NaverSA')) == ('GFA', 'BS', 'SA')


def test_dashboard_v2_and_bs_detail(app_env, monkeypatch):
    from app import collectors, db
    c = app_env
    login(c, 'boss@adef.co.kr', monkeypatch)
    monkeypatch.setattr(collectors, 'today', lambda: date(2026, 11, 20))
    d1 = date(2026, 11, 2)
    db.replace_raw('mars', ['GFA', 'NaverBS'], d1, d1, [
        {'date': d1, 'media': 'GFA', 'campaign': '(Mars Conf) 쇼핑고객확장', 'adgroup': 'g1', 'creative': 'cr1',
         'creative_id': '11', 'imps': 100, 'clicks': 10, 'cost': 11000, 'conv': 1, 'revenue': 50000},
        {'date': d1, 'media': 'NaverBS', 'campaign': 'PET_브랜드검색', 'adgroup': '시저_MO', 'imps': 50, 'clicks': 5,
         'cost': 0, 'conv': 2, 'revenue': 30000},
        {'date': d1, 'media': 'NaverBS', 'campaign': 'PET_브랜드검색', 'adgroup': '시저_PC', 'imps': 20, 'clicks': 2,
         'cost': 0, 'conv': 1, 'revenue': 10000}])
    db.save_creative_meta('mars', 'GFA', [{'creative_id': '11', 'name': 'cr1', 'image_url': 'https://img/x.jpg'}])
    c.post('/api/admin/contracts', json={'brand': '시저', 'start': '2026-11-01', 'end': '2026-11-30', 'amount': 300000})
    d = c.get('/api/dashboard', params={'since': '2026-11-02', 'until': '2026-11-02'}).json()
    lv = {(x['media'], x['cat'], x['group'], x['device']): x for x in d['leaves']}
    assert ('GFA', 'Conf', 'g1', '') in lv and round(lv[('GFA', 'Conf', 'g1', '')]['cost']) == 10000   # VAT 제외
    assert {k[3] for k in lv if k[0] == 'BS'} == {'MO', 'PC'} and all(k[2] == '시저' for k in lv if k[0] == 'BS')
    assert round(sum(x['cost'] for x in d['leaves'] if x['media'] == 'BS')) == 10000                   # CPT 하루치 1번
    assert d['gfaCreatives'][0]['image'] == 'https://img/x.jpg' and d['gfaCreatives'][0]['cat'] == 'Conf'
    assert {(x['media'], x['cat']) for x in d['dailyBy']} == {('GFA', 'Conf'), ('BS', 'Pet')}
    db.replace_bs_detail('mars', d1, d1, [
        {'date': d1, 'campaign': 'PET_브랜드검색', 'adgroup': '시저_MO', 'level': 'keyword', 'item_id': 'k1',
         'item_name': '시저', 'imps': 30, 'clicks': 3, 'conv': 1, 'revenue': 10000},
        {'date': d1, 'campaign': 'PET_브랜드검색', 'adgroup': '시저_PC', 'level': 'ad', 'item_id': 'a1',
         'item_name': '시저 PC 소재', 'image_url': 'https://img/a.png', 'imps': 20, 'clicks': 2, 'conv': 0, 'revenue': 0}])
    r = c.get('/api/bs-detail', params={'since': '2026-11-01', 'until': '2026-11-03'}).json()
    got = {(i['brand'], i['device'], i['level'], i['name']) for i in r['items']}
    assert got == {('시저', 'MO', 'keyword', '시저'), ('시저', 'PC', 'ad', '시저 PC 소재')}


def test_fetch_bs_detail():
    from app.sa_client import fetch_bs_detail
    tsv = ("20261102\tcus\tc1\tg1\tk1\t-\tbiz\tm\tM\t1\tpurchase\t2\t20000\n"
           "20261102\tcus\tc1\tg1\t-\ta1\tbiz\tm\tM\t2\tpurchase\t1\t5000\n"
           "20261102\tcus\tc1\tg1\tk1\t-\tbiz\tm\tM\t1\tadd_to_cart\t9\t0\n")

    class C:
        def list_campaigns(self):
            return [{'nccCampaignId': 'c1', 'name': 'PET_브랜드검색', 'campaignTp': 'BRAND_SEARCH'},
                    {'nccCampaignId': 'c2', 'name': '파워링크', 'campaignTp': 'WEB_SITE'}]

        def list_adgroups(self, cid):
            assert cid == 'c1'
            return [{'nccAdgroupId': 'g1', 'name': '시저_MO'}]

        def list_keywords(self, gid):
            return [{'nccKeywordId': 'k1', 'keyword': '시저'}]

        def list_ads(self, gid):
            return [{'nccAdId': 'a1', 'type': 'BRAND_SEARCH_AD',
                     'ad': {'headline': '시저 메인', 'image': {'url': 'https://img/a.png'}}}]

        def stats(self, ids, s, u):
            ids = list(ids)
            assert len({i[0] for i in ids}) == 1, '같은 종류 ID만'   # k… / a… 섞이면 실패
            return {i: {'k1': {'impCnt': 100, 'clkCnt': 10}, 'a1': {'impCnt': 80, 'clkCnt': 8}}[i] for i in ids}

    rows = fetch_bs_detail(C(), date(2026, 11, 2), date(2026, 11, 2), {date(2026, 11, 2): tsv})
    by = {r['item_id']: r for r in rows}
    assert (by['k1']['item_name'], by['k1']['conv'], by['k1']['revenue']) == ('시저', 2, 20000)   # 장바구니 제외
    assert (by['a1']['item_name'], by['a1']['image_url'], by['a1']['conv']) == ('시저 메인', 'https://img/a.png', 1)


def test_budget_status(app_env, monkeypatch):
    from app import collectors, dashboard, db, google, settings
    for k in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN', 'BUDGET_SHEET_ID'):
        monkeypatch.setenv(k, 'x')
    settings.reload()
    dashboard._budget_cache.clear()
    monkeypatch.setattr(collectors, 'today', lambda: date(2026, 11, 11))
    monkeypatch.setattr(google, 'monthly_budgets', lambda m: [
        {'sheet': 'NAVER GFA_Conf', 'name': 'ADVoost', 'budget': 1000000, 'memo': ''},
        {'sheet': 'NAVER GFA_Conf', 'name': '쇼고확', 'budget': 500000, 'memo': ''},
        {'sheet': 'NAVER BS_Pet', 'name': '브랜드검색', 'budget': 900000, 'memo': ''}])
    d1 = date(2026, 11, 3)
    db.replace_raw('mars', ['GFA'], d1, d1, [{'date': d1, 'media': 'GFA', 'campaign': '(Mars Conf) ADVoost 쇼핑',
                                             'adgroup': 'a', 'imps': 1, 'clicks': 1, 'cost': 110000, 'conv': 0, 'revenue': 0}])
    b = dashboard.budget_status(['*'], date(2026, 11, 1), date(2026, 11, 11), True, [])
    it = {(x['media'], x['cat']): x for x in b['items']}
    assert it[('GFA', 'Conf')]['budget'] == 1500000 and it[('GFA', 'Conf')]['used'] == 100000
    assert it[('BS', 'Pet')]['budget'] == 900000 and b['elapsed'] == round(10 / 30 * 100)
    assert dashboard.budget_status(['*'], date(2026, 10, 25), date(2026, 11, 2), True, []) is None   # 두 달에 걸치면 없음


def test_export_daily_matches_mars_daily_format(app_env, tmp_path):
    from app import report_job
    d1 = date(2026, 11, 20)
    rows = [{'date': d1, 'media': 'GFA', 'device': 'ALL', 'campaign': '(Mars Pet) ADVoost 쇼핑', 'adgroup': '애셋그룹 77',
             'imps': 10, 'clicks': 1, 'cost': 100.5, 'conv': 1, 'revenue': 1000},
            {'date': d1, 'media': 'NaverBS', 'device': 'ALL', 'campaign': '1. M_마즈코리아_PET_브랜드검색',
             'adgroup': '1. 시저', 'imps': 5, 'clicks': 1, 'cost': 0, 'conv': 0, 'revenue': 0}]
    p = str(tmp_path / 'd.xlsx')
    assert report_job.export_daily(rows, p) == 2
    got = [r for r in openpyxl.load_workbook(p).active.iter_rows(min_row=2, values_only=True)]
    assert got[0][:5] == ('2026-11-20', 'GFA', '-', '(Mars Pet) ADVoost 쇼핑', None) and got[0][7] == 100.5
    assert got[1][:5] == ('2026-11-20', 'NaverBS', 'M', '1. M_마즈코리아_PET_브랜드검색', '1. 시저')
