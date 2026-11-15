# -*- coding: utf-8 -*-
"""API로 받은 행들을 기존 로우데이터 엑셀(시트 'Msrs Daily') 형식으로 만든다.

→ raw_to_report.build_report / update_existing_report 에 그대로 넣을 수 있다.
"""
import io

import openpyxl
import pandas as pd

RAW_COLS = ['Date', 'Media', 'Device', '캠페인', '그룹', '노출', '클릭', '광고비', '구매완료수', '매출액']


def to_dataframe(rows):
    df = pd.DataFrame(rows)
    for c in RAW_COLS:
        if c not in df.columns:
            df[c] = 0 if c in ('노출', '클릭', '광고비', '구매완료수', '매출액') else ''
    return df


def to_raw_xlsx_bytes(df):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Msrs Daily'
    ws.append(RAW_COLS)
    df = df[RAW_COLS].sort_values(['Date', 'Media', '캠페인', '그룹'])
    for rec in df.itertuples(index=False):
        d = rec[0]
        ws.append([d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)[:10]] + list(rec[1:]))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def add_kpis(df, vat_exclude=False):
    """집계 DataFrame에 CTR/CPC/CPA/CVR/ROAS 컬럼 추가. vat_exclude=True면 광고비 ÷1.1."""
    df = df.copy()
    if vat_exclude:
        df['광고비'] = df['광고비'] / 1.1
    safe = lambda a, b: (a / b).where(b != 0, 0)
    df['CTR(%)'] = safe(df['클릭'], df['노출']) * 100
    df['CPC'] = safe(df['광고비'], df['클릭'])
    df['CPA'] = safe(df['광고비'], df['구매완료수'])
    df['CVR(%)'] = safe(df['구매완료수'], df['클릭']) * 100
    df['ROAS(%)'] = safe(df['매출액'], df['광고비']) * 100
    return df
