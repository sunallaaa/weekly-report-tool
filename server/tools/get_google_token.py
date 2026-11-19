# -*- coding: utf-8 -*-
"""구글 refresh token 발급 (내 PC에서 1회 실행).

준비: 구글 클라우드 콘솔 > API 및 서비스 > 사용자 인증 정보 > OAuth 클라이언트 ID (유형: 데스크톱 앱)
      Drive API · Gmail API · Google Sheets API '사용 설정'
실행: python get_google_token.py <클라이언트ID> <클라이언트 보안 비밀번호>
      → 브라우저에서 회사 구글 계정으로 로그인·허용 → 화면에 refresh token 이 출력됩니다.
      출력된 값을 서버 .env 의 GOOGLE_REFRESH_TOKEN 에 넣으세요. (메신저·메일로 공유 금지)
필요한 것: 파이썬 3 (추가 설치 없음)
"""
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPES = ['https://www.googleapis.com/auth/drive',            # 위클리·로우업로드 폴더 읽기/쓰기
          'https://www.googleapis.com/auth/gmail.send',       # 로그인 인증코드 메일
          'https://www.googleapis.com/auth/spreadsheets.readonly']   # 월예산 시트
PORT = 8765


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    cid, secret = sys.argv[1], sys.argv[2]
    state = secrets.token_urlsafe(16)
    redirect = 'http://127.0.0.1:{}/'.format(PORT)
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get('state', [''])[0] == state and 'code' in q:
                got['code'] = q['code'][0]
                msg = '완료되었습니다. 이 창을 닫고 터미널을 확인하세요.'
            else:
                msg = '오류: ' + str(q.get('error', ['알 수 없음'])[0])
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(msg.encode('utf-8'))

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(('127.0.0.1', PORT), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode({
        'client_id': cid, 'redirect_uri': redirect, 'response_type': 'code', 'scope': ' '.join(SCOPES),
        'access_type': 'offline', 'prompt': 'consent', 'state': state})
    print('브라우저가 열리지 않으면 아래 주소를 직접 여세요:\n' + url + '\n')
    webbrowser.open(url)
    srv.timeout = 300
    while 'code' not in got:
        srv.handle_request()
    data = urllib.parse.urlencode({'code': got['code'], 'client_id': cid, 'client_secret': secret,
                                   'redirect_uri': redirect, 'grant_type': 'authorization_code'}).encode()
    res = json.loads(urllib.request.urlopen('https://oauth2.googleapis.com/token', data=data).read())
    if 'refresh_token' not in res:
        print('refresh token 이 없습니다. 구글 계정 > 보안 > 타사 앱 액세스에서 이 앱을 삭제한 뒤 다시 실행하세요.')
        sys.exit(1)
    print('\nGOOGLE_REFRESH_TOKEN=' + res['refresh_token'])


if __name__ == '__main__':
    main()
