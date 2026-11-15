// Google Apps Script 전역 객체 흉내 → Code.gs를 Node에서 실행해 검증
const fs = require('fs'), vm = require('vm'), crypto = require('crypto'), path = require('path');

function makeEnv(opts) {
  opts = opts || {};
  const sheets = {}, props = Object.assign({}, opts.props || {}), cache = {}, calls = [];
  function Range(sh, r, c, nr, nc) {
    const self = {
      getValues() { const out = []; for (let i = 0; i < nr; i++) { const row = []; for (let j = 0; j < nc; j++) row.push(((sh.data[r - 1 + i] || [])[c - 1 + j]) ?? ''); out.push(row); } return out; },
      setValues(v) { if (v.length !== nr || v[0].length !== nc) throw new Error('range size mismatch'); v.forEach((row, i) => { const R = sh.data[r - 1 + i] = sh.data[r - 1 + i] || []; row.forEach((x, j) => R[c - 1 + j] = x); }); return self; },
      clearContent() { for (let i = 0; i < nr; i++) { const R = sh.data[r - 1 + i]; if (R) for (let j = 0; j < nc; j++) R[c - 1 + j] = ''; } trim(sh); return self; },
    };
    ['setNumberFormat', 'setFontWeight', 'setFontSize', 'setBackground', 'setFontStyle'].forEach(m => self[m] = () => self);
    return self;
  }
  function trim(sh) { while (sh.data.length && sh.data[sh.data.length - 1].every(x => x === '' || x == null)) sh.data.pop(); }
  function Sheet(name) {
    const sh = { name, data: [] };
    Object.assign(sh, {
      getLastRow: () => sh.data.length,
      getRange: (r, c, nr = 1, nc = 1) => Range(sh, r, c, nr, nc),
      getDataRange: () => Range(sh, 1, 1, sh.data.length, Math.max(1, ...sh.data.map(r => r.length))),
      appendRow: row => sh.data.push(row.slice()), setFrozenRows() {}, setColumnWidth() {},
      clear: () => { sh.data = []; }, getSheetId: () => 123,
    });
    return sh;
  }
  const ss = {
    getSheetByName: n => sheets[n] || null, insertSheet: n => (sheets[n] = Sheet(n)),
    getUrl: () => 'https://docs.google.com/spreadsheets/d/TESTID/edit', getId: () => 'TESTID',
  };
  const KST = 9 * 3600e3;
  const ctx = {
    console, JSON, Math, Date, Object, Array, String, Number, parseFloat, isNaN, encodeURIComponent,
    Utilities: {
      computeHmacSha256Signature: (msg, key) => Array.from(crypto.createHmac('sha256', key).update(msg, 'utf8').digest()).map(b => b > 127 ? b - 256 : b),
      base64Encode: bytes => Buffer.from(bytes.map(b => b & 255)).toString('base64'),
      formatDate: (d, tz, fmt) => { const k = new Date(d.getTime() + KST).toISOString(); return fmt === 'yyyy-MM-dd' ? k.slice(0, 10) : fmt === 'HH:mm:ss' ? k.slice(11, 19) : k.slice(0, 10) + ' ' + k.slice(11, 19); },
      sleep() {},
    },
    UrlFetchApp: { fetch: (url, o) => { calls.push({ url, o }); return opts.fetch(url, o); } },
    SpreadsheetApp: { getActiveSpreadsheet: () => ss, openById: () => ss },
    PropertiesService: { getScriptProperties: () => ({ getProperty: k => props[k] ?? null, setProperty: (k, v) => { props[k] = String(v); } }) },
    CacheService: { getScriptCache: () => ({ get: k => cache[k] ?? null, put: (k, v) => { cache[k] = v; } }) },
    LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
    ScriptApp: { getProjectTriggers: () => [], deleteTrigger() {}, newTrigger: () => { const b = { timeBased: () => b, everyDays: () => b, atHour: () => b, inTimezone: () => b, create: () => b }; return b; } },
    Logger: { log: m => (ctx._logs = (ctx._logs || []).concat(m)) },
    HtmlService: {},
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../gas/Code.gs'), 'utf8'), ctx);
  ctx._sheets = sheets; ctx._props = props; ctx._calls = calls; ctx._cache = cache;
  return ctx;
}

// 가짜 네이버 검색광고 서버 (tests/test_naver_ads.py FakeSession과 동일한 데이터)
function fakeNaver(opt) {
  opt = opt || {};
  return function (url, o) {
    const u = new URL(url), p = u.pathname, q = u.searchParams;
    const resp = (code, body) => ({ getResponseCode: () => code, getContentText: () => JSON.stringify(body) });
    if (u.host === 'api.searchad.naver.com') {
      const h = o.headers;
      ['X-Timestamp', 'X-API-KEY', 'X-Customer', 'X-Signature'].forEach(k => { if (!h[k]) throw new Error('missing ' + k); });
      const expect = crypto.createHmac('sha256', 's').update(h['X-Timestamp'] + '.GET.' + p).digest('base64');
      if (h['X-Signature'] !== expect) return resp(403, { title: 'bad signature' });
      if (p === '/ncc/campaigns') return resp(200, [
        { nccCampaignId: 'cmp-1', name: 'MARS_PET_브랜드검색', campaignTp: 'BRAND_SEARCH' },
        { nccCampaignId: 'cmp-2', name: 'PET_파워링크', campaignTp: 'WEB_SITE' }]);
      if (p === '/ncc/adgroups') {
        const cid = q.get('nccCampaignId');
        const g = { 'cmp-1': [['grp-1', '그리니즈_PC'], ['grp-2', '시저_MO']], 'cmp-2': [['grp-3', '일반']] }[cid];
        return resp(200, g.map(([id, n]) => ({ nccAdgroupId: id, nccCampaignId: cid, name: n })));
      }
      if (p === '/stats') {
        const fields = JSON.parse(q.get('fields')), tr = JSON.parse(q.get('timeRange'));
        if (opt.rejectConv && fields.includes('convAmt')) return resp(400, { title: 'invalid field' });
        if (tr.since !== tr.until) throw new Error('daily only');
        const day = +tr.since.slice(-2);
        return resp(200, { data: q.getAll('ids').map(id => Object.assign({ id, impCnt: 1000 + day, clkCnt: 10, salesAmt: 5000, ccnt: 2 }, fields.includes('convAmt') ? { convAmt: 30000 } : {})) });
      }
    }
    if (u.host === 'openapi.naver.com') {
      const b = JSON.parse(o.payload);
      if (o.headers['X-Naver-Client-Id'] !== 'cid') return resp(401, { errorMessage: 'auth' });
      return resp(200, { results: b.keywordGroups.map(g => ({ title: g.groupName, keywords: g.keywords, data: [{ period: b.startDate, ratio: 100 }, { period: b.endDate, ratio: 50 }] })) });
    }
    throw new Error('unexpected ' + url);
  };
}
module.exports = { makeEnv, fakeNaver };
