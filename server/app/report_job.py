# -*- coding: utf-8 -*-
"""월요일 리포트 작업.

1) 당월 1일~전일 재수집 (늦게 잡히는 전환 반영 — 담당자 규칙)
2) DB → 기존 로우 파일과 같은 형식으로 내보내기
     Mars_Daily.xlsx : Date, Media, Device, 캠페인, 그룹, 노출, 클릭, 광고비, 구매완료수, 매출액 (GFA·브랜드검색)
     result.csv      : GFA 소재별 일자 export 형식 (광고 소재 이름, 광고 소재 ID, … , 구매완료 광고수익률(%))
3) 드라이브 '로우업로드' 폴더에 업로드 → 지금의 주간 작성 절차가 그대로 이어받는다
4) (선택) REPORT_PIPELINE_CMD 가 설정되어 있으면 서버에서 엑셀까지 작성해 '위클리' 폴더에 저장
"""
import csv
import json
import os
import shlex
import subprocess
import traceback
from datetime import datetime, timedelta

import openpyxl
from sqlalchemy import select, update

from . import collectors, db, google
from .rules import device_of
from . import settings as S

DAILY_COLS = ['Date', 'Media', 'Device', '캠페인', '그룹', '노출', '클릭', '광고비', '구매완료수', '매출액']
RESULT_COLS = ['광고 소재 이름', '광고 소재 ID', '광고 그룹 이름', '광고 그룹 ID', '캠페인 이름', '캠페인 ID', '기간',
               '총비용', '노출수', '클릭수', '구매완료 수', '구매완료 전환매출액', '구매완료 광고수익률(%)']


def _n(v):
    v = float(v or 0)
    return int(v) if v.is_integer() else round(v, 2)


def export_daily(rows, path):
    """GFA는 소재 행을 광고그룹 단위로 합쳐 기존 Mars_Daily 와 같은 단위로 만든다."""
    agg = {}
    for r in rows:
        if r['media'] not in ('GFA', 'NaverBS'):
            continue
        # 기존 Mars_Daily 표기에 맞춤: GFA Device '-', 브랜드검색 'M'/'P', ADVoost(애셋그룹) 그룹명 빈칸
        if r['media'] == 'GFA':
            dev = '-'
        else:
            dev = {'MO': 'M', 'PC': 'P'}.get(device_of(r['adgroup'], r['campaign']), r['device'] or 'ALL')
        grp = '' if str(r['adgroup']).startswith('애셋그룹 ') else r['adgroup']
        k = (r['date'], r['media'], dev, r['campaign'], grp)
        a = agg.setdefault(k, [0.0] * 5)
        for i, f in enumerate(('imps', 'clicks', 'cost', 'conv', 'revenue')):
            a[i] += float(r[f] or 0)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Msrs Daily'
    ws.append(DAILY_COLS)
    for k in sorted(agg, key=lambda x: (x[0], x[1], x[3], x[4])):
        ws.append([k[0].strftime('%Y-%m-%d'), k[1], k[2], k[3], k[4] or None] + [_n(v) for v in agg[k]])
    wb.save(path)
    return len(agg)


def export_result(rows, path):
    n = 0
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(RESULT_COLS)
        for r in sorted((r for r in rows if r['media'] == 'GFA' and r['creative']),
                        key=lambda r: (r['date'], r['campaign'], r['adgroup'], r['creative'])):
            cost, rev = float(r['cost'] or 0), float(r['revenue'] or 0)
            w.writerow([r['creative'], r['creative_id'], r['adgroup'], '', r['campaign'], '',
                        r['date'].strftime('%Y.%m.%d.'), _n(cost), _n(r['imps']), _n(r['clicks']), _n(r['conv']),
                        _n(rev), '%.2f' % (rev / cost * 100) if cost else '0.00'])
            n += 1
    return n


def _job_start(kind):
    with db.engine().begin() as c:
        return c.execute(db.jobs.insert().values(kind=kind, status='running', message='')).inserted_primary_key[0]


def _job_end(jid, status, message, output=None):
    with db.engine().begin() as c:
        c.execute(update(db.jobs).where(db.jobs.c.id == jid).values(
            status=status, message=message[:5000], finished_at=datetime.utcnow(),
            output=json.dumps(output or {}, ensure_ascii=False)))


def recent_jobs(limit=20):
    with db.engine().connect() as c:
        return [dict(r._mapping) for r in c.execute(select(db.jobs).order_by(db.jobs.c.id.desc()).limit(limit))]


def run_collect_job(since=None, until=None):
    jid = _job_start('collect')
    try:
        res = collectors.run_collect(since, until)
        bad = [k for k, v in res['result'].items() if isinstance(v, str) and v.startswith('오류')]
        _job_end(jid, 'error' if bad else 'ok', json.dumps(res, ensure_ascii=False), res)
        return res
    except Exception as e:
        _job_end(jid, 'error', '{}\n{}'.format(e, traceback.format_exc()[-2000:]))
        raise


def work_dir():
    """쓸 수 있는 작업 폴더 — 지정 폴더에 권한이 없으면 임시 폴더로 바꾼다"""
    import tempfile
    d = S.settings.work_dir
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, '.write_test')
        open(probe, 'w').close()
        os.remove(probe)
        return d
    except OSError:
        d = os.path.join(tempfile.gettempdir(), 'naver-dashboard-work')
        os.makedirs(d, exist_ok=True)
        return d


def run_weekly_job(report_day=None, upload=True):
    """report_day: 작성일(기본 오늘). 데이터 기간 = 당월 1일 ~ 작성일 전일."""
    st = S.settings
    report_day = report_day or collectors.today()
    until = report_day - timedelta(days=1)
    since = until.replace(day=1)
    jid = _job_start('report')
    out, log = {}, []
    try:
        res = collectors.run_collect(since, until)
        log.append('재수집 {}~{}: {}'.format(since, until, res['result']))
        rows = db.read_raw([st.advertiser], since, until)
        wd = work_dir()
        tag = report_day.strftime('%y%m%d')
        daily = os.path.join(wd, 'Mars_Daily_자동_{}.xlsx'.format(tag))
        result = os.path.join(wd, 'result_자동_{}.csv'.format(tag))
        log.append('Mars_Daily {}행, result {}행'.format(export_daily(rows, daily), export_result(rows, result)))
        out['files'] = [daily, result]
        if upload and st.google_ready and st.raw_upload_folder_id:
            for p, mime in ((daily, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                            (result, 'text/csv')):
                f = google.drive_upload(p, os.path.basename(p), st.raw_upload_folder_id, mime=mime)
                out.setdefault('drive', []).append(f)
            log.append('로우업로드 폴더 업로드 완료')
        if st.pipeline_cmd:
            cmd = st.pipeline_cmd.format(daily=shlex.quote(daily), result=shlex.quote(result),
                                         since=since, until=until, workdir=shlex.quote(wd))
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3600)
            log.append('리포트 작성 종료코드 {}\n{}'.format(p.returncode, (p.stdout + p.stderr)[-3000:]))
            if p.returncode != 0:
                raise RuntimeError('리포트 작성 실패 (종료코드 {})'.format(p.returncode))
            made = [l.split('OUTPUT:', 1)[1].strip() for l in p.stdout.splitlines() if l.startswith('OUTPUT:')]
            if upload and st.google_ready and st.drive_weekly_folder_id:
                for path in made:
                    f = google.drive_upload(path, os.path.basename(path), st.drive_weekly_folder_id)
                    out.setdefault('weekly', []).append(f)
        _job_end(jid, 'ok', '\n'.join(log), out)
        return out
    except Exception as e:
        log.append('오류: {}'.format(e))
        _job_end(jid, 'error', '\n'.join(log) + '\n' + traceback.format_exc()[-2000:], out)
        raise
