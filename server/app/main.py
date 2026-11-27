# -*- coding: utf-8 -*-
"""웹 서버 (FastAPI). 실행: uvicorn app.main:app --host 0.0.0.0 --port 8080 (워커 1개 — 예약작업 중복 방지)"""
import json
import os
import threading
import time
from datetime import date, timedelta

import requests
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from . import auth, collectors, dashboard, db, google, report_job
from . import settings as S

STATIC = os.path.join(os.path.dirname(__file__), 'static')
app = FastAPI(title='네이버 광고 대시보드', docs_url=None, redoc_url=None, openapi_url=None)


# 하위 경로(/seonhwa_choi/naver) 대응: nginx가 경로를 떼고 넘기든 그대로 넘기든 둘 다 동작
@app.middleware('http')
async def strip_base_path(request: Request, call_next):
    bp = S.settings.base_path
    path = request.scope['path']
    if bp and path == bp:   # 끝 슬래시 없이 들어오면 상대경로가 어긋나므로 슬래시 붙여 이동
        return RedirectResponse(bp + '/')
    if bp and path.startswith(bp + '/'):
        request.scope['path'] = path[len(bp):] or '/'
    resp = await call_next(request)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Referrer-Policy'] = 'same-origin'
    return resp


@app.on_event('startup')
def _startup():
    db.engine()
    if os.environ.get('SCHEDULER', '1') == '1':
        from . import scheduler
        scheduler.start()


@app.get('/healthz')
def healthz():
    return {'ok': True}


# ───────────── 화면 ─────────────
def _page(name):
    return FileResponse(os.path.join(STATIC, name), headers={'Cache-Control': 'no-cache'})


@app.get('/')
def index(request: Request):
    u = auth.current_user(request)
    if not u:
        return RedirectResponse('login')
    return _page('index.html')


@app.get('/login')
def login_page():
    return _page('login.html')


@app.get('/admin')
def admin_page(request: Request):
    u = auth.current_user(request)
    if not u:
        return RedirectResponse('login')
    if u['role'] != 'admin':
        return RedirectResponse('./')
    return _page('admin.html')


# ───────────── 로그인 ─────────────
class EmailIn(BaseModel):
    email: str


class CodeIn(BaseModel):
    email: str
    code: str


_rate = {}


def _throttle(key, per_min=5):
    now = time.time()
    hits = [t for t in _rate.get(key, []) if now - t < 60]
    if len(hits) >= per_min:
        raise HTTPException(429, '잠시 후 다시 시도해 주세요.')
    _rate[key] = hits + [now]


@app.post('/api/auth/request')
def auth_request(body: EmailIn, request: Request):
    _throttle('req-ip:' + (request.client.host if request.client else ''), 30)   # 사무실은 IP 공유 → 넉넉히
    _throttle('req:' + auth.norm_email(body.email), 3)
    try:
        auth.issue_code(body.email, google.send_mail)
    except google.GoogleError:
        raise HTTPException(503, '인증 메일을 보낼 수 없습니다. 관리자에게 문의해 주세요.')
    return {'ok': True, 'message': '등록된 이메일이면 인증코드를 보냈습니다. 메일함을 확인해 주세요.'}


@app.post('/api/auth/verify')
def auth_verify(body: CodeIn, request: Request):
    _throttle('ver:' + auth.norm_email(body.email), 10)
    if not auth.verify_code(body.email, body.code):
        raise HTTPException(401, '인증코드가 맞지 않거나 만료되었습니다.')
    resp = JSONResponse({'ok': True})
    resp.set_cookie(auth.COOKIE, auth.make_session(body.email), max_age=S.settings.session_days * 86400,
                    httponly=True, secure=request.url.scheme == 'https' or
                    request.headers.get('x-forwarded-proto') == 'https', samesite='lax', path='/')
    db.log_audit(auth.norm_email(body.email), 'login')
    return resp


@app.post('/api/auth/logout')
def auth_logout():
    resp = JSONResponse({'ok': True})
    resp.delete_cookie(auth.COOKIE, path='/')
    return resp


@app.get('/api/me')
def me(u=Depends(auth.require_user)):
    return {'email': u['email'], 'name': u.get('name') or '', 'role': u['role'],
            'advertisers': auth.advertisers_of(u)}


# ───────────── 대시보드 ─────────────
def _d(s, default):
    try:
        return date.fromisoformat(s) if s else default
    except ValueError:
        raise HTTPException(400, '날짜 형식은 YYYY-MM-DD 입니다.')


@app.get('/api/dashboard')
def api_dashboard(since: str = '', until: str = '', media: str = 'ALL', filter: str = '', vat: int = 1,
                  force: int = 0, u=Depends(auth.require_user)):
    t = collectors.today()
    s, e = _d(since, t.replace(day=1)), _d(until, t)
    if s > e or (e - s).days > 400:
        raise HTTPException(400, '기간을 확인해 주세요 (최대 400일).')
    return dashboard.build(auth.advertisers_of(u), s, e, media, filter, bool(vat), bool(force))


class TrendIn(BaseModel):
    startDate: str
    endDate: str
    timeUnit: str = 'date'
    groups: list
    device: str = ''
    estimate: bool = True      # 키워드도구 월간 검색수로 추정 검색량 계산


@app.post('/api/trend')
def api_trend(body: TrendIn, u=Depends(auth.require_user)):
    from . import trend
    groups = [{'name': str(g.get('name') or '').strip()[:40],
               'keywords': [str(k).strip() for k in g.get('keywords', []) if str(k).strip()]}
              for g in body.groups if isinstance(g, dict)]
    groups = [g for g in groups if g['name'] and g['keywords']]
    if not groups:
        raise HTTPException(400, '키워드를 입력해 주세요. 한 줄에 "그룹명: 키워드1, 키워드2" 형식입니다.')
    s, e = _d(body.startDate, None), _d(body.endDate, None)
    if not (s and e and e >= s):
        raise HTTPException(400, '기간을 확인해 주세요.')
    unit = body.timeUnit if body.timeUnit in ('date', 'week', 'month') else 'week'
    sa = None
    if body.estimate and S.settings.sa_ready:
        sa = collectors.sa_client()
    try:
        res = trend.build(groups, s, e, unit, body.device if body.device in ('pc', 'mo') else '',
                          collectors.today(), sa)
    except trend.TrendError as ex:
        raise HTTPException(502, str(ex))
    except Exception as ex:   # 키워드도구 오류여도 상대지표는 보여준다
        if sa is None:
            raise HTTPException(502, str(ex)[:300])
        res = trend.build(groups, s, e, unit, body.device if body.device in ('pc', 'mo') else '',
                          collectors.today(), None)
        res['notice'] = '키워드도구 조회 실패로 추정 검색량은 빠졌습니다: {}'.format(str(ex)[:150])
    if body.estimate and not S.settings.sa_ready:
        res['notice'] = '검색광고 키가 없어 추정 검색량은 빠졌습니다.'
    return res


_img = {'at': 0, 'map': {}}


@app.get('/api/creative-images')
def creative_images(u=Depends(auth.require_user)):
    """소재 이미지 폴더(드라이브)의 '소재ID.jpg' / '소재이름.jpg' 목록 → 서버 경유 주소 (외부 공유 시에도 보이도록)"""
    st = S.settings
    folder = os.environ.get('GFA_IMAGE_FOLDER_ID', '')
    if not (folder and st.google_ready):
        return {'images': {}}
    if time.time() - _img['at'] > 600:
        files = google.drive_list(folder)
        _img['map'] = {os.path.splitext(f['name'])[0].strip(): f['id'] for f in files}
        _img['at'] = time.time()
    return {'images': {k: 'api/creative-image/' + v for k, v in _img['map'].items()}}


_img_bytes = {}


@app.get('/api/creative-image/{file_id}')
def creative_image(file_id: str, u=Depends(auth.require_user)):
    if file_id not in _img['map'].values():
        raise HTTPException(404)
    if file_id not in _img_bytes:
        r = requests.get('https://www.googleapis.com/drive/v3/files/' + file_id,
                         params={'alt': 'media', 'supportsAllDrives': 'true'},
                         headers={'Authorization': 'Bearer ' + google.access_token()}, timeout=60)
        if r.status_code != 200:
            raise HTTPException(502)
        _img_bytes[file_id] = (r.content, r.headers.get('Content-Type', 'image/jpeg'))
    data, ctype = _img_bytes[file_id]
    return Response(data, media_type=ctype, headers={'Cache-Control': 'private, max-age=3600'})


# ───────────── 관리 (팀장) ─────────────
class UserIn(BaseModel):
    email: str
    name: str = ''
    role: str = 'member'
    advertisers: str = '*'
    active: bool = True
    memo: str = ''


@app.get('/api/admin/users')
def admin_users(u=Depends(auth.require_admin)):
    return [dict(x, created_at=str(x['created_at'] or ''), last_login=str(x['last_login'] or ''))
            for x in auth.list_users()]


@app.post('/api/admin/users')
def admin_user_save(body: UserIn, u=Depends(auth.require_admin)):
    auth.upsert_user(u['email'], body.email, body.name, body.role, body.advertisers, body.active, body.memo)
    return {'ok': True}


@app.delete('/api/admin/users/{email}')
def admin_user_remove(email: str, u=Depends(auth.require_admin)):
    auth.remove_user(u['email'], email)
    return {'ok': True}


class ContractIn(BaseModel):
    brand: str
    start: str
    end: str
    amount: float
    memo: str = ''
    advertiser: str = ''


@app.get('/api/admin/contracts')
def admin_contracts(u=Depends(auth.require_admin)):
    with db.engine().connect() as c:
        return [dict(r._mapping, start=str(r.start), end=str(r.end))
                for r in c.execute(select(db.contracts).order_by(db.contracts.c.brand, db.contracts.c.start))]


@app.post('/api/admin/contracts')
def admin_contract_add(body: ContractIn, u=Depends(auth.require_admin)):
    s, e = _d(body.start, None), _d(body.end, None)
    if not (s and e and e >= s and body.amount > 0 and body.brand.strip()):
        raise HTTPException(400, '브랜드·시작일·종료일·금액을 확인해 주세요.')
    with db.engine().begin() as c:
        c.execute(db.contracts.insert().values(advertiser=body.advertiser or S.settings.advertiser,
                                               brand=body.brand.strip(), start=s, end=e, amount=body.amount,
                                               memo=body.memo))
    db.log_audit(u['email'], 'contract_add', json.dumps(body.dict(), ensure_ascii=False))
    return {'ok': True}


@app.delete('/api/admin/contracts/{cid}')
def admin_contract_remove(cid: int, u=Depends(auth.require_admin)):
    with db.engine().begin() as c:
        c.execute(delete(db.contracts).where(db.contracts.c.id == cid))
    db.log_audit(u['email'], 'contract_remove', str(cid))
    return {'ok': True}


class RangeIn(BaseModel):
    since: str = ''
    until: str = ''


@app.post('/api/admin/collect')
def admin_collect(body: RangeIn, u=Depends(auth.require_admin)):
    s, e = _d(body.since, None), _d(body.until, None)
    if s and e and (e - s).days > 62:
        raise HTTPException(400, '한 번에 62일까지 수집할 수 있습니다. 나눠서 실행해 주세요.')
    db.log_audit(u['email'], 'collect', '{}~{}'.format(s, e))
    return _background('collect', report_job.run_collect_job, s, e)


@app.post('/api/admin/report')
def admin_report(u=Depends(auth.require_admin)):
    db.log_audit(u['email'], 'report_run')
    return _background('report', report_job.run_weekly_job)


# 수집·리포트는 몇 분 걸려 회사 서버 입구에서 화면 연결이 끊긴다(HTTP 504).
# → 서버 안에서 따로 돌리고 화면에는 바로 '시작' 응답. 결과는 '작업 기록'에서 확인.
_bg_lock = threading.Lock()
_bg_running = {}


def _background(kind, fn, *args):
    with _bg_lock:
        t = _bg_running.get('job')
        if t and t.is_alive():
            raise HTTPException(409, '이미 실행 중인 작업이 있습니다. 작업 기록에서 끝난 뒤 다시 눌러 주세요.')

        def run():
            try:
                fn(*args)
            except Exception:
                pass   # 오류 내용은 작업 기록(job_runs)에 남는다

        t = threading.Thread(target=run, name='admin-' + kind, daemon=True)
        _bg_running['job'] = t
        t.start()
    return {'started': True, 'kind': kind}


def _kst(dt):
    """DB에는 UTC로 저장 → 화면에는 한국시간으로"""
    return str(dt + timedelta(hours=9)) if dt else ''


@app.get('/api/admin/jobs')
def admin_jobs(u=Depends(auth.require_admin)):
    return [dict(j, started_at=_kst(j['started_at']), finished_at=_kst(j['finished_at']))
            for j in report_job.recent_jobs()]


@app.get('/api/admin/sa/conversions')
def admin_sa_conversions(since: str, until: str, u=Depends(auth.require_admin)):
    """검색광고 전환을 유형·직접/간접·매체별로 합계 (조회만, 저장 안 함) — 구매완료 기준 확인용"""
    s, e = _d(since, None), _d(until, None)
    if not (s and e and e >= s):
        raise HTTPException(400, '기간을 확인해 주세요.')
    if (e - s).days > 31:
        raise HTTPException(400, '진단은 한 번에 31일까지 가능합니다.')
    if not S.settings.sa_ready:
        raise HTTPException(503, '검색광고 키가 설정되지 않았습니다.')
    try:
        return collectors.sa_conversion_breakdown(s, e)
    except Exception as ex:
        raise HTTPException(502, '검색광고 보고서 오류: {}'.format(str(ex)[:300]))


@app.get('/api/admin/status')
def admin_status(u=Depends(auth.require_admin)):
    st = S.settings
    with db.engine().connect() as c:
        last = {r[0]: str(r[1]) for r in c.execute(
            select(db.raw.c.media, func.max(db.raw.c.date)).group_by(db.raw.c.media))}
    from . import scheduler
    return {'searchad': st.sa_ready, 'gfa': st.gfa_ready, 'gfaReportPath': st.gfa_collect_ready,
            'google': st.google_ready, 'pipeline': bool(st.pipeline_cmd), 'lastDate': last,
            'schedule': scheduler.describe()}


@app.get('/api/admin/gfa/explore')
def admin_gfa_explore(path: str, params: str = '{}', u=Depends(auth.require_admin)):
    """게이트웨이 GET 응답을 그대로 보여준다 — 성과 조회 경로·필드명 확인용 (조회만, 저장 안 함)"""
    try:
        p = json.loads(params or '{}')
    except ValueError:
        raise HTTPException(400, 'params 는 JSON 형식이어야 합니다.')
    try:
        payload = collectors.gfa_get(path, p)
    except collectors.GfaError as e:
        raise HTTPException(502, str(e))
    recs = collectors._records(payload)
    norm, fmap = collectors.normalize_gfa(recs[:50]) if recs else ([], {})
    return {'raw': payload if len(json.dumps(payload, ensure_ascii=False)) < 200000 else '(응답이 커서 생략)',
            'records': len(recs), 'fieldMap': fmap,
            'sample': [dict(r, date=str(r['date'])) for r in norm[:10]]}


@app.get('/api/admin/budgets')
def admin_budgets(month: str = '', u=Depends(auth.require_admin)):
    month = month or collectors.today().strftime('%Y-%m')
    try:
        return {'month': month, 'rows': google.monthly_budgets(month)}
    except google.GoogleError as e:
        raise HTTPException(503, str(e))


@app.get('/api/admin/audit')
def admin_audit(u=Depends(auth.require_admin)):
    with db.engine().connect() as c:
        return [dict(r._mapping, at=_kst(r.at)) for r in
                c.execute(select(db.audit).order_by(db.audit.c.id.desc()).limit(200))]
