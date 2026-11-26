# -*- coding: utf-8 -*-
"""DB — 운영은 사내 MariaDB, 테스트는 SQLite. SQLAlchemy Core만 사용."""
import os
import time
from datetime import datetime

from sqlalchemy.exc import OperationalError
from sqlalchemy import (Boolean, Column, Date, DateTime, Float, Integer, MetaData, String, Table, Text,
                        create_engine, delete, select)

from . import settings as S

meta = MetaData()

# 일자별 성과 로우 (광고그룹·소재 단위). 기존 로우데이터 형식 + 광고주/소재
raw = Table(
    'raw_daily', meta,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('advertiser', String(40), nullable=False, default='mars'),
    Column('date', Date, nullable=False, index=True),
    Column('media', String(20), nullable=False),          # GFA / NaverBS / NaverSA / NaverShopping
    Column('device', String(10), nullable=False, default='ALL'),
    Column('campaign', String(255), nullable=False, default=''),
    Column('adgroup', String(255), nullable=False, default=''),
    Column('creative', String(255), nullable=False, default=''),
    Column('creative_id', String(64), nullable=False, default=''),
    Column('imps', Float, default=0), Column('clicks', Float, default=0), Column('cost', Float, default=0),
    Column('conv', Float, default=0), Column('revenue', Float, default=0),
    Column('collected_at', DateTime, default=datetime.utcnow),
    mysql_charset='utf8mb4',
)

# 브랜드검색 CPT 계약
contracts = Table(
    'bs_contracts', meta,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('advertiser', String(40), nullable=False, default='mars'),
    Column('brand', String(60), nullable=False),
    Column('start', Date, nullable=False), Column('end', Date, nullable=False),
    Column('amount', Float, nullable=False),               # 계약금액 VAT 제외
    Column('memo', String(255), default=''),
    mysql_charset='utf8mb4',
)

# 접근 권한 — 팀장(관리자)이 화면에서 추가/제거
users = Table(
    'users', meta,
    Column('email', String(190), primary_key=True),
    Column('name', String(60), default=''),
    Column('role', String(20), nullable=False, default='member'),   # admin / member / client
    Column('advertisers', String(255), nullable=False, default='*'),  # 볼 수 있는 광고주 (쉼표, *=전체)
    Column('active', Boolean, nullable=False, default=True),
    Column('memo', String(255), default=''),
    Column('created_at', DateTime, default=datetime.utcnow),
    Column('last_login', DateTime, nullable=True),
    mysql_charset='utf8mb4',
)

login_codes = Table(
    'login_codes', meta,
    Column('email', String(190), primary_key=True),
    Column('code_hash', String(128), nullable=False),
    Column('expires_at', DateTime, nullable=False),
    Column('tries', Integer, default=0),
    mysql_charset='utf8mb4',
)

audit = Table(
    'audit_log', meta,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('at', DateTime, default=datetime.utcnow),
    Column('actor', String(190), default=''),
    Column('action', String(60), default=''),
    Column('detail', Text),
    mysql_charset='utf8mb4',
)

jobs = Table(
    'job_runs', meta,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('kind', String(30)),                # collect / report
    Column('started_at', DateTime, default=datetime.utcnow),
    Column('finished_at', DateTime, nullable=True),
    Column('status', String(20), default='running'),   # running / ok / error
    Column('message', Text),
    Column('output', Text),                    # 결과 파일 링크 등 (JSON)
    mysql_charset='utf8mb4',
)

_engine = None


def engine():
    global _engine
    if _engine is None:
        url = S.settings.database_url
        kw = {'pool_pre_ping': True}
        if url.startswith('sqlite'):
            path = url.split('///', 1)[-1]
            if path and path != ':memory:':
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            kw = {'connect_args': {'check_same_thread': False}}
        else:
            kw['pool_recycle'] = 3600
        eng = create_engine(url, **kw)
        # 서버를 켤 때 DB(MariaDB)가 아직 준비 중일 수 있어 최대 90초 기다린다
        for i in range(30):
            try:
                meta.create_all(eng)
                break
            except OperationalError:
                if i == 29:
                    raise
                time.sleep(3)
        _engine = eng
    return _engine


def reset_engine():
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


RAW_KEYS = ('advertiser', 'date', 'media', 'device', 'campaign', 'adgroup', 'creative', 'creative_id',
            'imps', 'clicks', 'cost', 'conv', 'revenue')


def replace_raw(advertiser, medias, since, until, rows):
    """[since~until] 기간의 해당 매체 로우를 지우고 새로 넣는다 (재수집 시 중복 방지)."""
    with engine().begin() as c:
        c.execute(delete(raw).where(raw.c.advertiser == advertiser, raw.c.media.in_(list(medias)),
                                    raw.c.date >= since, raw.c.date <= until))
        if rows:
            now = datetime.utcnow()
            c.execute(raw.insert(), [dict({k: r.get(k, '' if k in ('creative', 'creative_id', 'campaign', 'adgroup')
                                                  else 0) for k in RAW_KEYS},
                                          advertiser=advertiser, device=r.get('device') or 'ALL',
                                          collected_at=now) for r in rows])
    return len(rows)


def read_raw(advertisers, since, until):
    q = select(raw).where(raw.c.date >= since, raw.c.date <= until)
    if advertisers != ['*']:
        q = q.where(raw.c.advertiser.in_(advertisers))
    with engine().connect() as c:
        return [dict(r._mapping) for r in c.execute(q.order_by(raw.c.date))]


def log_audit(actor, action, detail=''):
    with engine().begin() as c:
        c.execute(audit.insert().values(actor=actor, action=action, detail=detail))
