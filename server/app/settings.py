# -*- coding: utf-8 -*-
"""서버 설정 — 전부 환경변수(.env)에서 읽는다. 키는 절대 코드/깃에 넣지 않는다."""
import os


def env(name, default=''):
    v = os.environ.get(name)
    return v if v not in (None, '') else default


def env_list(name, default=''):
    return [x.strip() for x in env(name, default).split(',') if x.strip()]


class Settings:
    def __init__(self):
        # 웹
        self.base_path = env('BASE_PATH', '').rstrip('/')        # 예: /seonhwa_choi/naver
        self.secret_key = env('APP_SECRET_KEY', '')                # 세션 서명용 임의 문자열 (필수)
        self.session_days = int(env('SESSION_DAYS', '14'))
        # 로그인: 사내 SSO가 사용자 이메일을 헤더로 넘겨주는 경우 그 헤더명 (모르면 비워두기 → 이메일 인증코드 로그인)
        self.sso_email_header = env('SSO_EMAIL_HEADER', '')
        # 최초 관리자 (이 이메일은 사용자 목록이 비어 있어도 관리자로 로그인 가능)
        self.bootstrap_admins = [e.lower() for e in env_list('BOOTSTRAP_ADMIN_EMAILS')]

        # DB — 운영: mysql+pymysql://user:pass@host:3306/db?charset=utf8mb4 / 테스트: sqlite:///...
        self.database_url = env('DATABASE_URL', 'sqlite:///./data/app.db')

        # 네이버 검색광고 API
        self.sa_customer_id = env('NAVER_SA_CUSTOMER_ID')
        self.sa_api_key = env('NAVER_SA_API_KEY')
        self.sa_secret_key = env('NAVER_SA_SECRET_KEY')
        # 전환 기준: report = 전환상세 보고서에서 지정 유형만(기본 구매완료, 리포트 Mars_Daily 기준)
        #           stats  = /stats 의 전체 전환(모든 유형 합계 — 구매완료보다 크게 나옴)
        self.sa_conv_source = env('SA_CONV_SOURCE', 'report')
        self.sa_conv_types = env_list('SA_CONV_TYPES') or ['purchase']
        self.sa_conv_methods = env_list('SA_CONV_METHODS')            # 비우면 직접+간접 모두
        # 네이버 데이터랩
        self.naver_client_id = env('NAVER_CLIENT_ID')
        self.naver_client_secret = env('NAVER_CLIENT_SECRET')

        # GFA (사내 게이트웨이)
        self.gfa_base = env('GFA_API_BASE_URL', 'https://ai.adef.co.kr/sunghyun_yoon/api/v1/public/gfa-proxy/')
        self.gfa_key = env('GFA_API_KEY')
        self.gfa_team = env('GFA_TEAM_NAME')
        self.gfa_account_id = env('GFA_AD_ACCOUNT_NO')
        # 게이트웨이가 모든 요청에 요구하는 계정 파라미터 이름 (안내서: account_id)
        self.gfa_account_param = env('GFA_ACCOUNT_PARAM', 'account_id')
        # 성과 조회 경로·파라미터·필드 매핑 — 게이트웨이 연결 후 '관리 > GFA 탐색'으로 확인해 채운다
        self.gfa_report_path = env('GFA_REPORT_PATH')               # 예: adAccounts/{account}/performance
        self.gfa_report_params = env('GFA_REPORT_PARAMS', '{}')     # JSON, {since}/{until}/{account} 치환
        self.gfa_field_map = env('GFA_FIELD_MAP', '{}')             # JSON, 표준컬럼 → 응답 필드

        # 광고주 구분 (나중에 광고주 공유 시 권한 범위로 사용)
        self.advertiser = env('ADVERTISER_KEY', 'mars')

        # 구글 (드라이브 업로드 · 인증코드 메일 · 월예산 시트) — 미리 발급한 OAuth 토큰 방식
        self.google_client_id = env('GOOGLE_CLIENT_ID')
        self.google_client_secret = env('GOOGLE_CLIENT_SECRET')
        self.google_refresh_token = env('GOOGLE_REFRESH_TOKEN')
        self.drive_weekly_folder_id = env('DRIVE_WEEKLY_FOLDER_ID')
        self.budget_sheet_id = env('BUDGET_SHEET_ID')
        self.mail_from_name = env('MAIL_FROM_NAME', 'PD-1팀 네이버 대시보드')

        # 리포트 자동 작성
        self.marsrep_dir = env('MARSREP_DIR', '/app/marsrep')       # 데이터팀 패키지 (서버에만 둠, 깃 금지)
        self.work_dir = env('WORK_DIR', './data/work')
        self.report_enabled = env('REPORT_ENABLED', '0') == '1'
        self.raw_upload_folder_id = env('RAW_UPLOAD_FOLDER_ID')
        # 서버에서 엑셀까지 작성할 때의 실행 명령 ({daily} {result} {since} {until} {workdir} 치환,
        # 결과 파일은 표준출력에 'OUTPUT: 경로' 로 알려준다). 비워두면 로우 파일만 만들어 업로드.
        self.pipeline_cmd = env('REPORT_PIPELINE_CMD')
        self.anthropic_api_key = env('ANTHROPIC_API_KEY')           # 있으면 코멘트 문장 다듬기에 사용(선택)

        # 일정 (한국시간)
        self.collect_cron = env('COLLECT_CRON', '0 7 * * *')         # 매일 07:00 수집
        self.report_cron = env('REPORT_CRON', '30 13 * * mon')       # 월요일 13:30 리포트
        self.recollect_days = int(env('RECOLLECT_DAYS', '7'))        # 늦게 잡히는 전환 반영

    @property
    def sa_ready(self):
        return bool(self.sa_customer_id and self.sa_api_key and self.sa_secret_key)

    @property
    def gfa_ready(self):
        return bool(self.gfa_key and self.gfa_team)

    @property
    def google_ready(self):
        return bool(self.google_client_id and self.google_client_secret and self.google_refresh_token)


settings = Settings()


def reload():
    global settings
    settings = Settings()
    return settings
