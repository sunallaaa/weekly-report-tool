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
