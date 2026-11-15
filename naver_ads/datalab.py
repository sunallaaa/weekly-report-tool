# -*- coding: utf-8 -*-
"""네이버 데이터랩 검색어 트렌드 API (네이버 개발자센터 Client ID/Secret 사용).

POST https://openapi.naver.com/v1/datalab/search
※ 개발자센터 애플리케이션 설정의 '사용 API'에 '데이터랩(검색어트렌드)'가 추가되어 있어야 한다.
※ 키워드그룹은 요청당 최대 5개, 그룹당 키워드 최대 20개. 값(ratio)은 기간 내 최대값=100 기준 상대지표.
"""
import requests

URL = 'https://openapi.naver.com/v1/datalab/search'


class DataLabError(Exception):
    pass


def search_trend(client_id, client_secret, start_date, end_date, keyword_groups,
                 time_unit='week', device=None, session=None):
    """keyword_groups: {'그룹명': ['키워드1', '키워드2'], ...} (최대 5개)"""
    if not (client_id and client_secret):
        raise DataLabError('네이버 개발자센터 Client ID / Secret이 설정되지 않았습니다.')
    groups = [{'groupName': k, 'keywords': v[:20]} for k, v in list(keyword_groups.items())[:5]]
    body = {
        'startDate': str(start_date), 'endDate': str(end_date),
        'timeUnit': time_unit, 'keywordGroups': groups,
    }
    if device in ('pc', 'mo'):
        body['device'] = device
    s = session or requests
    r = s.post(URL, json=body, timeout=30, headers={
        'X-Naver-Client-Id': client_id,
        'X-Naver-Client-Secret': client_secret,
        'Content-Type': 'application/json',
    })
    if r.status_code >= 400:
        raise DataLabError('데이터랩 HTTP {}: {}'.format(r.status_code, r.text[:300]))
    rows = []
    for res in r.json().get('results', []):
        for p in res.get('data', []):
            rows.append({'period': p['period'], 'group': res['title'], 'ratio': p['ratio']})
    return rows
