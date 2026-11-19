# -*- coding: utf-8 -*-
"""구글 연동 — 미리 발급받은 OAuth 토큰(refresh token) 방식 (본부 관리페이지의 캘린더 연동과 같은 방식).

필요 권한(scope): drive.file 또는 drive, gmail.send, spreadsheets.readonly
"""
import base64
import json
import time
from email.header import Header
from email.mime.text import MIMEText

import requests

from . import settings as S

TOKEN_URL = 'https://oauth2.googleapis.com/token'
_cache = {'token': None, 'exp': 0}


class GoogleError(Exception):
    pass


def access_token(session=None):
    st = S.settings
    if not st.google_ready:
        raise GoogleError('.env 에 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN 이 필요합니다.')
    if _cache['token'] and _cache['exp'] > time.time() + 60:
        return _cache['token']
    r = (session or requests).post(TOKEN_URL, data={
        'client_id': st.google_client_id, 'client_secret': st.google_client_secret,
        'refresh_token': st.google_refresh_token, 'grant_type': 'refresh_token'}, timeout=30)
    if r.status_code != 200:
        raise GoogleError('구글 토큰 갱신 실패 HTTP {}: {}'.format(r.status_code, r.text[:200]))
    j = r.json()
    _cache.update(token=j['access_token'], exp=time.time() + int(j.get('expires_in', 3600)))
    return _cache['token']


def _auth(session=None):
    return {'Authorization': 'Bearer ' + access_token(session)}


def drive_upload(path, name, folder_id, mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                 session=None):
    """폴더에 파일 업로드 (같은 이름이 있으면 새 버전으로 덮어쓰지 않고 새 파일 생성). 반환: {id, webViewLink}"""
    s = session or requests
    meta = {'name': name, 'parents': [folder_id]}
    with open(path, 'rb') as f:
        data = f.read()
    boundary = 'naverdash' + str(int(time.time() * 1000))
    body = (('--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{m}\r\n'
             '--{b}\r\nContent-Type: {t}\r\n\r\n').format(b=boundary, m=json.dumps(meta), t=mime).encode('utf-8')
            + data + '\r\n--{}--\r\n'.format(boundary).encode('utf-8'))
    r = s.post('https://www.googleapis.com/upload/drive/v3/files',
               params={'uploadType': 'multipart', 'supportsAllDrives': 'true', 'fields': 'id,name,webViewLink'},
               headers=dict(_auth(session), **{'Content-Type': 'multipart/related; boundary=' + boundary}),
               data=body, timeout=300)
    if r.status_code not in (200, 201):
        raise GoogleError('드라이브 업로드 실패 HTTP {}: {}'.format(r.status_code, r.text[:300]))
    return r.json()


def drive_download(file_id, dest, session=None):
    r = (session or requests).get('https://www.googleapis.com/drive/v3/files/' + file_id,
                                  params={'alt': 'media', 'supportsAllDrives': 'true'},
                                  headers=_auth(session), timeout=300)
    if r.status_code != 200:
        raise GoogleError('드라이브 다운로드 실패 HTTP {}: {}'.format(r.status_code, r.text[:200]))
    with open(dest, 'wb') as f:
        f.write(r.content)
    return dest


def drive_list(folder_id, name_contains='', session=None):
    q = "'{}' in parents and trashed = false".format(folder_id)
    if name_contains:
        q += " and name contains '{}'".format(name_contains.replace("'", "\\'"))
    r = (session or requests).get('https://www.googleapis.com/drive/v3/files', params={
        'q': q, 'orderBy': 'modifiedTime desc', 'pageSize': 50, 'supportsAllDrives': 'true',
        'includeItemsFromAllDrives': 'true', 'fields': 'files(id,name,modifiedTime,webViewLink)'},
        headers=_auth(session), timeout=60)
    if r.status_code != 200:
        raise GoogleError('드라이브 목록 실패 HTTP {}: {}'.format(r.status_code, r.text[:200]))
    return r.json().get('files', [])


def sheet_values(sheet_id, rng, session=None):
    r = (session or requests).get('https://sheets.googleapis.com/v4/spreadsheets/{}/values/{}'.format(
        sheet_id, requests.utils.quote(rng)), headers=_auth(session), timeout=60)
    if r.status_code != 200:
        raise GoogleError('시트 읽기 실패 HTTP {}: {}'.format(r.status_code, r.text[:200]))
    return r.json().get('values', [])


def send_mail(to, subject, text, session=None):
    msg = MIMEText(text, 'plain', 'utf-8')
    msg['To'] = to
    msg['Subject'] = Header(subject, 'utf-8')
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    r = (session or requests).post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                                   headers=_auth(session), json={'raw': raw}, timeout=60)
    if r.status_code != 200:
        raise GoogleError('메일 발송 실패 HTTP {}: {}'.format(r.status_code, r.text[:200]))
    return True


def monthly_budgets(month, session=None):
    """대시보드 시트 '월예산' 탭에서 해당 월(YYYY-MM) 행 → [{sheet, name, budget, memo}]"""
    rows = sheet_values(S.settings.budget_sheet_id, '월예산!A2:E200', session=session)
    out = []
    for r in rows:
        r = list(r) + [''] * 5
        if str(r[0]).strip() != month or not str(r[3]).strip():
            continue
        try:
            budget = float(str(r[3]).replace(',', ''))
        except ValueError:
            continue
        out.append({'sheet': r[1].strip(), 'name': r[2].strip(), 'budget': budget, 'memo': r[4].strip()})
    return out
