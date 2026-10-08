const vm = require('vm');
const fs = require('fs');
const path = require('path');

const code = fs.readFileSync(path.join(__dirname, '../../web/office.js'), 'utf8');

function createHarness(options = {}) {
  const { reduceMotion = false } = options;

  let performanceNow = 0;
  let wallTime = Date.parse('2026-10-08T12:00:00Z'); // noon by default
  const rafCallbacks = [];
  const timeouts = [];
  let timeoutId = 1;

  const rects = [];
  const dom = {
    canvas: {
      getContext: () => {
        const ctx = {
          fillRect: function(x, y, w, h) { rects.push({x, y, w, h, color: this._fillStyle}); },
          drawImage: () => {},
          fillText: () => {},
          measureText: () => ({ width: 10 }),
          createLinearGradient: () => ({ addColorStop: () => {} }),
          createRadialGradient: () => ({ addColorStop: () => {} }),
          set fillStyle(c) { this._fillStyle = c; },
          get fillStyle() { return this._fillStyle; }
        };
        return ctx;
      },
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }),
      width: 800,
      height: 600
    }
  };

  const documentMock = {
    createElement: () => dom.canvas,
    addEventListener: () => {},
    hidden: false
  };

  const windowMock = {
    matchMedia: (query) => {
      if (query === '(prefers-reduced-motion: reduce)') {
        return { matches: reduceMotion };
      }
      return { matches: false };
    },
    devicePixelRatio: 1
  };

  const context = {
    document: documentMock,
    window: windowMock,
    requestAnimationFrame: (cb) => {
      rafCallbacks.push(cb);
      return rafCallbacks.length;
    },
    setTimeout: (cb, ms) => {
      const id = timeoutId++;
      timeouts.push({ id, cb, triggerTime: performanceNow + ms });
      return id;
    },
    performance: {
      now: () => performanceNow
    },
    Math: Object.create(Math),
    console: console
  };

  // Date mock
  const OriginalDate = Date;
  context.Date = class extends OriginalDate {
    constructor(...args) {
      if (args.length === 0) {
        super(wallTime);
      } else {
        super(...args);
      }
    }
    static now() {
      return wallTime;
    }
  };

  let randomVal = 0.5;
  context.Math.random = () => randomVal;

  vm.createContext(context);
  vm.runInContext(code, context);

  const office = context.window.PixelCrewOffice;
  if (!office) throw new Error("PixelCrewOffice not found in window");
  
  const api = office.create(dom.canvas);

  function advance(dtSeconds) {
    const target = performanceNow + dtSeconds * 1000;
    while (performanceNow < target) {
      const step = Math.min(16, target - performanceNow);
      performanceNow += step;
      wallTime += step;
      
      // trigger timeouts
      for (let i = timeouts.length - 1; i >= 0; i--) {
        if (performanceNow >= timeouts[i].triggerTime) {
          const t = timeouts.splice(i, 1)[0];
          t.cb();
        }
      }

      // trigger raf
      const cbs = rafCallbacks.slice();
      rafCallbacks.length = 0;
      cbs.forEach(cb => cb(performanceNow));
    }
  }

  return {
    api,
    advance,
    setRandom: (val) => { randomVal = val; },
    setHour: (h) => {
      const d = new OriginalDate(wallTime);
      d.setHours(h);
      wallTime = d.getTime();
    },
    getRects: () => rects,
    clearRects: () => { rects.length = 0; }
  };
}

module.exports = { createHarness };
