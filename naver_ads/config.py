# -*- coding: utf-8 -*-
"""API 키 로딩.

키는 절대 코드/깃에 넣지 않는다. 우선순위:
  1) Streamlit secrets (.streamlit/secrets.toml 또는 Streamlit Cloud > Settings > Secrets)
  2) 환경변수
"""
import os

KEYS = [
    # 네이버 검색광고 API (searchad.naver.com > 도구 > API 사용 관리)
    'NAVER_SA_CUSTOMER_ID', 'NAVER_SA_API_KEY', 'NAVER_SA_SECRET_KEY',
    # 네이버 개발자센터 애플리케이션 (데이터랩 검색어 트렌드 등)
    'NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET',
    # 네이버 GFA (대행사 공통키) — 규격 확정 후 사용
    'GFA_API_KEY', 'GFA_API_BASE_URL', 'GFA_KEY_HEADER', 'GFA_STATS_PATH', 'GFA_AD_ACCOUNT_NO',
]


def _from_streamlit(name):
    try:
        import streamlit as st
        if name in st.secrets:
            return str(st.secrets[name])
        # [naver] 같은 섹션 형태도 허용
        for section in ('naver', 'naver_sa', 'gfa'):
            if section in st.secrets and name in st.secrets[section]:
                return str(st.secrets[section][name])
    except Exception:
        pass
    return None


def get(name, default=None):
    v = _from_streamlit(name)
    if v is None:
        v = os.environ.get(name)
    return v if v not in (None, '') else default


def load_all():
    return {k: get(k) for k in KEYS}


def mask(v, keep=4):
    if not v:
        return '(미설정)'
    return v[:keep] + '…' + v[-2:] if len(v) > keep + 2 else '***'
