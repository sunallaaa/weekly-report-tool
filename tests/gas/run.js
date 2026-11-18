const assert = require('assert'), { makeEnv, fakeNaver } = require('./harness');
const PROPS = { NAVER_SA_CUSTOMER_ID: '1', NAVER_SA_API_KEY: 'k', NAVER_SA_SECRET_KEY: 's', NAVER_CLIENT_ID: 'cid', NAVER_CLIENT_SECRET: 'x' };
const out = {};
let pass = 0; const t = (name, fn) => { fn(); pass++; console.log('✓', name); };

// 1) 주차 라벨 — 파이썬과 비교용으로 출력
{ const g = makeEnv({ fetch: fakeNaver() }); const days = [];
  for (let d = '2025-01-01'; d <= '2027-12-31'; d = g.addDays_(d, 1)) days.push([d, g.weekLabel_(d).label]);
  out.weekLabels = days; }

// 2) 수집 → 시트 저장
const g = makeEnv({ fetch: fakeNaver(), props: PROPS });
t('백필 7일 = 21행 (광고그룹 3 × 7일)', () => assert.strictEqual(g.apiBackfill('2026-11-02', '2026-11-08'), 21));
t('재수집 시 중복 없음', () => { g.apiBackfill('2026-11-02', '2026-11-08'); assert.strictEqual(g.readRaw_().length, 21); });
g.apiBackfill('2026-11-09', '2026-11-15');
t('BS 매체/전환매출 매핑', () => {
  const raw = g.readRaw_(); assert.strictEqual(raw.filter(r => r[1] === 'NaverBS').length, 28);
  assert.ok(raw.every(r => r[9] === 30000));
});

// 3) GFA 붙여넣기 반영
const gfa = g.sheet_('GFA_업로드', null);
gfa.data = [['기간', '캠페인 이름', '광고 그룹 이름', '노출', '클릭', '총 비용', '구매완료 수', '구매완료 전환 매출액']];
for (let d = 2; d <= 15; d++) gfa.data.push(['2026.11.' + String(d).padStart(2, '0'), '(Mars Pet) ADVoost_시저', 'AG1', '1,000', 20, 11000, 1, 50000]);
gfa.data.push(['2026.11.03', '기타캠페인', 'X', 5, 1, 100, 0, 0]);
let gres;
t('GFA 반영 + 미분류 캠페인 경고', () => { gres = g.apiImportGfa(); assert.strictEqual(gres.rows, 15); assert.deepStrictEqual(gres.unknown, ['기타캠페인']); });
t('GFA 재반영 시 중복 없음', () => { g.apiImportGfa(); assert.strictEqual(g.readRaw_().filter(r => r[1] === 'GFA').length, 15); });

// 4) 주간 리포트
const rep = g.apiWeeklyReport();
out.report = rep.report; out.raw = g.readRaw_();
t('주간리포트: GFA_Pet TOTAL 1주차 광고비 70,000 (77,000/1.1)', () => {
  const w = rep.report['NAVER GFA_Pet'].TOTAL[0]; assert.strictEqual(w.week, '11월 1주차 (11/2~11/8)'); assert.ok(Math.abs(w.v[4] - 70000) < 1e-6);
});

// 5) 실시간 대시보드 (오늘 포함) — 시계를 2026-11-16 KST로 고정
{ const RealDate = Date; const fixed = new RealDate('2026-11-16T03:00:00Z').getTime();
  const g2 = makeEnv({ fetch: fakeNaver(), props: PROPS });
  g2.Date = class extends RealDate { constructor(...a) { super(...(a.length ? a : [fixed])); } static now() { return fixed; } };
  vm_patch(g2);
  const d = g2.apiDashboard('2026-11-09', '2026-11-16', { media: 'ALL' });
  t('대시보드: 과거 7일 자동수집 + 오늘 실시간', () => {
    assert.strictEqual(d.daily.length, 8); assert.ok(d.today && d.today.imps === 3 * 1016); assert.ok(d.liveAt);
  });
  const nCalls = g2._calls.length;
  g2.apiDashboard('2026-11-09', '2026-11-16', { media: 'ALL' });
  t('두 번째 조회는 캐시 사용 (API 재호출 없음)', () => assert.strictEqual(g2._calls.length, nCalls));
  const f = g2.apiDashboard('2026-11-09', '2026-11-16', { media: 'SA', filter: '브랜드', vatExclude: true });
  t('필터 + VAT 제외', () => { assert.strictEqual(f.byCampaign.length, 1); assert.ok(Math.abs(f.total.cost - 16 * 5000 / 1.1) < 1e-6); });
}
function vm_patch() {}

// 6) convAmt 미지원 계정 자동 대응
{ const g3 = makeEnv({ fetch: fakeNaver({ rejectConv: true }), props: PROPS });
  t('convAmt 미지원 시 기본필드로 재조회', () => { assert.strictEqual(g3.apiBackfill('2026-11-02', '2026-11-02'), 3); assert.strictEqual(g3._props.SA_NO_CONVAMT, '1'); }); }

// 7) 데이터랩
t('데이터랩 트렌드', () => { const r = g.apiTrend('2026-01-01', '2026-06-30', 'week', [{ name: '시저', keywords: ['시저'] }], ''); assert.strictEqual(r[0].title, '시저'); });

// 8) 키 없음 에러
t('키 미설정 시 안내 에러', () => { const g4 = makeEnv({ fetch: fakeNaver() }); assert.throws(() => g4.apiBackfill('2026-11-02', '2026-11-02'), /스크립트 속성/); });

// 9) 서명: 파이썬과 비교용
out.signature = g.saSignature_('1700000000000', 'GET', '/ncc/campaigns', 'dummy-secret==');
require('fs').writeFileSync(process.argv[2], JSON.stringify(out));
console.log(pass + ' passed');

// ───── 브랜드검색 계약 광고비 ─────
{
  const g5 = makeEnv({ fetch: fakeNaver(), props: PROPS });
  const fixed = new Date('2026-11-16T03:00:00Z').getTime(); const RD = Date;
  g5.Date = class extends RD { constructor(...a) { super(...(a.length ? a : [fixed])); } static now() { return fixed; } };
  g5.apiBackfill('2026-11-02', '2026-11-15');
  g5.setup();
  const sh = g5._sheets['BS계약'];
  t('계약 시트 자동 생성 (기본 브랜드 + 뉴트로)', () => {
    assert.ok(sh); assert.deepStrictEqual(sh.data.slice(1).map(r => r[0]), ['그리니즈', '시저', '템테이션', '쉬바', '위스카스', '뉴트로']);
  });
  // 시저: 90일 9,000,000 → 하루 100,000 / 그리니즈: 11/5부터 30일 3,000,000 → 하루 100,000 (주 중간 시작)
  sh.data[2] = ['시저', '2026-11-01', '2027-01-29', '9,000,000', ''];
  sh.data[1] = ['그리니즈', '2026-11-05', '2026-12-04', 3000000, ''];
  t('계약 로딩: 90일 계약 하루 광고비 = 100,000', () => {
    const c = g5.loadContracts_(); const s = c.find(x => x.brand === '시저');
    assert.strictEqual(s.days, 90); assert.strictEqual(s.daily, 100000); assert.strictEqual(c.length, 2);
  });
  const d = g5.apiDashboard('2026-11-09', '2026-11-15', { media: 'ALL' });
  t('대시보드: 브랜드별 광고비·ROAS 계산', () => {
    const grp = Object.fromEntries(d.byGroup.map(x => [x.key.split('|')[2], x]));
    assert.strictEqual(grp['시저_MO'].cost, 700000); assert.strictEqual(grp['그리니즈_PC'].cost, 700000);
    assert.ok(Math.abs(grp['시저_MO'].roas - 7 * 30000 / 700000 * 100) < 1e-9);
    assert.ok(!/계약금액 미입력/.test(d.notice));
  });
  const rep = g5.apiWeeklyReport().report['NAVER BS_Pet'];
  t('주간: 시저 주 광고비 700,000 / 그리니즈는 계약 시작 주에 4일분 400,000', () => {
    assert.strictEqual(rep['시저'][0].v[4], 700000);
    assert.strictEqual(rep['그리니즈'][0].week, '11월 1주차 (11/2~11/8)');
    assert.strictEqual(rep['그리니즈'][0].v[4], 400000); assert.strictEqual(rep['그리니즈'][1].v[4], 700000);
    assert.strictEqual(rep['TOTAL'][0].v[4], 1100000);
  });
  t('계약 없는 날·브랜드는 기존 규칙/안내', () => {
    sh.data[1] = ['그리니즈', '', '', '', ''];
    const d2 = g5.apiDashboard('2026-11-09', '2026-11-15', { media: 'ALL' });
    assert.ok(/계약금액 미입력: 그리니즈/.test(d2.notice));
    assert.strictEqual(g5.apiWeeklyReport().report['NAVER BS_Pet']['그리니즈'][0].v[4], 800000 * 7 / 30);
  });
  t('추가 브랜드(뉴트로) 분류 + 긴 이름 우선', () => {
    const c = [{ brand: '뉴트로', start: '2026-11-02', end: '2026-11-08', amount: 70000, days: 7, daily: 10000 }];
    const raw = [];
    for (let i = 0; i < 7; i++) raw.push([g5.addDays_('2026-11-02', i), 'NaverBS', 'ALL', '1. M_마즈코리아_PET_브랜드검색', '6. 뉴트로', 100, 10, 0, 1, 5000]);
    const a = g5.weeklyAggregate_(raw, c);
    assert.strictEqual(a['NAVER BS_Pet']['뉴트로'][Object.keys(a['NAVER BS_Pet']['뉴트로'])[0]].v[2], 70000);
    assert.strictEqual(g5.brandOf_('7. 시저캣', [{ brand: '시저캣' }]), '시저캣');
  });
  t('VAT 체크는 검색광고 실비용에만 (계약금액은 그대로)', () => {
    sh.data[1] = ['그리니즈', '2026-11-05', '2026-12-04', 3000000, ''];
    const v = g5.apiDashboard('2026-11-09', '2026-11-15', { media: 'ALL', vatExclude: true });
    const grp = Object.fromEntries(v.byGroup.map(x => [x.key.split('|')[2], x]));
    assert.strictEqual(grp['시저_MO'].cost, 700000); assert.ok(Math.abs(grp['일반'].cost - 7 * 5000 / 1.1) < 1e-6);
  });
}
console.log(pass + ' passed (total)');

// ───── 노출 없는 날도 계약 광고비 반영 ─────
{
  const g6 = makeEnv({ fetch: fakeNaver(), props: PROPS });
  const fixed = new Date('2026-11-16T03:00:00Z').getTime(); const RD = Date;
  g6.Date = class extends RD { constructor(...a) { super(...(a.length ? a : [fixed])); } static now() { return fixed; } };
  g6.apiBackfill('2026-11-09', '2026-11-15');
  g6.setup();
  const sh = g6._sheets['BS계약'];
  sh.data[1] = ['그리니즈', '2026-10-02', '2026-12-30', 2400000, ''];
  sh.data[6] = ['뉴트로', '2026-09-30', '2026-12-28', 2400000, ''];   // 뉴트로는 데이터 행이 전혀 없음
  // 그리니즈 11/12 행 삭제 → 노출 없는 날 흉내
  const raw = g6._sheets['Msrs Daily']; raw.data = raw.data.filter(r => !(r[0] === '2026-11-12' && String(r[4]).includes('그리니즈')));
  const d = g6.apiDashboard('2026-11-09', '2026-11-16', { media: 'ALL' });
  const grp = Object.fromEntries(d.byGroup.map(x => [x.key.split('|')[2], x]));
  t('노출 없는 날도 계약 광고비 포함 (그리니즈 8일 × 26,667)', () => assert.ok(Math.abs(grp['그리니즈_PC'].cost - 8 * 2400000 / 90) < 1e-6));
  t('데이터 행 없는 계약 브랜드(뉴트로)도 광고비 표시', () => assert.ok(Math.abs(grp['뉴트로'].cost - 8 * 2400000 / 90) < 1e-6));
  t('GFA만 보기에선 계약 광고비 제외', () => assert.strictEqual(g6.apiDashboard('2026-11-09', '2026-11-16', { media: 'GFA' }).total.cost, 0));
  t('계약 시작 전 기간엔 안 붙음', () => {
    const e = g6.apiDashboard('2026-09-01', '2026-09-29', { media: 'ALL' });
    assert.strictEqual(e.byGroup.filter(x => x.key.includes('뉴트로')).length, 0);
  });
}
console.log(pass + ' passed (all)');

// ───── GFA 게이트웨이 시험 함수 ─────
{
  const mk = (code, body) => makeEnv({ props: Object.assign({}, PROPS, { GFA_API_KEY: 'gk', GFA_TEAM_NAME: '광고사업1-1' }), fetch: (url, o) => {
    const u = new URL(url);
    assert.strictEqual(u.host, 'ai.adef.co.kr'); assert.strictEqual(u.pathname, '/sunghyun_yoon/api/v1/public/gfa-proxy/adAccounts');
    assert.strictEqual(u.searchParams.get('team_name'), '광고사업1-1'); assert.strictEqual(o.headers['X-API-Key'], 'gk');
    return { getResponseCode: () => code, getContentText: () => body };
  } });
  const ok = mk(200, '[{"adAccountNo":1}]'); ok.testGfaConnection();
  t('GFA 시험: 주소·팀명·헤더 형식 + 성공 로그', () => assert.ok(/연결 성공/.test(ok._logs[0])));
  const ng = mk(403, '{"detail":"ip not allowed"}'); ng.testGfaConnection();
  t('GFA 시험: 403 안내 문구', () => assert.ok(/IP 제한/.test(ng._logs[0]) && /ip not allowed/.test(ng._logs[1])));
  const none = makeEnv({ props: PROPS, fetch: () => { throw new Error('no call'); } });
  t('GFA 시험: 팀 이름 없으면 안내', () => assert.throws(() => none.testGfaConnection(), /GFA_TEAM_NAME/));
}
console.log(pass + ' passed (final)');

// ───── GFA 소재 단위 + 추가 채널 ─────
{
  const g7 = makeEnv({ fetch: fakeNaver(), props: PROPS });
  const fixed = new Date('2026-11-18T03:00:00Z').getTime(); const RD = Date;
  g7.Date = class extends RD { constructor(...a) { super(...(a.length ? a : [fixed])); } static now() { return fixed; } };
  const H = ['광고 소재 이름', '광고 소재 ID', '광고 그룹 이름', '광고 그룹 ID', '캠페인 이름', '캠페인 ID', '기간', '총비용', '노출수', '클릭수', '구매완료 수', '구매완료 전환매출액', '구매완료 광고수익률(%)'];
  const rows = [H];
  for (let i = 0; i < 14; i++) {
    const d = g7.addDays_('2026-11-02', i).replace(/-/g, '.') + '.';
    rows.push(['소재A', '1', '그룹1', '9', '(Mars Pet) 쇼핑고객확장', '7', d, '11000', '1000', '10', '1', '50000', '454']);
    rows.push(['소재B', '2', '그룹2', '8', '(Mars Pet) 스마트채널&피드', '6', d, '22000', '2000', '20', '2', '10000', '45']);
  }
  g7.sheet_('GFA_업로드', null).data = rows;
  g7.apiImportGfa();
  t('GFA 소재명 저장 (11번째 열) + 헤더 갱신', () => {
    const r = g7.readRaw_(); assert.ok(r.every(x => x[10] === '소재A' || x[10] === '소재B'));
    assert.strictEqual(g7._sheets['Msrs Daily'].data[0][10], '소재');
  });
  const d = g7.apiDashboard('2026-11-09', '2026-11-15', { media: 'GFA', vatExclude: true });
  t('대시보드 소재별 성과 + GFA VAT 제외', () => {
    assert.strictEqual(d.byCreative.length, 2);
    const a = d.byCreative.find(x => x.key.endsWith('소재A')); assert.ok(Math.abs(a.cost - 70000) < 1e-6);
  });
  const rep = g7.apiWeeklyReport().report['NAVER GFA_Pet'];
  t('주간: 스마트채널&피드→스마트채널, 쇼핑고객확장은 별도 채널로 표시', () => {
    assert.ok(rep['스마트채널'] && rep['쇼핑고객확장']); assert.strictEqual(rep['TOTAL'][0].v[4], 210000);
  });
}
console.log(pass + ' passed (gfa)');
