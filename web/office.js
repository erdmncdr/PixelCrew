/* PixelCrew pixel office.
 * The scene is drawn at 384x216 logical pixels onto an offscreen canvas and
 * scaled up with nearest-neighbour sampling; text (bubbles, labels) is drawn
 * afterwards at display resolution so it stays sharp.
 */
(function () {
  'use strict';

  const W = 384, H = 216;
  const VH = 204; // visible rows: the strip of empty floor below the rug is cropped
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const FONT = '"Pixelify Sans", ui-monospace, monospace';
  const FONT_UI = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", system-ui, sans-serif';

  // Night office in charcoal, walnut and black; colour is kept for the agents and status.
  const C = {
    wall: '#1B1B1E', wallStripe: '#1F1F23', molding: '#2C2C31', baseboard: '#0D0D0F',
    floorA: '#3A2A23', floorB: '#412F27', floorSeam: '#2A1E19',
    rugA: '#1E1E21', rugB: '#232327', rugEdge: '#36363C',
    deskTop: '#7D5E46', deskEdge: '#9A7759', deskFront: '#5C4433', deskDark: '#3F2F24',
    wood: '#5F4636', woodDark: '#3F2F24',
    metal: '#8D8D93', metalDark: '#4A4A50', screenFrame: '#2A2A2F', screenOff: '#0B0B0D',
    paper: '#F3E9D2', paperShade: '#D9CCB0', ink: '#111113', inkSoft: '#3A3A40',
    claude: '#FFA552', claudeDark: '#D9803A', claudeHair: '#6B3A2A', claudeCollar: '#FBE3C4',
    codex: '#4FE0B6', codexShirt: '#34406A', codexShirtDark: '#262F52', codexHair: '#19191C',
    gemini: '#5B8CFF', geminiDark: '#3F68D6', geminiHair: '#3A2A1E', geminiCollar: '#DDE6FF',
    grok: '#E6E6E6', grokShirt: '#2A2A2F', grokShirtDark: '#1C1C20', grokHair: '#CFCFD4',
    skinC: '#E8B48F', skinCDark: '#C99372',
    skinA: '#F5CDAA', skinADark: '#DDA985', skinB: '#C98E6A', skinBDark: '#A87252',
    pants: '#232327', shoes: '#0F0F11',
    green: '#7BE07B', red: '#FF6B6B', yellow: '#FFD35C', blue: '#8AA8FF', purple: '#7FA7E8',
    plant: '#4E9F5A', plantDark: '#356E40', pot: '#B4643F',
    sky: '#0F1828', skyLow: '#15223A', moon: '#FFF4C9', star: '#E8EAF0',
    board: '#ECEBE6', boardLine: '#CFCDC6',
  };

  const PAL = {
    claude: { shirt: C.claude, shirtDark: C.claudeDark, collar: C.claudeCollar, hair: C.claudeHair, skin: C.skinA, skinDark: C.skinADark, accent: C.claude },
    codex: { shirt: C.codexShirt, shirtDark: C.codexShirtDark, collar: C.codex, hair: C.codexHair, skin: C.skinB, skinDark: C.skinBDark, accent: C.codex },
    gemini: { shirt: C.gemini, shirtDark: C.geminiDark, collar: C.geminiCollar, hair: C.geminiHair, skin: C.skinC, skinDark: C.skinCDark, accent: C.gemini },
    grok: { shirt: C.grokShirt, shirtDark: C.grokShirtDark, collar: C.grok, hair: C.grokHair, skin: C.skinA, skinDark: C.skinADark, accent: C.grok },
  };
  // A darker shade of each accent, readable on paper (board rows, bubble icons).
  const INK = { claude: '#D9803A', codex: '#1F9E7D', gemini: '#3F68D6', grok: '#6E6E76' };
  const AGENT_NAMES = ['claude', 'codex', 'gemini', 'grok'];

  // Words the office shows (bubbles, screen captions, the UI's state lines), in the page language.
  const LANG = (typeof window !== 'undefined' && window.I18N && window.I18N.lang) || 'en';
  const LOCALE = LANG === 'tr' ? 'tr-TR' : 'en-US';
  const WORDS = {
    en: {
      state: {
        idle: 'idle', booting: 'getting ready', thinking: 'thinking', talking: 'writing', reading: 'reading',
        searching: 'searching', writing: 'writing code', running: 'running a command', browsing: 'browsing the web',
        delegating: 'delegating to a sub-agent', planning: 'making a list', working: 'working', error: 'error',
        waiting: 'waiting for your approval', done: 'done', off: 'offline',
      },
      job: { plan: 'planning', plan_review: 'reviewing the plan', implement: 'building', review: 'reviewing', fix: 'fixing', chat: 'answering a question', integrate: 'final check' },
      take: "I'll take this",
      screen: {
        code: ['CODING', 'CODE'], term: ['COMMAND', 'SHELL'], doc: ['READING', 'READ'], search: ['SEARCHING', 'SEARCH'],
        review: ['REVIEW', 'REVIEW'], think: ['THINKING', null], chat: ['TYPING', 'TYPING'], boot: ['BOOTING', 'BOOTING'],
        web: ['WEB', 'WEB'], list: ['PLAN', 'PLAN'], waiting: ['AWAITING OK', 'WAITING'], done: ['DONE', 'DONE'],
        error: ['ERROR', 'ERROR'], saver: ['IDLE', 'IDLE'],
      },
    },
    tr: {
      state: {
        idle: 'boşta', booting: 'hazırlanıyor', thinking: 'düşünüyor', talking: 'yazıyor', reading: 'okuyor',
        searching: 'arıyor', writing: 'kod yazıyor', running: 'komut çalıştırıyor', browsing: 'web’e bakıyor',
        delegating: 'alt ajana devrediyor', planning: 'liste yapıyor', working: 'çalışıyor', error: 'hata',
        waiting: 'onayını bekliyor', done: 'bitti', off: 'çevrimdışı',
      },
      job: { plan: 'planlıyor', plan_review: 'planı kontrol ediyor', implement: 'uyguluyor', review: 'kontrol ediyor', fix: 'düzeltiyor', chat: 'soru yanıtlıyor', integrate: 'son kontrol' },
      take: 'Bunu ben alıyorum',
      screen: {
        code: ['KOD YAZIYOR', 'KOD'], term: ['KOMUT', 'KOMUT'], doc: ['OKUYOR', 'OKUYOR'], search: ['ARIYOR', 'ARIYOR'],
        review: ['KONTROL', 'KONTROL'], think: ['DÜŞÜNÜYOR', null], chat: ['YAZIYOR', 'YAZIYOR'], boot: ['AÇILIYOR', 'AÇILIYOR'],
        web: ['WEB', 'WEB'], list: ['PLAN', 'PLAN'], waiting: ['ONAY BEKLİYOR', 'BEKLİYOR'], done: ['BİTTİ', 'BİTTİ'],
        error: ['HATA', 'HATA'], saver: ['BOŞTA', 'BOŞTA'],
      },
    },
  }[LANG];
  const LABELS = { state: WORDS.state, job: WORDS.job };

  // --- deterministic noise for static textures ---------------------------
  function rng(seed) {
    let s = seed >>> 0;
    return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
  }

  // --- room layouts -------------------------------------------------------
  // Two agents keep the original room: one desk on each side of the board.
  // Three or four agents use a staggered open office: the back row moves up and
  // in, the front row (Gemini, Grok) sits lower and further out, so the rows do
  // not touch and the centre stays free for the review table.
  // Each layout has desks (cx: centre, top: desk surface row, side: monitor side)
  // and a walking graph of foot positions.
  const LAYOUTS = {
    two: {
      desks: [{ cx: 90, top: 122, side: -1 }, { cx: 294, top: 122, side: 1 }],
      nodes: {
        C_SEAT: [90, 136], C_SIDE: [142, 136], C_FRONT: [142, 156],
        X_SEAT: [294, 136], X_SIDE: [244, 136], X_FRONT: [244, 156],
        BOARD_C: [182, 101], BOARD_X: [204, 101], COFFEE: [135, 104],
        HUB: [192, 150], HUB_C: [178, 152], HUB_X: [206, 152],
        TABLE_C: [158, 180], TABLE_X: [226, 180],
        DOOR: [17, 98], DOOR_S: [30, 156],
      },
      edges: [
        ['C_SEAT', 'C_SIDE'], ['C_SIDE', 'C_FRONT'], ['C_SIDE', 'COFFEE'], ['C_SIDE', 'BOARD_C'],
        ['COFFEE', 'BOARD_C'], ['BOARD_C', 'BOARD_X'], ['BOARD_X', 'X_SIDE'], ['BOARD_C', 'HUB'], ['BOARD_X', 'HUB'],
        ['X_SEAT', 'X_SIDE'], ['X_SIDE', 'X_FRONT'], ['C_FRONT', 'HUB'], ['X_FRONT', 'HUB'],
        ['HUB', 'TABLE_C'], ['HUB', 'TABLE_X'], ['C_FRONT', 'TABLE_C'], ['X_FRONT', 'TABLE_X'],
        ['HUB', 'HUB_C'], ['HUB', 'HUB_X'], ['C_FRONT', 'HUB_C'], ['X_FRONT', 'HUB_X'],
        ['C_FRONT', 'DOOR_S'], ['DOOR_S', 'DOOR'],
      ],
      rug: { x: 146, y: 160, w: 92, h: 38 },
      table: { x: 170, w: 44 },
      plants: [[356, 132]],
      cat: [214, 194],
    },
    four: {
      desks: [
        { cx: 104, top: 110, side: -1 }, { cx: 280, top: 110, side: 1 },
        { cx: 62, top: 160, side: -1 }, { cx: 322, top: 160, side: 1 },
      ],
      nodes: {
        C_SEAT: [104, 124], C_SIDE: [156, 124], C_FRONT: [156, 146],
        X_SEAT: [280, 124], X_SIDE: [228, 124], X_FRONT: [228, 146],
        G_SEAT: [62, 174], G_SIDE: [116, 174], K_SEAT: [322, 174], K_SIDE: [268, 174],
        BOARD_C: [182, 101], BOARD_X: [204, 101], BOARD_G: [166, 103], BOARD_K: [220, 103], COFFEE: [135, 101],
        HUB: [192, 152], HUB_C: [178, 150], HUB_X: [206, 150], HUB_G: [164, 160], HUB_K: [220, 160],
        TABLE_C: [152, 180], TABLE_X: [232, 180], TABLE_G: [182, 196], TABLE_K: [204, 196],
        DOOR: [17, 98], DOOR_S: [30, 146],
      },
      edges: [
        ['C_SEAT', 'C_SIDE'], ['C_SIDE', 'C_FRONT'], ['C_SIDE', 'COFFEE'], ['C_SIDE', 'BOARD_C'],
        ['COFFEE', 'BOARD_C'], ['COFFEE', 'BOARD_G'], ['BOARD_G', 'BOARD_C'], ['BOARD_C', 'BOARD_X'],
        ['BOARD_X', 'BOARD_K'], ['BOARD_K', 'X_SIDE'], ['BOARD_X', 'X_SIDE'], ['BOARD_C', 'HUB'], ['BOARD_X', 'HUB'],
        ['BOARD_G', 'HUB'], ['BOARD_K', 'HUB'],
        ['X_SEAT', 'X_SIDE'], ['X_SIDE', 'X_FRONT'], ['C_FRONT', 'HUB'], ['X_FRONT', 'HUB'],
        ['HUB', 'TABLE_C'], ['HUB', 'TABLE_X'], ['C_FRONT', 'TABLE_C'], ['X_FRONT', 'TABLE_X'],
        ['HUB', 'HUB_C'], ['HUB', 'HUB_X'], ['HUB', 'HUB_G'], ['HUB', 'HUB_K'],
        ['C_FRONT', 'HUB_C'], ['X_FRONT', 'HUB_X'], ['C_FRONT', 'HUB_G'], ['X_FRONT', 'HUB_K'],
        ['C_FRONT', 'DOOR_S'], ['DOOR_S', 'DOOR'],
        ['G_SEAT', 'G_SIDE'], ['G_SIDE', 'C_FRONT'], ['G_SIDE', 'TABLE_C'], ['G_SIDE', 'HUB_G'],
        ['K_SEAT', 'K_SIDE'], ['K_SIDE', 'X_FRONT'], ['K_SIDE', 'TABLE_X'], ['K_SIDE', 'HUB_K'],
        ['TABLE_C', 'TABLE_G'], ['TABLE_X', 'TABLE_K'], ['TABLE_G', 'TABLE_K'],
      ],
      rug: { x: 132, y: 146, w: 120, h: 54 },
      table: { x: 162, w: 60 },
      plants: [[356, 132]],  // fills the corner under the window
      cat: [228, 194],
    },
  };
  for (const layout of Object.values(LAYOUTS)) {
    layout.adj = {};
    for (const [a, b] of layout.edges) {
      (layout.adj[a] = layout.adj[a] || []).push(b);
      (layout.adj[b] = layout.adj[b] || []).push(a);
    }
  }
  const dist = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1]);

  function route(layout, from, target) {
    const NODES = layout.nodes;
    let start = null, best = Infinity;
    for (const [k, p] of Object.entries(NODES)) {
      const d = dist(from, p);
      if (d < best) { best = d; start = k; }
    }
    const D = { [start]: 0 }, prev = {}, open = new Set([start]);
    while (open.size) {
      let u = null;
      for (const n of open) if (u === null || D[n] < D[u]) u = n;
      open.delete(u);
      if (u === target) break;
      for (const v of layout.adj[u] || []) {
        const nd = D[u] + dist(NODES[u], NODES[v]);
        if (D[v] === undefined || nd < D[v]) { D[v] = nd; prev[v] = u; open.add(v); }
      }
    }
    const pts = [];
    let n = target;
    while (n && n !== start) { pts.unshift(NODES[n].slice()); n = prev[n]; }
    if (best > 1) pts.unshift(NODES[start].slice());
    return pts;
  }

  // Places in team order: back row left and right, then front row left and right.
  const SLOT_PLACES = [
    { desk: 'C_SEAT', board: 'BOARD_C', table: 'TABLE_C', coffee: 'COFFEE', hub: 'HUB_C' },
    { desk: 'X_SEAT', board: 'BOARD_X', table: 'TABLE_X', coffee: 'COFFEE', hub: 'HUB_X' },
    { desk: 'G_SEAT', board: 'BOARD_G', table: 'TABLE_G', coffee: 'COFFEE', hub: 'HUB_G' },
    { desk: 'K_SEAT', board: 'BOARD_K', table: 'TABLE_K', coffee: 'COFFEE', hub: 'HUB_K' },
  ];
  const SAVER = { claude: [13, 9, 0], codex: [11, 8, 31], gemini: [12, 7, 17], grok: [9, 10, 47] };

  // --- tiny pixel icons for bubbles (5x5) ----------------------------------
  const ICONS = {
    pencil: ['...##', '..###', '.###.', '###..', '##...'],
    term: ['#....', '.#...', '..#..', '.#...', '#.###'],
    book: ['##.##', '#.#.#', '#.#.#', '#.#.#', '##.##'],
    lens: ['.##..', '#..#.', '#..#.', '.###.', '....#'],
    globe: ['.###.', '#.#.#', '#####', '#.#.#', '.###.'],
    dots: ['.....', '.....', '#.#.#', '.....', '.....'],
    check: ['....#', '...#.', '#.#..', '.#...', '.....'],
    cross: ['#...#', '.#.#.', '..#..', '.#.#.', '#...#'],
    bang: ['..#..', '..#..', '..#..', '.....', '..#..'],
    quest: ['.###.', '#...#', '..##.', '.....', '..#..'],
  };
  ICONS.dash = ['.....', '.....', '#####', '.....', '.....'];

  // 3x5 bitmap font so text on the board and name plates is part of the pixel art.
  const GLYPHS = {
    0: ['###', '#.#', '#.#', '#.#', '###'], 1: ['.#.', '##.', '.#.', '.#.', '###'], 2: ['###', '..#', '###', '#..', '###'],
    3: ['###', '..#', '.##', '..#', '###'], 4: ['#.#', '#.#', '###', '..#', '..#'], 5: ['###', '#..', '###', '..#', '###'],
    6: ['###', '#..', '###', '#.#', '###'], 7: ['###', '..#', '..#', '.#.', '.#.'], 8: ['###', '#.#', '###', '#.#', '###'],
    9: ['###', '#.#', '###', '..#', '###'],
    A: ['.#.', '#.#', '###', '#.#', '#.#'], B: ['##.', '#.#', '##.', '#.#', '##.'], C: ['.##', '#..', '#..', '#..', '.##'],
    D: ['##.', '#.#', '#.#', '#.#', '##.'], E: ['###', '#..', '##.', '#..', '###'], F: ['###', '#..', '##.', '#..', '#..'],
    G: ['.##', '#..', '#.#', '#.#', '.##'], H: ['#.#', '#.#', '###', '#.#', '#.#'], I: ['###', '.#.', '.#.', '.#.', '###'],
    K: ['#.#', '#.#', '##.', '#.#', '#.#'], L: ['#..', '#..', '#..', '#..', '###'], M: ['#...#', '##.##', '#.#.#', '#...#', '#...#'],
    N: ['##.', '#.#', '#.#', '#.#', '#.#'], O: ['.#.', '#.#', '#.#', '#.#', '.#.'], P: ['##.', '#.#', '##.', '#..', '#..'],
    R: ['##.', '#.#', '##.', '#.#', '#.#'], S: ['.##', '#..', '.#.', '..#', '##.'], T: ['###', '.#.', '.#.', '.#.', '.#.'],
    U: ['#.#', '#.#', '#.#', '#.#', '###'], V: ['#.#', '#.#', '#.#', '#.#', '.#.'], X: ['#.#', '#.#', '.#.', '#.#', '#.#'],
    W: ['#...#', '#...#', '#.#.#', '##.##', '#...#'],
    Y: ['#.#', '#.#', '.#.', '.#.', '.#.'], Z: ['###', '..#', '.#.', '#..', '###'],
    '/': ['..#', '..#', '.#.', '#..', '#..'], '+': ['...', '.#.', '###', '.#.', '...'], ' ': ['...', '...', '...', '...', '...'],
  };
  // Turkish letters reuse a base glyph and add a mark one row above or below it.
  const MARKS = {
    'Ü': ['U', '#.#', null], 'Ö': ['O', '#.#', null], 'İ': ['I', '.#.', null], 'Ğ': ['G', '###', null],
    'Ş': ['S', null, '.#.'], 'Ç': ['C', null, '.#.'],
  };
  // Most glyphs are 3 wide; M and W need 5 to stay readable.
  const glyphWidth = (ch) => (GLYPHS[ch] || GLYPHS[(MARKS[ch] || [' '])[0]] || GLYPHS[' '])[0].length;
  const textWidth = (s) => [...String(s).toUpperCase()].reduce((w, ch) => w + glyphWidth(ch) + 1, 0) - 1;

  const STATE_ICON = {
    writing: 'pencil', running: 'term', reading: 'book', searching: 'lens', browsing: 'globe',
    thinking: 'dots', talking: 'dots', booting: 'dots', planning: 'pencil', delegating: 'term', working: 'term',
  };

  function createOffice(canvas) {
    const ctx = canvas.getContext('2d');
    const buf = document.createElement('canvas');
    buf.width = W; buf.height = H;
    const g = buf.getContext('2d');
    const bg = document.createElement('canvas');
    bg.width = W; bg.height = H;
    drawBackground(bg.getContext('2d'));
    // The room continues past its walls: a plain strip repeats outward, and the
    // stretch right next to the room gets its own furniture so it reads as space, not filler.
    const side = makeCanvas(48, H, (b) => drawSideTile(b));
    const sideLeft = makeCanvas(96, H, (b) => { drawSideTile(b); drawSideTile(b, 48); drawLeftDecor(b); });
    const sideRight = makeCanvas(96, H, (b) => { drawSideTile(b); drawSideTile(b, 48); drawRightDecor(b); });

    let scale = 2, ox = 0, oy = 0, dpr = 1;
    let t = 0, last = performance.now();
    let phase = 'idle';
    let runActive = false;
    let waiting = false;
    let tasks = [];
    const rows = new Map(); // task id -> { pop } for the board's appear animation
    const particles = [];
    let boardScribble = 0;
    let reviewStart = 0;  // when the plan review began, for the reviewer's tick marks
    const quota = { claude: null, codex: null, gemini: null, grok: null }; // remaining 0..100, null = unknown

    let L = LAYOUTS.two;
    function makeAgent(name, slot) {
      const seat = L.nodes[SLOT_PLACES[slot].desk] || [0, 0];
      return {
        name, pal: PAL[name], slot, x: seat[0], y: seat[1], path: [], place: 'desk', seated: true, arrived: true, facing: 0,
        state: 'idle', target: '', job: null, msg: null, emote: null, available: true,
        wanderAt: performance.now() / 1000 + 12 + Math.random() * 20, returnAt: 0, cheerUntil: 0,
        // cup: 0–3 yudum; coffee: walk → brew → carry. holding yalnızca taşırken true.
        // coffee veya sipUntil > t sırasında kupa masada çizilmez; süreler sahne saniyesidir.
        cup: 1 + Math.floor(Math.random() * 3), coffee: null, holding: false, hotUntil: t + 30,
        sipUntil: 0, nextSip: t + 4 + Math.random() * 6, stretchUntil: 0, pendingStretch: false,
        lookDir: 0, lookUntil: 0, nextLook: 3 + Math.random() * 5, yawnUntil: 0, nextYawn: 20 + Math.random() * 30,
      };
    }
    const agents = Object.fromEntries(AGENT_NAMES.map((n, i) => [n, makeAgent(n, i)]));
    let team = ['claude', 'codex'];
    for (const a of Object.values(agents)) a.slot = team.indexOf(a.name);
    const members = () => team.map((n) => agents[n]);
    // by yürüyüşte rezervasyon, demlenirken sahipliktir; until > t yalnızca demlemedir.
    // cupTaken taşıma başlayınca true kalır; sonraki demleme fincanı yeniden yerleştirir.
    const machine = { by: null, start: 0, until: 0, cupTaken: false };
    // Dört saniye: 0–0.8 uyanma, 0.8–2.8 gerinme, 2.8–4 kıvrılıp uykuya dönüş.
    let catStretchUntil = 0;
    let finishVersion = 0;
    const desk = (a) => L.desks[a.slot];
    const placeNode = (a, place) => L.nodes[SLOT_PLACES[a.slot][place]];
    // Wall screens: one wide screen per column, split in two when the front row is used.
    let screens = [];
    function layoutScreens() {
      const at = (slot) => team[slot] && agents[team[slot]];
      const left = at(2) ? [[at(0), 48, 38], [at(2), 88, 38]] : [[at(0), 50, 78]];
      const right = at(3) ? [[at(1), 256, 38], [at(3), 296, 38]] : [[at(1), 256, 78]];
      screens = [...left, ...right];
    }
    layoutScreens();

    // ------------------------------------------------------------------ API
    function releaseMachine(a) {
      if (machine.by === a.name) Object.assign(machine, { by: null, start: 0, until: 0, cupTaken: true });
    }
    function interruptRoutine(a) {
      releaseMachine(a);
      // İş önceliklidir: yarım kahve bırakılır; fincan görev yerine taşınmaz.
      if (a.coffee || a.holding) { a.cup = 0; a.hotUntil = 0; }
      a.coffee = null; a.holding = false; a.sipUntil = 0;
      a.pendingStretch = false; a.stretchUntil = 0;
      a.lookDir = 0; a.lookUntil = 0; a.yawnUntil = 0; a.returnAt = 0;
    }
    function clearRoutines(a) {
      interruptRoutine(a);
      a.cup = 0; a.hotUntil = 0;
      a.nextSip = t + 4 + Math.random() * 6;
      a.nextLook = t + 3 + Math.random() * 5;
      a.nextYawn = t + 20 + Math.random() * 30;
      a.wanderAt = performance.now() / 1000 + 12 + Math.random() * 20;
    }
    function invalidateFinish() {
      finishVersion++;
      for (const a of Object.values(agents)) a.cheerUntil = 0;
    }
    function startStretch(a) {
      if (a.pendingStretch && a.available && !a.job && !waiting && a.place === 'desk' && a.arrived && !a.path.length) {
        a.pendingStretch = false;
        // Çizim 1.8 saniyelik gerinmeyi masaya varıldığı anda başlatır.
        a.stretchUntil = t + 1.8;
      }
    }
    function goTo(a, place) {
      if (a.slot < 0) return;
      if (a.coffee && place !== 'coffee' && !(a.coffee.stage === 'carry' && place === 'desk')) interruptRoutine(a);
      if (a.place === place && (a.path.length || a.arrived)) return;
      a.sipUntil = 0; a.lookUntil = 0; a.lookDir = 0; a.yawnUntil = 0;
      a.place = place;
      a.seated = false;
      a.arrived = false;
      a.path = route(L, [a.x, a.y], SLOT_PLACES[a.slot][place]);
      if (reduceMotion) {
        const node = placeNode(a, place);
        a.x = node[0]; a.y = node[1]; a.path = [];
      }
      if (!a.path.length) arrive(a);
    }
    function arrive(a) {
      a.arrived = true;
      a.seated = a.place === 'desk';
      a.facing = 0;
      if (a.seated && a.coffee?.stage === 'carry' && a.holding) {
        a.holding = false; a.coffee = null; a.cup = 3;
        a.nextSip = t + 2 + Math.random() * 3;
        a.wanderAt = performance.now() / 1000 + 60 + Math.random() * 60;
      }
      startStretch(a);
    }
    function homePlace(a) {
      if (!a.available) return 'desk';
      if (waiting) return 'board';
      if (a.job === 'plan' || a.job === 'plan_review') return 'board';
      if (a.job === 'review') return 'table';
      if (phase === 'done' && t < a.cheerUntil) return 'hub';
      return 'desk';
    }
    function settle() {
      for (const a of members()) {
        if (a.coffee && a.available && !a.job && !waiting && homePlace(a) === 'desk') continue;
        if (a.coffee) interruptRoutine(a);
        goTo(a, homePlace(a));
        startStretch(a);
      }
    }

    const api = {
      reset() {
        invalidateFinish();
        phase = 'idle'; runActive = false; waiting = false; tasks = []; rows.clear(); boardScribble = 0;
        particles.length = 0;
        for (const a of Object.values(agents)) {
          clearRoutines(a);
          a.state = 'idle'; a.target = ''; a.job = null; a.msg = null; a.emote = null; a.cheerUntil = 0;
        }
        Object.assign(machine, { by: null, start: 0, until: 0, cupTaken: false });
        catStretchUntil = 0;
        settle();
      },
      setAvailable(list) {
        for (const a of Object.values(agents)) {
          a.available = !list || list.includes(a.name);
          if (!a.available) {
            clearRoutines(a); a.job = null; a.state = 'off'; a.cheerUntil = 0;
            catStretchUntil = 0;
          } else if (a.state === 'off') a.state = 'idle';
        }
        settle();
      },
      /** Who has a desk. Agents that change seat (or join) sit down there at once. */
      setTeam(list) {
        const next = AGENT_NAMES.filter((n) => (list || []).includes(n));
        if (!next.length || next.join() === team.join()) return;
        invalidateFinish();
        catStretchUntil = 0;
        particles.length = 0;
        team = next;
        const layout = team.length > 2 ? LAYOUTS.four : LAYOUTS.two;
        const moved = layout !== L;  // a new room: everyone takes their new seat
        L = layout;
        for (const a of Object.values(agents)) {
          clearRoutines(a);
          const slot = team.indexOf(a.name);
          if (slot === a.slot && !moved) continue;
          a.slot = slot;
          if (slot < 0) { a.path = []; a.arrived = false; a.job = null; a.state = 'idle'; continue; }
          const seat = L.nodes[SLOT_PLACES[slot].desk];
          a.x = seat[0]; a.y = seat[1]; a.path = []; a.place = 'desk'; arrive(a);
        }
        Object.assign(machine, { by: null, start: 0, until: 0, cupTaken: false });
        layoutScreens();
        settle();
      },
      setPhase(p) {
        phase = p;
        runActive = !['idle', 'done', 'failed', 'cancelled'].includes(p);
        if (runActive) invalidateFinish();
        if (p !== 'approval') waiting = false;
        if (p === 'planning') boardScribble = 0;
        if (p === 'plan_review') reviewStart = t;
        settle();
      },
      setWaiting(on) {
        waiting = on;
        for (const a of members()) a.emote = on ? { kind: 'quest', until: Infinity } : null;
        settle();
      },
      setAgent(name, state, target) {
        const a = agents[name];
        if (!a) return;
        a.state = state;
        a.target = target || '';
        if (!['idle', 'done', 'waiting'].includes(state)) {
          a.lookDir = 0; a.lookUntil = 0; a.yawnUntil = 0;
          // Görev bildirimi gelmeden başlayan çalışma da molayı keser.
          if (a.coffee) { interruptRoutine(a); goTo(a, homePlace(a)); }
        }
        if (state === 'error') a.emote = { kind: 'bang', until: t + 4 };
      },
      /** author: whose work a review is about, so the sheet flies from that desk. */
      job(name, job, author) {
        const a = agents[name];
        if (!a) return;
        invalidateFinish();
        interruptRoutine(a);
        const prevJob = a.job;
        a.job = job;
        const from = agents[author];
        if (job === 'review' && prevJob !== 'review' && from && from !== a && from.slot >= 0) {
          flyPaper(desk(from).cx, desk(from).top - 8, 192, 164);
        }
        settle();
      },
      jobEnd(name, ok) {
        const a = agents[name];
        if (!a) return;
        a.job = null;
        a.state = ok ? 'idle' : 'error'; a.target = '';
        a.stretchUntil = 0;
        a.pendingStretch = !!ok && !reduceMotion;
        if (!ok) a.emote = { kind: 'bang', until: t + 5 };
        settle();
      },
      say(name, text) {
        const a = agents[name];
        if (a) a.msg = { text: oneLine(text), until: t + 5 };
      },
      verdict(name, verdict) {
        const a = agents[name];
        if (!a) return;
        a.emote = { kind: verdict === 'approve' ? 'check' : 'cross', until: t + 4 };
      },
      /** Work moves from one agent to the other: a sheet flies desk to desk, the receiver says so. */
      handoff(from, to, text) {
        if (!agents[from] || !agents[to] || from === to || agents[from].slot < 0 || agents[to].slot < 0) return;
        const df = desk(agents[from]), dt = desk(agents[to]);
        flyPaper(df.cx, df.top - 8, dt.cx, dt.top - 8);
        agents[to].msg = { text: oneLine(text || WORDS.take), until: t + 5 };
        agents[from].emote = { kind: 'bang', until: t + 3 };
      },
      fileChanged(name) {
        const a = agents[name];
        if (!a || a.slot < 0 || reduceMotion) return;
        const d = desk(a);
        const x = d.cx + d.side * 26;
        for (let i = 0; i < 4; i++) {
          particles.push({ kind: 'spark', x: x + rand(-4, 4), y: d.top - 18 + rand(-3, 3), vx: rand(-8, 8), vy: rand(-18, -8), life: 0.8, max: 0.8, color: a.pal.accent });
        }
      },
      setQuota(name, remaining) {
        if (name in quota) quota[name] = typeof remaining === 'number' ? Math.max(0, Math.min(100, remaining)) : null;
      },
      setTasks(list) {
        tasks = list.slice();
        const seen = new Set(tasks.map((task) => task.id));
        for (const task of tasks) if (!rows.has(task.id)) rows.set(task.id, { pop: 0 });
        for (const id of [...rows.keys()]) if (!seen.has(id)) rows.delete(id);
      },
      /** What is under a page point: the board, an agent, or nothing. */
      hit(clientX, clientY) {
        const rect = canvas.getBoundingClientRect();
        const lx = ((clientX - rect.left) * dpr - ox) / scale;
        const ly = ((clientY - rect.top) * dpr - oy) / scale;
        if (lx >= BOARD.x - 2 && lx <= BOARD.x + BOARD.w + 2 && ly >= BOARD.y - 2 && ly <= BOARD.y + BOARD.h + 4) return { kind: 'board' };
        for (const a of members()) {
          if (a._top === undefined) continue;
          const bottom = a.seated && !a.path.length ? a._top + 20 : a.y;
          if (lx >= a.x - 7 && lx <= a.x + 7 && ly >= a._top - 3 && ly <= bottom) return { kind: 'agent', name: a.name };
        }
        return null;
      },
      envelope() {
        if (reduceMotion) return;
        particles.push({ kind: 'letter', x: 22, y: 64, sx: 22, sy: 64, ex: 192, ey: 34, p: 0, life: 1.4, max: 1.4 });
      },
      finish(status) {
        invalidateFinish();
        const version = finishVersion;
        phase = status;
        runActive = false;
        waiting = false;
        for (const a of members()) {
          interruptRoutine(a);
          a.job = null;
          a.pendingStretch = status === 'done' && !reduceMotion;
          a.emote = status === 'done' ? { kind: 'check', until: t + 6 } : status === 'failed' ? { kind: 'bang', until: t + 6 } : null;
          a.cheerUntil = status === 'done' ? t + 7 : 0;
          a.state = status === 'done' ? 'done' : status === 'failed' ? 'error' : 'idle';
        }
        catStretchUntil = status === 'done' && !reduceMotion ? t + 4 : 0;
        if (status === 'done' && !reduceMotion) confetti();
        settle();
        setTimeout(() => {
          if (version !== finishVersion) return;
          for (const a of Object.values(agents)) {
            a.cheerUntil = 0;
            if (a.state === 'done' || a.state === 'error') a.state = 'idle';
          }
          settle();
        }, 8000);
      },
      /** Jump straight to the state implied by a replayed history, no walking. */
      snap() {
        for (const a of members()) {
          const node = placeNode(a, a.place);
          a.x = node[0]; a.y = node[1]; a.path = []; arrive(a);
        }
        for (const r of rows.values()) r.pop = 1;
        particles.length = 0;
      },
    };

    function oneLine(s) {
      return String(s || '').replace(/\s+/g, ' ').trim();
    }
    function rand(a, b) { return a + Math.random() * (b - a); }

    function flyPaper(x0, y0, x1, y1) {
      if (reduceMotion) return;
      particles.push({ kind: 'paper', sx: x0, sy: y0, ex: x1, ey: y1, x: x0, y: y0, p: 0, life: 1.1, max: 1.1 });
    }
    function confetti() {
      const colors = [...members().map((a) => a.pal.accent), C.yellow, C.paper];
      for (let i = 0; i < 70; i++) {
        particles.push({ kind: 'confetti', x: 192 + rand(-30, 30), y: 120 + rand(-10, 10), vx: rand(-60, 60), vy: rand(-110, -50), life: 2.6, max: 2.6, color: colors[i % colors.length], spin: rand(0, 6) });
      }
    }

    // Whiteboard: one row per task with a four-step track (planned, building, review, done).
    const BOARD = { x: 150, y: 8, w: 84, h: 56 };

    // --------------------------------------------------------------- update
    function update(dt) {
      t += dt;
      const now = t;
      for (const a of members()) {
        if (a.path.length) {
          const [tx, ty] = a.path[0];
          const dx = tx - a.x, dy = ty - a.y;
          const d = Math.hypot(dx, dy);
          const step = 46 * dt;
          if (Math.abs(dx) > 0.5) a.facing = dx > 0 ? 1 : -1;
          if (d <= step) {
            a.x = tx; a.y = ty; a.path.shift();
            if (!a.path.length) arrive(a);
          } else {
            a.x += (dx / d) * step; a.y += (dy / d) * step;
          }
        } else {
          routines(a, now);
        }
        if (a.msg && a.msg.until < now) a.msg = null;
        if (a.emote && a.emote.until < now) a.emote = null;
        if (phase === 'done' && a.cheerUntil && now > a.cheerUntil && a.place === 'hub') { a.cheerUntil = 0; goTo(a, 'desk'); }
        // Kupa, sahibi başka yerde çalışırken de masada soğuyabilir.
        if (!reduceMotion && a.available && !a.coffee && !a.holding && now >= a.sipUntil && a.hotUntil > now && a.cup > 0 && Math.random() < dt * 1.5) {
          const d = desk(a);
          const mugX = d.side < 0 ? d.cx + 28 : d.cx - 32;
          particles.push({ kind: 'steam', x: mugX + 2 + rand(-1, 1), y: d.top - 6, vx: rand(-1, 1), vy: -5, life: 1.6, max: 1.6 });
        }
      }
      for (const r of rows.values()) r.pop = Math.min(1, r.pop + dt * 2.5);
      if (phase === 'planning') boardScribble = Math.min(1, boardScribble + dt / 25);
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        p.life -= dt;
        if (p.life <= 0) { particles.splice(i, 1); continue; }
        if (p.kind === 'letter' || p.kind === 'paper') {
          p.p = 1 - p.life / p.max;
          const e = p.p < 0.5 ? 2 * p.p * p.p : 1 - Math.pow(-2 * p.p + 2, 2) / 2;
          p.x = p.sx + (p.ex - p.sx) * e;
          p.y = p.sy + (p.ey - p.sy) * e - Math.sin(Math.PI * e) * 26;
        } else {
          p.vy += (p.kind === 'confetti' ? 120 : p.kind === 'steam' ? -6 : 30) * dt;
          p.x += p.vx * dt; p.y += p.vy * dt;
        }
      }
      const brewing = machine.by && t < machine.until;
      if (!reduceMotion && brewing && Math.random() < dt * 6) {
        particles.push({ kind: 'steam', x: 135 + rand(-1, 1), y: 63, vx: rand(-2, 2), vy: -6, life: 2, max: 2 });
      }
    }

    // Görevsiz ajanlar aktif iş sırasında da mola verebilir; çalışma ve onay önceliklidir.
    function routines(a, now) {
      if (!a.available || reduceMotion) return;
      const free = !a.job && !waiting && ['idle', 'done', 'waiting'].includes(a.state);
      const seated = a.place === 'desk' && a.arrived;
      if (a.coffee) {
        if (!free) { interruptRoutine(a); goTo(a, homePlace(a)); return; }
        if (a.coffee.stage === 'walk' && a.place === 'coffee' && a.arrived) {
          if (machine.by !== a.name) { interruptRoutine(a); goTo(a, 'desk'); return; }
          a.coffee = { stage: 'brew', until: now + 3.2 };
          Object.assign(machine, { by: a.name, start: now, until: now + 3.2, cupTaken: false });
        } else if (a.coffee.stage === 'brew' && now >= a.coffee.until) {
          a.coffee = { stage: 'carry' };
          a.holding = true; a.cup = 3; a.hotUntil = now + 30;
          releaseMachine(a);
          goTo(a, 'desk');
        }
        return;
      }
      startStretch(a);
      // Son yudum tamamlanmadan veya gerinme bitmeden yeni yolculuk başlamaz.
      if (seated && free && a.cup === 0 && now >= a.sipUntil && now >= a.stretchUntil
          && !a.pendingStretch && !machine.by) {
        a.coffee = { stage: 'walk' };
        a.hotUntil = 0;
        Object.assign(machine, { by: a.name, start: 0, until: 0, cupTaken: true });
        goTo(a, 'coffee');
        return;
      }
      if (!seated || a.path.length) return;
      const calm = ['idle', 'done', 'thinking', 'waiting'].includes(a.state);
      const resting = now >= a.sipUntil && now >= a.stretchUntil && now >= a.yawnUntil;
      if (calm && resting && a.cup > 0 && now >= a.nextSip) {
        a.sipUntil = now + 1.4; a.cup = Math.max(0, Math.min(3, a.cup - 1)); a.nextSip = now + 10 + Math.random() * 12;
        a.lookDir = 0; a.lookUntil = 0;
      }
      if (free && ['idle', 'done'].includes(a.state) && resting && now >= a.sipUntil && now > a.nextLook) {
        a.lookDir = [-1, 1, 0][Math.floor(Math.random() * 3)]; a.lookUntil = now + 1.2 + Math.random();
        a.nextLook = now + 4 + Math.random() * 7;
      }
      if (calm && resting && now >= a.sipUntil && now > a.nextYawn) {
        if (new Date().getHours() < 6) a.yawnUntil = now + 1.3;
        a.nextYawn = now + 35 + Math.random() * 40;
      }
    }

    // --------------------------------------------------------------- render
    function px(x, y, w, h, color) {
      g.fillStyle = color;
      g.fillRect(Math.round(x), Math.round(y), w, h);
    }

    function drawBackground(b) {
      const r = rng(7);
      // wall
      b.fillStyle = C.wall; b.fillRect(0, 0, W, 88);
      for (let x = 0; x < W; x += 12) { b.fillStyle = C.wallStripe; b.fillRect(x, 0, 6, 84); }
      b.fillStyle = C.molding; b.fillRect(0, 0, W, 3);
      b.fillStyle = '#141416'; b.fillRect(0, 3, W, 1);
      b.fillStyle = C.baseboard; b.fillRect(0, 84, W, 4);
      b.fillStyle = C.molding; b.fillRect(0, 84, W, 1);
      // floor planks
      for (let y = 88; y < H; y += 7) {
        const row = (y - 88) / 7;
        b.fillStyle = row % 2 ? C.floorA : C.floorB; b.fillRect(0, y, W, 7);
        b.fillStyle = C.floorSeam; b.fillRect(0, y, W, 1);
        let x = -Math.floor(r() * 40);
        while (x < W) { x += 34 + Math.floor(r() * 30); b.fillRect(x, y, 1, 7); }
      }
      // shadow where floor meets wall
      b.fillStyle = 'rgba(0,0,0,0.35)'; b.fillRect(0, 88, W, 3);
      // door
      b.fillStyle = C.woodDark; b.fillRect(2, 30, 30, 58);
      b.fillStyle = C.wood; b.fillRect(5, 33, 24, 55);
      b.fillStyle = C.woodDark; b.fillRect(8, 37, 18, 18); b.fillRect(8, 60, 18, 22);
      b.fillStyle = C.wood; b.fillRect(9, 38, 16, 16); b.fillRect(9, 61, 16, 20);
      b.fillStyle = C.yellow; b.fillRect(25, 60, 2, 2);
      // window frame
      b.fillStyle = C.molding; b.fillRect(340, 12, 38, 44);
      b.fillStyle = '#34343A'; b.fillRect(340, 54, 38, 3);
      // digital clock housing above the door
      b.fillStyle = C.molding; b.fillRect(1, 8, 35, 15);
      b.fillStyle = '#100B08'; b.fillRect(2, 9, 33, 13);
      b.fillStyle = 'rgba(0,0,0,0.35)'; b.fillRect(2, 23, 34, 1);
      // whiteboard frame
      b.fillStyle = C.metal; b.fillRect(148, 6, 88, 60);
      b.fillStyle = C.board; b.fillRect(150, 8, 84, 56);
      b.fillStyle = C.metalDark; b.fillRect(148, 64, 88, 2);
      b.fillStyle = C.metal; b.fillRect(160, 66, 20, 2);
      b.fillStyle = C.red; b.fillRect(163, 65, 4, 2);
      b.fillStyle = C.blue; b.fillRect(170, 65, 4, 2);
    }

    function makeCanvas(w, h, paint) {
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      paint(c.getContext('2d'));
      return c;
    }

    function drawSideTile(b, dx = 0) {
      b.fillStyle = C.wall; b.fillRect(dx, 0, 48, 88);
      for (let x = 0; x < 48; x += 12) { b.fillStyle = C.wallStripe; b.fillRect(dx + x, 0, 6, 84); }
      b.fillStyle = C.molding; b.fillRect(dx, 0, 48, 3);
      b.fillStyle = '#141416'; b.fillRect(dx, 3, 48, 1);
      b.fillStyle = C.baseboard; b.fillRect(dx, 84, 48, 4);
      b.fillStyle = C.molding; b.fillRect(dx, 84, 48, 1);
      for (let y = 88; y < H; y += 7) {
        const row = (y - 88) / 7;
        b.fillStyle = row % 2 ? C.floorA : C.floorB; b.fillRect(dx, y, 48, 7);
        b.fillStyle = C.floorSeam; b.fillRect(dx, y, 48, 1); b.fillRect(dx + (row * 17) % 48, y, 1, 7);
      }
      b.fillStyle = 'rgba(0,0,0,0.35)'; b.fillRect(dx, 88, 48, 3);
    }

    // Bookshelf and a framed poster just left of the door.
    function drawLeftDecor(b) {
      const p = (x, y, w, h, c) => { b.fillStyle = c; b.fillRect(x, y, w, h); };
      // Bookcase: a walnut carcass with a dark back, four shelves and a plinth.
      const X = 50, Y = 16, Wd = 40, Ht = 72;
      p(X - 1, Y + Ht, Wd + 2, 3, 'rgba(0,0,0,0.35)');            // floor shadow
      p(X, Y, Wd, Ht, '#3B2A20');                                  // carcass
      p(X - 1, Y - 2, Wd + 2, 3, '#6E5140'); p(X - 1, Y - 2, Wd + 2, 1, '#8A6A54'); // top board
      p(X, Y + 1, 2, Ht - 1, '#5A4334'); p(X + Wd - 2, Y + 1, 2, Ht - 1, '#2E2119'); // side panels
      p(X + 2, Y + 1, Wd - 4, Ht - 6, '#211812');                  // back panel
      p(X - 1, Y + Ht - 5, Wd + 2, 5, '#4A3528'); p(X - 1, Y + Ht - 5, Wd + 2, 1, '#6E5140'); // plinth
      const shelfH = 16;
      const palette = ['#7A3B3B', '#3E5C4A', '#33496B', '#8C6A3F', '#5A4B6E', '#B8A88A', '#9A4E2E', '#2F3E4F'];
      const r = rng(41);
      for (let shelf = 0; shelf < 4; shelf++) {
        const floor = Y + 1 + (shelf + 1) * shelfH;                // top of this shelf board
        p(X + 2, floor, Wd - 4, 2, '#5A4334'); p(X + 2, floor, Wd - 4, 1, '#7A5C47');
        p(X + 2, floor - shelfH + 1, Wd - 4, 1, 'rgba(0,0,0,0.35)'); // shade under the board above
        let x = X + 3;
        const end = X + Wd - 3;
        // Top shelf: a small plant and a photo frame instead of books.
        if (shelf === 0) {
          p(x + 1, floor - 4, 5, 4, C.pot); p(x + 1, floor - 4, 5, 1, '#CE7A52');
          p(x + 2, floor - 8, 1, 4, C.plant); p(x + 3, floor - 9, 1, 5, C.plantDark); p(x + 4, floor - 7, 2, 2, C.plant);
          p(x + 9, floor - 9, 7, 9, '#C9A45C'); p(x + 10, floor - 8, 5, 7, '#1D2A3E'); p(x + 11, floor - 4, 3, 3, '#8AA8C8');
          x += 19;
        }
        // Second shelf from the bottom: a stack of books lying flat.
        if (shelf === 2) {
          const stack = ['#33496B', '#B8A88A', '#7A3B3B'];
          stack.forEach((c, i) => { p(x + (i % 2), floor - 3 - i * 3, 11 - i, 3, c); p(x + (i % 2), floor - 3 - i * 3, 11 - i, 1, 'rgba(255,255,255,0.12)'); });
          x += 14;
        }
        while (x < end - 2) {
          const w = 2 + Math.floor(r() * 3);
          const h = 8 + Math.floor(r() * 5);
          if (x + w > end) break;
          const c = palette[Math.floor(r() * palette.length)];
          if (r() < 0.12 && x + w + 3 < end) {
            // a book leaning on its neighbour
            for (let i = 0; i < h; i++) p(x + Math.floor(i / 4), floor - 1 - i, w, 1, c);
            x += w + 3;
            continue;
          }
          p(x, floor - h, w, h, c);
          p(x, floor - h, 1, h, 'rgba(255,255,255,0.14)');           // spine highlight
          if (r() < 0.5) p(x, floor - h + 2, w, 1, '#C9A45C');       // gilt band
          x += w + (r() < 0.25 ? 1 : 0);
        }
      }
      // Framed print on the wall beside it.
      p(14, 18, 26, 20, C.metalDark); p(16, 20, 22, 16, '#1D2A3E');
      p(19, 30, 6, 6, '#B87A4B'); p(27, 27, 6, 9, '#3E8E78'); p(22, 23, 3, 3, '#C9A45C');
    }

    // A tall plant and a lamp just right of the window.
    function drawRightDecor(b) {
      const p = (x, y, w, h, c) => { b.fillStyle = c; b.fillRect(x, y, w, h); };
      p(14, 76, 14, 14, C.pot); p(14, 76, 14, 2, '#CE7A52');
      p(20, 44, 2, 32, C.plantDark);
      p(10, 50, 9, 5, C.plant); p(23, 46, 10, 5, C.plant); p(12, 60, 8, 5, C.plant); p(22, 58, 10, 5, C.plant);
      p(16, 40, 7, 5, C.plant); p(13, 68, 7, 4, C.plantDark); p(23, 66, 8, 4, C.plantDark);
      p(58, 30, 2, 58, C.metalDark); p(54, 86, 10, 2, C.metalDark);
      p(50, 22, 18, 9, '#E9C46A'); p(52, 31, 14, 1, 'rgba(255, 220, 140, 0.45)');
      p(44, 32, 30, 20, 'rgba(255, 211, 92, 0.06)');
    }

    function drawSides() {
      const th = Math.round(H * scale);
      const right = ox + Math.round(W * scale);
      if (ox <= 0 && right >= canvas.width) return;
      const tw = Math.round(48 * scale), dw = Math.round(96 * scale);
      ctx.drawImage(sideLeft, 0, 0, 96, H, ox - dw, oy, dw, th);
      for (let x = ox - dw - tw; x > -tw; x -= tw) ctx.drawImage(side, 0, 0, 48, H, x, oy, tw, th);
      ctx.drawImage(sideRight, 0, 0, 96, H, right, oy, dw, th);
      for (let x = right + dw; x < canvas.width; x += tw) ctx.drawImage(side, 0, 0, 48, H, x, oy, tw, th);
      // A light falloff toward the window edges only.
      const fade = Math.min(ox, Math.round(120 * scale));
      if (fade > 0) {
        const gl = ctx.createLinearGradient(0, 0, fade, 0);
        gl.addColorStop(0, 'rgba(11,11,12,0.55)'); gl.addColorStop(1, 'rgba(11,11,12,0)');
        ctx.fillStyle = gl; ctx.fillRect(0, oy, fade, th);
        const gr = ctx.createLinearGradient(canvas.width - fade, 0, canvas.width, 0);
        gr.addColorStop(0, 'rgba(11,11,12,0)'); gr.addColorStop(1, 'rgba(11,11,12,0.55)');
        ctx.fillStyle = gr; ctx.fillRect(canvas.width - fade, oy, fade, th);
      }
    }

    function drawWindow() {
      px(342, 14, 34, 38, C.sky);
      px(342, 38, 34, 14, C.skyLow);
      const r = rng(3);
      for (let i = 0; i < 12; i++) {
        const sx = 343 + Math.floor(r() * 32), sy = 15 + Math.floor(r() * 30);
        const tw = Math.sin(t * 2 + i * 1.7) > 0.3;
        if (tw) px(sx, sy, 1, 1, C.star);
      }
      px(366, 19, 5, 5, C.moon); px(365, 20, 1, 3, C.moon); px(371, 20, 1, 3, C.moon); px(368, 19, 2, 2, C.sky);
      // city silhouette
      const hs = [10, 6, 12, 8, 5, 11, 7];
      hs.forEach((h, i) => px(342 + i * 5, 52 - h, 5, h, '#0B1120'));
      hs.forEach((h, i) => { if ((i + Math.floor(t / 3)) % 3 === 0) px(344 + i * 5, 52 - h + 3, 1, 1, C.yellow); });
      px(358, 14, 2, 38, C.molding); px(342, 32, 34, 2, C.molding);
    }

    // Digital wall clock: the time in large LED digits (the 3x5 font drawn at 2x).
    function drawClock() {
      const d = new Date();
      const hh = String(d.getHours()).padStart(2, '0'), mm = String(d.getMinutes()).padStart(2, '0');
      const LED = '#FFA552', GHOST = '#2A1A10';
      const digit = (ch, x, y) => {
        const g8 = GLYPHS['8'], g = GLYPHS[ch];
        for (let r = 0; r < 5; r++) for (let c = 0; c < 3; c++) {
          if (g[r][c] === '#') px(x + c * 2, y + r * 2, 2, 2, LED);
          else if (g8[r][c] === '#') px(x + c * 2, y + r * 2, 2, 2, GHOST);  // unlit segments
        }
      };
      const y = 11;
      digit(hh[0], 4, y); digit(hh[1], 11, y);
      const colon = reduceMotion || Math.floor(Date.now() / 1000) % 2 === 0 ? LED : '#7A4A28';  // pulses, never vanishes
      px(18, y + 2, 1, 2, colon); px(18, y + 6, 1, 2, colon);
      digit(mm[0], 20, y); digit(mm[1], 27, y);
      px(2, 9, 33, 1, 'rgba(255,255,255,0.06)'); // glass glare
    }

    // Wall calendar beside the door, showing today's date.
    function drawCalendar() {
      const d = new Date();
      const x = 37, y = 32;
      px(x, y, 9, 12, C.paper);
      px(x, y, 9, 3, C.red);
      px(x + 2, y - 1, 1, 2, C.inkSoft); px(x + 6, y - 1, 1, 2, C.inkSoft);
      const day = String(d.getDate()).padStart(2, '0');
      drawText(day, x + 1, y + 5, C.ink);
      px(x, y + 12, 9, 1, 'rgba(0,0,0,0.3)');
    }

    // What a wall screen shows changes at most every MIN_HOLD seconds, so a burst of
    // agent events (read, search, read, think...) does not make it flicker; errors,
    // approvals, finishes and switching off still show at once.
    const MIN_HOLD = 1.6;
    const URGENT = new Set(['error', 'waiting', 'done', 'off']);
    function shownMode(a) {
      const want = screenMode(a);
      const cur = a._screen;
      if (!cur || (want !== cur.mode && (URGENT.has(want) || URGENT.has(cur.mode) || t - cur.since >= MIN_HOLD))) {
        a._screen = { mode: want, since: t, state: a.state };
      }
      return a._screen.mode;
    }
    // Caption words: the long one on a wide screen, the short one (or just the icon) on a narrow one.
    const SCREEN_LABEL = WORDS.screen;

    function screenMode(a) {
      if (!a.available) return 'off';
      if (a.emote && a.emote.kind === 'bang') return 'error';
      if (a.emote && a.emote.kind === 'quest') return 'waiting';
      if (a.state === 'done') return 'done';
      if (a.job === 'review' && ['thinking', 'idle', 'talking'].includes(a.state)) return 'review';
      return {
        reading: 'doc', searching: 'doc', writing: 'code', running: 'term', thinking: 'think', talking: 'chat',
        booting: 'boot', browsing: 'web', planning: 'list', delegating: 'list', working: 'term', error: 'error',
      }[a.state] || (runActive ? 'think' : 'saver');
    }

    // Wall screen frame plus mounts; sw is 78 (one per column) or 38 (split column).
    function drawScreenFrame(sx, sw) {
      px(sx + Math.round(sw * 0.26), 3, 2, 8, C.metalDark); px(sx + sw - Math.round(sw * 0.26) - 2, 3, 2, 8, C.metalDark);
      px(sx, 10, sw, 48, C.screenFrame);
      px(sx, 56, sw, 2, '#1E1E22');
    }

    function drawScreen(a, sx, sw) {
      drawScreenFrame(sx, sw);
      const x = sx + 3, y = 13, w = sw - 6, h = 36;
      const k = w / 72; // horizontal scale of the drawings, made for the wide screen
      const mid = x + Math.floor(w / 2);
      px(x, y, w, h, C.screenOff);
      if (!a) {
        px(sx + 3, 49, w, 7, '#34343A');
        return;
      }
      const mode = shownMode(a);
      const acc = a.pal.accent;
      const seed = 11 + AGENT_NAMES.indexOf(a.name) * 18;
      const since = t - a._screen.since;  // how long this mode has been on, for calm in-place animation
      if (mode === 'off') {
        px(mid - 2, y + 17, 4, 1, '#19191C');
      } else if (mode === 'saver') {
        // Bounce one pixel inside the screen edge: flush against the dark frame the bright
        // face reads as overlapping it, a 1px gap reads as touching.
        const size = 7, gap = 1;
        const bounce = (v, range) => { const m = v % (2 * range); return m < range ? m : 2 * range - m; };
        const [vx, vy, start] = SAVER[a.name];
        const bx = x + gap + Math.round(bounce(t * vx + start, w - size - 2 * gap));
        const by = y + gap + Math.round(bounce(t * vy + start, h - size - 2 * gap));
        px(bx, by, size, size, acc); px(bx + 2, by + 2, 1, 1, C.ink); px(bx + 4, by + 2, 1, 1, C.ink); px(bx + 2, by + 4, 3, 1, C.ink);
      } else if (mode === 'boot') {
        const bw = Math.min(40, w - 8);
        px(mid - bw / 2, y + 17, bw, 3, '#19191C');
        px(mid - bw / 2, y + 17, Math.floor((since * 18) % bw), 3, acc);
      } else if (mode === 'think') {
        // three dots lighting in turn, unhurried, over a slow wave
        for (let i = 0; i < 3; i++) {
          const on = Math.floor(t * 1.5) % 3 === i;
          px(mid - 8 + i * 6, y + 14, 4, 4, on ? acc : '#34343A');
        }
        for (let i = 0; i < w; i += 2) px(x + i, y + 27 + Math.round(Math.sin(i / 7 + t * 1.2) * 2), 1, 1, '#2A2A30');
      } else if (mode === 'chat') {
        // a conversation: one new bubble every 1.6s, older ones move up
        const n = Math.floor(since / 1.6);
        for (let j = 0; j < 4; j++) {
          const idx = n - 3 + j;
          if (idx < 0) continue;
          const r = rng(seed * 97 + idx);
          const right = idx % 2 === 1;
          const lw = Math.max(8, Math.round((16 + Math.floor(r() * 30)) * k));
          px(right ? x + w - 4 - lw : x + 4, y + 3 + j * 8, lw, 5, right ? '#34343A' : acc);
        }
      } else if (mode === 'doc' || mode === 'review') {
        // a page that scrolls slowly; the highlight steps one line at a time
        const pw = w >= 60 ? 48 : w - 6, pl = mid - pw / 2;
        px(pl, y + 2, pw, 34, C.paper);
        const travel = since * 2.5;                     // pixels scrolled
        const first = Math.floor(travel / 4), off = Math.floor(travel % 4);
        for (let j = 0; j < 9; j++) {
          const ly = y + 5 + j * 4 - off;
          if (ly < y + 3 || ly > y + 33) continue;
          const r = rng(seed * 31 + first + j);
          px(pl + 3, ly, Math.round((12 + Math.floor(r() * 28)) * pw / 48), 1, C.paperShade);
          if (mode === 'review' && first + j < Math.floor(since / 0.8)) px(pl + pw - 4, ly - 1, 2, 2, C.green);
        }
        if (mode === 'doc') {
          const hl = Math.floor(since / 0.9) % 7;
          g.globalAlpha = 0.3;
          px(pl + 1, y + 4 + hl * 4, pw - 2, 3, acc);
          g.globalAlpha = 1;
        }
        if (a._screen.state === 'searching') {
          const mx = pl + Math.round(pw * 0.6) + Math.round(Math.sin(t * 0.9) * 6 * k), my = y + 14 + Math.round(Math.cos(t * 0.9) * 5);
          px(mx, my, 6, 1, C.ink); px(mx, my + 5, 6, 1, C.ink); px(mx, my, 1, 6, C.ink); px(mx + 5, my, 1, 6, C.ink); px(mx + 6, my + 6, 2, 2, C.ink);
        }
      } else if (mode === 'code') {
        // an editor: lines are typed one after another and the file scrolls up
        px(x, y, 7, h, '#141417');
        const colors = [acc, C.blue, C.purple, C.paper, C.yellow];
        const pos = since * 1.3, n = Math.floor(pos), frac = pos - n;
        for (let j = 0; j < 8; j++) {
          const idx = n - 7 + j;
          if (idx < 0) continue;
          const ly = y + 3 + j * 4;
          px(x + 2, ly, 3, 1, '#34343A');
          const r = rng(seed * 53 + idx);
          let cx = x + 10 + Math.floor(r() * 3) * 3;
          const parts = 1 + Math.floor(r() * 3);
          const limit = j === 7 ? cx + Math.round(frac * 40 * k) : Infinity;  // the line being typed
          for (let q = 0; q < parts; q++) {
            let pw = Math.min(4 + Math.floor(r() * 12), x + w - 2 - cx, limit - cx);
            const c = colors[Math.floor(r() * colors.length)];
            if (pw < 1) break;
            px(cx, ly, pw, 2, c);
            cx += pw + 2;
          }
          if (j === 7 && Math.floor(t * 1.5) % 2) px(Math.min(cx, x + w - 2), ly - 1, 1, 4, C.paper);
        }
      } else if (mode === 'term') {
        // a terminal: output lines arrive one by one at the bottom and push the rest up
        px(x, y, w, h, '#070709');
        const pos = since * (a._screen.state === 'running' ? 1.8 : 1.2), n = Math.floor(pos), frac = pos - n;
        for (let j = 0; j < 7; j++) {
          const idx = n - 6 + j;
          if (idx < 0) continue;
          const r = rng(seed * 71 + idx);
          const full = Math.max(6, Math.round((10 + Math.floor(r() * 46)) * k));
          const lw = j === 6 ? Math.max(1, Math.round(full * frac)) : full;
          const prompt = idx % 6 === 0;
          if (prompt) px(x + 3, y + 3 + j * 4, 2, 2, acc);
          px(x + (prompt ? 7 : 3), y + 3 + j * 4, Math.min(lw, w - 8), 2, prompt ? C.paper : r() > 0.18 ? '#6FD66F' : C.yellow);
        }
        if (Math.floor(t * 1.5) % 2) px(x + 3, y + 31, 3, 2, C.paper);
      } else if (mode === 'web') {
        const cx = mid, cy = y + 18;
        for (let i = 0; i < 40; i++) {
          const ang = i / 40 * Math.PI * 2;
          px(cx + Math.round(Math.cos(ang) * 12), cy + Math.round(Math.sin(ang) * 12), 1, 1, C.blue);
        }
        const kk = Math.round(Math.sin(t * 1.2) * 6);
        px(cx - 12, cy, 25, 1, C.blue); px(cx + kk, cy - 11, 1, 23, C.blue);
      } else if (mode === 'list') {
        for (let i = 0; i < 5; i++) {
          px(x + 8, y + 5 + i * 6, 3, 3, i < (Math.floor(since / 1.2) % 6) ? C.green : '#34343A');
          px(x + 14, y + 6 + i * 6, Math.min(w - 16, 20 + ((i * 13) % 25)), 1, C.paper);
        }
      } else if (mode === 'error') {
        const pulse = Math.floor(t * 2) % 2;            // a slow alarm pulse, not a strobe
        px(x, y, w, h, pulse ? '#3A1426' : '#2A0F1E');
        for (let i = 0; i < 12; i++) { px(mid - 6 + i, y + 12 + i, 2, 2, C.red); px(mid + 5 - i, y + 12 + i, 2, 2, C.red); }
      } else if (mode === 'waiting') {
        const q = ICONS.quest, col = Math.floor(t) % 2 ? C.yellow : '#8A7430';
        for (let r = 0; r < 5; r++) for (let c = 0; c < 5; c++) if (q[r][c] === '#') px(mid - 10 + c * 4, y + 8 + r * 4, 4, 4, col);
      } else if (mode === 'done') {
        for (let r = 0; r < 5; r++) for (let c = 0; c < 5; c++) if (ICONS.check[r][c] === '#') px(mid - 10 + c * 4, y + 8 + r * 4, 4, 4, C.green);
      }
      // Idle: no caption, only a faint line in the agent's colour under the screensaver.
      if (mode === 'saver') {
        g.globalAlpha = 0.45; px(sx + 3, 54, w, 1, acc); g.globalAlpha = 1;
        px(x + w - 10, y + 1, 1, 6, 'rgba(255,255,255,0.08)');
        return;
      }
      // caption strip: an icon and a word for what the agent is doing
      px(sx + 3, 49, w, 7, a.available ? a.pal.accent : '#34343A');
      px(sx + 3, 49, w, 1, 'rgba(255,255,255,0.25)');
      const capIcon = { code: 'pencil', term: 'term', doc: 'book', review: 'lens', think: 'dots', chat: 'dots', boot: 'dots',
        web: 'globe', list: 'pencil', waiting: 'quest', done: 'check', error: 'cross', saver: 'dots' }[mode];
      const labels = SCREEN_LABEL[mode === 'doc' && a._screen.state === 'searching' ? 'search' : mode] || [];
      const ink = a.name === 'grok' ? C.ink : '#0E0E10';
      const room = w - 4;
      const long = labels[0], short = labels[1];
      if (mode !== 'off') {
        if (long && capIcon && textWidth(long) + 7 <= room) {
          const tw = textWidth(long) + 7, lx = sx + 3 + Math.floor((w - tw) / 2);
          drawIcon(capIcon, lx, 50, ink); drawText(long, lx + 7, 50, ink);
        } else if (long && textWidth(long) <= room) {
          drawText(long, sx + 3 + Math.floor((w - textWidth(long)) / 2), 50, ink);
        } else if (short && capIcon && textWidth(short) + 7 <= room) {
          const tw = textWidth(short) + 7, lx = sx + 3 + Math.floor((w - tw) / 2);
          drawIcon(capIcon, lx, 50, ink); drawText(short, lx + 7, 50, ink);
        } else if (short && textWidth(short) <= room) {
          drawText(short, sx + 3 + Math.floor((w - textWidth(short)) / 2), 50, ink);
        } else if (capIcon) {
          drawIcon(capIcon, sx + Math.floor(sw / 2) - 2, 50, ink);
        }
      }
      // screen glare
      px(x + w - 10, y + 1, 1, 6, 'rgba(255,255,255,0.08)');
    }

    function drawText(str, x, y, color) {
      let cx = x;
      for (const ch of String(str).toLocaleUpperCase(LOCALE)) {
        const mark = MARKS[ch];
        const g = GLYPHS[mark ? mark[0] : ch];
        if (g) for (let r = 0; r < 5; r++) for (let c = 0; c < g[r].length; c++) if (g[r][c] === '#') px(cx + c, y + r, 1, 1, color);
        if (mark && mark[1]) for (let c = 0; c < 3; c++) if (mark[1][c] === '#') px(cx + c, y - 1, 1, 1, color);
        if (mark && mark[2]) for (let c = 0; c < 3; c++) if (mark[2][c] === '#') px(cx + c, y + 5, 1, 1, color);
        cx += glyphWidth(ch) + 1;
      }
      return cx - x - 1;
    }

    function drawIcon(name, x, y, color) {
      const icon = ICONS[name];
      if (!icon) return;
      for (let r = 0; r < 5; r++) for (let c = 0; c < 5; c++) if (icon[r][c] === '#') px(x + c, y + r, 1, 1, color);
    }

    const EMPTY_SEG = '#D6D4CD';
    const SLATE = '#8F8D86';
    const REVIEW = '#7C8BA6'; // slate, so it does not read as Gemini blue
    function segments(task) {
      const own = INK[task.assignee] || SLATE;
      const blink = Math.floor(t * 3) % 2 === 0;
      const live = (c) => (blink ? c : EMPTY_SEG);
      switch (task.status) {
        case 'todo': return [SLATE, EMPTY_SEG, EMPTY_SEG, EMPTY_SEG];
        case 'working': return [SLATE, live(own), EMPTY_SEG, EMPTY_SEG];
        case 'changes_requested': case 'fixing': return [SLATE, live('#E0A800'), REVIEW, EMPTY_SEG];
        case 'awaiting_review': case 'reviewing': return [SLATE, own, live(REVIEW), EMPTY_SEG];
        case 'done': return [SLATE, own, REVIEW, '#3FAE5A'];
        case 'failed': return [SLATE, C.red, EMPTY_SEG, EMPTY_SEG];
        default: return ['#BDBBB4', '#BDBBB4', '#BDBBB4', '#BDBBB4'];
      }
    }
    const ROW_ICON = {
      todo: ['dots', SLATE], working: ['pencil', null], changes_requested: ['pencil', '#C08A00'], fixing: ['pencil', '#C08A00'],
      awaiting_review: ['lens', '#5D6E8C'], reviewing: ['lens', '#5D6E8C'], done: ['check', '#2E9A4A'], failed: ['cross', C.red],
      blocked: ['dash', SLATE], cancelled: ['dash', SLATE],
    };

    // A hand-written plan draft, written in order as `progress` goes 0 -> 1: a heading in
    // blue marker, numbered bullet lines in black, then a small box-and-arrow sketch in red.
    // Strokes are written left to right with a slight hand-writing wobble; the pen marks
    // where it is writing.
    function drawPlanDraft(x, y, progress) {
      const BLUE = '#2F5FBF', INKC = '#2A2A30', RED = '#C0392B';
      let pen = null;
      // one hand-written line: words with gaps, a few taller letters, revealed up to `frac`
      const scrawl = (lx, ly, width, frac, color, sd) => {
        const r = rng(sd);
        const end = Math.floor(width * Math.max(0, Math.min(1, frac)));
        let i = 0, word = 3 + Math.floor(r() * 6);
        while (i < end) {
          if (word === 0) { i += 2; word = 3 + Math.floor(r() * 6); continue; }
          const tall = r() < 0.18, low = r() < 0.08;
          px(lx + i, ly + (r() < 0.25 ? 1 : 0), 1, 1, color);
          if (tall) px(lx + i, ly - 1, 1, 1, color);
          if (low) px(lx + i, ly + 2, 1, 1, color);
          i += 1; word -= 1;
        }
        if (frac > 0 && frac < 1) pen = [lx + end, ly, color];
      };
      const steps = [];
      steps.push({ w: 3, draw: (f) => { scrawl(x + 4, y + 4, 34, f, BLUE, 3); if (f >= 1) px(x + 4, y + 8, 34, 1, BLUE); } });
      const widths = [40, 31, 44, 27, 36];
      widths.forEach((wd, i) => steps.push({ w: 2, draw: (f) => {
        const ly = y + 13 + i * 8;
        if (f > 0) drawText(String(i + 1), x + 4, ly - 2, INKC);           // number
        scrawl(x + 9, ly, wd, f, INKC, 20 + i);
      } }));
      // sketch: three boxes linked by arrows on the right
      const bx = x + 60, bw = 20;
      [0, 1, 2].forEach((i) => steps.push({ w: 1.2, draw: (f) => {
        const by = y + 12 + i * 15, bh = 8;
        const per = 2 * (bw + bh), n = Math.floor(per * Math.min(1, f));
        for (let k = 0; k < n; k++) {
          let qx, qy;
          if (k < bw) { qx = bx + k; qy = by; }
          else if (k < bw + bh) { qx = bx + bw - 1; qy = by + (k - bw); }
          else if (k < 2 * bw + bh) { qx = bx + bw - 1 - (k - bw - bh); qy = by + bh - 1; }
          else { qx = bx; qy = by + bh - 1 - (k - 2 * bw - bh); }
          px(qx, qy, 1, 1, RED);
        }
        if (f > 0 && f < 1) pen = [bx + bw / 2, by, RED];
        if (f >= 1 && i < 2) {                                           // arrow to the next box
          const ax = bx + bw / 2, ay = by + bh;
          px(ax, ay, 1, 6, RED); px(ax - 1, ay + 4, 3, 1, RED); px(ax - 2, ay + 3, 1, 1, RED); px(ax + 2, ay + 3, 1, 1, RED);
        }
      } }));
      const total = steps.reduce((sum, st) => sum + st.w, 0);
      let at = progress * total;
      for (const st of steps) {
        st.draw(Math.max(0, Math.min(1, at / st.w)));
        at -= st.w;
        if (at <= 0) break;
      }
      if (pen && phase === 'planning') {                                // the marker in hand
        const [qx, qy, col] = pen;
        px(qx + 1, qy - 4, 2, 4, '#E8E6E0'); px(qx + 1, qy - 4, 2, 1, col); px(qx + 1, qy, 1, 1, col);
      }
    }

    function drawBoard() {
      const { x, y } = BOARD;
      if (!tasks.length) {
        // Planning: the lead writes an orderly draft on the board; otherwise it waits blank.
        if (phase === 'planning' || boardScribble > 0) {
          drawPlanDraft(x, y, boardScribble);
          return;
        }
        // Idle: faint placeholders for the header and task rows that will appear here.
        const ghost = '#DDDBD4';
        px(x + 3, y + 3, 62, 3, ghost);
        px(x + 3, y + 9, 78, 1, C.boardLine);
        for (let i = 0; i < 4; i++) {
          const ry = y + 12 + i * 7;
          px(x + 3, ry, 2, 5, ghost);
          px(x + 7, ry + 1, 7, 3, ghost);
          for (let sgm = 0; sgm < 4; sgm++) px(x + 17 + sgm * 14, ry + 1, 12, 3, ghost);
        }
        return;
      }
      // header: overall progress and a done/total counter
      const done = tasks.filter((task) => task.status === 'done').length;
      const label = `${done}/${tasks.length}`;
      const lw = textWidth(label);
      drawText(label, x + 81 - lw, y + 2, C.ink);
      const barW = 78 - lw - 4;
      px(x + 3, y + 3, barW, 3, EMPTY_SEG);
      px(x + 3, y + 3, Math.round(barW * done / tasks.length), 3, '#3FAE5A');
      px(x + 3, y + 9, 78, 1, C.boardLine);

      const max = 6;
      const shown = tasks.length > max ? tasks.slice(0, max - 1) : tasks;
      shown.forEach((task, i) => {
        const ry = y + 12 + i * 7;
        const pop = (rows.get(task.id) || { pop: 1 }).pop;
        px(x + 3, ry, 2, 5, (PAL[task.assignee] || {}).accent || SLATE);
        drawText(task.id.replace(/^T/, 'T'), x + 7, ry, C.ink);
        const segs = segments(task);
        const visible = Math.ceil(pop * 4);
        for (let s = 0; s < 4; s++) {
          if (s >= visible) break;
          px(x + 17 + s * 14, ry + 1, 12, 3, segs[s]);
        }
        if (phase === 'plan_review' && i < Math.floor((t - reviewStart) / 2.2)) {
          drawIcon('check', x + 75, ry, '#C0392B');                   // reviewed by the plan checker
          return;
        }
        const [icon, color] = ROW_ICON[task.status] || ['dots', SLATE];
        drawIcon(icon, x + 75, ry, color || INK[task.assignee] || SLATE);
      });
      if (tasks.length > max) drawText(`+${tasks.length - (max - 1)}`, x + 7, y + 12 + (max - 1) * 7, SLATE);
    }

    // Half-height server rack, as far right of the whiteboard as the coffee corner is left
    // of it, and low enough to sit under the wall screens.
    function drawRack() {
      const x = 240;
      px(x, 60, 16, 34, '#111113');
      px(x + 1, 61, 14, 32, '#1C1C20');
      px(x, 60, 16, 1, '#2A2A30');
      const busy = members().some((a) => a.state === 'running');
      for (let i = 0; i < 5; i++) {
        px(x + 2, 63 + i * 6, 12, 4, '#111113');
        const speed = busy ? 9 : 2;
        const on = (Math.floor(t * speed + i * 1.3) % 3) !== 0;
        px(x + 3, 64 + i * 6, 1, 2, on ? C.green : '#2E4A2E');
        px(x + 5, 64 + i * 6, 1, 2, (Math.floor(t * speed * 0.7 + i) % 2) ? C.blue : '#1F2633');
        px(x + 9, 65 + i * 6, 4, 1, '#2A2A30');  // vent slot
      }
      px(x, 94, 16, 1, 'rgba(0,0,0,0.3)');
    }

    // Coffee corner: a cabinet with the machine, as far left of the whiteboard as the rack
    // is right of it. The machine stays below the wall screens' bottom edge.
    function drawCoffee() {
      px(124, 70, 22, 24, C.woodDark);
      px(125, 71, 20, 22, C.wood);
      px(125, 80, 20, 1, C.woodDark);
      px(122, 68, 26, 3, C.deskEdge);
      px(129, 59, 13, 9, '#2B2B30');
      px(130, 60, 11, 3, '#45454C');
      const brewing = machine.by && t < machine.until;
      // Demleme boyunca ışık sürekli yanar; boşta yavaş kırmızı yanıp söner.
      // Fincan yalnız makinenin kendi bayrağına bağlıdır; eldeki kupa başka ajana aittir.
      px(139, 61, 1, 1, brewing ? C.green : (reduceMotion || Math.floor(t * 2) % 2 ? C.red : '#7A2C2C'));
      px(134, 63, 2, 1, '#1A1A1E');                  // nozzle
      if (!machine.cupTaken) {
        px(133, 64, 4, 4, C.paper);                  // cup on the drip tray
        px(134, 64, 2, 1, '#E4D7BC');                // rim; kahve bunun altında birikir
        if (brewing) {
          const span = Math.max(0.001, machine.until - machine.start);
          const fill = Math.max(0, Math.min(3, Math.floor(((t - machine.start) / span) * 4)));
          if (fill > 0) px(134, 68 - fill, 2, fill, '#5A3A24');
          const drop = reduceMotion ? 0 : Math.floor(t * 8) % 2;
          px(134, 63, 1, 2, '#5A3A24');              // akış, ağızlıktan fincanın içine
          px(135, 63 + drop, 1, 1, '#5A3A24');       // damla
        }
      }
      px(142, 64, 3, 4, C.paper);
      px(143, 65, 3, 2, C.paper);
    }

    function drawPlant() {
      px(38, 82, 12, 12, C.pot);
      px(38, 82, 12, 2, '#CE7A52');
      const sway = reduceMotion ? 0 : Math.round(Math.sin(t * 0.8));
      px(43 + sway, 62, 2, 20, C.plantDark);
      px(36 + sway, 64, 7, 4, C.plant); px(45 + sway, 60, 8, 4, C.plant);
      px(37, 72, 7, 4, C.plant); px(45, 70, 8, 4, C.plant);
      px(40 + sway, 58, 5, 4, C.plant);
    }

    function drawChair(d) {
      const y = d.top - 12;
      px(d.cx - 8, y, 16, 16, '#1F1F23');
      px(d.cx - 7, y + 1, 14, 14, '#2A2A2F');
      px(d.cx - 8, y + 16, 16, 3, '#19191C');
    }

    // Yudumlama, ancak gerinme ve çalışma onu kesmiyorsa çizilir. Aksi halde kupa masada kalır.
    function showingSip(a) {
      const walking = a.path.length > 0;
      const sitting = a.seated && !walking;
      if (!(sitting && t < a.sipUntil)) return false;
      if (phase === 'done' && a.cheerUntil > t && a.arrived && a.place === 'hub') return false;
      const atMachine = !!(a.coffee && a.place === 'coffee' && a.arrived && !walking);
      const back = (a.place === 'board' && a.arrived && !walking && !waiting) || atMachine;
      if (back && (a.job === 'plan' || a.job === 'plan_review')) return false;
      if (!a.job && t < a.stretchUntil) return false;
      if (['writing', 'running', 'working', 'delegating', 'planning'].includes(a.state)) return false;
      return true;
    }

    // Everything on a desk is laid out from its surface row (top), so the front row reuses it.
    function drawDesk(a) {
      const d = desk(a);
      const cx = d.cx, top = d.top, dy = top - 122;
      const left = cx - 44;
      px(left, top, 88, 6, C.deskTop);
      px(left, top, 88, 1, C.deskEdge);
      px(left + 2, top + 6, 84, 18, C.deskFront);
      px(left + 2, top + 22, 84, 2, C.deskDark);
      px(left + 2, top + 6, 84, 1, C.deskDark);
      for (let i = 12; i < 84; i += 22) px(left + 2 + i, top + 8, 1, 13, C.deskDark);
      px(left + 4, top + 24, 80, 2, 'rgba(0,0,0,0.35)');
      // monitor (seen from behind) on the outer side of the desk
      const mx = d.side < 0 ? cx - 36 : cx + 20;
      const active = a.available && runActive && a.state !== 'idle';
      px(mx, 104 + dy, 16, 17, '#1E1E22');
      px(mx + 1, 105 + dy, 14, 15, '#2A2A2F');
      px(mx + 7, 121 + dy, 2, 2, C.metalDark);
      px(mx + 4, 122 + dy, 8, 1, C.metalDark);
      if (active) {
        g.globalAlpha = 0.28;
        px(d.side < 0 ? mx + 16 : mx - 1, 104 + dy, 1, 17, a.pal.accent);
        px(mx - 2, 123 + dy, 20, 2, a.pal.accent);
        g.globalAlpha = 1;
      }
      // keyboard
      px(cx - 9, 123 + dy, 18, 3, '#2B2B30');
      for (let i = 0; i < 8; i++) px(cx - 8 + i * 2, 124 + dy, 1, 1, '#7A7A80');
      // mug + papers
      const mugX = d.side < 0 ? cx + 28 : cx - 32;
      if (!a.coffee && !a.holding && !showingSip(a)) { // Yolculukta ve yudumlarken masada ikinci kupa yok.
        px(mugX, 117 + dy, 5, 6, a.pal.accent);
        px(mugX + 5, 118 + dy, 2, 3, a.pal.accent);
        const level = Math.max(0, Math.min(3, a.cup));
        px(mugX + 1, 118 + dy, 3, 3, '#1A1410');
        if (level > 0) px(mugX + 1, 118 + dy + (3 - level), 3, level, '#5A3A24');
      }
      const papX = d.side < 0 ? cx + 14 : cx - 24;
      px(papX, 120 + dy, 10, 2, C.paper); px(papX + 1, 119 + dy, 9, 1, C.paperShade);
      drawKeepsake(a, d.side < 0 ? cx + 41 : cx - 41, top, active);
      // name plate: a dark plaque with the agent's colour; the name itself is drawn sharp in the overlay
      px(cx - 17, 132 + dy, 34, 9, C.deskDark);
      px(cx - 16, 133 + dy, 32, 7, '#17120E');
      px(cx - 16, 133 + dy, 32, 1, 'rgba(255,255,255,0.06)');
      px(cx - 14, 135 + dy, 3, 3, a.available ? a.pal.accent : '#6E6E72');
      a._plate = { x: cx - 9, y: 136.5 + dy };
      // battery: how much of the subscription limit is left
      const q = quota[a.name];
      const bx = cx + 19, by = 134 + dy;
      px(bx, by, 12, 6, C.deskDark);
      px(bx + 12, by + 2, 1, 2, C.deskDark);
      px(bx + 1, by + 1, 10, 4, '#2A1E17');
      if (q === null) {
        if (Math.floor(t * 1.5) % 2) px(bx + 5, by + 2, 2, 2, C.paperShade);
      } else {
        const segs = Math.ceil(q / 20);
        const col = q > 50 ? C.green : q > 20 ? C.yellow : C.red;
        const blink = q <= 10 && Math.floor(t * 3) % 2;
        for (let i = 0; i < segs && !blink; i++) px(bx + 1 + i * 2, by + 1, 1, 4, col);
      }
    }

    // Rug under the review table; larger in the four-desk room.
    function drawRug() {
      const { x, y, w, h } = L.rug;
      px(x, y, w, h, C.rugEdge);
      px(x + 2, y + 2, w - 4, h - 4, C.rugA);
      for (let i = 0; i < w - 4; i += 8) px(x + 2 + i, y + 2, 4, h - 4, C.rugB);
      px(x + 6, y + 6, w - 12, 1, C.rugEdge); px(x + 6, y + h - 7, w - 12, 1, C.rugEdge);
    }

    function drawTable() {
      const { x, w } = L.table;
      px(x, 166, w, 8, C.deskTop);
      px(x, 166, w, 1, C.deskEdge);
      px(x + 2, 174, w - 4, 3, C.deskFront);
      px(x + 4, 177, 3, 7, C.deskDark); px(x + w - 7, 177, 3, 7, C.deskDark);
      px(x + 2, 184, w - 4, 2, 'rgba(0,0,0,0.3)');
      if (members().some((a) => a.job === 'review')) {
        const mid = x + Math.floor(w / 2);
        px(mid - 6, 167, 12, 5, C.paper);
        px(mid - 4, 168, 7, 1, C.paperShade); px(mid - 4, 170, 5, 1, C.paperShade);
      }
    }

    // Each agent keeps something of its own at the inner end of its desk.
    function drawKeepsake(a, x, top, active) {
      if (a.name === 'claude') {          // a small succulent
        px(x - 2, top - 3, 5, 3, C.pot); px(x - 2, top - 3, 5, 1, '#CE7A52');
        px(x - 2, top - 6, 1, 3, C.plant); px(x, top - 7, 1, 4, C.plantDark); px(x + 2, top - 5, 1, 2, C.plant); px(x - 1, top - 5, 1, 1, C.plant);
      } else if (a.name === 'codex') {    // the rubber duck it debugs with
        px(x - 2, top - 3, 5, 3, C.yellow); px(x - 2, top - 1, 5, 1, '#D9A93A');
        px(x, top - 5, 3, 2, C.yellow); px(x + 3, top - 4, 1, 1, '#E8742A'); px(x + 1, top - 5, 1, 1, C.ink);
      } else if (a.name === 'gemini') {   // a little star lamp, lit while it works
        px(x - 2, top - 1, 5, 1, C.metalDark); px(x, top - 3, 1, 2, C.metalDark);
        const c = active ? '#9FC0FF' : C.gemini;
        px(x, top - 8, 1, 5, c); px(x - 2, top - 6, 5, 1, c); px(x, top - 6, 1, 1, active ? C.paper : '#C8D8FF');
        if (active && Math.floor(t * 3) % 2) {  // a soft twinkle around the star
          g.globalAlpha = 0.55;
          px(x - 2, top - 8, 1, 1, c); px(x + 2, top - 8, 1, 1, c); px(x - 2, top - 4, 1, 1, c); px(x + 2, top - 4, 1, 1, c);
          g.globalAlpha = 1;
        }
      } else if (a.name === 'grok') {     // a toy rocket; the flame burns while it works
        px(x - 1, top - 8, 2, 6, C.grok); px(x - 1, top - 9, 2, 1, C.red); px(x - 1, top - 6, 2, 1, '#3A6EA5');
        px(x - 2, top - 3, 1, 2, C.metalDark); px(x + 1, top - 3, 1, 2, C.metalDark);
        if (active && Math.floor(t * 8) % 2) px(x - 1, top - 2, 2, 2, '#FFB347');
      }
    }

    // A small waste bin on the floor at the outer end of a desk.
    function drawBin(x, y) {
      px(x - 1, y + 7, 8, 1, 'rgba(0,0,0,0.35)');
      px(x, y, 6, 7, '#3A3A40'); px(x, y, 6, 1, '#55555C'); px(x + 1, y + 1, 4, 1, '#1E1E22');
      px(x + 2, y - 1, 2, 1, C.paper);
    }

    // A grey cat asleep on the rug; it breathes and now and then flicks its tail.
    // catStretchUntil dört saniyelik bir penceredir: uyanma, gerinme, yeniden kıvrılma.
    function drawCat(x, y) {
      const fur = '#5A5A62', stripe = '#4A4A52';
      if (t < catStretchUntil) {
        const elapsed = Math.max(0, 4 - (catStretchUntil - t));
        px(x - 1, y + 1, 15, 1, 'rgba(0,0,0,0.3)');
        if (elapsed < 0.8) {                         // baş kalkar, gözler açılır
          const up = elapsed >= 0.35;
          px(x + 2, y - 4, 9, 5, fur);
          px(x + 4, y - 4, 2, 4, stripe); px(x + 7, y - 4, 1, 4, stripe);
          const hy = up ? y - 6 : y - 5;
          px(x, hy + 1, 4, 4, fur);
          px(x, hy, 1, 1, fur); px(x + 3, hy, 1, 1, fur);
          px(x + 1, hy + 2, 1, 1, up ? C.green : C.ink);
          px(x + 2, hy + 2, 1, 1, up ? C.green : C.ink);
          px(x + 10, y - 1, 3, 1, stripe); px(x + 12, y - 2, 1, 1, stripe);
          return;
        }
        if (elapsed < 2.8) {                         // ön patiler ileri, kuyruk yukarı
          px(x + 3, y - 4, 9, 3, fur); px(x + 5, y - 4, 2, 3, stripe); px(x + 8, y - 4, 1, 3, stripe);
          px(x + 3, y - 1, 1, 2, fur); px(x + 10, y - 1, 1, 2, fur);
          px(x - 1, y - 1, 4, 1, fur); px(x - 1, y, 2, 1, fur);
          px(x - 1, y - 6, 4, 4, fur); px(x - 1, y - 7, 1, 1, fur); px(x + 2, y - 7, 1, 1, fur);
          px(x, y - 5, 1, 1, C.green); px(x + 2, y - 5, 1, 1, C.green);
          px(x + 12, y - 8, 1, 4, stripe); px(x + 11, y - 8, 1, 1, stripe);
          return;
        }
        if (elapsed < 3.4) {                         // patiler çekilir, gövde alçalır
          px(x + 2, y - 4, 9, 4, fur);
          px(x + 4, y - 4, 2, 3, stripe); px(x + 7, y - 4, 1, 3, stripe);
          px(x + 1, y - 1, 2, 2, fur); px(x + 9, y - 1, 2, 2, fur);
          px(x, y - 5, 4, 4, fur);
          px(x, y - 6, 1, 1, fur); px(x + 3, y - 6, 1, 1, fur);
          px(x + 1, y - 4, 1, 1, C.green); px(x + 2, y - 4, 1, 1, C.green);
          px(x + 11, y - 3, 1, 3, stripe);
          return;
        }
        px(x + 2, y - 4, 9, 5, fur);                 // yeniden kıvrılıp gözleri kapanır
        px(x + 4, y - 4, 2, 4, stripe); px(x + 7, y - 4, 1, 4, stripe);
        px(x, y - 3, 4, 4, fur);
        px(x, y - 4, 1, 1, fur); px(x + 3, y - 4, 1, 1, fur);
        px(x + 1, y - 2, 1, 1, C.ink); px(x + 2, y - 2, 1, 1, C.ink);
        px(x + 10, y, 3, 1, stripe); px(x + 12, y - 1, 1, 1, stripe);
        return;
      }
      const breath = !reduceMotion && Math.floor(t / 1.6) % 2 ? 1 : 0;
      const flick = !reduceMotion && Math.floor(t * 2) % 23 === 0 ? 1 : 0;
      px(x - 1, y + 1, 14, 1, 'rgba(0,0,0,0.3)');
      px(x + 2, y - 4 + breath, 9, 5 - breath, '#5A5A62');   // body
      px(x + 4, y - 4 + breath, 2, 4 - breath, '#4A4A52'); px(x + 7, y - 4 + breath, 1, 4 - breath, '#4A4A52'); // stripes
      px(x, y - 3, 4, 4, '#5A5A62');                          // head tucked in
      px(x, y - 4, 1, 1, '#5A5A62'); px(x + 3, y - 4, 1, 1, '#5A5A62'); // ears
      px(x + 1, y - 2, 1, 1, C.ink); px(x + 2, y - 2, 1, 1, C.ink); // closed eyes
      px(x + 10, y - flick, 3, 1, '#4A4A52'); px(x + 12, y - 1 - flick, 1, 1, '#4A4A52'); // tail
    }

    // A potted plant standing on the floor; baseY is where the pot meets the floor.
    function drawFloorPlant(x, baseY) {
      const sway = reduceMotion ? 0 : Math.round(Math.sin(t * 0.7 + x) * 1);
      px(x - 7, baseY + 1, 14, 2, 'rgba(0,0,0,0.35)');
      px(x - 6, baseY - 11, 12, 12, C.pot);
      px(x - 6, baseY - 11, 12, 2, '#CE7A52');
      px(x - 1 + sway, baseY - 32, 2, 21, C.plantDark);
      px(x - 9 + sway, baseY - 30, 8, 4, C.plant); px(x + 1 + sway, baseY - 34, 9, 4, C.plant);
      px(x - 8, baseY - 22, 7, 4, C.plant); px(x + 1, baseY - 24, 8, 4, C.plant);
      px(x - 3 + sway, baseY - 37, 6, 4, C.plant); px(x - 7, baseY - 16, 6, 3, C.plantDark); px(x + 2, baseY - 17, 6, 3, C.plantDark);
    }

    function drawCharacter(a) {
      const p = a.pal;
      const walking = a.path.length > 0;
      const sitting = a.seated && !walking;
      const atMachine = !!(a.coffee && a.place === 'coffee' && a.arrived && !walking);
      const back = (a.place === 'board' && a.arrived && !walking && !waiting) || atMachine;
      const cheering = phase === 'done' && a.cheerUntil > t && a.arrived && a.place === 'hub';
      const frame = Math.floor(t * 8) % 2;
      let jump = 0;
      if (cheering && !reduceMotion) jump = Math.max(0, Math.round(Math.sin(t * 10) * 3));
      const posingStill = sitting && (t < a.stretchUntil || t < a.sipUntil || t < a.yawnUntil);
      const bob = !reduceMotion && sitting && !posingStill && (a.state === 'idle' || !runActive) ? (Math.floor(t * 1.5) % 2) : 0;
      const top = (sitting ? desk(a).top - 18 : Math.round(a.y) - 22) - jump + bob;
      const x0 = Math.round(a.x) - 6;
      const dim = !a.available;
      const S = (c) => (dim ? '#55555A' : c);

      // shadow
      if (!sitting) px(x0 + 2, Math.round(a.y) - 1, 8, 2, 'rgba(0,0,0,0.35)');

      // legs
      if (sitting) {
        px(x0 + 2, top + 16, 8, 2, S(C.pants));
      } else {
        const lOff = walking && frame ? 1 : 0, rOff = walking && !frame ? 1 : 0;
        px(x0 + 3, top + 16, 3, 5 - lOff, S(C.pants));
        px(x0 + 6, top + 16, 3, 5 - rOff, S(C.pants));
        px(x0 + 3, top + 21 - lOff, 3, 1, C.shoes);
        px(x0 + 6, top + 21 - rOff, 3, 1, C.shoes);
      }

      // torso
      px(x0 + 2, top + 8, 8, 8, S(p.shirt));
      px(x0 + 2, top + 15, 8, 1, S(p.shirtDark));
      if (!back) {
        if (a.name === 'claude') { px(x0 + 4, top + 8, 4, 2, S(p.collar)); px(x0 + 5, top + 11, 2, 2, S(C.paper)); }
        else if (a.name === 'codex') { px(x0 + 3, top + 8, 6, 1, S(p.shirtDark)); px(x0 + 5, top + 9, 1, 3, S(p.collar)); px(x0 + 7, top + 9, 1, 3, S(p.collar)); }
        else if (a.name === 'gemini') { px(x0 + 4, top + 8, 4, 1, S(p.collar)); px(x0 + 5, top + 11, 2, 1, S(p.collar)); px(x0 + 5, top + 13, 2, 1, S(p.collar)); }
        else { px(x0 + 4, top + 8, 4, 1, S(C.grokShirtDark)); px(x0 + 4, top + 9, 1, 3, S(p.collar)); px(x0 + 7, top + 9, 1, 3, S(p.collar)); px(x0 + 4, top + 13, 4, 1, S(C.grokShirtDark)); }
      } else if (a.name === 'codex' || a.name === 'grok') {
        px(x0 + 3, top + 8, 6, 3, S(p.shirtDark)); // hood
      }

      // arms
      const arm = (ax, ay, h) => px(ax, ay, 1, h, S(p.shirt));
      const hand = (hx, hy) => px(hx, hy, 1, 1, S(p.skin));
      let pose = 'down';
      const typing = ['writing', 'running', 'working', 'delegating', 'planning'].includes(a.state);
      if (cheering) pose = 'cheer';
      else if (back && (a.job === 'plan' || a.job === 'plan_review')) pose = 'up';
      else if (sitting && !a.job && t < a.stretchUntil) pose = 'stretch';
      else if (sitting && typing) pose = 'type';
      else if (sitting && t < a.sipUntil) pose = 'sip';
      else if (walking && a.holding) pose = 'carry';
      else if (sitting && t < a.yawnUntil) pose = 'yawn';
      else if (sitting && a.state === 'thinking') pose = 'chin';
      else if (a.place === 'table' && a.arrived && a.job === 'review') pose = 'paper';
      else if (waiting && a.arrived) pose = 'down';
      if (pose === 'down' || pose === 'yawn') {
        const sw = pose === 'down' && walking ? (frame ? 1 : -1) : 0;
        arm(x0 + 1, top + 9 + (sw > 0 ? 1 : 0), 6); hand(x0 + 1, top + 15 + (sw > 0 ? 1 : 0));
        arm(x0 + 10, top + 9 + (sw < 0 ? 1 : 0), 6); hand(x0 + 10, top + 15 + (sw < 0 ? 1 : 0));
      } else if (pose === 'type') {
        const tf = reduceMotion ? 0 : Math.floor(t * (a.state === 'running' ? 4 : 10)) % 2;
        arm(x0 + 1, top + 9, 5); arm(x0 + 10, top + 9, 5);
        px(x0 + 2, top + 14, 2, 1, S(p.shirt)); px(x0 + 8, top + 14, 2, 1, S(p.shirt));
        hand(x0 + 3, top + 15 + tf); hand(x0 + 8, top + 16 - tf);
      } else if (pose === 'chin') {
        arm(x0 + 1, top + 9, 6); hand(x0 + 1, top + 15);
        arm(x0 + 10, top + 10, 4); px(x0 + 9, top + 8, 1, 2, S(p.shirt)); hand(x0 + 8, top + 7);
      } else if (pose === 'up') {
        const wave = reduceMotion ? 0 : Math.floor(t * 4) % 2;
        arm(x0 + 1, top + 9, 6); hand(x0 + 1, top + 15);
        arm(x0 + 10, top + 1 + wave, 8); hand(x0 + 10, top + wave);
      } else if (pose === 'cheer') {
        arm(x0 + 1, top + 1, 8); hand(x0 + 1, top);
        arm(x0 + 10, top + 1, 8); hand(x0 + 10, top);
      } else if (pose === 'stretch') {  // arms rise beside the head; hands are drawn above hats
        arm(x0 + 1, top + 2, 7); arm(x0 + 10, top + 2, 7);
        px(x0 + 2, top + 1, 1, 1, S(p.shirt)); px(x0 + 9, top + 1, 1, 1, S(p.shirt));
      } else if (pose === 'sip') {      // near arm stays down; the mug arm is finished after the hair
        arm(x0 + 1, top + 9, 6); hand(x0 + 1, top + 15);
        arm(x0 + 10, top + 6, 5);
        px(x0 + 9, top + 5, 1, 2, S(p.shirt));
      } else if (pose === 'carry') {    // a cup held out in front while walking
        const sw = reduceMotion ? 0 : (frame ? 1 : 0);
        arm(x0 + 1, top + 9 + sw, 6); hand(x0 + 1, top + 15 + sw);
        arm(x0 + 10, top + 9, 4); hand(x0 + 10, top + 13);
        px(x0 + 10, top + 11, 3, 3, C.paper); px(x0 + 10, top + 11, 3, 1, '#5A3A24');
        if (!reduceMotion && Math.floor(t * 3) % 2) px(x0 + 11, top + 9, 1, 1, 'rgba(255,255,255,0.45)');
      } else if (pose === 'paper') {
        arm(x0 + 1, top + 9, 4); arm(x0 + 10, top + 9, 4);
        px(x0 + 1, top + 10, 10, 7, C.paper);
        px(x0 + 3, top + 12, 6, 1, C.paperShade); px(x0 + 3, top + 14, 4, 1, C.paperShade);
        hand(x0 + 1, top + 13); hand(x0 + 10, top + 13);
      }

      // head. Bakınma yalnız boştaki öne bakan pozda başı bir piksel çevirir.
      const workGaze = sitting && ['reading', 'searching', 'writing', 'running'].includes(a.state);
      const glancing = pose === 'down' && sitting && !workGaze && t < a.lookUntil && a.lookDir;
      const hx = x0 + 2 + (glancing ? a.lookDir : 0), hy = top;
      px(hx + 1, hy, 6, 1, S(p.hair));
      px(hx, hy + 1, 8, 2, S(p.hair));
      if (back) {
        px(hx, hy + 3, 8, 5, S(p.hair));
        px(hx + 1, hy + 7, 6, 1, S(p.hair));
      } else {
        px(hx + 1, hy + 3, 6, 4, S(p.skin));
        px(hx, hy + 3, 1, 3, S(p.hair)); px(hx + 7, hy + 3, 1, 3, S(p.hair));
        px(hx + 2, hy + 7, 4, 1, S(p.skin));
        const look = a.facing || (workGaze ? desk(a).side : (glancing ? a.lookDir : 0));
        const yawning = pose === 'yawn';
        const blink = yawning || (!reduceMotion && Math.floor((t + AGENT_NAMES.indexOf(a.name) * 1.3) * 10) % 37 === 0);
        const eyeY = hy + 4;
        const shift = look < 0 ? -1 : look > 0 ? 1 : 0;
        if (!blink) {
          px(hx + 2 + shift, eyeY, 1, 1, C.ink);
          px(hx + 5 + shift, eyeY, 1, 1, C.ink);
        } else {
          px(hx + 2, eyeY, 1, 1, S(p.skinDark)); px(hx + 5, eyeY, 1, 1, S(p.skinDark));
        }
        if (yawning) {
          px(hx + 3, hy + 5, 2, 3, '#5A2A2A');
        } else {
          const talking = a.state === 'talking' || (a.msg && a.msg.until > t);
          if (talking && !reduceMotion && Math.floor(t * 8) % 2) px(hx + 3, hy + 6, 2, 1, '#7A3B3B');
          else px(hx + 3, hy + 6, 2, 1, S(p.skinDark));
          if (cheering || a.state === 'done') px(hx + 3, hy + 6, 2, 1, '#7A3B3B');
        }
      }
      // accessories
      if (a.name === 'claude') {
        px(hx + 8, hy + 1, 2, 3, S(p.hair)); // side bun
        px(hx + 6, hy + 1, 1, 1, S(C.claude)); // hair clip
      } else if (a.name === 'codex') {
        px(hx - 1, hy - 1, 10, 1, S(C.codex)); // headphone band
        px(hx - 1, hy + 3, 2, 3, S('#1E1E22')); px(hx + 7, hy + 3, 2, 3, S('#1E1E22'));
        px(hx - 1, hy + 4, 1, 1, S(C.codex)); px(hx + 8, hy + 4, 1, 1, S(C.codex));
      } else if (a.name === 'gemini') {
        px(hx - 1, hy + 2, 1, 7, S(p.hair)); px(hx + 8, hy + 2, 1, 7, S(p.hair)); // long hair
        if (!back) { px(hx, hy + 6, 1, 3, S(p.hair)); px(hx + 7, hy + 6, 1, 3, S(p.hair)); }
        // four-point sparkle pin
        px(hx + 6, hy, 1, 3, S(C.gemini)); px(hx + 5, hy + 1, 3, 1, S(C.gemini)); px(hx + 6, hy + 1, 1, 1, S(C.paper));
      } else {
        px(hx, hy - 1, 8, 2, S('#141417')); px(hx - 1, hy + 1, 10, 1, S('#141417')); // beanie
        px(hx + 1, hy - 1, 6, 1, S('#26262B'));
        px(hx + 3, hy, 2, 1, S(C.grok)); // small mark on the beanie
      }
      if (pose === 'stretch') {          // eller şapka ve saçın üstünde birleşir
        px(x0 + 2, top - 1, 1, 2, S(p.shirt)); px(x0 + 9, top - 1, 1, 2, S(p.shirt));
        hand(x0 + 3, top - 2); hand(x0 + 8, top - 2);
      } else if (pose === 'sip') {       // kupa ve el ağza değer; seviye 0–3 ayırt edilir
        const level = Math.max(0, Math.min(3, a.cup));
        const mx = hx + 4, my = hy + 4;
        arm(x0 + 10, top + 6, 5);
        px(x0 + 9, top + 5, 1, 2, S(p.shirt));
        px(mx, my, 3, 3, p.accent);
        px(mx + 3, my + 1, 1, 2, p.accent);
        px(mx + 1, my + 1, 1, 2, '#1A1410');
        const rows = level >= 2 ? 2 : level;
        if (rows > 0) px(mx + 1, my + 1 + (2 - rows), 1, rows, '#5A3A24');
        if (level >= 3) px(mx + 1, my, 1, 1, '#E4C98A');
        hand(mx + 2, my + 2); hand(mx + 3, my + 2);
      }
      if (dim) {
        const z = Math.floor(t * 1.2) % 3;
        px(x0 + 12 + z, top - 3 - z * 2, 3, 1, C.paper); px(x0 + 14 + z, top - 2 - z * 2, 1, 1, C.paper); px(x0 + 12 + z, top - 1 - z * 2, 3, 1, C.paper);
      }
      a._top = top;
    }

    function drawParticles() {
      for (const p of particles) {
        const alpha = Math.max(0, Math.min(1, p.life / p.max));
        if (p.kind === 'steam') {
          const sx = Math.round(p.x), sy = Math.round(p.y);
          g.globalAlpha = alpha * 0.7; px(sx, sy, 1, 1, '#F7F4EE');
          g.globalAlpha = alpha * 0.35; px(sx + (alpha > 0.5 ? 1 : -1), sy + 1, 1, 1, C.paper);
          g.globalAlpha = 1;
        } else if (p.kind === 'spark') {
          g.globalAlpha = alpha; px(p.x, p.y, 1, 1, p.color); px(p.x - 1, p.y, 3, 1, p.color); px(p.x, p.y - 1, 1, 3, p.color); g.globalAlpha = 1;
        } else if (p.kind === 'confetti') {
          const w = Math.sin(t * 8 + p.spin) > 0 ? 2 : 1;
          px(p.x, p.y, w, 2, p.color);
        } else if (p.kind === 'letter') {
          px(p.x - 4, p.y - 3, 9, 6, C.paper); px(p.x - 4, p.y - 3, 9, 1, C.paperShade);
          px(p.x - 3, p.y - 2, 1, 1, C.red); px(p.x - 1, p.y - 1, 3, 1, C.paperShade); px(p.x, p.y - 2, 1, 1, C.red);
        } else if (p.kind === 'paper') {
          px(p.x - 3, p.y - 2, 7, 5, C.paper); px(p.x - 2, p.y - 1, 5, 1, C.paperShade); px(p.x - 2, p.y + 1, 3, 1, C.paperShade);
        }
      }
    }

    function drawEmote(a) {
      if (!a.emote || a._top === undefined) return;
      const icon = ICONS[a.emote.kind];
      if (!icon) return;
      const color = { check: C.green, cross: C.red, bang: C.red, quest: C.yellow }[a.emote.kind] || C.paper;
      const bx = Math.round(a.x) - 4, by = a._top - 11 - (reduceMotion ? 0 : Math.floor(t * 3) % 2);
      px(bx, by, 9, 9, C.ink);
      px(bx + 1, by + 1, 7, 7, C.paper);
      for (let r = 0; r < 5; r++) for (let c = 0; c < 5; c++) if (icon[r][c] === '#') px(bx + 2 + c, by + 2 + r, 1, 1, color === C.paper ? C.ink : color);
    }

    function renderScene() {
      g.drawImage(bg, 0, 0);
      drawRug();
      drawWindow();
      drawClock();
      drawCalendar();
      for (const [a, sx, sw] of screens) drawScreen(a, sx, sw);
      drawBoard();
      const items = [
        { y: 94, draw: drawCoffee }, { y: 94, draw: drawPlant }, { y: 94, draw: drawRack },
        { y: 176, draw: drawTable },
        ...L.plants.map(([x, y]) => ({ y, draw: () => drawFloorPlant(x, y) })),
        { y: L.cat[1], draw: () => drawCat(L.cat[0], L.cat[1]) },
      ];
      for (const a of members()) {
        const d = desk(a);
        items.push({ y: d.top + 13.5, draw: () => drawChair(d) }, { y: d.top + 24, draw: () => drawDesk(a) });
        const bx = d.side < 0 ? d.cx - 53 : d.cx + 47;
        items.push({ y: d.top + 22, draw: () => drawBin(bx, d.top + 14) });
        items.push({ y: a.seated && !a.path.length ? d.top + 14 : a.y + 0.1, draw: () => drawCharacter(a) });
      }
      items.sort((p, q) => p.y - q.y);
      for (const it of items) it.draw();
      drawParticles();
      for (const a of members()) drawEmote(a);
      // soft vignette
      g.fillStyle = 'rgba(0,0,0,0.18)';
      g.fillRect(0, 0, W, 2); g.fillRect(0, H - 2, W, 2);
    }

    // ---------------------------------------------------- overlay (hi-res)
    function S(x) { return x * scale; }
    // Text is drawn at whole device pixels so it stays sharp at any zoom.
    function font(sizeLogical, weight, family) {
      const px = Math.max(Math.round(10 * dpr), Math.round(sizeLogical * scale));
      return `${weight || 500} ${px}px ${family || FONT}`;
    }
    const R = Math.round;

    function bubbleText(a) {
      if (!a.available) return null;
      if (a.msg && a.msg.until > t) return { icon: 'dots', text: a.msg.text };
      if (!runActive || a.state === 'idle' || a.state === 'done') return null;
      const icon = STATE_ICON[a.state] || 'dots';
      if (a.state === 'thinking' || a.state === 'booting') return { icon, text: LABELS.state[a.state] };
      const target = a.target ? a.target.split('/').slice(-2).join('/') : LABELS.state[a.state] || a.state;
      return { icon, text: target };
    }

    function drawBubble(a) {
      const b = bubbleText(a);
      if (!b || a._top === undefined) return;
      ctx.font = font(4.6, 600, FONT_UI);
      const maxChars = scale >= 3 ? 36 : 26;
      let text = b.text.length > maxChars ? b.text.slice(0, maxChars - 1) + '…' : b.text;
      const tw = ctx.measureText(text).width;
      const pad = S(2.5), iconW = S(7);
      const w = Math.ceil(tw + pad * 2 + iconW), h = Math.ceil(S(9));
      const [cx, cyTop] = [ox + S(a.x), oy + S(a._top - (a.emote ? 13 : 3))];
      let bx = Math.round(cx - w / 2), by = Math.round(cyTop - h - S(3));
      bx = Math.max(ox + S(2), Math.min(bx, ox + S(W - 2) - w));
      by = Math.max(oy + S(1), by);
      const u = Math.max(1, Math.round(scale / 2));
      ctx.fillStyle = a.pal.accent;
      ctx.fillRect(bx + u, by, w - 2 * u, h); ctx.fillRect(bx, by + u, w, h - 2 * u);
      ctx.fillStyle = C.paper;
      ctx.fillRect(bx + u, by + u, w - 2 * u, h - 2 * u);
      // tail
      const tx = Math.round(Math.max(bx + S(3), Math.min(cx, bx + w - S(4))));
      ctx.fillStyle = a.pal.accent;
      ctx.fillRect(tx - u, by + h - u, 3 * u, u); ctx.fillRect(tx, by + h, u * 2, u * 2);
      // icon
      const icon = ICONS[b.icon] || ICONS.dots;
      const ip = Math.max(1, Math.floor(scale * 0.9));
      const iy = by + Math.round((h - ip * 5) / 2), ix = bx + pad;
      ctx.fillStyle = INK[a.name];
      for (let r = 0; r < 5; r++) for (let c = 0; c < 5; c++) if (icon[r][c] === '#') ctx.fillRect(ix + c * ip, iy + r * ip, ip, ip);
      ctx.fillStyle = C.ink;
      ctx.textBaseline = 'middle';
      ctx.fillText(text, R(bx + pad + iconW), R(by + h / 2 + scale * 0.3));
    }

    const PLATE_NAME = { claude: 'Claude', codex: 'Codex', gemini: 'Gemini', grok: 'Grok' };
    function drawPlate(a) {
      if (!a._plate) return;
      const size = Math.round(5.4 * scale);   // sized to the plaque, so no minimum like the bubbles
      if (size < 9) return;
      ctx.font = `600 ${size}px ${FONT}`;
      ctx.textBaseline = 'middle';
      ctx.fillStyle = a.available ? '#EDE6D6' : '#8A8A8E';
      ctx.fillText(PLATE_NAME[a.name] || a.name, R(ox + S(a._plate.x)), R(oy + S(a._plate.y)));
    }

    function drawLabels() {
      // The room is pixel art; desk name plates and speech bubbles are drawn sharp on top.
      ctx.textAlign = 'left';
      for (const a of members()) drawPlate(a);
      for (const a of members()) drawBubble(a);
    }

    // --------------------------------------------------------------- frame
    function resize() {
      const rect = canvas.getBoundingClientRect();
      dpr = window.devicePixelRatio || 1;
      const cw = Math.max(1, Math.round(rect.width * dpr)), ch = Math.max(1, Math.round(rect.height * dpr));
      if (canvas.width !== cw || canvas.height !== ch) { canvas.width = cw; canvas.height = ch; }
      const fit = Math.min(cw / W, ch / VH);
      // On dense screens uneven pixel widths are invisible, so use every available pixel;
      // on 1x screens snap to whole pixels when that costs little.
      scale = fit >= 3 ? fit : Math.floor(fit) >= 1 && Math.floor(fit) / fit >= 0.9 ? Math.floor(fit) : fit;
      ox = Math.floor((cw - W * scale) / 2);
      oy = Math.floor((ch - VH * scale) / 2);
    }

    function frame(now) {
      const dt = Math.min(0.25, (now - last) / 1000);
      last = now;
      update(dt);
      renderScene();
      resize();
      ctx.imageSmoothingEnabled = false;
      // Above and below the room (tall, narrow windows): continue wall and floor colours.
      ctx.fillStyle = C.wall;
      ctx.fillRect(0, 0, canvas.width, Math.max(0, oy) + 1);
      ctx.fillStyle = C.floorA;
      ctx.fillRect(0, oy + Math.round(VH * scale) - 1, canvas.width, canvas.height);
      drawSides();
      ctx.drawImage(buf, 0, 0, W, VH, ox, oy, Math.round(W * scale), Math.round(VH * scale));
      drawLabels();
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
    // Animation frames pause in background tabs; on return, jump to the
    // current state instead of replaying minutes of walking.
    let hiddenAt = 0;
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) hiddenAt = performance.now();
      else if (hiddenAt && performance.now() - hiddenAt > 1500) { api.snap(); last = performance.now(); }
    });

    api.labels = LABELS;
    api.agents = agents;
    // For checking the drawing from devtools: render the scene at a given time into the 384x216 buffer.
    api.debug = { buffer: buf, sides: { left: sideLeft, right: sideRight }, machine, time: () => t,
      setBoardProgress(v) { boardScribble = v; },
      renderAt(time) { t = time; renderScene(); } };
    return api;
  }

  window.PixelCrewOffice = { create: createOffice, LABELS };
})();
