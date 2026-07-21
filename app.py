# -*- coding: utf-8 -*-
"""
주간 광고 리포트 코멘트 자동화 앱 (Streamlit)

이번 주 리포트 파일(.xlsx)을 업로드하면:
  1) 시트별 브랜드/채널 WEEK 테이블에서 최신 주 vs 직전 주 수치를 스스로 찾아 증감률을 계산하고
  2) 화면에서 이번 주 특이사항(타임딜, 브랜드데이 등)을 입력받아
  3) Claude API로 기존 코멘트와 동일한 톤/형식의 문장을 생성한 뒤
  4) 화면에서 검수/수정하고
  5) 실제 셀에 반영한 엑셀 파일을 다운로드한다.

배포: Streamlit Community Cloud (무료) 사용을 권장. 같은 폴더의 배포방법.md 참고.
"""
import io
import re
import json
from datetime import datetime

import streamlit as st
import openpyxl

# ─────────────────────────────────────────────────────────────
# 1) 시트 구조 설정 — 매주 파일을 복사/이름변경만 하는 구조라 그대로 재사용 가능
# ─────────────────────────────────────────────────────────────
SHEET_GROUP_KEYWORDS = {
    'NAVER GFA_Conf': ['TOTAL', 'ADVoost', 'NDA', '쇼핑프로모션'],
    'NAVER GFA_Pet':  ['TOTAL', 'ADVoost', 'NDA', '쇼핑프로모션', '스마트채널'],
    'NAVER BS_Pet':   ['TOTAL', '그리니즈', '시저', '템테이션', '쉬바', '위스카스'],
}

# 그룹별로 코멘트가 들어가는 셀 좌표 (파일 레이아웃 고정 — 매주 동일)
CELL_MAP = {
    'NAVER GFA_Conf': {
        'TOTAL': ['B6', 'B7'],
        'ADVoost': ['B10', 'B11'],
        'NDA': ['B14', 'B15'],
        '쇼핑프로모션': ['B18', 'B19', 'B20'],
    },
    'NAVER GFA_Pet': {
        'TOTAL': ['B6', 'B7'],
        'ADVoost': ['B10'],
        'NDA': ['B13'],
        '쇼핑프로모션': ['B16', 'B17', 'B18'],
        '스마트채널': ['B21', 'B22'],
    },
    'NAVER BS_Pet': {
        'TOTAL': ['B6', 'B7'],
        '그리니즈': ['B9'],
        '시저': ['B11'],
        '템테이션': ['B13'],
        '쉬바': ['B15'],
        '위스카스': ['B17'],
    },
}

# 스타일 예시 (실제로 이미 사용한 2주차 코멘트 — Claude에게 톤/형식 참고용으로 제공)
STYLE_EXAMPLES = {
    'NAVER BS_Pet': {
        'TOTAL': ["7월 2주차 매출액 1,844만원 확보하며 ROAS 1,613% 기록, 전주 대비 매출 80% 이상 증가",
                   "7/13 브랜드데이에 이어 15일 저녁~16일 새벽 위스카스 타임딜, 19일 그리니즈 타임딜 소재 운영 영향으로 전 브랜드 CTR·CVR·ROAS 동반 개선"],
        '위스카스': ["5. 위스카스 : 노출 5% 증가, 유입 33% 증가, CPC 25% 하락(2,074원→1,556원)하며 주간 매출액 281만원 확보 (전주 대비 174% 증가, 전 브랜드 중 최대 성장폭) — 15일 저녁~16일 새벽 타임딜 운영이 주요 동인, ROAS 550%→1,506% 기록"],
    },
    'NAVER GFA_Pet': {
        'TOTAL': ["7/10~13 브랜드데이 소재 운영 효과가 2주차 데이터에 본격 반영되며 전체 매출액 4,721만원 확보",
                   "전주 대비 매출 166% 증가, 전환수 366건→1,080건(+195%)으로 급증하며 ROAS 521%→1,385%로 큰 폭 개선"],
    },
    'NAVER GFA_Conf': {
        'TOTAL': ["넾다세일 종료 여파 지속되며 노출 확대에도 전환 효율 하락, 주간 매출액 1,241만원으로 전주 대비 23% 감소하며 ROAS 965%→742%로 하락",
                   "→ ADVoost 대비 NDA·쇼핑프로모션에서 전환 감소폭이 더 크게 나타나며 전체 평균 매출 감소에 영향"],
    },
}

WEEK_RE = re.compile(r'^\d+월\s*\d+주차\s*\(')
BAD_LABELS = {'WEEK', 'IMPS', 'CLICK', 'CTR', 'CPC', 'COST (-vat)',
              'TRANS.\n(구매완료 수)', 'SALES\n(구매완료 매출액)', 'CPA', 'CVR',
              'ROAS', 'WoW', '<그룹 별 상세 성과>', None}
COLS = ['IMPS', 'CLICK', 'CTR', 'CPC', 'COST', 'TRANS', 'SALES', 'CPA', 'CVR', 'ROAS']


# ─────────────────────────────────────────────────────────────
# 2) 데이터 추출 (extract_weekly_brief.py와 동일한 로직)
# ─────────────────────────────────────────────────────────────
def nearest_group_label(ws, row):
    for rr in range(row, max(row - 40, 0), -1):
        for col in (3, 2):
            v = ws.cell(row=rr, column=col).value
            if isinstance(v, str) and v not in BAD_LABELS and not WEEK_RE.match(v) and v.strip() != '':
                return v
    return None


def find_group_tables(ws):
    tables = {}
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and WEEK_RE.match(cell.value):
                r = cell.row
                header_row = r - 1
                gname = nearest_group_label(ws, header_row - 1)
                vals = [ws.cell(row=r, column=c).value for c in range(3, 13)]
                tables.setdefault(gname, []).append((r, cell.value, vals))
    for g in tables:
        tables[g].sort(key=lambda x: x[0])
    return tables


def latest_complete_pair(rows):
    last_idx = None
    for i, (r, label, vals) in enumerate(rows):
        sales = vals[6] if len(vals) > 6 else None
        if sales not in (None, 0):
            last_idx = i
    if last_idx is None or last_idx == 0:
        return None
    return rows[last_idx - 1], rows[last_idx]


def pct(a, b):
    if a in (0, None) or b is None:
        return None
    return round((b - a) / a * 100, 1)


def build_brief(wb):
    brief = {}
    for sheet_name, group_keywords in SHEET_GROUP_KEYWORDS.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        tables = find_group_tables(ws)
        sheet_brief = {}
        for gname, rows in tables.items():
            if gname is None:
                continue
            matched_kw = next((kw for kw in group_keywords if kw in gname), None)
            if not matched_kw or matched_kw in sheet_brief:
                continue
            pair = latest_complete_pair(rows)
            if not pair:
                continue
            (r1, label1, v1), (r2, label2, v2) = pair
            deltas = {col: {'prev': a, 'curr': b, 'pct_change': pct(a, b)}
                      for col, a, b in zip(COLS, v1, v2)}
            sheet_brief[matched_kw] = {
                'prev_week': label1, 'curr_week': label2,
                'metrics': deltas, 'user_note': '',
            }
        if sheet_brief:
            brief[sheet_name] = sheet_brief
    return brief


# ─────────────────────────────────────────────────────────────
# 3) Claude API로 코멘트 문장 생성
# ─────────────────────────────────────────────────────────────
def build_prompt(sheet_name, sheet_brief):
    cell_map = CELL_MAP[sheet_name]
    examples = STYLE_EXAMPLES.get(sheet_name, {})

    lines = [f"시트: {sheet_name}", "", "아래는 이번 주(최신 주) vs 직전 주 데이터와 담당자가 입력한 특이사항이다.", ""]
    for group, data in sheet_brief.items():
        if group not in cell_map:
            continue
        lines.append(f"[{group}] {data['prev_week']} → {data['curr_week']}")
        for col, m in data['metrics'].items():
            lines.append(f"  {col}: {m['prev']} → {m['curr']} ({m['pct_change']}%)")
        if data['user_note']:
            lines.append(f"  담당자 특이사항: {data['user_note']}")
        lines.append(f"  필요한 줄 수: {len(cell_map[group])}줄 (셀: {', '.join(cell_map[group])})")
        if group in examples:
            lines.append(f"  참고 예시(지난 주 실제 작성 문장, 톤/형식만 참고): {json.dumps(examples[group], ensure_ascii=False)}")
        lines.append("")

    lines.append(
        "위 데이터로 한국어 광고 성과 리포트 코멘트를 작성하라. "
        "규칙: (1) 숫자는 반드시 주어진 데이터에서만 사용하고 지어내지 말 것 "
        "(2) ROAS는 소수×100을 '%'로 표기 (예: 8.93 → '893%') "
        "(3) 매출은 '만원' 단위, 원 단위는 CPC/CPA에만 사용 "
        "(4) 담당자 특이사항이 있으면 자연스럽게 근거로 녹여 쓸 것 "
        "(5) 그룹별로 지정된 줄 수를 정확히 지킬 것 (한 줄 = 한 문장 정도) "
        "(6) 마지막 문장에는 반드시 ROAS 이전→이후 흐름을 명시할 것. "
        "출력은 아래 JSON 형식만 반환 (설명, 마크다운 코드펜스 없이 순수 JSON):\n"
        '{"셀좌표": "문장", ...}  — 예: {"B6": "...", "B7": "..."}'
    )
    return "\n".join(lines)


def generate_comments_with_claude(brief, api_key, model="claude-sonnet-5"):
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    all_comments = {}
    for sheet_name, sheet_brief in brief.items():
        if sheet_name not in CELL_MAP:
            continue
        prompt = build_prompt(sheet_name, sheet_brief)
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        text = re.sub(r'^```(json)?|```$', '', text.strip(), flags=re.MULTILINE).strip()
        try:
            cell_texts = json.loads(text)
        except json.JSONDecodeError:
            cell_texts = {}
            st.warning(f"[{sheet_name}] Claude 응답을 JSON으로 파싱하지 못했습니다. 원문:\n{text}")
        all_comments[sheet_name] = cell_texts
    return all_comments


# ─────────────────────────────────────────────────────────────
# 4) Streamlit 화면
# ─────────────────────────────────────────────────────────────
import raw_to_report

st.set_page_config(page_title="주간 리포트 코멘트 자동화", layout="wide")
st.title("📊 주간 광고 리포트 자동화")
st.caption("로우데이터 또는 완성된 리포트 파일을 올리면, 데이터 분석 → 특이사항 반영 → 코멘트 자동 작성까지 진행합니다.")

api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
if not api_key:
    api_key = st.text_input("Anthropic API 키 (Streamlit Secrets에 등록하면 이 입력창은 안 보입니다)", type="password")

mode = st.radio(
    "무엇을 업로드하시나요?",
    ["① 로우데이터 (일별 원본) → 리포트 새로 생성", "② 이미 만들어진 리포트 파일 → 코멘트만 작성"],
    horizontal=True,
)

file_bytes = None

if mode.startswith("①"):
    raw_uploaded = st.file_uploader("로우데이터 파일 업로드 (.xlsx)", type="xlsx", key="raw_uploader")
    st.caption("지원 매체/시트: GFA(Conf·Pet), NaverBS(Pet) — 컬럼: Date, Media, Device, 캠페인, 그룹, 노출, 클릭, 광고비, 구매완료수, 매출액")
    if raw_uploaded:
        with st.spinner("로우데이터를 분석해서 리포트를 생성하는 중..."):
            wb_generated = raw_to_report.build_report(io.BytesIO(raw_uploaded.getvalue()))
            buf = io.BytesIO()
            wb_generated.save(buf)
            file_bytes = buf.getvalue()
        st.success("리포트 생성 완료! 아래에서 이어서 특이사항을 입력하고 코멘트를 생성하세요.")
else:
    uploaded = st.file_uploader("이번 주 리포트 파일 업로드 (.xlsx)", type="xlsx", key="report_uploader")
    if uploaded:
        file_bytes = uploaded.getvalue()

if file_bytes:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    brief = build_brief(wb)

    if not brief:
        st.error("지원하는 시트(NAVER GFA_Conf / NAVER GFA_Pet / NAVER BS_Pet)에서 최근 완료된 주 데이터를 찾지 못했습니다. "
                 "엑셀에서 파일을 한 번 열어 저장한 뒤 다시 업로드해보세요 (수식 캐시 문제).")
    else:
        st.subheader("① 이번 주 특이사항 입력 (선택)")
        notes = {}
        cols = st.columns(len(brief))
        for col, (sheet_name, sheet_brief) in zip(cols, brief.items()):
            with col:
                st.markdown(f"**{sheet_name}**")
                for group in sheet_brief:
                    key = f"note_{sheet_name}_{group}"
                    notes[(sheet_name, group)] = st.text_input(group, key=key, placeholder="예: 15일 타임딜 운영")

        if st.button("② 코멘트 생성", type="primary", disabled=not api_key):
            for (sheet_name, group), memo in notes.items():
                if memo and memo.strip():
                    brief[sheet_name][group]['user_note'] = memo.strip()
            with st.spinner("Claude가 코멘트를 작성하는 중..."):
                st.session_state['comments'] = generate_comments_with_claude(brief, api_key)
                st.session_state['file_bytes'] = file_bytes

        if not api_key:
            st.info("API 키를 입력해야 코멘트 생성 버튼이 활성화됩니다.")

if 'comments' in st.session_state:
    st.subheader("③ 생성된 코멘트 검수 (필요시 직접 수정)")
    edited = {}
    for sheet_name, cell_texts in st.session_state['comments'].items():
        st.markdown(f"**{sheet_name}**")
        edited[sheet_name] = {}
        for cell, text in cell_texts.items():
            edited[sheet_name][cell] = st.text_area(f"{sheet_name} · {cell}", value=text, key=f"edit_{sheet_name}_{cell}", height=80)

    if st.button("④ 엑셀에 반영하고 다운로드 생성", type="primary"):
        wb_out = openpyxl.load_workbook(io.BytesIO(st.session_state['file_bytes']), data_only=False)
        for sheet_name, cell_texts in edited.items():
            ws = wb_out[sheet_name]
            for cell, text in cell_texts.items():
                ws[cell] = text
        wb_out.calculation.fullCalcOnLoad = True  # 엑셀에서 열면 자동 재계산되도록 보장
        buf = io.BytesIO()
        wb_out.save(buf)
        buf.seek(0)
        st.success("완료! 아래 버튼으로 다운로드하세요.")
        st.download_button(
            "📥 업데이트된 엑셀 다운로드",
            data=buf.getvalue(),
            file_name=f"report_updated_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
