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
