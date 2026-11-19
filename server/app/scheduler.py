# -*- coding: utf-8 -*-
"""예약 작업 (한국시간). uvicorn 워커는 1개로 실행해야 중복 실행되지 않는다."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import report_job
from . import settings as S

log = logging.getLogger('scheduler')
_sched = None


def _safe(fn, name):
    def run():
        try:
            fn()
        except Exception:
            log.exception('%s 실패', name)
    return run


def start():
    global _sched
    if _sched:
        return _sched
    st = S.settings
    _sched = BackgroundScheduler(timezone='Asia/Seoul')
    _sched.add_job(_safe(report_job.run_collect_job, '매일 수집'),
                   CronTrigger.from_crontab(st.collect_cron, timezone='Asia/Seoul'),
                   id='collect', max_instances=1, coalesce=True, misfire_grace_time=3600)
    if st.report_enabled:
        _sched.add_job(_safe(report_job.run_weekly_job, '주간 리포트'),
                       CronTrigger.from_crontab(st.report_cron, timezone='Asia/Seoul'),
                       id='report', max_instances=1, coalesce=True, misfire_grace_time=3600)
    _sched.start()
    return _sched


def describe():
    if not _sched:
        return []
    return [{'id': j.id, 'next': str(j.next_run_time)} for j in _sched.get_jobs()]
