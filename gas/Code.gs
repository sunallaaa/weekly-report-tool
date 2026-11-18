/**
 * 한국마즈 네이버 광고 실시간 대시보드 — Google Apps Script 백엔드
 *
 * 구조
 *  - 오늘 데이터: 대시보드 조회 시 네이버 검색광고 API를 실시간 호출 (5분 캐시)
 *  - 지난 데이터: 매일 아침 트리거가 수집해서 'Msrs Daily' 시트에 저장 (기존 로우데이터와 같은 컬럼)
 *  - GFA: 'GFA_업로드' 시트에 GFA 리포트를 붙여넣고 [GFA 반영] → 같은 로우데이터 시트로 합침
 *  - 주간 리포트: 기존 파이썬 리포트 생성기(raw_to_report.py)와 같은 규칙으로 '주간리포트' 시트 생성
 *
 * 🔒 API 키는 코드에 넣지 말고 [프로젝트 설정 > 스크립트 속성]에 저장한다.
 *    NAVER_SA_CUSTOMER_ID, NAVER_SA_API_KEY, NAVER_SA_SECRET_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
 */

// ───────────────────────── 설정 ─────────────────────────
var TZ = 'Asia/Seoul';
var SA_BASE = 'https://api.searchad.naver.com';
var SA_FIELDS = ['impCnt', 'clkCnt', 'salesAmt', 'ctr', 'cpc', 'avgRnk', 'ccnt'];
var SA_OPTIONAL_FIELDS = ['convAmt'];       // 전환매출. 미지원 시 자동으로 빼고 재조회
var ID_CHUNK = 50;
var RECOLLECT_DAYS = 3;                     // 늦게 잡히는 전환 반영을 위해 최근 3일은 매일 다시 수집
var LIVE_CACHE_SEC = 300;                   // 실시간 조회 캐시 5분

var SHEET_RAW = 'Msrs Daily';
var SHEET_GFA = 'GFA_업로드';
var SHEET_WEEKLY = '주간리포트';
var SHEET_LOG = '수집로그';
var RAW_COLS = ['Date', 'Media', 'Device', '캠페인', '그룹', '노출', '클릭', '광고비', '구매완료수', '매출액', '소재'];   // 앞 10개는 기존 로우데이터와 동일
var SA_MEDIA = ['NaverBS', 'NaverSA', 'NaverShopping'];

// 기존 리포트 규칙 (raw_to_report.py와 동일)
var GFA_VAT_DIVISOR = 1.1;
var BS_PET_MONTHLY_BUDGET = { '그리니즈': 800000, '시저': 1700000, '템테이션': 800000, '쉬바': 800000, '위스카스': 800000 };
var BS_PET_BRANDS = Object.keys(BS_PET_MONTHLY_BUDGET);
var SHEET_GROUP_ORDER = {
  'NAVER GFA_Conf': ['TOTAL', 'ADVoost', 'NDA', '쇼핑프로모션'],
  'NAVER GFA_Pet': ['TOTAL', 'ADVoost', 'NDA', '쇼핑프로모션', '스마트채널', '피드'],
  'NAVER BS_Pet': ['TOTAL'].concat(BS_PET_BRANDS)
};

// 브랜드검색 계약 시트: 브랜드별 계약기간·계약금액 → 하루 광고비 = 계약금액 ÷ 계약일수
var SHEET_BS_CONTRACT = 'BS계약';
var BS_CONTRACT_HEADER = ['브랜드(광고그룹명에 포함된 단어)', '시작일', '종료일', '계약금액(VAT 제외, 원)', '메모'];

var GFA_COLUMN_CANDIDATES = {
  'Date': ['기간', '일', '날짜', '일자', 'Date', 'date'],
  '캠페인': ['캠페인 이름', '캠페인명', '캠페인', 'Campaign', 'campaign'],
  '그룹': ['광고 그룹 이름', '광고그룹 이름', '광고그룹명', '광고 그룹', '광고그룹', 'Ad Set', 'adset'],
  '노출': ['노출', '노출수', 'Impressions', 'impressions'],
  '클릭': ['클릭', '클릭수', 'Clicks', 'clicks'],
  '광고비': ['총 비용', '총비용', '비용', '광고비', '총 비용(VAT 포함)', 'Cost', 'cost', 'spend'],
  '구매완료수': ['구매완료 수', '구매완료수', '구매 완료 수', '전환수', '구매', 'Conversions', 'purchases'],
  '소재': ['광고 소재 이름', '광고소재 이름', '소재 이름', '소재명', 'Creative', 'creative'],
  '매출액': ['구매완료 전환 매출액', '구매완료 매출액', '구매완료매출액', '전환 매출액', '매출액', 'Revenue', 'revenue']
};

// ───────────────────────── 웹앱 진입점 ─────────────────────────
function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('한국마즈 네이버 광고 대시보드')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

// ───────────────────────── 날짜 유틸 (yyyy-MM-dd 문자열 기준) ─────────────────────────
function todayYmd_() { return Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd'); }
function parseYmd_(s) { var p = s.split('-'); return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])); }
function fmtYmd_(d) {
  return d.getUTCFullYear() + '-' + ('0' + (d.getUTCMonth() + 1)).slice(-2) + '-' + ('0' + d.getUTCDate()).slice(-2);
}
function addDays_(s, n) { var d = parseYmd_(s); d.setUTCDate(d.getUTCDate() + n); return fmtYmd_(d); }
function dayList_(since, until) {
  var out = [], d = since;
  while (d <= until) { out.push(d); d = addDays_(d, 1); }
  return out;
}
function toYmd_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, TZ, 'yyyy-MM-dd');
  return String(v).trim().replace(/\./g, '-').slice(0, 10);
}
function mondayOf_(s) {
  var d = parseYmd_(s), wd = (d.getUTCDay() + 6) % 7;   // 월=0
  return addDays_(s, -wd);
}
/** raw_to_report.week_label 과 동일: "11월 1주차 (11/2~11/8)" */
function weekLabel_(s) {
  var mon = mondayOf_(s), sun = addDays_(mon, 6);
  var m = parseYmd_(mon), e = parseYmd_(sun);
  var idx = 1, cur = addDays_(mon, -7);
  while (parseYmd_(cur).getUTCMonth() === m.getUTCMonth()) { idx++; cur = addDays_(cur, -7); }
  return {
    label: (m.getUTCMonth() + 1) + '월 ' + idx + '주차 (' + (m.getUTCMonth() + 1) + '/' + m.getUTCDate() + '~' +
      (e.getUTCMonth() + 1) + '/' + e.getUTCDate() + ')',
    monday: mon
  };
}

// ───────────────────────── 검색광고 API ─────────────────────────
function props_() { return PropertiesService.getScriptProperties(); }

function saSignature_(ts, method, uri, secret) {
  var sig = Utilities.computeHmacSha256Signature(ts + '.' + method + '.' + uri, secret);
  return Utilities.base64Encode(sig);
}

function saGet_(uri, params) {
  var p = props_();
  var cid = p.getProperty('NAVER_SA_CUSTOMER_ID'), key = p.getProperty('NAVER_SA_API_KEY'),
      secret = p.getProperty('NAVER_SA_SECRET_KEY');
  if (!cid || !key || !secret) throw new Error('스크립트 속성에 검색광고 API 키(NAVER_SA_*)가 없습니다.');
  var qs = [];
  Object.keys(params || {}).forEach(function (k) {
    var v = params[k];
    (Array.isArray(v) ? v : [v]).forEach(function (x) { qs.push(encodeURIComponent(k) + '=' + encodeURIComponent(x)); });
  });
  var url = SA_BASE + uri + (qs.length ? '?' + qs.join('&') : '');
  for (var attempt = 0; attempt < 3; attempt++) {
    var ts = String(Date.now());
    var res = UrlFetchApp.fetch(url, {
      method: 'get', muteHttpExceptions: true,
      headers: {
        'X-Timestamp': ts, 'X-API-KEY': key, 'X-Customer': String(cid),
        'X-Signature': saSignature_(ts, 'GET', uri, secret)
      }
    });
    var code = res.getResponseCode();
    if (code === 429 || code >= 500) { Utilities.sleep(1000 * (attempt + 1)); continue; }
    if (code >= 400) throw new Error('GET ' + uri + ' → HTTP ' + code + ': ' + res.getContentText().slice(0, 300));
    return JSON.parse(res.getContentText() || 'null');
  }
  throw new Error('GET ' + uri + ' 재시도 실패');
}

function mediaOf_(c) {
  var tp = String(c.campaignTp || '').toUpperCase(), name = c.name || '';
  if (tp === 'BRAND_SEARCH' || name.indexOf('브랜드검색') >= 0) return 'NaverBS';
  if (tp === 'SHOPPING') return 'NaverShopping';
  return 'NaverSA';
}

/** 캠페인/광고그룹 구조 (1시간 캐시) */
function saStructure_() {
  var cache = CacheService.getScriptCache();
  var hit = cache.get('sa_structure');
  if (hit) return JSON.parse(hit);
  var camps = saGet_('/ncc/campaigns') || [];
  var groups = {};
  camps.forEach(function (c) {
    (saGet_('/ncc/adgroups', { nccCampaignId: c.nccCampaignId }) || []).forEach(function (g) {
      groups[g.nccAdgroupId] = { name: g.name, camp: c.name, media: mediaOf_(c) };
    });
  });
  try { cache.put('sa_structure', JSON.stringify(groups), 3600); } catch (e) { /* 100KB 초과 시 캐시 생략 */ }
  return groups;
}

function saStats_(ids, day) {
  var p = props_(), useConv = p.getProperty('SA_NO_CONVAMT') !== '1';
  var out = {};
  for (var i = 0; i < ids.length; i += ID_CHUNK) {
    var chunk = ids.slice(i, i + ID_CHUNK);
    var params = { ids: chunk, timeRange: JSON.stringify({ since: day, until: day }) };
    var res;
    try {
      params.fields = JSON.stringify(useConv ? SA_FIELDS.concat(SA_OPTIONAL_FIELDS) : SA_FIELDS);
      res = saGet_('/stats', params);
    } catch (e) {
      if (!useConv) throw e;
      useConv = false;
      p.setProperty('SA_NO_CONVAMT', '1');
      params.fields = JSON.stringify(SA_FIELDS);
      res = saGet_('/stats', params);
    }
    var rows = Array.isArray(res) ? res : ((res && res.data) || []);
    rows.forEach(function (r) { if (r.id) out[r.id] = r; });
  }
  return out;
}

/** 하루치 광고그룹별 로우데이터 행 */
function saDayRows_(day) {
  var groups = saStructure_(), ids = Object.keys(groups);
  if (!ids.length) return [];
  var stats = saStats_(ids, day), rows = [];
  Object.keys(stats).forEach(function (gid) {
    var s = stats[gid], g = groups[gid];
    if (!g) return;
    var imps = +s.impCnt || 0, clk = +s.clkCnt || 0, cost = +s.salesAmt || 0;
    if (!imps && !clk && !cost) return;
    rows.push([day, g.media, 'ALL', g.camp, g.name, imps, clk, cost, +s.ccnt || 0, +s.convAmt || 0]);
  });
  return rows;
}

// ───────────────────────── 시트 ─────────────────────────
function ss_() {
  var id = props_().getProperty('SPREADSHEET_ID');
  return id ? SpreadsheetApp.openById(id) : SpreadsheetApp.getActiveSpreadsheet();
}
function sheet_(name, header) {
  var ss = ss_(), sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    if (header) { sh.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold'); sh.setFrozenRows(1); }
  }
  return sh;
}
function readRaw_() {
  var sh = sheet_(SHEET_RAW, RAW_COLS), n = sh.getLastRow();
  if (n < 2) return [];
  return sh.getRange(2, 1, n - 1, RAW_COLS.length).getValues()
    .filter(function (r) { return r[0] !== '' && r[0] !== null; })
    .map(function (r) { r[0] = toYmd_(r[0]); return r; });
}
/** 지정 매체·날짜 행을 지우고 새 행으로 교체 (중복 방지) */
function replaceRaw_(mediaList, days, newRows) {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var daySet = {}, mediaSet = {};
    days.forEach(function (d) { daySet[d] = 1; });
    mediaList.forEach(function (m) { mediaSet[m] = 1; });
    var kept = readRaw_().filter(function (r) { return !(daySet[r[0]] && mediaSet[r[1]]); });
    var all = kept.concat(newRows).sort(function (a, b) {
      return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : (a[1] + a[3] + a[4]).localeCompare(b[1] + b[3] + b[4]);
    });
    all = all.map(function (r) { r = r.slice(0, RAW_COLS.length); while (r.length < RAW_COLS.length) r.push(''); return r; });
    var sh = sheet_(SHEET_RAW, RAW_COLS);
    sh.getRange(1, 1, 1, RAW_COLS.length).setValues([RAW_COLS]).setFontWeight('bold');
    if (sh.getLastRow() > 1) sh.getRange(2, 1, sh.getLastRow() - 1, RAW_COLS.length).clearContent();
    if (all.length) {
      sh.getRange(2, 1, all.length, 1).setNumberFormat('@');   // 날짜는 텍스트로 (엑셀 호환)
      sh.getRange(2, 1, all.length, RAW_COLS.length).setValues(all);
    }
  } finally { lock.releaseLock(); }
}
function log_(msg) {
  sheet_(SHEET_LOG, ['시각', '내용']).appendRow([Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd HH:mm:ss'), msg]);
}

// ───────────────────────── 수집 (트리거/백필) ─────────────────────────
function collectDays_(days) {
  var rows = [];
  days.forEach(function (d) { rows = rows.concat(saDayRows_(d)); });
  replaceRaw_(SA_MEDIA, days, rows);
  markCollected_(days);
  return rows.length;
}

/** 수집 완료 날짜 기록 (광고비 0인 날도 '수집됨'으로 처리해 반복 수집 방지) */
var SHEET_COLLECTED = '_수집일자';
function collectedSet_() {
  var sh = sheet_(SHEET_COLLECTED, ['수집된 날짜']), n = sh.getLastRow(), set = {};
  if (n >= 2) sh.getRange(2, 1, n - 1, 1).getValues().forEach(function (r) { set[toYmd_(r[0])] = 1; });
  return set;
}
function markCollected_(days) {
  var set = collectedSet_(), add = days.filter(function (d) { return !set[d]; });
  if (!add.length) return;
  var sh = sheet_(SHEET_COLLECTED, ['수집된 날짜']), start = sh.getLastRow() + 1;
  sh.getRange(start, 1, add.length, 1).setNumberFormat('@').setValues(add.map(function (d) { return [d]; }));
}

/** 매일 아침 트리거: 어제 포함 최근 3일 다시 수집 */
function dailyCollect() {
  var t = todayYmd_(), days = [];
  for (var i = RECOLLECT_DAYS; i >= 1; i--) days.push(addDays_(t, -i));
  try {
    var n = collectDays_(days);
    log_('일일 수집 완료 ' + days[0] + '~' + days[days.length - 1] + ' (' + n + '행)');
  } catch (e) { log_('일일 수집 실패: ' + e.message); throw e; }
}

/** 최초 1회 실행: 시트 생성 + 매일 07시 수집 트리거 설치 */
function setup() {
  [[SHEET_RAW, RAW_COLS], [SHEET_GFA, null], [SHEET_LOG, ['시각', '내용']]].forEach(function (x) { sheet_(x[0], x[1]); });
  ensureContractSheet_();
  ScriptApp.getProjectTriggers().forEach(function (tr) {
    if (tr.getHandlerFunction() === 'dailyCollect') ScriptApp.deleteTrigger(tr);
  });
  ScriptApp.newTrigger('dailyCollect').timeBased().everyDays(1).atHour(7).inTimezone(TZ).create();
  log_('설치 완료: 매일 07시 자동 수집 트리거 등록');
}

/** 연결 테스트 (편집기에서 실행 → 실행 로그 확인) */
function testConnection() {
  var camps = saGet_('/ncc/campaigns') || [];
  Logger.log('캠페인 ' + camps.length + '개: ' + camps.slice(0, 10).map(function (c) { return c.name + '(' + c.campaignTp + ')'; }).join(', '));
  var y = addDays_(todayYmd_(), -1), rows = saDayRows_(y);
  Logger.log(y + ' 광고그룹 행 ' + rows.length + '개, 광고비 합계 ' + rows.reduce(function (a, r) { return a + r[7]; }, 0));
}

// ───────────────────────── 브랜드검색 계약 광고비 ─────────────────────────
function ensureContractSheet_() {
  var ss = ss_(), sh = ss.getSheetByName(SHEET_BS_CONTRACT);
  if (sh) return sh;
  sh = sheet_(SHEET_BS_CONTRACT, BS_CONTRACT_HEADER);
  var brands = BS_PET_BRANDS.concat(['뉴트로']);
  sh.getRange(2, 1, brands.length, 5).setValues(brands.map(function (b) { return [b, '', '', '', '시작일·종료일·금액 입력']; }));
  return sh;
}

/** [{brand, start, end, amount, days, daily}] — 금액/날짜가 채워진 행만 */
function loadContracts_() {
  var sh = ss_().getSheetByName(SHEET_BS_CONTRACT);
  if (!sh || sh.getLastRow() < 2) return [];
  return sh.getRange(2, 1, sh.getLastRow() - 1, 4).getValues().map(function (r) {
    var brand = String(r[0]).trim(), amount = num_(r[3]);
    if (!brand || !r[1] || !r[2] || !amount) return null;
    var start = toYmd_(r[1]), end = toYmd_(r[2]);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || end < start) return null;
    var days = dayList_(start, end).length;
    return { brand: brand, start: start, end: end, amount: amount, days: days, daily: amount / days };
  }).filter(Boolean);
}

/** 광고그룹명에 포함된 브랜드 (계약 시트 브랜드 + 기본 브랜드, 긴 이름 우선) */
function brandOf_(group, contracts) {
  var names = BS_PET_BRANDS.slice();
  (contracts || []).forEach(function (c) { if (names.indexOf(c.brand) < 0) names.push(c.brand); });
  names.sort(function (a, b) { return b.length - a.length; });
  group = String(group || '');
  for (var i = 0; i < names.length; i++) if (group.indexOf(names[i]) >= 0) return names[i];
  return null;
}

/** 해당 브랜드·날짜의 계약 기준 하루 광고비 (계약 없으면 null) */
function contractDaily_(brand, day, contracts) {
  var sum = null;
  contracts.forEach(function (c) {
    if (c.brand === brand && day >= c.start && day <= c.end) sum = (sum || 0) + c.daily;
  });
  return sum;
}

/** 브랜드검색(NaverBS) 행의 광고비를 계약 기준 하루 광고비로 바꿈. 반환: {rows, missing:[계약 없는 브랜드]} */
function applyContractCost_(rows, contracts) {
  var missing = {};
  var out = rows.map(function (r) {
    if (r[1] !== 'NaverBS') return r;
    var brand = brandOf_(r[4], contracts), daily = brand ? contractDaily_(brand, r[0], contracts) : null;
    if (daily === null) { missing[brand || String(r[4])] = 1; return r; }
    r = r.slice(); r[7] = daily; return r;
  });
  return { rows: out, missing: Object.keys(missing) };
}

/**
 * 계약 기간 중인데 노출이 없어 데이터 행이 없는 날도 광고비는 나가므로, 광고비만 있는 행을 채워 넣는다.
 * (예: 오늘 아직 노출 전인 브랜드) — since~lastDay 범위 안에서만
 */
function fillContractDays_(rows, contracts, since, lastDay) {
  var have = {}, tmpl = {}, anyCamp = null;
  rows.forEach(function (r) {
    if (r[1] !== 'NaverBS') return;
    var b = brandOf_(r[4], contracts);
    anyCamp = anyCamp || r[3];
    if (!b) return;
    have[b + '|' + r[0]] = 1;
    tmpl[b] = tmpl[b] || r;
  });
  var add = [];
  contracts.forEach(function (c) {
    var s = c.start > since ? c.start : since, e = c.end < lastDay ? c.end : lastDay;
    if (s > e) return;
    dayList_(s, e).forEach(function (d) {
      if (have[c.brand + '|' + d]) return;
      have[c.brand + '|' + d] = 1;
      var t = tmpl[c.brand];
      add.push([d, 'NaverBS', 'ALL', t ? t[3] : (anyCamp || '브랜드검색 (계약)'), t ? t[4] : c.brand,
        0, 0, contractDaily_(c.brand, d, contracts), 0, 0]);
    });
  });
  return rows.concat(add);
}

// ───────────────────────── GFA (사내 게이트웨이) ─────────────────────────
// 사내 안내서 기준: Base URL + {네이버 GFA API 경로}?team_name={팀명}, 헤더 X-API-Key
var GFA_GATEWAY_BASE = 'https://ai.adef.co.kr/sunghyun_yoon/api/v1/public/gfa-proxy/';
var GFA_STATUS_TEXT = {
  400: 'account_id 누락', 401: 'API 키 누락 또는 불일치 (GFA_API_KEY 확인)',
  403: '등록되지 않은 팀 / 허용되지 않은 IP / 권한 없는 계정 (팀 이름 확인 → 맞다면 IP 제한 가능성 큼)',
  502: '네이버 GFA API 통신 오류', 504: '네이버 GFA API 응답 시간 초과'
};

function gfaGet_(path, params) {
  var p = props_(), key = p.getProperty('GFA_API_KEY'), team = p.getProperty('GFA_TEAM_NAME');
  if (!key || !team) throw new Error('스크립트 속성에 GFA_API_KEY / GFA_TEAM_NAME 이 필요합니다.');
  var base = p.getProperty('GFA_API_BASE_URL') || GFA_GATEWAY_BASE;
  var qs = ['team_name=' + encodeURIComponent(team)];
  Object.keys(params || {}).forEach(function (k) { qs.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k])); });
  var res = UrlFetchApp.fetch(base.replace(/\/$/, '') + '/' + String(path).replace(/^\//, '') + '?' + qs.join('&'), {
    method: 'get', muteHttpExceptions: true, headers: { 'X-API-Key': key }
  });
  return { code: res.getResponseCode(), text: res.getContentText() };
}

/** GFA 연결 시험 (편집기에서 실행 → 실행 로그 확인) */
function testGfaConnection() {
  var r = gfaGet_('adAccounts');
  if (r.code === 200) {
    Logger.log('✅ GFA 게이트웨이 연결 성공 (HTTP 200)');
    Logger.log('응답 앞부분: ' + r.text.slice(0, 1500));
  } else {
    Logger.log('❌ GFA 연결 실패 HTTP ' + r.code + ' — ' + (GFA_STATUS_TEXT[r.code] || '알 수 없는 오류'));
    Logger.log('서버 메시지: ' + r.text.slice(0, 500));
  }
}

// ───────────────────────── 집계 ─────────────────────────
function emptyAgg_() { return { imps: 0, clk: 0, cost: 0, conv: 0, rev: 0 }; }
function addAgg_(a, r) { a.imps += +r[5] || 0; a.clk += +r[6] || 0; a.cost += +r[7] || 0; a.conv += +r[8] || 0; a.rev += +r[9] || 0; return a; }
function kpi_(a) {
  return {
    imps: a.imps, clk: a.clk, cost: a.cost, conv: a.conv, rev: a.rev,
    ctr: a.imps ? a.clk / a.imps * 100 : 0, cpc: a.clk ? a.cost / a.clk : 0,
    cpa: a.conv ? a.cost / a.conv : 0, cvr: a.clk ? a.conv / a.clk * 100 : 0,
    roas: a.cost ? a.rev / a.cost * 100 : 0
  };
}
function groupBy_(rows, keyFn) {
  var m = {};
  rows.forEach(function (r) { var k = keyFn(r); addAgg_(m[k] || (m[k] = emptyAgg_()), r); });
  return Object.keys(m).map(function (k) { var o = kpi_(m[k]); o.key = k; return o; });
}

/** 실시간(오늘) 행 — 5분 캐시 */
function liveTodayRows_(force) {
  var cache = CacheService.getScriptCache(), t = todayYmd_();
  if (!force) {
    var hit = cache.get('live_' + t);
    if (hit) return JSON.parse(hit);
  }
  var rows = saDayRows_(t);
  var payload = { rows: rows, at: Utilities.formatDate(new Date(), TZ, 'HH:mm:ss') };
  try { cache.put('live_' + t, JSON.stringify(payload), LIVE_CACHE_SEC); } catch (e) {}
  return payload;
}

// ───────────────────────── 대시보드 API (google.script.run) ─────────────────────────
/**
 * @param {string} since yyyy-MM-dd
 * @param {string} until yyyy-MM-dd
 * @param {Object} opt {media:'ALL'|'SA'|'GFA', filter:'캠페인명 포함 문자열', force:bool, vatExclude:bool}
 */
function apiDashboard(since, until, opt) {
  opt = opt || {};
  var t = todayYmd_(), live = null, rows = readRaw_().filter(function (r) { return r[0] >= since && r[0] <= until && r[0] < t; });
  // 시트에 없는 과거 날짜(검색광고)는 최대 14일까지 즉시 수집해서 채움
  var have = collectedSet_();
  var missing = dayList_(since, until < t ? until : addDays_(t, -1)).filter(function (d) { return !have[d]; });
  var notice = '';
  if (missing.length && missing.length <= 14) {
    collectDays_(missing);
    rows = readRaw_().filter(function (r) { return r[0] >= since && r[0] <= until && r[0] < t; });
  } else if (missing.length > 14) {
    notice = '수집되지 않은 날짜가 ' + missing.length + '일 있습니다. [과거 데이터 수집] 버튼으로 먼저 채워주세요.';
  }
  if (until >= t) { live = liveTodayRows_(opt.force); rows = rows.concat(live.rows); }
  if (opt.vatExclude) rows = rows.map(function (r) {
    if (SA_MEDIA.indexOf(r[1]) < 0 && r[1] !== 'GFA') return r;
    r = r.slice(); r[7] = r[7] / 1.1; return r;
  });
  ensureContractSheet_();
  var contracts = loadContracts_();
  var applied = applyContractCost_(rows, contracts);
  rows = fillContractDays_(applied.rows, contracts, since, until < t ? until : t);
  if (applied.missing.length) {
    notice = (notice ? notice + ' ' : '') + '브랜드검색 계약금액 미입력: ' + applied.missing.join(', ') +
      " → 구글시트 'BS계약' 탭에 계약기간·금액을 입력하면 광고비·ROAS가 계산됩니다.";
  }

  if (opt.media === 'SA') rows = rows.filter(function (r) { return SA_MEDIA.indexOf(r[1]) >= 0; });
  if (opt.media === 'GFA') rows = rows.filter(function (r) { return r[1] === 'GFA'; });
  if (opt.filter) {
    var f = String(opt.filter).toLowerCase();
    rows = rows.filter(function (r) { return String(r[3]).toLowerCase().indexOf(f) >= 0; });
  }

  var total = kpi_(rows.reduce(function (a, r) { return addAgg_(a, r); }, emptyAgg_()));
  var today = live ? kpi_(rows.filter(function (r) { return r[0] === t; }).reduce(function (a, r) { return addAgg_(a, r); }, emptyAgg_())) : null;
  return {
    since: since, until: until, total: total, today: today, liveAt: live ? live.at : null, notice: notice,
    daily: groupBy_(rows, function (r) { return r[0]; }).sort(function (a, b) { return a.key < b.key ? -1 : 1; }),
    byCampaign: groupBy_(rows, function (r) { return r[1] + '|' + r[3]; }).sort(function (a, b) { return b.cost - a.cost; }),
    byGroup: groupBy_(rows, function (r) { return r[1] + '|' + r[3] + '|' + r[4]; }).sort(function (a, b) { return b.cost - a.cost; }).slice(0, 300),
    byChannel: groupBy_(rows.filter(function (r) { return r[1] === 'GFA' && classifyGfa_(String(r[3]), String(r[4]), String(r[10] || '')); }),
      function (r) { var c = classifyGfa_(String(r[3]), String(r[4]), String(r[10] || '')); return c[0].replace('NAVER ', '') + '|' + c[1]; })
      .sort(function (a, b) { return b.cost - a.cost; }),
    byCreative: groupBy_(rows.filter(function (r) { return r[10]; }), function (r) { return r[1] + '|' + r[3] + '|' + r[4] + '|' + r[10]; })
      .sort(function (a, b) { return b.cost - a.cost; }).slice(0, 300),
    spreadsheetUrl: ss_().getUrl()
  };
}

/** 과거 데이터 수집 (대시보드에서 7일씩 나눠 호출 — Apps Script 6분 제한 대응) */
function apiBackfill(since, until) {
  var n = collectDays_(dayList_(since, until));
  log_('백필 ' + since + '~' + until + ' (' + n + '행)');
  return n;
}

function apiStatus() {
  var p = props_();
  var raw = readRaw_(), last = raw.length ? raw[raw.length - 1][0] : null;
  return {
    sa: !!(p.getProperty('NAVER_SA_CUSTOMER_ID') && p.getProperty('NAVER_SA_API_KEY') && p.getProperty('NAVER_SA_SECRET_KEY')),
    datalab: !!(p.getProperty('NAVER_CLIENT_ID') && p.getProperty('NAVER_CLIENT_SECRET')),
    trigger: ScriptApp.getProjectTriggers().some(function (tr) { return tr.getHandlerFunction() === 'dailyCollect'; }),
    rawRows: raw.length, lastDate: last, today: todayYmd_(), spreadsheetUrl: ss_().getUrl()
  };
}

// ───────────────────────── GFA (시트 붙여넣기 → 로우데이터) ─────────────────────────
function guessGfaMapping_(header) {
  var cols = header.map(function (c) { return String(c).trim(); }), map = {};
  Object.keys(GFA_COLUMN_CANDIDATES).forEach(function (std) {
    var cands = GFA_COLUMN_CANDIDATES[std], hit = -1;
    cands.some(function (c) { hit = cols.indexOf(c); return hit >= 0; });
    if (hit < 0) cands.some(function (c) {
      for (var i = 0; i < cols.length; i++) if (cols[i].indexOf(c) >= 0) { hit = i; return true; }
      return false;
    });
    map[std] = hit;
  });
  return map;
}
function num_(v) { var n = parseFloat(String(v).replace(/[,원\s]/g, '')); return isNaN(n) ? 0 : n; }

function apiImportGfa() {
  var sh = sheet_(SHEET_GFA, null), values = sh.getDataRange().getValues();
  if (values.length < 2) throw new Error("'" + SHEET_GFA + "' 시트에 GFA 리포트(첫 행 = 컬럼명)를 붙여넣어 주세요.");
  var map = guessGfaMapping_(values[0]);
  var need = ['Date', '캠페인', '노출', '클릭', '광고비'].filter(function (k) { return map[k] < 0; });
  if (need.length) throw new Error('GFA 컬럼을 찾지 못했습니다: ' + need.join(', ') + ' (첫 행 컬럼명: ' + values[0].join(', ') + ')');
  var rows = [], days = {}, unknown = {};
  values.slice(1).forEach(function (v) {
    var d = toYmd_(v[map['Date']]);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return;
    var camp = String(v[map['캠페인']]).trim();
    if (camp.indexOf('(Mars Conf)') !== 0 && camp.indexOf('(Mars Pet)') !== 0) unknown[camp] = 1;
    days[d] = 1;
    rows.push([d, 'GFA', 'ALL', camp, map['그룹'] >= 0 ? String(v[map['그룹']]).trim() : '',
      num_(v[map['노출']]), num_(v[map['클릭']]), num_(v[map['광고비']]),
      map['구매완료수'] >= 0 ? num_(v[map['구매완료수']]) : 0, map['매출액'] >= 0 ? num_(v[map['매출액']]) : 0,
      map['소재'] >= 0 ? String(v[map['소재']]).trim() : '']);
  });
  replaceRaw_(['GFA'], Object.keys(days), rows);
  var dl = Object.keys(days).sort();
  log_('GFA 반영 ' + rows.length + '행 (' + dl[0] + '~' + dl[dl.length - 1] + ')');
  return { rows: rows.length, since: dl[0], until: dl[dl.length - 1], unknown: Object.keys(unknown).slice(0, 10) };
}

// ───────────────────────── 주간 리포트 (raw_to_report.py 규칙 이식) ─────────────────────────
/**
 * GFA 채널 분류. 마즈는 스마트채널과 피드를 별개 채널로 본다.
 * '스마트채널&피드'처럼 한 캠페인에 둘이 섞인 경우 광고그룹명(_스마트채널_ / _피드_) → 소재명(smart / feed) 순으로 판단.
 */
function classifyGfa_(camp, group, creative) {
  var sheet;
  if (camp.indexOf('(Mars Conf)') === 0) sheet = 'NAVER GFA_Conf';
  else if (camp.indexOf('(Mars Pet)') === 0) sheet = 'NAVER GFA_Pet';
  else return null;
  var name = camp.split(')').slice(1).join(')').trim(), ch = name;
  group = String(group || ''); creative = String(creative || '').toLowerCase();
  if (name.indexOf('ADVoost') >= 0) ch = 'ADVoost';
  else if (name.indexOf('NDA') >= 0) ch = 'NDA';
  else if (name.indexOf('스마트채널') >= 0 || name.indexOf('피드') >= 0) {
    var hasS = name.indexOf('스마트채널') >= 0, hasF = name.indexOf('피드') >= 0;
    if (hasS && !hasF) ch = '스마트채널';
    else if (hasF && !hasS) ch = '피드';
    else if (group.indexOf('피드') >= 0) ch = '피드';
    else if (group.indexOf('스마트채널') >= 0) ch = '스마트채널';
    else if (creative.indexOf('feed') >= 0 || creative.indexOf('피드') >= 0) ch = '피드';
    else ch = '스마트채널';
  }
  else if (name.indexOf('쇼핑프로모션') >= 0 || name.indexOf('쇼핑고객확장') >= 0) ch = '쇼핑프로모션';   // 매체 업데이트로 '쇼핑프로모션' → '쇼핑고객확장' 명칭 변경
  return [sheet, ch];
}
function classifyBs_(camp, group, contracts) {
  if (camp.indexOf('PET') >= 0 && camp.indexOf('브랜드검색') >= 0) {
    var b = brandOf_(group, contracts);
    if (b) return ['NAVER BS_Pet', b];
  }
  return null;
}

/** 시트·그룹·주차별 [노출, 클릭, 광고비, 전환, 매출] — 완전한 주만 포함 */
function weeklyAggregate_(raw, contracts) {
  contracts = contracts || [];
  if (!raw.length) return {};
  var minD = raw[0][0], maxD = raw[0][0];
  raw.forEach(function (r) { if (r[0] < minD) minD = r[0]; if (r[0] > maxD) maxD = r[0]; });
  var agg = {};   // sheet -> group -> monday -> {label, v:[5]}
  raw.forEach(function (r) {
    var c = r[1] === 'GFA' ? classifyGfa_(String(r[3]), String(r[4]), String(r[10] || '')) : r[1] === 'NaverBS' ? classifyBs_(String(r[3]), String(r[4]), contracts) : null;
    if (!c) return;
    var w = weekLabel_(r[0]);
    if (w.monday < minD || addDays_(w.monday, 6) > maxD) return;   // 앞뒤로 잘린 주 제외
    var sg = (agg[c[0]] = agg[c[0]] || {}), g = (sg[c[1]] = sg[c[1]] || {});
    var b = (g[w.monday] = g[w.monday] || { label: w.label, v: [0, 0, 0, 0, 0] });
    b.v[0] += +r[5] || 0; b.v[1] += +r[6] || 0;
    b.v[2] += r[1] === 'GFA' ? (+r[7] || 0) / GFA_VAT_DIVISOR : (+r[7] || 0);
    b.v[3] += +r[8] || 0; b.v[4] += +r[9] || 0;
  });
  // BS_Pet 광고비: 계약 시트가 있으면 그 주 7일의 하루 광고비 합계, 없으면 기존 규칙(월예산 × 7/30)
  var bs = agg['NAVER BS_Pet'] || {};
  Object.keys(bs).forEach(function (brand) {
    Object.keys(bs[brand]).forEach(function (mon) {
      var sum = null;
      for (var i = 0; i < 7; i++) {
        var d = contractDaily_(brand, addDays_(mon, i), contracts);
        if (d !== null) sum = (sum || 0) + d;
      }
      bs[brand][mon].v[2] = sum !== null ? sum : (BS_PET_MONTHLY_BUDGET[brand] || 0) * 7 / 30;
    });
  });
  // TOTAL
  Object.keys(agg).forEach(function (sheet) {
    var tot = {};
    Object.keys(agg[sheet]).forEach(function (grp) {
      Object.keys(agg[sheet][grp]).forEach(function (mon) {
        var src = agg[sheet][grp][mon], t = (tot[mon] = tot[mon] || { label: src.label, v: [0, 0, 0, 0, 0] });
        for (var i = 0; i < 5; i++) t.v[i] += src.v[i];
      });
    });
    agg[sheet]['TOTAL'] = tot;
  });
  return agg;
}
function computeRow_(v) {
  var imps = v[0], clk = v[1], cost = v[2], trans = v[3], sales = v[4];
  return [imps, clk, imps ? clk / imps : 0, clk ? cost / clk : 0, cost, trans, sales,
    trans ? cost / trans : 0, clk ? trans / clk : 0, cost ? sales / cost : 0];
}

function apiWeeklyReport() {
  ensureContractSheet_();
  var contracts = loadContracts_();
  var agg = weeklyAggregate_(readRaw_(), contracts);
  var order = JSON.parse(JSON.stringify(SHEET_GROUP_ORDER));
  Object.keys(order).forEach(function (sheet) {
    Object.keys(agg[sheet] || {}).forEach(function (b) {
      if (order[sheet].indexOf(b) < 0) order[sheet].push(b);   // 뉴트로, 쇼핑고객확장 등 추가 브랜드/채널
    });
  });
  var sh = sheet_(SHEET_WEEKLY, null);
  sh.clear();
  var out = [], fmt = [], result = {};
  var HEAD = ['WEEK', 'IMPS', 'CLICK', 'CTR', 'CPC', 'COST (-vat)', 'TRANS', 'SALES', 'CPA', 'CVR', 'ROAS'];
  Object.keys(order).forEach(function (sheet) {
    if (!agg[sheet]) return;
    result[sheet] = {};
    out.push([sheet, '', '', '', '', '', '', '', '', '', '']); fmt.push('title');
    order[sheet].forEach(function (grp) {
      var g = agg[sheet][grp];
      if (!g) return;
      out.push(['<' + grp + '>', '', '', '', '', '', '', '', '', '', '']); fmt.push('group');
      out.push(HEAD); fmt.push('head');
      var mons = Object.keys(g).sort(), rows = [];
      mons.forEach(function (m) {
        var row = computeRow_(g[m].v);
        rows.push({ week: g[m].label, v: row });
        out.push([g[m].label].concat(row)); fmt.push('data');
      });
      if (rows.length >= 2) {
        var a = rows[rows.length - 2].v, b = rows[rows.length - 1].v;
        out.push(['WoW'].concat(b.map(function (x, i) { return a[i] ? (x - a[i]) / a[i] : ''; }))); fmt.push('wow');
      }
      out.push(['', '', '', '', '', '', '', '', '', '', '']); fmt.push('blank');
      result[sheet][grp] = rows;
    });
  });
  if (out.length) {
    sh.getRange(1, 1, out.length, 11).setValues(out);
    fmt.forEach(function (f, i) {
      var r = sh.getRange(i + 1, 1, 1, 11);
      if (f === 'title') r.setFontWeight('bold').setFontSize(12);
      if (f === 'group') r.setFontWeight('bold');
      if (f === 'head') r.setFontWeight('bold').setBackground('#F2F2F2');
      if (f === 'data') {
        sh.getRange(i + 1, 2, 1, 2).setNumberFormat('#,##0'); sh.getRange(i + 1, 4).setNumberFormat('0.00%');
        sh.getRange(i + 1, 5, 1, 4).setNumberFormat('#,##0'); sh.getRange(i + 1, 9).setNumberFormat('#,##0');
        sh.getRange(i + 1, 10).setNumberFormat('0.00%'); sh.getRange(i + 1, 11).setNumberFormat('0%');
      }
      if (f === 'wow') r.setNumberFormat('0.0%').setFontStyle('italic');
    });
    sh.setColumnWidth(1, 180);
  }
  log_('주간리포트 생성');
  var ss = ss_();
  return {
    report: result,
    xlsxUrl: 'https://docs.google.com/spreadsheets/d/' + ss.getId() + '/export?format=xlsx',
    sheetUrl: ss.getUrl() + '#gid=' + sh.getSheetId()
  };
}

// ───────────────────────── 데이터랩 검색어 트렌드 ─────────────────────────
function apiTrend(startDate, endDate, timeUnit, groups, device) {
  var p = props_(), id = p.getProperty('NAVER_CLIENT_ID'), sec = p.getProperty('NAVER_CLIENT_SECRET');
  if (!id || !sec) throw new Error('스크립트 속성에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 없습니다.');
  var body = {
    startDate: startDate, endDate: endDate, timeUnit: timeUnit,
    keywordGroups: groups.slice(0, 5).map(function (g) { return { groupName: g.name, keywords: g.keywords.slice(0, 20) }; })
  };
  if (device === 'pc' || device === 'mo') body.device = device;
  var res = UrlFetchApp.fetch('https://openapi.naver.com/v1/datalab/search', {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(body), muteHttpExceptions: true,
    headers: { 'X-Naver-Client-Id': id, 'X-Naver-Client-Secret': sec }
  });
  if (res.getResponseCode() >= 400) throw new Error('데이터랩 HTTP ' + res.getResponseCode() + ': ' + res.getContentText().slice(0, 300));
  return JSON.parse(res.getContentText()).results || [];
}
