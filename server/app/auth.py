# -*- coding: utf-8 -*-
"""로그인 · 권한.

로그인 방법 (둘 중 가능한 것)
  1) 사내 SSO가 사용자 이메일을 헤더로 넘겨주면(SSO_EMAIL_HEADER) 그대로 사용
  2) 이메일 인증코드: 등록된 이메일로 6자리 코드를 보내고 입력받음 (외부 광고주도 가능)
어느 경우든 '사용자 목록'에 등록되고 활성화된 사람만 들어올 수 있다. 목록은 관리자가 화면에서 추가/제거.

역할
  admin  : 전체 열람 + 사용자 관리 + 수집/리포트 실행
  member : 지정된 광고주 열람 (팀원)
  client : 지정된 광고주 열람, 관리 메뉴·내부 메모 숨김 (광고주 공유용)
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, select, update

from . import db
from . import settings as S

COOKIE = 'nd_session'
ROLES = ('admin', 'member', 'client')
CODE_TTL_MIN = 10
CODE_MAX_TRIES = 5


def _serializer():
    if not S.settings.secret_key:
        raise RuntimeError('.env 에 APP_SECRET_KEY 를 설정하세요 (임의의 긴 문자열).')
    return URLSafeTimedSerializer(S.settings.secret_key, salt='nd-session')


def norm_email(e):
    return str(e or '').strip().lower()


def get_user(email):
    email = norm_email(email)
    with db.engine().connect() as c:
        row = c.execute(select(db.users).where(db.users.c.email == email)).first()
    if row:
        return dict(row._mapping)
    if email in S.settings.bootstrap_admins:
        return {'email': email, 'name': '', 'role': 'admin', 'advertisers': '*', 'active': True}
    return None


def allowed(user):
    return bool(user and user.get('active'))


def make_session(email):
    return _serializer().dumps({'e': norm_email(email)})


def current_user(request: Request):
    st = S.settings
    if st.sso_email_header:
        sso = request.headers.get(st.sso_email_header)
        if sso:
            u = get_user(sso)
            if allowed(u):
                return u
    tok = request.cookies.get(COOKIE)
    if tok:
        try:
            data = _serializer().loads(tok, max_age=st.session_days * 86400)
            u = get_user(data.get('e'))
            if allowed(u):
                return u
        except (BadSignature, SignatureExpired):
            pass
    return None


def require_user(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401, '로그인이 필요합니다.')
    return u


def require_admin(request: Request):
    u = require_user(request)
    if u['role'] != 'admin':
        raise HTTPException(403, '관리자만 사용할 수 있습니다.')
    return u


def advertisers_of(user):
    a = [x.strip() for x in str(user.get('advertisers') or '').split(',') if x.strip()]
    return ['*'] if (user['role'] == 'admin' or '*' in a) else a


# ── 이메일 인증코드 ──
def _hash(code, email):
    return hmac.new(S.settings.secret_key.encode(), (email + ':' + code).encode(), hashlib.sha256).hexdigest()


def issue_code(email, sender):
    """등록된 사용자에게만 발송. 미등록이어도 같은 응답을 돌려 목록 노출을 막는다."""
    email = norm_email(email)
    if not allowed(get_user(email)):
        return False
    code = '{:06d}'.format(secrets.randbelow(1000000))
    with db.engine().begin() as c:
        c.execute(delete(db.login_codes).where(db.login_codes.c.email == email))
        c.execute(db.login_codes.insert().values(email=email, code_hash=_hash(code, email), tries=0,
                                                 expires_at=datetime.utcnow() + timedelta(minutes=CODE_TTL_MIN)))
    sender(email, '[네이버 광고 대시보드] 로그인 인증코드',
           '인증코드: {}\n\n{}분 안에 입력해 주세요. 요청하지 않았다면 이 메일을 무시하세요.'.format(code, CODE_TTL_MIN))
    return True


def verify_code(email, code):
    email = norm_email(email)
    with db.engine().begin() as c:
        row = c.execute(select(db.login_codes).where(db.login_codes.c.email == email)).first()
        if not row:
            return False
        row = row._mapping
        if row['expires_at'] < datetime.utcnow() or row['tries'] >= CODE_MAX_TRIES:
            c.execute(delete(db.login_codes).where(db.login_codes.c.email == email))
            return False
        if not hmac.compare_digest(row['code_hash'], _hash(str(code).strip(), email)):
            c.execute(update(db.login_codes).where(db.login_codes.c.email == email)
                      .values(tries=row['tries'] + 1))
            return False
        c.execute(delete(db.login_codes).where(db.login_codes.c.email == email))
    if not allowed(get_user(email)):
        return False
    with db.engine().begin() as c:
        c.execute(update(db.users).where(db.users.c.email == email).values(last_login=datetime.utcnow()))
    return True


# ── 사용자 관리 (관리자) ──
def list_users():
    with db.engine().connect() as c:
        return [dict(r._mapping) for r in c.execute(select(db.users).order_by(db.users.c.role, db.users.c.email))]


def upsert_user(actor, email, name='', role='member', advertisers='*', active=True, memo=''):
    email = norm_email(email)
    if '@' not in email:
        raise HTTPException(400, '이메일 형식이 아닙니다.')
    if role not in ROLES:
        raise HTTPException(400, '역할은 admin / member / client 중 하나입니다.')
    if role == 'client' and advertisers.strip() in ('', '*'):
        raise HTTPException(400, '광고주(client) 계정은 볼 수 있는 광고주를 지정해야 합니다.')
    vals = dict(name=name, role=role, advertisers=advertisers.strip() or '*', active=bool(active), memo=memo)
    with db.engine().begin() as c:
        if c.execute(select(db.users.c.email).where(db.users.c.email == email)).first():
            c.execute(update(db.users).where(db.users.c.email == email).values(**vals))
            act = 'user_update'
        else:
            c.execute(db.users.insert().values(email=email, **vals))
            act = 'user_add'
    db.log_audit(actor, act, '{} role={} adv={} active={}'.format(email, role, vals['advertisers'], vals['active']))


def remove_user(actor, email):
    email = norm_email(email)
    if email == norm_email(actor):
        raise HTTPException(400, '본인 계정은 삭제할 수 없습니다.')
    with db.engine().begin() as c:
        c.execute(delete(db.users).where(db.users.c.email == email))
    db.log_audit(actor, 'user_remove', email)
