# -*- coding: utf-8 -*-
"""마즈 리포트 규칙 — 구글 Apps Script(gas/Code.gs) 버전과 같은 규칙을 파이썬으로 옮김.

- GFA 채널 분류: 스마트채널·피드는 별개 채널, 쇼핑고객확장 = 쇼핑프로모션(명칭 변경)
- 브랜드검색 광고비: CPT 계약 — 계약 기간 동안 노출 여부와 무관하게 매일 (계약금액 / 계약일수)
- GFA 광고비는 VAT 포함 → 리포트에서는 ÷1.1
"""
from datetime import date, timedelta

GFA_VAT_DIVISOR = 1.1
SA_MEDIA = ('NaverBS', 'NaverSA', 'NaverShopping')
MEDIA_LABEL = {'GFA': 'GFA (디스플레이)', 'NaverBS': '브랜드검색', 'NaverSA': '검색광고(파워링크 등)',
               'NaverShopping': '쇼핑검색'}
BS_DEFAULT_BRANDS = ['그리니즈', '시저', '템테이션', '쉬바', '위스카스', '뉴트로']


PET_BRANDS = ['그리니즈', '시저', '템테이션', '쉬바', '위스카스', '뉴트로', '페디그리', '로얄캐닌', '아이엠스']
MEDIA_KEY = {'GFA': 'GFA', 'NaverBS': 'BS'}      # 대시보드 매체 구분 (그 외 검색광고는 'SA')


def media_key(media):
    return MEDIA_KEY.get(media, 'SA')


def category_of(media, camp, group=''):
    """Conf(스낵) / Pet(반려동물) / 기타 — GFA 는 캠페인 머리말, 검색광고는 캠페인명·브랜드로 판단"""
    camp = str(camp or '')
    up = camp.upper()
    if media == 'GFA':
        if camp.startswith('(Mars Conf)'):
            return 'Conf'
        if camp.startswith('(Mars Pet)'):
            return 'Pet'
    if 'CONF' in up or '스낵' in camp:
        return 'Conf'
    if 'PET' in up or any(b in str(group or '') for b in PET_BRANDS):
        return 'Pet'
    return '기타'


def device_of(group):
    """광고그룹 이름의 _PC / _MO 꼬리표 → PC / MO / 전체"""
    toks = [t.strip().upper() for t in str(group or '').replace('-', '_').replace(' ', '_').split('_') if t.strip()]
    for t in reversed(toks):
        if t in ('PC',):
            return 'PC'
        if t in ('MO', 'MOBILE', 'M'):
            return 'MO'
    return '전체'


def classify_gfa(camp, group='', creative=''):
    """(시트, 채널) 또는 None."""
    camp = str(camp or '')
    if camp.startswith('(Mars Conf)'):
        sheet = 'NAVER GFA_Conf'
    elif camp.startswith('(Mars Pet)'):
        sheet = 'NAVER GFA_Pet'
    else:
        return None
    name = ')'.join(camp.split(')')[1:]).strip()
    group = str(group or '')
    cr = str(creative or '').lower()
    ch = name
    if 'ADVoost' in name:
        ch = 'ADVoost'
    elif 'NDA' in name:
        ch = 'NDA'
    elif '스마트채널' in name or '피드' in name:
        has_s, has_f = '스마트채널' in name, '피드' in name
        if has_s and not has_f:
            ch = '스마트채널'
        elif has_f and not has_s:
            ch = '피드'
        elif '피드' in group:
            ch = '피드'
        elif '스마트채널' in group:
            ch = '스마트채널'
        elif 'feed' in cr or '피드' in cr:
            ch = '피드'
        else:
            ch = '스마트채널'
    elif '쇼핑프로모션' in name or '쇼핑고객확장' in name:
        ch = '쇼핑고객확장'
    return sheet, ch


class Contract:
    def __init__(self, brand, start, end, amount):
        self.brand, self.start, self.end, self.amount = brand, start, end, float(amount)
        self.days = (end - start).days + 1
        self.daily = self.amount / self.days if self.days > 0 else 0


def brand_of(group, contracts=()):
    names = list(BS_DEFAULT_BRANDS)
    for c in contracts:
        if c.brand not in names:
            names.append(c.brand)
    names.sort(key=len, reverse=True)
    group = str(group or '')
    return next((n for n in names if n in group), None)


def contract_daily(brand, day, contracts):
    vals = [c.daily for c in contracts if c.brand == brand and c.start <= day <= c.end]
    return sum(vals) if vals else None


def classify_bs(camp, group, contracts=()):
    if 'PET' in str(camp) and '브랜드검색' in str(camp):
        b = brand_of(group, contracts)
        if b:
            return 'NAVER BS_Pet', b
    return None


def daterange(since, until):
    d = since
    while d <= until:
        yield d
        d += timedelta(days=1)


def apply_bs_cpt(rows, contracts, since, last_day):
    """NaverBS 행의 광고비를 계약 하루 광고비로 바꾸고, 노출이 없어 행이 없는 계약일은 광고비만 있는 행을 채운다.

    rows: dict 목록 (date, media, campaign, adgroup, cost ...). 반환: (rows, 계약 없는 브랜드 목록)
    """
    out, missing, have, tmpl = [], set(), set(), {}
    for r in rows:
        if r['media'] != 'NaverBS':
            out.append(r)
            continue
        b = brand_of(r['adgroup'], contracts)
        daily = contract_daily(b, r['date'], contracts) if b else None
        if daily is None:
            missing.add(b or r['adgroup'])
            out.append(r)
            continue
        key = (b, r['date'])
        r = dict(r, cost=0 if key in have else daily)   # 같은 브랜드·날짜에 행이 여럿이면 하루치는 한 번만
        have.add(key)
        tmpl.setdefault(b, r)
        out.append(r)
    for c in contracts:
        s, e = max(c.start, since), min(c.end, last_day)
        for d in daterange(s, e) if s <= e else []:
            if (c.brand, d) in have:
                continue
            have.add((c.brand, d))
            t = tmpl.get(c.brand, {})
            out.append({'advertiser': t.get('advertiser', ''), 'date': d, 'media': 'NaverBS', 'device': 'ALL',
                        'campaign': t.get('campaign', '브랜드검색 (계약)'), 'adgroup': t.get('adgroup', c.brand),
                        'creative': '', 'creative_id': '', 'imps': 0, 'clicks': 0,
                        'cost': contract_daily(c.brand, d, contracts), 'conv': 0, 'revenue': 0})
    return out, sorted(missing - {None})


def vat_exclude(rows):
    """검색광고·GFA 광고비 ÷1.1 (브랜드검색 계약금액은 이미 VAT 제외라 제외)."""
    return [dict(r, cost=r['cost'] / GFA_VAT_DIVISOR) if r['media'] in ('GFA', 'NaverSA', 'NaverShopping') else r
            for r in rows]


def kpi(a):
    imps, clk, cost, conv, rev = a['imps'], a['clicks'], a['cost'], a['conv'], a['revenue']
    return {'imps': imps, 'clk': clk, 'cost': cost, 'conv': conv, 'rev': rev,
            'ctr': clk / imps * 100 if imps else 0, 'cpc': cost / clk if clk else 0,
            'cpa': cost / conv if conv else 0, 'cvr': conv / clk * 100 if clk else 0,
            'roas': rev / cost * 100 if cost else 0}


def group_by(rows, key_fn):
    m = {}
    for r in rows:
        k = key_fn(r)
        if k is None:
            continue
        a = m.setdefault(k, {'imps': 0, 'clicks': 0, 'cost': 0, 'conv': 0, 'revenue': 0})
        for f in a:
            a[f] += float(r.get(f) or 0)
    return [dict(kpi(v), key=k) for k, v in m.items()]


def total(rows):
    g = group_by(rows, lambda r: 'all')
    return g[0] if g else kpi({'imps': 0, 'clicks': 0, 'cost': 0, 'conv': 0, 'revenue': 0})


def to_date(v):
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])
