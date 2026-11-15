# -*- coding: utf-8 -*-
"""한국마즈 네이버 검색광고 / GFA API 실시간 대시보드 + 리포트 출력.

키는 .streamlit/secrets.toml (로컬) 또는 Streamlit Cloud > Settings > Secrets 에 넣는다.
(.streamlit/secrets.toml.example 참고 — 실제 키 파일은 .gitignore 처리되어 깃에 올라가지 않음)
"""
import io
import os
import sys
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import raw_to_report  # noqa: E402
from naver_ads import config, datalab, gfa, raw_builder, searchad  # noqa: E402

st.set_page_config(page_title="네이버 API 대시보드", layout="wide")
st.title("📡 한국마즈 네이버 광고 API 대시보드")
st.caption("검색광고 API 실시간 조회 · GFA 데이터 연동 · 키워드 트렌드 · 주간 리포트 엑셀 출력")

cfg = config.load_all()
sa_ready = all(cfg[k] for k in ('NAVER_SA_CUSTOMER_ID', 'NAVER_SA_API_KEY', 'NAVER_SA_SECRET_KEY'))
dl_ready = bool(cfg['NAVER_CLIENT_ID'] and cfg['NAVER_CLIENT_SECRET'])
gfa_client = gfa.GFAClient(cfg['GFA_API_KEY'], cfg['GFA_API_BASE_URL'], cfg['GFA_KEY_HEADER'],
                           cfg['GFA_STATS_PATH'], cfg['GFA_AD_ACCOUNT_NO'])

# ── 사이드바: 연결 상태 ───────────────────────────────────────
with st.sidebar:
    st.header("🔌 연결 상태")
    st.write(("🟢" if sa_ready else "🔴") + " 검색광고 API  ·  CUSTOMER_ID " + config.mask(cfg['NAVER_SA_CUSTOMER_ID'], 3))
    st.write(("🟢" if dl_ready else "🔴") + " 데이터랩 (Client ID " + config.mask(cfg['NAVER_CLIENT_ID']) + ")")
    if gfa_client.configured:
        st.write("🟢 GFA API")
    elif cfg['GFA_API_KEY']:
        st.write("🟡 GFA 키 등록됨 · API 규격 설정 대기 (파일 업로드로 사용)")
    else:
        st.write("🔴 GFA 키 미설정 (파일 업로드로 사용)")
    st.divider()
    vat_exclude = st.checkbox("검색광고 광고비 VAT 제외 환산 (÷1.1)", value=False,
                              help="API 광고비(salesAmt)의 VAT 포함 여부는 광고관리 화면의 '총비용'과 한 번 대조해서 맞춰주세요.")
    if st.button("🔄 캐시 비우고 새로 조회"):
        st.cache_data.clear()
        st.rerun()
    st.caption("조회 결과는 10분간 캐시됩니다. 당일 데이터는 네이버 집계 지연으로 수십 분~수 시간 늦게 반영될 수 있습니다.")

if not sa_ready:
    st.warning("검색광고 API 키가 없습니다. `.streamlit/secrets.toml.example`을 참고해 키를 등록해주세요.")


@st.cache_data(ttl=600, show_spinner=False)
def load_sa_rows(since, until, name_filter):
    c = config.load_all()
    client = searchad.SearchAdClient(c['NAVER_SA_CUSTOMER_ID'], c['NAVER_SA_API_KEY'], c['NAVER_SA_SECRET_KEY'])
    flt = (lambda camp: name_filter.lower() in (camp.get('name') or '').lower()) if name_filter else None
    return searchad.fetch_daily_raw_rows(client, since, until, campaign_filter=flt)


def period_from_preset(preset, custom=None):
    t = searchad.today()
    if preset == "오늘":
        return t, t
    if preset == "어제":
        return t - timedelta(days=1), t - timedelta(days=1)
    if preset == "최근 7일 (어제까지)":
        return t - timedelta(days=7), t - timedelta(days=1)
    if preset == "지난주 (월~일)":
        mon = t - timedelta(days=t.weekday() + 7)
        return mon, mon + timedelta(days=6)
    if preset == "이번 달 (어제까지)":
        return t.replace(day=1), max(t.replace(day=1), t - timedelta(days=1))
    return custom


def kpi_row(df):
    tot = df[['노출', '클릭', '광고비', '구매완료수', '매출액']].sum()
    ctr = tot['클릭'] / tot['노출'] * 100 if tot['노출'] else 0
    cpc = tot['광고비'] / tot['클릭'] if tot['클릭'] else 0
    roas = tot['매출액'] / tot['광고비'] * 100 if tot['광고비'] else 0
    cols = st.columns(7)
    cols[0].metric("노출", f"{tot['노출']:,.0f}")
    cols[1].metric("클릭", f"{tot['클릭']:,.0f}")
    cols[2].metric("CTR", f"{ctr:.2f}%")
    cols[3].metric("광고비", f"{tot['광고비']:,.0f}원")
    cols[4].metric("CPC", f"{cpc:,.0f}원")
    cols[5].metric("전환수", f"{tot['구매완료수']:,.0f}")
    cols[6].metric("ROAS", f"{roas:,.0f}%")


def agg_table(df, keys):
    g = df.groupby(keys, as_index=False)[['노출', '클릭', '광고비', '구매완료수', '매출액']].sum()
    g = raw_builder.add_kpis(g)
    return g.sort_values('광고비', ascending=False)


FMT = {'노출': '{:,.0f}', '클릭': '{:,.0f}', '광고비': '{:,.0f}', '구매완료수': '{:,.0f}', '매출액': '{:,.0f}',
       'CTR(%)': '{:.2f}', 'CPC': '{:,.0f}', 'CPA': '{:,.0f}', 'CVR(%)': '{:.2f}', 'ROAS(%)': '{:,.0f}'}

tab_sa, tab_gfa, tab_trend, tab_report = st.tabs(
    ["🔎 검색광고 (실시간)", "🖼️ GFA 디스플레이", "📈 키워드 트렌드", "🧾 리포트 출력"])

# ── 1) 검색광고 ────────────────────────────────────────────────
with tab_sa:
    c1, c2, c3 = st.columns([1.2, 1.2, 1])
    with c1:
        preset = st.selectbox("기간", ["오늘", "어제", "최근 7일 (어제까지)", "지난주 (월~일)", "이번 달 (어제까지)", "직접 선택"], index=2)
    custom = None
    with c2:
        if preset == "직접 선택":
            t = searchad.today()
            rng = st.date_input("기간 선택", (t - timedelta(days=14), t - timedelta(days=1)))
            custom = tuple(rng) if isinstance(rng, (list, tuple)) and len(rng) == 2 else None
    with c3:
        name_filter = st.text_input("캠페인명 필터 (선택)", placeholder="예: PET")
    period = period_from_preset(preset, custom)

    if sa_ready and period and st.button("📥 검색광고 데이터 조회", type="primary"):
        since, until = period
        try:
            with st.spinner(f"{since} ~ {until} 광고그룹별 일별 통계 조회 중..."):
                st.session_state['sa_rows'] = load_sa_rows(since, until, name_filter.strip())
                st.session_state['sa_period'] = (since, until)
        except Exception as e:
            st.error(f"조회 실패: {e}")

    rows = st.session_state.get('sa_rows')
    if rows is not None:
        since, until = st.session_state['sa_period']
        if not rows:
            st.info(f"{since} ~ {until} 기간에 노출/클릭 데이터가 없습니다.")
        else:
            df = raw_builder.to_dataframe(rows)
            if vat_exclude:
                df['광고비'] = df['광고비'] / 1.1
            st.subheader(f"📊 {since} ~ {until} 요약")
            kpi_row(df)

            daily = agg_table(df, ['Date']).sort_values('Date')
            fig = go.Figure()
            fig.add_bar(name='광고비', x=daily['Date'], y=daily['광고비'])
            fig.add_bar(name='매출액', x=daily['Date'], y=daily['매출액'])
            fig.add_trace(go.Scatter(name='ROAS(%)', x=daily['Date'], y=daily['ROAS(%)'], mode='lines+markers', yaxis='y2'))
            fig.update_layout(barmode='group', yaxis=dict(title='원'),
                              yaxis2=dict(title='ROAS(%)', overlaying='y', side='right'),
                              legend=dict(orientation='h', y=1.1), margin=dict(t=40), height=380)
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("**캠페인별 성과**")
            st.dataframe(agg_table(df, ['Media', '캠페인']).style.format(FMT), use_container_width=True, hide_index=True)
            with st.expander("광고그룹별 성과 보기"):
                st.dataframe(agg_table(df, ['Media', '캠페인', '그룹']).style.format(FMT), use_container_width=True, hide_index=True)
            if df['매출액'].sum() == 0 and df['구매완료수'].sum() > 0:
                st.caption("※ 전환매출(convAmt)이 0으로 나오면 계정에 전환매출 집계가 설정되지 않았거나 API에서 해당 필드를 지원하지 않는 경우입니다.")

# ── 2) GFA ─────────────────────────────────────────────────────
with tab_gfa:
    st.info("GFA API는 대행사 공통키로 발급되며, 공개 기술문서가 없어 파트너사 지원 메일(agency_cs@navercorp.com)로 "
            "API 주소·인증 헤더·리포트 엔드포인트 규격을 받아야 합니다. 규격을 받으면 secrets에 값만 넣으면 연결됩니다. "
            "그 전까지는 GFA 광고관리자에서 받은 **일별 리포트 파일**을 올려서 사용하세요.")
    if gfa_client.configured:
        g1, g2 = st.columns(2)
        gs = g1.date_input("GFA 시작일", searchad.today() - timedelta(days=7), key='gfa_s')
        ge = g2.date_input("GFA 종료일", searchad.today() - timedelta(days=1), key='gfa_e')
        if st.button("GFA API 연결 테스트"):
            try:
                st.json(gfa_client.fetch(gs, ge))
            except Exception as e:
                st.error(f"GFA API 호출 실패: {e}")

    up = st.file_uploader("GFA 일별 리포트 업로드 (.csv / .xlsx)", type=['csv', 'xlsx'], key='gfa_up')
    if up:
        try:
            if up.name.lower().endswith('.csv'):
                raw = up.getvalue()
                try:
                    gdf = pd.read_csv(io.BytesIO(raw), encoding='utf-8-sig')
                except UnicodeDecodeError:
                    gdf = pd.read_csv(io.BytesIO(raw), encoding='cp949')
            else:
                gdf = pd.read_excel(io.BytesIO(up.getvalue()))
        except Exception as e:
            st.error(f"파일을 읽지 못했습니다: {e}")
            gdf = None
        if gdf is not None:
            st.caption(f"{len(gdf):,}행 · 컬럼: {', '.join(map(str, gdf.columns))}")
            guess = gfa.guess_mapping(gdf.columns)
            opts = ['(없음)'] + [str(c) for c in gdf.columns]
            mcols = st.columns(4)
            mapping = {}
            for i, k in enumerate(gfa.COLUMN_CANDIDATES):
                default = opts.index(guess[k]) if guess.get(k) in opts else 0
                sel = mcols[i % 4].selectbox(f"{k} 컬럼", opts, index=default, key=f"map_{k}")
                mapping[k] = None if sel == '(없음)' else sel
            try:
                norm = gfa.normalize_export(gdf, mapping)
                st.session_state['gfa_rows'] = norm
                unknown = norm[~norm['캠페인'].str.startswith(('(Mars Conf)', '(Mars Pet)'))]['캠페인'].unique()
                st.success(f"GFA 데이터 {len(norm):,}행 변환 완료 ({norm['Date'].min()} ~ {norm['Date'].max()})")
                if len(unknown):
                    st.warning("리포트 시트(GFA_Conf/Pet)에 분류되지 않는 캠페인: " + ', '.join(unknown[:10])
                               + " — 캠페인명이 '(Mars Conf)' 또는 '(Mars Pet)'로 시작해야 주간 리포트에 반영됩니다.")
                kpi_row(norm)
                st.dataframe(agg_table(norm, ['캠페인']).style.format(FMT), use_container_width=True, hide_index=True)
            except ValueError as e:
                st.warning(str(e))

# ── 3) 키워드 트렌드 ───────────────────────────────────────────
with tab_trend:
    st.caption("네이버 데이터랩 검색어 트렌드 — 기간 내 최대 검색량을 100으로 둔 상대지표입니다. (브랜드 관심도 추이 확인용)")
    default_groups = "그리니즈: 그리니즈\n시저: 시저 강아지, 시저사료\n템테이션: 템테이션 고양이, 템테이션 간식\n쉬바: 쉬바 고양이, 쉬바 파우치\n위스카스: 위스카스"
    groups_text = st.text_area("키워드 그룹 (한 줄에 '그룹명: 키워드1, 키워드2', 최대 5줄)", default_groups, height=140)
    t1, t2, t3, t4 = st.columns(4)
    ds = t1.date_input("시작일", searchad.today() - timedelta(days=180), key='dl_s')
    de = t2.date_input("종료일", searchad.today() - timedelta(days=1), key='dl_e')
    unit = t3.selectbox("단위", ['date', 'week', 'month'], index=1, format_func={'date': '일', 'week': '주', 'month': '월'}.get)
    dev = t4.selectbox("기기", ['', 'pc', 'mo'], format_func={'': '전체', 'pc': 'PC', 'mo': '모바일'}.get)
    if st.button("트렌드 조회", disabled=not dl_ready):
        groups = {}
        for line in groups_text.splitlines():
            if ':' in line:
                name, kws = line.split(':', 1)
                kw_list = [k.strip() for k in kws.split(',') if k.strip()]
                if name.strip() and kw_list:
                    groups[name.strip()] = kw_list
        try:
            trend = datalab.search_trend(cfg['NAVER_CLIENT_ID'], cfg['NAVER_CLIENT_SECRET'], ds, de, groups, unit, dev or None)
            tdf = pd.DataFrame(trend)
            if tdf.empty:
                st.info("결과가 없습니다.")
            else:
                fig = go.Figure()
                for gname, part in tdf.groupby('group', sort=False):
                    fig.add_trace(go.Scatter(name=gname, x=part['period'], y=part['ratio'], mode='lines'))
                fig.update_layout(height=400, yaxis=dict(title='상대 검색량 (최대=100)'), legend=dict(orientation='h', y=1.1))
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(tdf.pivot(index='period', columns='group', values='ratio'), use_container_width=True)
        except Exception as e:
            st.error(f"조회 실패: {e}  (개발자센터 애플리케이션의 사용 API에 '데이터랩(검색어트렌드)'가 추가되어 있는지 확인해주세요.)")

# ── 4) 리포트 출력 ─────────────────────────────────────────────
with tab_report:
    st.markdown("검색광고 탭에서 조회한 데이터와 GFA 탭에서 올린 데이터를 합쳐 **기존 로우데이터와 같은 형식**의 엑셀을 만들고, "
                "그대로 주간 리포트(GFA_Conf / GFA_Pet / BS_Pet 시트)에 반영합니다.")
    st.caption("💡 지난주 리포트를 반영하려면 검색광고는 '지난주 (월~일)' 또는 월요일부터 시작하는 기간으로 조회하세요. "
               "(주 중간부터 시작하거나 아직 안 끝난 주는 자동으로 제외됩니다)")
    parts = []
    if st.session_state.get('sa_rows'):
        parts.append(raw_builder.to_dataframe(st.session_state['sa_rows']))
    if isinstance(st.session_state.get('gfa_rows'), pd.DataFrame):
        parts.append(st.session_state['gfa_rows'])
    s1, s2 = st.columns(2)
    s1.metric("검색광고 행", f"{len(st.session_state.get('sa_rows') or []):,}")
    s2.metric("GFA 행", f"{len(st.session_state['gfa_rows']) if isinstance(st.session_state.get('gfa_rows'), pd.DataFrame) else 0:,}")

    if not parts:
        st.info("먼저 검색광고 데이터를 조회하거나 GFA 파일을 올려주세요.")
    else:
        merged = pd.concat(parts, ignore_index=True)[raw_builder.RAW_COLS]
        raw_bytes = raw_builder.to_raw_xlsx_bytes(merged)
        stamp = searchad.today().strftime('%Y%m%d')
        st.download_button("⬇️ 로우데이터 엑셀 다운로드", raw_bytes, file_name=f"mars_naver_raw_{stamp}.xlsx")

        prev = st.file_uploader("(선택) 지난 리포트 파일 — 올리면 서식 그대로 숫자만 업데이트", type='xlsx', key='rep_prev')
        if st.button("🧾 주간 리포트 생성", type="primary"):
            try:
                if prev:
                    wb, updated = raw_to_report.update_existing_report(io.BytesIO(prev.getvalue()), io.BytesIO(raw_bytes))
                    msg = f"반영 완료: {', '.join(sorted(set(u[2] for u in updated)))}" if updated else "새로 반영할 완료된 주가 없습니다."
                else:
                    wb = raw_to_report.build_report(io.BytesIO(raw_bytes))
                    msg = "새 리포트 생성 완료"
                buf = io.BytesIO()
                wb.save(buf)
                st.session_state['report_bytes'] = buf.getvalue()
                st.success(msg)
            except Exception as e:
                st.error(f"리포트 생성 실패: {e}")
        if st.session_state.get('report_bytes'):
            st.download_button("⬇️ 주간 리포트 다운로드", st.session_state['report_bytes'],
                               file_name=f"mars_weekly_report_{stamp}.xlsx")
            st.caption("코멘트 자동 작성은 메인 페이지(Report Automation Program)의 ③번 모드에 이 파일을 올려 진행하세요.")
