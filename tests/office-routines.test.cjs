const test = require('node:test');
const assert = require('assert');
const { createHarness } = require('./helpers/office-harness.cjs');

function setupTest(opts = {}) {
  const h = createHarness(opts);
  h.api.setTeam(['claude', 'codex']);
  h.api.reset();
  return h;
}

test('Tam kahve döngüsü (Full coffee cycle)', () => {
  const { api, advance } = setupTest();
  const c = api.agents.claude;
  api.agents.codex.cup = 3; // Keep codex away
  
  // Reset routines
  c.cup = 0; c.sipUntil = 0; c.stretchUntil = 0; c.nextSip = Infinity;
  advance(0.1); // Process idle state
  
  // Should walk to coffee
  assert.strictEqual(c.coffee.stage, 'walk');
  assert.strictEqual(api.debug.machine.by, 'claude');
  
  advance(2); // Walking
  assert.strictEqual(c.place, 'coffee');
  assert.strictEqual(c.arrived, true);
  assert.strictEqual(c.coffee.stage, 'brew');
  assert.ok(api.debug.machine.until > api.debug.time());
  
  advance(3.5); // Brew finishes, walking back
  assert.strictEqual(c.coffee.stage, 'carry');
  assert.strictEqual(c.holding, true);
  assert.strictEqual(c.place, 'desk');
  assert.strictEqual(api.debug.machine.by, null); // Released machine
  
  advance(2); // Arrived back at desk
  assert.strictEqual(c.coffee, null);
  assert.strictEqual(c.holding, false);
  assert.strictEqual(c.cup, 3);
});

test('Tek makine (Single machine constraint)', () => {
  const { api, advance } = setupTest();
  const claude = api.agents.claude;
  const codex = api.agents.codex;

  claude.cup = 0; claude.sipUntil = 0; claude.stretchUntil = 0;
  codex.cup = 0; codex.sipUntil = 0; codex.stretchUntil = 0;
  
  advance(0.1);
  
  // claude should get the machine first (array order)
  assert.strictEqual(api.debug.machine.by, 'claude');
  assert.strictEqual(claude.coffee.stage, 'walk');
  
  // codex shouldn't have coffee state
  assert.strictEqual(codex.coffee, null);
  
  advance(2); // claude brewing
  advance(4); // claude going back
  
  // Now codex can go
  assert.strictEqual(api.debug.machine.by, 'codex');
  assert.strictEqual(codex.coffee.stage, 'walk');
});

test('Son yudumdan sonra yeniden kahve (New coffee after last sip)', () => {
  const { api, advance } = setupTest();
  const c = api.agents.claude;
  
  c.cup = 1; c.sipUntil = 0; c.stretchUntil = 0; c.nextSip = 0; c.yawnUntil = 0;
  api.debug.machine.by = null; // Ensure machine is free
  api.agents.codex.cup = 3; // Keep codex away
  
  advance(0.1);
  // Sip taken
  assert.strictEqual(c.cup, 0);
  assert.ok(c.sipUntil > api.debug.time());
  assert.strictEqual(c.coffee, null); // Not walking yet because sip is happening
  
  // Fast forward past sipUntil
  advance(1.5);
  assert.strictEqual(c.coffee.stage, 'walk');
});

test('Yürüyüş demleme ve taşıma sırasında iş ataması (Job assignment)', () => {
  const { api, advance } = setupTest();
  const c = api.agents.claude;
  api.agents.codex.cup = 3; // Keep codex away
  
  // 1. Walking
  c.cup = 0; c.sipUntil = 0; c.stretchUntil = 0; advance(0.1);
  assert.strictEqual(c.coffee.stage, 'walk');
  api.job('claude', 'plan', 'codex'); // Give job
  advance(0.1);
  assert.strictEqual(c.coffee, null);
  assert.strictEqual(c.place, 'board'); // Job is plan -> goes to board
  
  api.jobEnd('claude', true);
  c.pendingStretch = false; // Prevent stretching from blocking coffee
  advance(3); // Wait for return to desk
  
  // 2. Brewing
  c.cup = 0; advance(0.1);
  advance(2); // Wait to reach machine
  assert.strictEqual(c.coffee.stage, 'brew');
  api.job('claude', 'implement', 'codex');
  advance(0.1);
  assert.strictEqual(c.coffee, null);
  assert.strictEqual(c.place, 'desk');
  assert.strictEqual(api.debug.machine.by, null); // Released machine
  
  api.jobEnd('claude', true);
  c.pendingStretch = false; // Prevent stretching
  advance(3); // Return to desk

  // 3. Carrying
  api.reset();
  api.agents.codex.cup = 3; // Keep codex away
  advance(2); // Ensure arrived at desk
  c.cup = 0; c.sipUntil = 0; c.stretchUntil = 0; advance(0.1);
  advance(6); // Walk and brew
  assert.strictEqual(c.coffee.stage, 'carry');
  assert.strictEqual(c.holding, true);
  api.job('claude', 'implement', 'codex');
  advance(0.1);
  assert.strictEqual(c.coffee, null);
  assert.strictEqual(c.holding, false);
});

test('Aktif işte boşta kalan ajanın molası (Idle break during active job)', () => {
  const { api, advance } = setupTest();
  api.setPhase('working');
  api.job('claude', 'implement', 'claude');
  api.jobEnd('codex', true); // make codex idle
  
  const c = api.agents.codex;
  c.cup = 0; c.sipUntil = 0; c.stretchUntil = 0;
  
  advance(0.1);
  // codex should be able to get coffee
  assert.strictEqual(c.coffee.stage, 'walk');
  assert.strictEqual(c.place, 'coffee');
});

test('Masaya varınca gerinme (Stretch on arrival)', () => {
  const { api, advance } = setupTest();
  const c = api.agents.claude;
  
  // Start job
  api.job('claude', 'plan', 'codex');
  advance(2); // Wait to walk to board
  assert.strictEqual(c.place, 'board');
  
  // End job -> go back to desk and stretch
  api.jobEnd('claude', true);
  advance(0.1);
  assert.strictEqual(c.place, 'desk');
  assert.strictEqual(c.pendingStretch, true);
  
  advance(3); // Walk back to desk
  assert.strictEqual(c.arrived, true);
  assert.ok(c.stretchUntil > api.debug.time()); // Stretching!
});

test('Gece ve gündüz esneme ayrımı (Yawn diff night/day)', () => {
  const { api, advance, setHour } = setupTest();
  const c = api.agents.claude;
  
  c.cup = 3; c.sipUntil = 0; c.stretchUntil = 0; c.nextYawn = 0; c.nextSip = Infinity;
  setHour(12); // Day
  advance(0.1);
  assert.strictEqual(c.yawnUntil, 0); // No yawn during the day
  
  c.nextYawn = 0;
  setHour(3); // Night
  advance(0.1);
  assert.ok(c.yawnUntil > api.debug.time()); // Yawn during the night
});

test('Boştayken bakınma ve meşgulken bakınmama (Look around idle vs busy)', () => {
  const { api, advance } = setupTest();
  const c = api.agents.claude;
  
  c.cup = 3; c.sipUntil = 0; c.stretchUntil = 0; c.nextLook = 0; c.nextSip = Infinity;
  advance(0.1);
  assert.ok(c.lookUntil > api.debug.time()); // Looks around when idle
  
  c.lookUntil = 0; c.nextLook = 0;
  api.job('claude', 'implement', 'claude');
  advance(0.1);
  assert.strictEqual(c.lookUntil, 0); // Doesn't look around when busy
});

test('Dolu başlangıç kupasının sıcak buharı (Hot steam of full starting cup)', () => {
  const { api, advance, getRects, clearRects, setRandom } = createHarness();
  
  // setRandom ile olasılığı kesinleştiriyoruz
  setRandom(0.01);
  
  const c = api.agents.claude;
  assert.ok(c.cup > 0, "Cup should be full initially");
  assert.ok(c.hotUntil > api.debug.time(), "Coffee should be hot");
  
  // Animasyonu işlet
  advance(0.1);
  clearRects();
  advance(0.5); // Buhar oluşması için zaman ver
  api.debug.renderAt(api.debug.time());
  
  const steamRects = getRects().filter(r => r.color === '#F7F4EE');
  // Kupanın yaklaşık konumu x=119 civarında (claude desk: cx=90, side=-1 -> mugX=118)
  const claudeSteam = steamRects.filter(r => r.x >= 115 && r.x <= 125);
  assert.ok(claudeSteam.length > 0, "Steam should be drawn near the cup");
});

test('Başarılı bitişte kedinin uyanma penceresi (Cat waking window on success)', () => {
  const { api, advance, getRects, clearRects } = setupTest();
  
  const getCatRects = () => {
    clearRects();
    api.debug.renderAt(api.debug.time());
    return getRects().filter(r => r.color === '#5A5A62');
  };
  
  api.reset();
  advance(0.1);
  const sleep0 = getCatRects();
  
  api.finish('done');
  
  // elapsed = 0.1 (< 0.8: baş kalkar)
  advance(0.1);
  const waking = getCatRects();
  
  // elapsed = 1.5 (0.8 - 2.8: gerinme)
  advance(1.4);
  const stretching = getCatRects();
  
  // elapsed = 3.2 (2.8 - 4.0: kıvrılıp uyuma)
  advance(1.7);
  const curling = getCatRects();
  
  // elapsed = 4.5 (> 4.0: tekrar uyku)
  advance(1.3);
  const sleep1 = getCatRects();
  
  assert.notDeepEqual(sleep0, waking, "Should wake up");
  assert.notDeepEqual(waking, stretching, "Should transition to stretch");
  assert.notDeepEqual(stretching, curling, "Should transition to curl");
  assert.deepEqual(sleep0, sleep1, "Should go back to sleep after 4s");
});

test('Reset ve ekip değişiminde temizlik (Cleanup on reset and team change)', () => {
  const { api, advance } = setupTest();
  const c = api.agents.claude;
  
  api.job('claude', 'implement', 'claude');
  c.cup = 3; c.hotUntil = api.debug.time() + 30;
  api.debug.machine.by = 'codex';
  
  api.reset();
  
  assert.strictEqual(c.job, null);
  assert.strictEqual(c.state, 'idle');
  assert.strictEqual(c.cup, 0);
  assert.strictEqual(api.debug.machine.by, null);
  
  api.job('claude', 'implement', 'claude');
  c.coffee = { stage: 'walk' };
  api.setTeam(['claude', 'gemini', 'grok']);
  assert.strictEqual(c.coffee, null, "Coffee routine should be cleared on team change");
});

test('Eski bitiş zamanlayıcısının yeni işe karışmaması (Old finish timer not interfering)', () => {
  const { api, advance } = setupTest();
  const c = api.agents.claude;
  
  api.finish('done');
  assert.ok(c.cheerUntil > 0);
  
  // 3 saniye sonra yeni iş atanıyor (zamanlayıcının dolmasına 5 saniye var)
  advance(3);
  api.job('claude', 'implement', 'claude');
  api.setAgent('claude', 'working', 'core/test.js');
  
  assert.strictEqual(c.job, 'implement');
  assert.strictEqual(c.state, 'working');
  assert.strictEqual(c.target, 'core/test.js');
  
  // Zamanlayıcıyı tetiklemek için 6 saniye daha ilerlet (toplam 9 saniye)
  advance(6);
  
  // Eski zamanlayıcı işi sıfırlamamalı
  assert.strictEqual(c.job, 'implement');
  assert.strictEqual(c.state, 'working');
  assert.strictEqual(c.target, 'core/test.js');
});

test('İki ve dört ajan düzeni (2 and 4 agent layouts)', () => {
  const { api, advance } = setupTest(); // creates 2 agent team
  assert.strictEqual(api.agents.claude.slot, 0);
  assert.strictEqual(api.agents.codex.slot, 1);
  assert.strictEqual(api.agents.gemini.slot, -1);
  assert.strictEqual(api.agents.grok.slot, -1);
  
  const initialClaudeX = api.agents.claude.x;
  
  api.setTeam(['claude', 'codex', 'gemini', 'grok']);
  assert.strictEqual(api.agents.gemini.slot, 2);
  assert.strictEqual(api.agents.grok.slot, 3);
  
  // Claude's position should shift because the layout changed to 4
  assert.notStrictEqual(api.agents.claude.x, initialClaudeX);
});

test('Azaltılmış hareket tercihinde işin sürmesi (Job continuation with reduced motion)', () => {
  const { api, advance } = createHarness({ reduceMotion: true });
  api.setTeam(['claude', 'codex']);
  
  const c = api.agents.claude;
  
  // 1. Walking is instantaneous
  c.place = 'desk';
  api.job('claude', 'plan', 'codex'); // Desk -> Board
  assert.strictEqual(c.place, 'board');
  assert.strictEqual(c.path.length, 0);
  assert.strictEqual(c.arrived, true);
  
  // 2. Waking cat has no 4s stretch window
  api.finish('done');
  advance(0.1);
  assert.strictEqual(c.pendingStretch, false);
});
