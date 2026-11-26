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
    out = c.stat_reports_tsv('AD_CONVERSION_DETAIL', [date(2026, 11, 1), date(2026, 11, 2)])
    assert out[date(2026, 11, 1)] == '20261101\tx' and out[date(2026, 11, 2)] == ''
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
