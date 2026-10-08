/* PixelCrew UI. All state is derived from the server's event stream, so a
 * live run and a replayed history go through the same reducer. */
(function () {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => [...(root || document).querySelectorAll(sel)];
  const AGENTS = ['claude', 'codex', 'gemini', 'grok'];
  const NAME = { claude: 'Claude', codex: 'Codex', gemini: 'Gemini', grok: 'Grok' };
  const TO = { claude: "Claude'a", codex: "Codex'e", gemini: "Gemini'ye", grok: "Grok'a" };
  const tr = window.I18N.t;
  const LOCALE = window.I18N.locale;
  const pct = (n) => (window.I18N.lang === 'tr' ? `%${n}` : `${n}%`);
  const keyed = (prefix, keys) => Object.fromEntries(keys.map((k) => [k, tr(`${prefix}.${k}`)]));
  const DEFAULT_TEAM = ['claude', 'codex'];
  let team = DEFAULT_TEAM.slice();
  const teamOf = (list) => AGENTS.filter((a) => (list || []).includes(a));
  const L = window.PixelCrewOffice.LABELS;
  const office = window.PixelCrewOffice.create($('#office'));
  window.PixelCrewOffice.instance = office; // handy for poking at the scene from devtools

  // Inside the macOS app the page talks to Swift through a WebKit message handler;
  // in a normal browser this is absent and everything falls back to web behaviour.
  const native = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.pixelcrew;
  if (native) document.documentElement.classList.add('native');
  function nativePost(msg) {
    if (!native) return;
    try { native.postMessage(msg); } catch (e) { /* bridge gone during reload */ }
  }
  const pending = {};
  window.PixelCrewNative = {
    reply(id, value) { const fn = pending[id]; delete pending[id]; if (fn) fn(value); },
    action(name) { const fn = menuActions[name]; if (fn) fn(); },
    update(state) { renderUpdate(state); },
  };
  function nativeAsk(msg) {
    return new Promise((resolve) => {
      const id = Math.random().toString(36).slice(2);
      pending[id] = resolve;
      nativePost(Object.assign({ id }, msg));
    });
  }
  const menuActions = {};

  const STATUS = keyed('status', ['todo', 'working', 'awaiting_review', 'reviewing', 'changes_requested', 'fixing', 'done', 'failed', 'blocked', 'cancelled']);
  const VERB = keyed('verb', ['reading', 'searching', 'writing', 'running', 'browsing', 'delegating', 'planning', 'working']);
  const JOB_TITLE = keyed('job', ['plan', 'plan_review', 'implement', 'review', 'fix', 'chat', 'integrate']);
  const FROM = { claude: "Claude'dan", codex: "Codex'ten", gemini: "Gemini'den", grok: "Grok'tan" };
  const QUOTA_LEVEL = { ok: [tr('qlevel.ok'), 'ok'], unknown: [tr('qlevel.unknown'), ''], tight: [tr('qlevel.tight'), 'tight'], critical: [tr('qlevel.critical'), 'critical'], empty: [tr('qlevel.empty'), 'empty'] };
  const TIER_NAME = keyed('tier', ['strong', 'standard', 'fast']);
  // Agent name with its Turkish case endings, for strings like "{to} soruldu".
  const named = (a, extra) => Object.assign({ name: NAME[a] || a, to: TO[a] || a, from: FROM[a] || a }, extra || {});
  let modelTiers = {};
  function modelTag(ev) {
    if (!ev.model) return null;
    const tag = el('em', 'effort-tag model-tag', prettyModel(ev.model));
    tag.title = ev.tier ? tr('model.tag.tier', { model: ev.model, tier: (TIER_NAME[ev.tier] || ev.tier).toLocaleLowerCase(LOCALE) }) : tr('model.tag.pinned', { model: ev.model });
    return tag;
  }
  const LEVEL_TR = keyed('level', ['low', 'medium', 'high', 'xhigh', 'minimal', 'max']);

  // ------------------------------------------------------------------ state
  let S = fresh();
  let CH = freshChat();
  let mode = 'task';
  try { mode = localStorage.getItem('pixelcrew.mode') === 'ask' ? 'ask' : 'task'; } catch (e) { /* private mode */ }
  let settings = {};
  let health = {};
  let replay = false;
  let es = null;
  let overrides = {};
  let effortOverrides = {};
  const openTasks = new Set();
  const openFiles = new Set();
  let dirty = { tasks: true, changes: true, summary: true, heads: true, phases: true, chat: true };

  function fresh() {
    return {
      run: null, phase: 'idle', plan: null, planReview: null, tasks: new Map(), order: [],
      agents: Object.fromEntries(AGENTS.map((a) => [a, { state: 'idle', target: '', job: null, task: null, todo: [], in: 0, out: 0, cost: 0, available: true }])),
      diffs: [], notices: [], finished: null, approval: false, startTs: null, endTs: null, firstTs: null,
      reviewSeen: false, answer: null, final: null, quota: null, decisions: [],
    };
  }
  function freshChat() { return { id: null, items: [], busy: false }; }
  function chatItem(qid) { return CH.items.find((i) => i.qid === qid); }

  // ------------------------------------------------------------------ feed
  const feeds = Object.fromEntries(AGENTS.map((a) => [a, $(`#feed-${a}`)]));
  const toolRows = new Map();
  // A feed follows new rows until the reader scrolls up; scrolling back down re-pins it.
  const pinned = Object.fromEntries(AGENTS.map((a) => [a, true]));
  const EMPTY_FEED = keyed('feed.empty', AGENTS);
  for (const a of AGENTS) {
    feeds[a].addEventListener('scroll', () => {
      const f = feeds[a];
      pinned[a] = f.scrollHeight - f.scrollTop - f.clientHeight < 60;
    }, { passive: true });
  }
  function follow(a) { if (pinned[a]) feeds[a].scrollTop = feeds[a].scrollHeight; }

  function clearFeeds() {
    toolRows.clear();
    for (const a of AGENTS) {
      pinned[a] = true;
      feeds[a].innerHTML = '';
      const li = document.createElement('li');
      li.className = 'empty-feed';
      li.textContent = EMPTY_FEED[a];
      feeds[a].appendChild(li);
    }
  }

  // Which agents get a console, a lamp and a desk: the team in the settings,
  // plus whoever takes part in a running or replayed run (it may predate a change).
  function applyTeam() {
    const runTeam = S.run && (replay || isRunning()) ? ((S.run.settings && S.run.settings.team) || DEFAULT_TEAM) : [];
    const next = teamOf([...(settings.team || DEFAULT_TEAM), ...runTeam]);
    team = next.length ? next : DEFAULT_TEAM.slice();
    for (const a of AGENTS) {
      const on = team.includes(a);
      $(`.console[data-agent="${a}"]`).hidden = !on;
      $(`#lamp-${a}`).hidden = !on;
      $(`#targets .chip[data-agent="${a}"]`).hidden = !on;
    }
    $('#consoles').dataset.count = String(team.length);
    $('#toAllLabel').textContent = team.length === 2 ? tr('to.both') : tr('to.all');
    const sel = $('input[name="to"]:checked');
    if (sel && sel.value !== 'both' && !team.includes(sel.value)) $('input[name="to"][value="both"]').checked = true;
    office.setTeam(team);
  }

  function stamp(ts) {
    const base = S.firstTs || ts;
    const s = Math.max(0, Math.round(ts - base));
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  }

  function pushRow(agent, cls, ts, build) {
    const feed = feeds[agent];
    if (!feed) return null;
    const empty = feed.querySelector('.empty-feed');
    if (empty) empty.remove();
    const li = document.createElement('li');
    li.className = cls;
    if (!cls.includes('row-job')) {
      const t = document.createElement('span'); t.className = 't'; t.textContent = stamp(ts);
      const i = document.createElement('span'); i.className = 'ico';
      const b = document.createElement('div'); b.className = 'body';
      li.append(t, i, b);
      build(b);
    } else {
      build(li);
    }
    feed.appendChild(li);
    while (feed.children.length > 600) feed.firstChild.remove();
    follow(agent);
    return li;
  }

  function text(el, str) { el.textContent = str; return el; }
  function el(tag, cls, str) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (str !== undefined) e.textContent = str;
    return e;
  }

  // --------------------------------------------------------------- reducer
  function apply(ev, live) {
    const a = ev.agent;
    const ag = S.agents[a];
    if (S.firstTs === null) S.firstTs = ev.ts;
    switch (ev.kind) {
      case 'run.started': {
        const keepFirst = ev.ts;
        S = fresh();
        S.firstTs = keepFirst;
        S.startTs = ev.ts;
        S.run = { id: ev.run, prompt: ev.prompt, workspace: ev.workspace, settings: ev.settings, available: ev.available, health: ev.health };
        for (const n of AGENTS) S.agents[n].available = (ev.available || AGENTS).includes(n);
        overrides = {};
        effortOverrides = {};
        openTasks.clear(); openFiles.clear();
        clearFeeds();
        office.reset();
        applyTeam();
        office.setAvailable(ev.available);
        if (live) office.envelope();
        markAll();
        break;
      }
      case 'run.phase':
        S.phase = ev.phase;
        if (ev.phase !== 'approval') S.approval = false;
        office.setPhase(ev.phase);
        dirty.phases = dirty.tasks = true;
        break;
      case 'run.notice':
        S.notices.push({ level: ev.level, text: ev.text });
        if (live) toast(ev.text, ev.level === 'error');
        dirty.tasks = dirty.summary = true;
        break;
      case 'run.error':
        break;
      case 'plan.proposed':
        S.plan = ev.plan;
        setTasks(ev.plan.tasks);
        break;
      case 'plan.reviewed':
        S.planReview = { by: ev.by, verdict: ev.verdict, comments: ev.comments || [], revised: ev.revised };
        if (ev.revised && ev.plan) { S.plan = ev.plan; setTasks(ev.plan.tasks); }
        dirty.tasks = true;
        break;
      case 'approval.waiting':
        S.approval = true;
        office.setWaiting(true);
        if (live) nativePost({ type: 'notify', kind: 'approval', title: tr('notify.plan.title'), body: tr('notify.plan.body') });
        if (live) { selectTab('tasks'); toast(tr('toast.plan')); }
        dirty.tasks = dirty.phases = true;
        break;
      case 'plan.final':
        S.plan = ev.plan;
        S.approval = false;
        office.setWaiting(false);
        setTasks(ev.plan.tasks);
        break;
      case 'run.answer':
        S.answer = { by: ev.agent === 'system' ? ev.by : ev.agent, text: ev.text };
        S.answer.by = ev.by || S.answer.by;
        pushRow(S.answer.by, 'row-msg', ev.ts, (b) => { b.textContent = ev.text; });
        if (live) {
          office.say(S.answer.by, ev.text);
          toast(tr('toast.answer'));
        }
        dirty.tasks = dirty.summary = true;
        break;
      case 'final.result':
        S.final = { agent: a === 'system' ? ev.agent : a, status: ev.status, summary: ev.summary, checks: ev.checks || [], files: ev.files || [] };
        if (live) toast(tr(ev.status === 'problems' ? 'toast.final.problems' : ev.status === 'fixed' ? 'toast.final.fixed' : 'toast.final.ok'), ev.status === 'problems');
        dirty.tasks = dirty.summary = dirty.phases = true;
        break;
      case 'quota.status':
        S.quota = ev.agents;
        dirty.tasks = dirty.summary = true;
        break;
      case 'quota.decision': {
        S.decisions.push(ev);
        const moving = ['reassign', 'takeover', 'lead', 'exclude'].includes(ev.action) && ev.frm && ev.to && ev.frm !== ev.to;
        if (live && moving) office.handoff(ev.frm, ev.to, ev.action === 'lead' ? tr('bubble.lead') : ev.task_id ? tr('bubble.task', { id: ev.task_id }) : tr('bubble.take'));
        if (live && (ev.action === 'takeover' || ev.action === 'exclude')) toast(ev.text, true);
        const where = moving ? ev.to : ev.frm;
        if (where && S.agents[where]) pushRow(where, 'row-quota', ev.ts, (b) => { b.textContent = ev.text; });
        dirty.tasks = dirty.summary = true;
        break;
      }
      case 'plan.adjusted':
        S.plan = ev.plan;
        setTasks(ev.plan.tasks);
        break;
      case 'chat.started':
        CH = freshChat();
        CH.id = ev.run;
        dirty.chat = true;
        break;
      case 'chat.reset':
        CH = freshChat();
        dirty.chat = true;
        break;
      case 'chat.question':
        CH.items.push({ qid: ev.qid, text: ev.text, to: ev.to || [], ts: ev.ts, note: ev.note || '',
          answers: Object.fromEntries((ev.to || []).map((n) => [n, { pending: true }])) });
        CH.busy = true;
        dirty.chat = true;
        break;
      case 'chat.answer': {
        const item = chatItem(ev.qid);
        if (item) item.answers[a] = { pending: false, ok: ev.ok, text: ev.text, error: ev.error };
        if (live && ev.ok && ev.text) office.say(a, ev.text);
        dirty.chat = true;
        break;
      }
      case 'chat.idle':
        if (live) refreshStatus();
        if (live) nativePost({ type: 'notify', kind: 'chat', title: tr('notify.chat.title'), body: tr('notify.chat.body') });
        CH.busy = false;
        dirty.chat = true;
        break;
      case 'agent.limit':
        if (!ag) break;
        ag.limit = { status: ev.status, utilization: ev.utilization, window: ev.window, resets: ev.resets_at };
        if (live && ev.status === 'rejected') toast(tr('toast.limit', { name: NAME[a] }), true);
        if (live) refreshStatus();
        dirty.heads = true;
        break;
      case 'agent.session':
      case 'agent.model':
        if (live) refreshStatus();
        break;
      case 'task.update': {
        const prev = S.tasks.get(ev.task.id) || {};
        S.tasks.set(ev.task.id, Object.assign({}, prev, ev.task));
        if (!S.order.includes(ev.task.id)) S.order.push(ev.task.id);
        if (ev.task.status === 'reviewing') S.reviewSeen = true;
        office.setTasks(taskList());
        dirty.tasks = dirty.phases = dirty.summary = true;
        break;
      }
      case 'job.started':
        if (!ag) break;
        if (ev.job === 'chat') {
          ag.chat = true;
          if (!ag.job) { office.job(a, 'chat'); ag.effort = ev.effort || null; ag.model = ev.model || null; }
          pushRow(a, 'row-job j-review', ev.ts, (li) => {
            li.append(el('span', '', JOB_TITLE.chat));
            const mt = modelTag(ev);
            if (mt) li.append(mt);
            if (ev.effort) li.append(el('em', 'effort-tag', tr('feed.effort', { level: LEVEL_TR[ev.effort] || ev.effort })));
          });
          dirty.heads = true;
          break;
        }
        ag.job = ev.job; ag.task = ev.task_id; ag.effort = ev.effort || null; ag.model = ev.model || null;
        office.job(a, ev.job, ev.task_id && S.tasks.has(ev.task_id) ? S.tasks.get(ev.task_id).assignee : null);
        pushRow(a, `row-job j-${ev.job}`, ev.ts, (li) => {
          const task = ev.task_id ? S.tasks.get(ev.task_id) : null;
          const id = ev.task_id && ev.task_id !== 'SON' ? ev.task_id + ' ' : '';
          li.append(text(el('span'), `${id}${JOB_TITLE[ev.job] || ev.job}`));
          if (task) li.append(el('small', '', task.title));
          const mt = modelTag(ev);
          if (mt) li.append(mt);
          if (ev.effort) li.append(el('em', 'effort-tag', tr('feed.effort', { level: LEVEL_TR[ev.effort] || ev.effort })));
          if (ev.resumed) li.append(el('em', 'effort-tag', tr('feed.resumed')));
        });
        dirty.heads = dirty.phases = true;
        break;
      case 'job.finished':
        if (!ag) break;
        if (ev.job === 'chat') {
          ag.chat = false;
          if (!ag.job) { office.jobEnd(a, ev.ok); ag.effort = null; ag.model = null; }
          if (!ev.ok) pushRow(a, 'row-end bad', ev.ts, (b) => { b.textContent = tr('feed.unanswered', { err: ev.error || tr('feed.unknown_error') }); });
          dirty.heads = true;
          break;
        }
        ag.job = null; ag.task = null; ag.todo = []; ag.effort = null; ag.model = null;
        office.jobEnd(a, ev.ok);
        pushRow(a, `row-end ${ev.ok ? '' : 'bad'}`, ev.ts, (b) => {
          b.textContent = ev.ok ? tr('feed.done', { dur: fmtDur(ev.duration) }) : tr('feed.failed', { err: ev.error || tr('feed.unknown_error') });
        });
        dirty.heads = true;
        break;
      case 'agent.state':
        if (!ag) break;
        if (ev.job === 'chat' && ag.job) break; // a side question must not hide the task work in the office
        ag.state = ev.state; ag.target = ev.target || '';
        office.setAgent(a, ev.state, ev.target);
        dirty.heads = true;
        break;
      case 'agent.tool': {
        if (!ag) break;
        const row = pushRow(a, `row-tool s-${ev.state}`, ev.ts, (b) => {
          const verb = el('span', 'verb', `${toolVerb(ev)} `);
          b.append(verb);
          if (ev.target) b.append(el('code', '', shorten(ev.target, 160)));
        });
        if (row && ev.tid) toolRows.set(`${a}:${ev.tid}`, row);
        break;
      }
      case 'agent.tool_result': {
        const row = toolRows.get(`${a}:${ev.tid}`);
        if (!row) break;
        if (!ev.ok) row.classList.add('failed');
        const out = (ev.output || '').trim();
        if (out || !ev.ok) {
          const body = row.querySelector('.body');
          const d = el('details', 'result');
          const lines = out ? out.split('\n').length : 0;
          d.append(el('summary', '', ev.ok ? tr('feed.output', { n: lines }) : ev.exit_code != null ? tr('feed.error_code', { code: ev.exit_code }) : tr('feed.error')));
          d.append(el('pre', '', out || tr('feed.no_output')));
          body.append(d);
          follow(a);
        }
        break;
      }
      case 'agent.message':
        if (!ag) break;
        if (ev.structured) {
          const what = tr(['plan', 'plan_review', 'review', 'integrate'].includes(ev.job) ? `feed.delivered.${ev.job}` : 'feed.delivered.other');
          pushRow(a, 'row-tool s-writing', ev.ts, (b) => {
            b.append(el('span', 'verb', what));
            const d = el('details', 'result');
            d.append(el('summary', '', 'JSON'), el('pre', '', ev.text.replace(/^```json\s*|```\s*$/g, '').trim()));
            b.append(d);
          });
          if (live) office.say(a, what);
          break;
        }
        pushRow(a, 'row-msg', ev.ts, (b) => { b.textContent = ev.text; });
        if (live) office.say(a, ev.text);
        break;
      case 'agent.thinking':
        pushRow(a, 'row-think', ev.ts, (b) => { b.textContent = ev.text; });
        break;
      case 'agent.stderr':
        pushRow(a, 'row-warn', ev.ts, (b) => { b.textContent = ev.text; });
        break;
      case 'agent.todo':
        if (ag) { ag.todo = ev.items || []; dirty.heads = true; }
        break;
      case 'agent.usage':
        if (!ag) break;
        ag.in += ev.input_tokens || 0; ag.out += ev.output_tokens || 0; ag.cost += ev.cost_usd || 0;
        dirty.heads = dirty.summary = true;
        break;
      case 'file.changed':
        if (live) office.fileChanged(a);
        break;
      case 'task.diff':
        if ((ev.files || []).length) S.diffs.push(ev);
        dirty.changes = true;
        break;
      case 'review.result': {
        office.verdict(a, ev.verdict);
        pushRow(a, `row-review ${ev.verdict === 'approve' ? 'approve' : 'changes'}`, ev.ts, (b) => {
          b.append(el('div', `verdict-line ${ev.verdict}`, `${ev.task_id}: ${verdictText(ev.verdict)}`));
          if (ev.summary) b.append(el('p', '', ev.summary));
          if ((ev.issues || []).length) {
            const ul = el('ul', 'issues');
            for (const i of ev.issues) ul.append(el('li', i.severity || '', `${i.file ? i.file + ': ' : ''}${i.message || ''}`));
            b.append(ul);
          }
        });
        break;
      }
      case 'run.finished':
        if (live) refreshStatus();
        if (live && ev.status !== 'answered') {
          nativePost({
            type: 'notify', kind: 'finished',
            title: tr(ev.status === 'failed' ? 'run.failed.title' : ev.status === 'cancelled' ? 'run.cancelled.title' : 'run.done.title'),
            body: S.run ? S.run.prompt.slice(0, 120) : '',
          });
        }
        S.finished = ev;
        S.endTs = ev.ts;
        S.phase = ev.status;
        office.finish(ev.status);
        if (live && ev.status !== 'answered') toast(tr(ev.status === 'done' ? 'toast.run.done' : ev.status === 'cancelled' ? 'toast.run.cancelled' : 'toast.run.failed'), ev.status === 'failed');
        if (live && ev.status === 'done') selectTab('summary');
        markAll();
        break;
      default:
        break;
    }
    schedule();
  }

  function setTasks(list) {
    for (const t of list) S.tasks.set(t.id, Object.assign({}, S.tasks.get(t.id) || {}, t));
    S.order = list.map((t) => t.id);
    for (const id of [...S.tasks.keys()]) if (!S.order.includes(id)) S.tasks.delete(id);
    office.setTasks(taskList());
    dirty.tasks = dirty.phases = dirty.summary = true;
  }
  function taskList() { return S.order.map((id) => S.tasks.get(id)).filter(Boolean); }
  function markAll() { for (const k of Object.keys(dirty)) dirty[k] = true; }

  function toolVerb(ev) {
    const v = VERB[ev.state] || VERB.working;
    if (ev.state === 'running') return tr('tool.ran');
    if (ev.state === 'writing') return ev.tool === 'Write' ? tr('tool.wrote') : tr('tool.edited');
    if (ev.state === 'reading') return tr('tool.read');
    if (ev.state === 'searching') return tr('tool.searched');
    if (ev.tool === 'TodoWrite') return tr('tool.todo');
    return (window.I18N.lang === 'tr' ? `${ev.tool || ''} ${v}` : `${cap(v)} ${ev.tool || ''}`).trim();
  }
  function verdictText(v) {
    return tr(v === 'approve' ? 'verdict.approve' : v === 'changes_requested' ? 'verdict.changes_requested' : 'verdict.unknown');
  }
  function shorten(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  function fmtDur(sec) {
    sec = Math.round(sec || 0);
    return sec < 60 ? tr('dur.sec', { s: sec }) : tr('dur.min', { m: Math.floor(sec / 60), s: sec % 60 });
  }
  function fmtTok(n) { return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(Math.round(n)); }

  // --------------------------------------------------------------- render
  let raf = 0;
  function schedule() { if (!raf) raf = requestAnimationFrame(render); }

  function render() {
    raf = 0;
    if (dirty.heads) { renderHeads(); dirty.heads = false; }
    if (dirty.phases) { renderPhases(); dirty.phases = false; }
    if (dirty.tasks) { renderTasks(); dirty.tasks = false; }
    if (dirty.changes) { renderChanges(); dirty.changes = false; }
    if (dirty.summary) { renderSummary(); dirty.summary = false; }
    if (dirty.chat) { renderChat(); dirty.chat = false; }
    renderControls();
  }

  function renderHeads() {
    for (const a of AGENTS) {
      const ag = S.agents[a];
      renderTodo(a);
      if (quota) renderModel(a);
      const st = $(`#state-${a}`);
      const h = health[a];
      let label, cls = '';
      if (S.run && !ag.available) { label = tr('head.absent'); cls = 'offline'; }
      else if (!S.run && h && !h.ready && !settings.demo) { label = h.problem || tr('head.not_ready'); cls = 'offline'; }
      else if (ag.chat && !ag.job) { label = JOB_TITLE.chat; cls = 'busy'; }
      else if (ag.job) {
        const what = L.state[ag.state] || ag.state;
        label = `${ag.task ? ag.task + ' ' : ''}${L.job[ag.job] || ag.job}${ag.state && ag.state !== 'idle' ? `, ${what}` : ''}`;
        cls = 'busy';
      } else label = isRunning() ? tr('head.waiting') : tr('head.idle');
      st.textContent = label;
      st.className = `console-state ${cls}`;
      const m = $(`#meter-${a}`);
      m.innerHTML = '';
      if (ag.in || ag.out) {
        m.append(meter(tr('meter.in'), fmtTok(ag.in)), meter(tr('meter.out'), fmtTok(ag.out)));
        if (ag.cost) {
          const c = meter(tr('meter.cost'), `$${ag.cost.toFixed(2)}`);
          c.title = tr('meter.cost.tip');
          m.append(c);
        }
      }

    }
  }
  function meter(label, value) {
    const d = el('div'); d.append(el('dt', '', label), el('dd', '', value)); return d;
  }

  function renderTodo(a) {
    const box = $(`#todo-${a}`);
    const items = S.agents[a].todo || [];
    const hidden = !items.length || !S.agents[a].job;
    const sig = hidden ? '' : JSON.stringify(items);
    if (box.dataset.sig === sig) return;
    box.dataset.sig = sig;
    box.hidden = hidden;
    box.innerHTML = '';
    for (const i of items.slice(0, 12)) box.append(el('li', i.done ? 'done' : i.active ? 'active' : '', i.text));
    const active = box.querySelector('.active');
    if (active) active.scrollIntoView({ block: 'nearest' });
    follow(a);
  }

  function renderPhases() {
    const s = S.run ? S.run.settings || {} : settings;
    const order = ['planning', 'plan_review', 'approval', 'executing', 'reviewing', 'integrating', 'finished'];
    const END = 6;
    const skipped = new Set();
    const tasks = taskList();
    if (!s.planReview || (S.run && (S.run.available || []).length < 2)) skipped.add('plan_review');
    if (!s.approvePlan) skipped.add('approval');
    if (!s.crossReview) skipped.add('reviewing');
    if (s.finalCheck === false || (S.plan && tasks.length < 2) || (S.finished && !S.final)) skipped.add('integrating');
    let current = { planning: 0, plan_review: 1, approval: 2, executing: 3, integrating: 5, done: END, answered: END, failed: END, cancelled: END }[S.phase];
    const reviewing = tasks.some((t) => ['reviewing', 'awaiting_review', 'changes_requested', 'fixing'].includes(t.status));
    for (const li of $$('#phases li')) {
      const step = li.dataset.step;
      const idx = order.indexOf(step);
      let state = '';
      if (!S.run) state = skipped.has(step) ? 'skipped' : '';
      else if (S.phase === 'answered') state = step === 'planning' || step === 'finished' ? 'done' : '';
      else if (skipped.has(step)) state = 'skipped';
      else if (step === 'reviewing') {
        state = S.phase === 'executing' && reviewing ? 'active' : S.reviewSeen ? 'done' : '';
      } else if (step === 'integrating') {
        state = S.phase === 'integrating' ? 'active' : S.final ? (S.final.status === 'problems' ? 'failed' : 'done') : '';
      } else if (step === 'finished') {
        state = S.phase === 'done' || S.phase === 'answered' ? 'done' : S.phase === 'failed' || S.phase === 'cancelled' ? 'failed' : '';
      } else if (current !== undefined) {
        state = idx < current ? 'done' : idx === current && current < END ? 'active' : current >= END ? 'done' : '';
      }
      li.dataset.state = state;
    }
    $('#phases li[data-step="finished"]').textContent = tr(['failed', 'cancelled', 'answered'].includes(S.phase) ? `phase.${S.phase}` : 'phase.finished');
  }

  function renderTasks() {
    const tasks = taskList();
    $('#taskCount').textContent = tasks.length ? String(tasks.length) : '';
    $('#tasksEmpty').hidden = tasks.length > 0 || S.notices.length > 0 || !!S.answer;
    $('#approvalBar').hidden = !(S.approval && !replay);

    const nb = $('#notices');
    nb.innerHTML = '';
    for (const n of S.notices.slice(-6)) {
      const d = el('div', `notice ${n.level}`);
      d.append(document.createTextNode(n.text));
      nb.append(d);
    }

    renderQuotaBox();
    const pb = $('#planBox');
    pb.innerHTML = '';
    if (S.answer) {
      const card = el('div', 'answer-card');
      card.dataset.agent = S.answer.by;
      card.append(el('b', '', tr('answer.by', { name: NAME[S.answer.by] || S.answer.by })));
      const body = el('div', 'md'); body.innerHTML = md(S.answer.text);
      card.append(body, el('small', '', tr('answer.note')));
      pb.hidden = false;
      pb.append(card);
    }
    if (S.plan && S.plan.summary) {
      pb.hidden = false;
      pb.append(el('h3', '', tr('plan.approach')), el('p', '', S.plan.summary));
      if (S.plan.rationale) pb.append(el('p', '', S.plan.rationale));
      if (S.planReview) {
        const r = el('div', 'plan-review');
        const who = NAME[S.planReview.by] || S.planReview.by;
        r.append(el('strong', '', tr(S.planReview.revised ? 'plan.revised' : S.planReview.verdict === 'approve' ? 'plan.approved' : 'plan.reviewed', { name: who })));
        if (S.planReview.comments.length) {
          const ul = el('ul');
          for (const c of S.planReview.comments) ul.append(el('li', '', c));
          r.append(ul);
        }
        pb.append(r);
      }
    } else if (!S.answer) pb.hidden = true;

    const list = $('#taskList');
    list.innerHTML = '';
    const avail = teamOf((S.run && S.run.available) || []);
    for (const t of tasks) {
      const assignee = overrides[t.id] || t.assignee;
      // The server picks the real reviewer on approval; this is the likely one.
      const reviewer = overrides[t.id] ? (t.reviewer && t.reviewer !== assignee ? t.reviewer : avail.find((x) => x !== assignee) || assignee) : t.reviewer;
      const li = el('li', 'task');
      li.dataset.assignee = assignee;
      li.dataset.status = t.status;
      const head = el('div', 'task-head');
      head.append(el('span', 'task-id', t.id), el('h3', '', t.title), el('span', `pill ${t.status}`, STATUS[t.status] || t.status));
      const who = el('div', 'task-who');
      if (S.approval && !replay && avail.length > 1) {
        const b = el('button', `who ${assignee}`, NAME[assignee]);
        b.type = 'button';
        b.title = avail.length > 2 ? tr('task.cycle') : tr('task.swap');
        b.addEventListener('click', () => {
          overrides[t.id] = avail[(avail.indexOf(assignee) + 1) % avail.length];
          if (overrides[t.id] === t.assignee) delete overrides[t.id];
          dirty.tasks = true; schedule();
        });
        who.append(b);
      } else who.append(el('span', `who ${assignee}`, NAME[assignee]));
      who.append(document.createTextNode(tr('task.does')));
      const crossOn = (S.run && S.run.settings ? S.run.settings.crossReview : settings.crossReview);
      if (crossOn && reviewer) {
        who.append(document.createTextNode(', '), el('span', `who ${reviewer}`, NAME[reviewer]), document.createTextNode(reviewer === assignee ? tr('task.reviews_self') : tr('task.reviews')));
      }
      if (t.depends_on && t.depends_on.length) who.append(document.createTextNode(tr('task.after', { list: t.depends_on.join(', ') })));
      if (t.fixes) who.append(document.createTextNode(tr(t.fixes === 1 ? 'task.fix1' : 'task.fixes', { n: t.fixes })));
      li.append(head, who, taskMeta(t));

      const det = el('details');
      det.open = openTasks.has(t.id);
      det.addEventListener('toggle', () => { det.open ? openTasks.add(t.id) : openTasks.delete(t.id); });
      det.append(el('summary', '', tr('task.details')));
      const body = el('div', 'detail');
      if (t.description) body.append(section(tr('task.description'), t.description));
      const files = (t.files_changed && t.files_changed.length ? t.files_changed : t.files) || [];
      if (files.length) {
        const f = el('div');
        f.append(el('h4', '', tr(t.files_changed && t.files_changed.length ? 'task.files_changed' : 'task.files_planned')));
        const wrap = el('div', 'files');
        for (const p of files) wrap.append(el('code', '', p));
        f.append(wrap); body.append(f);
      }
      if (t.report) body.append(section(tr('task.report', { name: NAME[t.assignee] }), t.report));
      if (t.review) {
        const r = el('div');
        r.append(el('h4', '', tr('task.review', { name: NAME[t.reviewer] || t.reviewer })));
        r.append(el('div', `verdict-line ${t.review.verdict}`, verdictText(t.review.verdict)));
        if (t.review.summary) r.append(el('p', '', t.review.summary));
        if ((t.review.issues || []).length) {
          const ul = el('ul', 'issues');
          for (const i of t.review.issues) ul.append(el('li', i.severity || '', `${i.file ? i.file + ': ' : ''}${i.message || ''}`));
          r.append(ul);
        }
        body.append(r);
      }
      if (t.fix_report) body.append(section(tr('task.fix_report'), t.fix_report));
      if (t.error) body.append(section(tr('task.error'), t.error));
      det.append(body);
      li.append(det);
      list.append(li);
    }
    if (S.final) list.append(finalCard());
  }
  function renderQuotaBox() {
    let box = $('#quotaBox');
    if (!box) {
      box = el('div', 'quota-box');
      box.id = 'quotaBox';
      $('#notices').before(box);
    }
    box.innerHTML = '';
    const show = S.quota && (S.decisions.length || Object.values(S.quota).some((q) => q.level && !['ok', 'unknown'].includes(q.level)));
    box.hidden = !show;
    if (!show) return;
    const head = el('div', 'quota-head');
    head.append(el('h3', '', tr('quota.box')));
    for (const a of AGENTS) {
      const q = S.quota[a] || {};
      if (q.left == null) continue;
      const [label, cls] = QUOTA_LEVEL[q.level] || ['', ''];
      const chip = el('span', `qchip ${cls}`);
      chip.append(el('b', '', NAME[a]), document.createTextNode(` ${pct(q.left)} ${label}`));
      chip.title = tr('quota.chip.tip', { window: windowName(q.window), left: q.left }) + (q.resets_at ? tr('quota.chip.resets', { when: when(q.resets_at) }) : '');
      head.append(chip);
    }
    box.append(head);
    if (S.decisions.length) {
      const ul = el('ul', 'decisions');
      for (const d of S.decisions.slice(-8)) ul.append(el('li', `d-${d.action}`, d.text));
      box.append(ul);
    }
  }

  function taskMeta(t) {
    const meta = el('div', 'task-meta');
    const cx = t.complexity || 'medium';
    meta.append(el('span', `level l-${cx}`, tr('meta.difficulty', { level: LEVEL_TR[cx] })));
    const mode = (S.run && S.run.settings && S.run.settings.effortMode) || settings.effortMode || 'auto';
    const chosen = effortOverrides[t.id] || t.effort || null;
    let label;
    if (mode === 'cli') label = tr('meta.effort.cli');
    else if (mode !== 'auto') label = tr('meta.effort.always', { level: LEVEL_TR[mode] });
    else label = chosen ? tr('meta.effort.manual', { level: LEVEL_TR[chosen] }) : tr('meta.effort.auto', { level: LEVEL_TR[cx] });
    if (t.handoff) meta.append(el('span', 'task-handoff', tr('meta.handoff', named(t.handoff.from))));
    if (S.approval && !replay && mode === 'auto') {
      const b = el('button', 'effort-btn', label);
      b.type = 'button';
      b.title = tr('meta.effort.tip');
      b.addEventListener('click', () => {
        const cycle = [null, 'low', 'medium', 'high'];
        const next = cycle[(cycle.indexOf(effortOverrides[t.id] || null) + 1) % cycle.length];
        if (next) effortOverrides[t.id] = next; else delete effortOverrides[t.id];
        dirty.tasks = true; schedule();
      });
      meta.append(b);
    } else meta.append(el('span', '', label));
    return meta;
  }

  function finalCard() {
    const f = S.final;
    const card = el('li', `task final-card s-${f.status}`);
    const head = el('div', 'task-head');
    const pill = { ok: ['done', tr('final.ok')], fixed: ['fixing', tr('final.fixed')], problems: ['failed', tr('final.problems')] }[f.status] || ['todo', f.status];
    head.append(el('span', 'task-id', tr('final.id')), el('h3', '', tr('phase.integrating')), el('span', `pill ${pill[0]}`, pill[1]));
    card.append(head);
    if (f.summary) card.append(el('p', 'final-summary', f.summary));
    if (f.checks.length) {
      const ul = el('ul', 'checks');
      for (const c of f.checks) {
        const li = el('li', `c-${c.result || ''}`);
        li.append(el('code', '', c.command || ''), document.createTextNode(` ${['passed', 'failed', 'fixed'].includes(c.result) ? tr(`check.${c.result}`) : c.result || ''}${c.note ? `: ${c.note}` : ''}`));
        ul.append(li);
      }
      card.append(ul);
    }
    if (f.files.length) card.append(el('small', '', tr('final.files', { list: f.files.join(', ') })));
    return card;
  }

  function section(title, content) {
    const d = el('div');
    d.append(el('h4', '', title), el('p', '', content));
    return d;
  }

  function renderChanges() {
    const box = $('#changeList');
    box.innerHTML = '';
    const byFile = new Map();
    for (const d of S.diffs) for (const f of d.files) byFile.set(f.path, true);
    $('#changeCount').textContent = byFile.size ? String(byFile.size) : '';
    $('#changesEmpty').hidden = S.diffs.length > 0;
    for (const d of S.diffs.slice().reverse()) {
      const group = el('div', 'change-group');
      const task = S.tasks.get(d.task_id);
      const h = el('h3');
      h.append(el('span', `who ${d.agent}`, NAME[d.agent]), document.createTextNode(`${d.task_id} ${d.job === 'fix' ? tr('change.fix') : ''}`));
      if (task) h.append(el('small', '', task.title));
      group.append(h);
      for (const f of d.files) {
        const key = `${d.task_id}:${d.job}:${f.path}:${S.diffs.indexOf(d)}`;
        const det = el('details', 'change-file');
        det.open = openFiles.has(key);
        det.addEventListener('toggle', () => { det.open ? openFiles.add(key) : openFiles.delete(key); });
        const sum = el('summary');
        const stat = el('span', 'stat');
        if (f.binary) stat.textContent = tr('change.binary');
        else stat.append(el('span', 'plus', `+${f.added || 0}`), el('span', 'minus', `−${f.removed || 0}`));
        const path = el('span', 'path');
        path.append(el('bdi', '', f.path));
        sum.append(el('span', `kind ${f.status}`), path, stat);
        det.append(sum);
        if (f.diff) {
          const pre = el('pre', 'diff');
          for (const line of f.diff.split('\n')) {
            const cls = line.startsWith('+++') || line.startsWith('---') ? 'meta' : line.startsWith('@@') ? 'hunk' : line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : '';
            pre.append(el('span', cls, line || ' '));
          }
          det.append(pre);
        }
        group.append(det);
      }
      box.append(group);
    }
  }

  function renderSummary() {
    const box = $('#summaryBox');
    box.innerHTML = '';
    const f = S.finished;
    $('#summaryEmpty').hidden = !!S.run;
    if (!S.run) return;
    const status = f ? f.status : 'running';
    const title = tr(`sum.${status}`);
    box.append(el('p', `summary-status ${status}`, title));
    box.append(el('p', '', S.run.prompt));
    const tasks = taskList();
    const done = tasks.filter((t) => t.status === 'done').length;
    const files = new Set();
    for (const d of S.diffs) for (const x of d.files) files.add(x.path);
    const grid = el('dl', 'summary-grid');
    const dur = f ? f.duration : (Date.now() / 1000 - (S.startTs || Date.now() / 1000));
    grid.append(stat(tr('sum.duration'), fmtDur(dur)), stat(tr('sum.tasks'), `${done}/${tasks.length}`), stat(tr('sum.files'), String(files.size)));
    for (const a of team) {
      const ag = S.agents[a];
      grid.append(stat(tr('sum.tokens', { name: NAME[a] }), fmtTok(ag.in + ag.out)));
    }
    const cost = AGENTS.reduce((sum, a) => sum + S.agents[a].cost, 0);
    const costStat = stat(tr('meter.cost'), cost ? `$${cost.toFixed(2)}` : '-');
    costStat.title = tr('sum.cost.tip');
    grid.append(costStat);
    box.append(grid);
    if (tasks.length) {
      const sec = el('div', 'summary-section');
      sec.append(el('h3', '', tr('sum.tasks_head')));
      const ul = el('ul', 'summary-tasks');
      for (const t of tasks) {
        const li = el('li');
        li.append(el('span', 'task-id', t.id), el('span', '', `${t.title}`), el('span', `who ${t.assignee}`, `${NAME[t.assignee]}, ${STATUS[t.status] || t.status}`));
        ul.append(li);
      }
      sec.append(ul);
      box.append(sec);
    }
    if (S.decisions.length) {
      const sec = el('div', 'summary-section');
      sec.append(el('h3', '', tr('sum.decisions')));
      const ul = el('ul', 'decisions');
      for (const d of S.decisions) ul.append(el('li', `d-${d.action}`, d.text));
      sec.append(ul);
      box.append(sec);
    }
    if (S.final) {
      const sec = el('div', 'summary-section');
      const label = ['ok', 'fixed', 'problems'].includes(S.final.status) ? tr(`sum.final.${S.final.status}`) : S.final.status;
      sec.append(el('h3', '', tr('phase.integrating')), el('p', '', `${NAME[S.final.agent] || ''} ${label}. ${S.final.summary || ''}`));
      box.append(sec);
    }
    if (S.run.workspace) {
      const sec = el('div', 'summary-section');
      sec.append(el('h3', '', tr('sum.workspace')), el('code', '', S.run.workspace));
      box.append(sec);
    }
  }
  function stat(label, value) { const d = el('div'); d.append(el('dt', '', label), el('dd', '', value)); return d; }

  function isRunning() { return !!(S.run && !S.finished); }

  // Minimal, escape-first markdown for agent answers: fences, lists, headings, `code`, **bold**.
  function esc(t) { return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function inline(t) {
    return esc(t)
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<code title="$2">$1</code>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  }
  function md(text) {
    const out = [];
    const parts = String(text || '').split(/```[a-zA-Z0-9_-]*\n?/);
    parts.forEach((part, i) => {
      if (i % 2 === 1) { out.push(`<pre>${esc(part.replace(/\n$/, ''))}</pre>`); return; }
      for (const block of part.split(/\n{2,}/)) {
        const lines = block.split('\n').filter((l) => l.trim());
        if (!lines.length) continue;
        if (lines.every((l) => /^\s*[-*•]\s+/.test(l))) {
          out.push(`<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*•]\s+/, ''))}</li>`).join('')}</ul>`);
        } else if (lines.every((l) => /^\s*\d+[.)]\s+/.test(l))) {
          out.push(`<ol>${lines.map((l) => `<li>${inline(l.replace(/^\s*\d+[.)]\s+/, ''))}</li>`).join('')}</ol>`);
        } else if (lines.length === 1 && /^#{1,4}\s+/.test(lines[0])) {
          out.push(`<h4>${inline(lines[0].replace(/^#{1,4}\s+/, ''))}</h4>`);
        } else {
          out.push(`<p>${lines.map(inline).join('<br>')}</p>`);
        }
      }
    });
    return out.join('');
  }

  function renderChat() {
    const log = $('#chatLog');
    const panel = $('#tab-chat');
    const stick = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 80;
    log.innerHTML = '';
    $('#chatEmpty').hidden = CH.items.length > 0;
    $('#chatCount').textContent = CH.items.length ? String(CH.items.length) : '';
    $('#newChatBtn').hidden = !CH.items.length;
    $('#newChatBtn').disabled = CH.busy;
    for (const item of CH.items) {
      const q = el('li', 'chat-q', item.text);
      const toLabel = item.to.length === 2 ? tr('chat.asked_both') : item.to.length > 2 ? tr('chat.asked_n', { n: item.to.length })
        : item.to.length ? tr('chat.asked', named(item.to[0])) : '';
      q.append(el('small', '', item.note || toLabel));
      log.append(q);
      for (const name of item.to) {
        const ans = item.answers[name] || { pending: true };
        const li = el('li', `chat-a ${ans.pending ? 'pending' : ''} ${!ans.pending && !ans.ok ? 'failed' : ''}`);
        li.dataset.agent = name;
        const head = el('div', 'who-line');
        head.append(el('b', '', NAME[name]));
        if (ans.pending) head.append(el('small', 'dots', tr('chat.typing')));
        li.append(head);
        const body = el('div', 'md');
        if (ans.pending) body.textContent = tr('chat.pending');
        else if (!ans.ok) body.textContent = ans.error || tr('chat.failed');
        else body.innerHTML = md(ans.text || tr('chat.empty_answer'));
        li.append(body);
        log.append(li);
      }
    }
    if (stick) panel.scrollTop = panel.scrollHeight;
  }

  function renderControls() {
    const running = isRunning() && !replay;
    const ask = mode === 'ask';
    for (const r of $$('input[name="mode"]')) r.checked = r.value === mode;
    $('#targets').hidden = !ask;
    $('#taskToggles').hidden = ask;
    $('#stopBtn').hidden = ask ? !CH.busy : !running;
    $('#startBtn').disabled = replay || (ask ? CH.busy : running);
    $('#startBtn').textContent = ask ? tr('btn.ask') : tr('btn.start');
    const ph = ask ? tr('ph.ask') : tr('ph.task');
    if ($('#prompt').placeholder !== ph) $('#prompt').placeholder = ph;
    $('#replayBanner').hidden = !replay;
    const rp = $('#runPrompt');
    if (S.run) { rp.textContent = S.run.prompt; rp.classList.add('live'); }
    else { rp.textContent = tr('run.empty_office'); rp.classList.remove('live'); }
    $('#runClock').hidden = !S.run;
  }

  setInterval(() => {
    if (!S.run) return;
    const end = S.endTs || Date.now() / 1000;
    const s = Math.max(0, Math.round(end - S.startTs));
    $('#runClock').textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
    if (isRunning()) { dirty.summary = true; schedule(); }
  }, 1000);

  // ----------------------------------------------------------- scene hover
  // The office is drawn in pixels; hovering explains it in words.
  const sceneCanvas = $('#office');
  const tip = $('#sceneTip');
  const TASK_LINE = {
    todo: () => [tr('line.todo'), ''], working: (t) => [tr('line.working', { name: NAME[t.assignee] }), 'on'],
    awaiting_review: (t) => [t.reviewer ? tr('line.awaiting', { name: NAME[t.reviewer] }) : tr('line.awaiting.anyone'), ''],
    reviewing: (t) => [tr('line.reviewing', { name: NAME[t.reviewer] }), 'on'], changes_requested: () => [STATUS.changes_requested, 'on'],
    fixing: (t) => [tr('line.fixing', { name: NAME[t.assignee] }), 'on'], done: () => [STATUS.done, 'ok'], failed: () => [STATUS.failed, 'bad'],
    blocked: () => [STATUS.blocked, 'bad'], cancelled: () => [tr('line.cancelled'), ''],
  };
  function boardTip() {
    const tasks = taskList();
    const box = document.createDocumentFragment();
    const h = el('h4', '', tr('tip.board'));
    if (tasks.length) h.append(el('small', '', tr('tip.board.done', { done: tasks.filter((t) => t.status === 'done').length, n: tasks.length })));
    box.append(h);
    if (!tasks.length) {
      box.append(el('p', '', tr(S.phase === 'planning' ? 'tip.board.planning' : 'tip.board.idle')));
      return box;
    }
    const ul = el('ul');
    for (const t of tasks) {
      const li = el('li');
      const [label, cls] = (TASK_LINE[t.status] || (() => [t.status, '']))(t);
      li.append(el('i', t.assignee), el('code', '', t.id), el('span', '', t.title), el('em', cls, label));
      ul.append(li);
    }
    box.append(ul);
    const legend = el('p', 'legend');
    const track = el('span', 'track');
    for (const c of ['#8F8D86', 'var(--claude)', '#7C8BA6', '#3FAE5A']) { const b = el('b'); b.style.background = c; track.append(b); }
    legend.append(track, document.createTextNode(tr('tip.board.legend')));
    box.append(legend);
    return box;
  }
  function agentTip(a) {
    const ag = S.agents[a];
    const box = document.createDocumentFragment();
    const h = el('h4', '', NAME[a]);
    const q = quota && quota[a];
    if (q && q.model) h.append(el('small', '', prettyModel(q.model)));
    box.append(h);
    let line;
    if (S.run && !ag.available) line = tr('tip.absent');
    else if (ag.job) {
      const task = ag.task && ag.task !== 'SON' ? S.tasks.get(ag.task) : null;
      line = `${JOB_TITLE[ag.job] || ag.job}${task ? `: ${task.id} ${task.title}` : ''}`;
    } else if (ag.chat) line = JOB_TITLE.chat;
    else line = isRunning() ? tr('head.waiting') : tr('tip.idle');
    box.append(el('p', '', line));
    if ((ag.job || ag.chat) && ag.state && ag.state !== 'idle') {
      const now = el('p', 'legend', tr('tip.now', { state: L.state[ag.state] || ag.state }) + (ag.target ? `: ${ag.target}` : ''));
      box.append(now);
    }
    return box;
  }
  let tipKey = '';
  sceneCanvas.addEventListener('mousemove', (e) => {
    const hit = office.hit(e.clientX, e.clientY);
    sceneCanvas.style.cursor = hit ? 'pointer' : 'default';
    if (!hit) { tip.hidden = true; tipKey = ''; return; }
    const key = hit.kind === 'board' ? `board:${S.order.map((id) => (S.tasks.get(id) || {}).status).join()}` : `agent:${hit.name}:${S.agents[hit.name].state}:${S.agents[hit.name].target}`;
    if (key !== tipKey) {
      tip.innerHTML = '';
      tip.append(hit.kind === 'board' ? boardTip() : agentTip(hit.name));
      tipKey = key;
    }
    tip.hidden = false;
    const stage = tip.parentElement.getBoundingClientRect(); // the .stage box
    let x = e.clientX - stage.left + 16, y = e.clientY - stage.top + 16;
    if (x + tip.offsetWidth > stage.width - 8) x = e.clientX - stage.left - tip.offsetWidth - 16;
    if (y + tip.offsetHeight > stage.height - 8) y = Math.max(8, stage.height - tip.offsetHeight - 8);
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${y}px`;
  });
  sceneCanvas.addEventListener('mouseleave', () => { tip.hidden = true; tipKey = ''; });
  window.addEventListener('resize', () => { tip.hidden = true; tipKey = ''; });
  sceneCanvas.addEventListener('click', (e) => {
    const hit = office.hit(e.clientX, e.clientY);
    if (!hit) return;
    if (hit.kind === 'board') { selectTab('tasks'); return; }
    const card = $(`.console[data-agent="${hit.name}"]`);
    card.classList.add('flash');
    card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    setTimeout(() => card.classList.remove('flash'), 900);
  });

  // ------------------------------------------------------------------ tabs
  function selectTab(name) {
    for (const b of $$('.tabs button')) b.setAttribute('aria-selected', String(b.dataset.tab === name));
    for (const p of $$('.tabpanel')) p.hidden = p.id !== `tab-${name}`;
  }
  for (const b of $$('.tabs button')) b.addEventListener('click', () => selectTab(b.dataset.tab));

  // ------------------------------------------------------------------ toast
  let toastTimer = 0;
  // Shows or hides a popover; re-showing moves it above a dialog opened since.
  function layer(node, on) {
    if (!node.showPopover) { node.hidden = !on; return; }
    const open = node.matches(':popover-open');
    if (open) node.hidePopover();
    if (on) node.showPopover();
  }
  function toast(msg, isError) {
    const t = $('#toast');
    t.textContent = msg;
    t.className = `toast ${isError ? 'error' : ''}`;
    layer(t, true);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => layer(t, false), isError ? 7000 : 3500);
  }

  // -------------------------------------------------------------------- api
  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || tr('api.failed', { status: res.status }));
    return data;
  }

  async function loadState(refresh) {
    const data = await api(`/api/state${refresh ? '?refresh=1' : ''}`);
    settings = data.settings;
    if (data.keychain !== undefined) keychainOK = data.keychain;
    // First launch: show setup right away; the cards fill in as the CLI checks finish.
    if (!settings.onboarded && !setupShown) { setupShown = true; openSetup(); }
    if (data.modelTiers) modelTiers = data.modelTiers;
    if (data.health) health = data.health;
    renderSettingsUI();
    applyTeam();
    renderLamps();
    dirty.heads = dirty.phases = true;
    schedule();
    if (!data.health) {
      const h = await api('/api/health');
      health = h.health;
      renderLamps();
      dirty.heads = true;
      schedule();
    }
  }

  function splitPath(p) {
    const tidy = String(p || '').replace(/^\/Users\/[^/]+/, '~').replace(/\/$/, '');
    const i = tidy.lastIndexOf('/');
    return { name: i >= 0 ? tidy.slice(i + 1) || tidy : tidy, parent: i > 0 ? tidy.slice(0, i) : '', tidy };
  }
  function renderSettingsUI() {
    const ws = splitPath(settings.workspace);
    $('#workspaceName').textContent = ws.name || tr('ws.choose');
    $('#workspacePath').textContent = ws.parent ? `\u200E${ws.parent}\u200E` : '';
    $('#workspaceBtn').title = tr('ws.tip', { path: settings.workspace });
    nativePost({ type: 'title', subtitle: ws.name });
    for (const inp of $$('[data-setting]')) inp.checked = !!settings[inp.dataset.setting];
  }

  function renderLamps() {
    for (const a of AGENTS) {
      const lamp = $(`#lamp-${a}`);
      const h = health[a] || {};
      const known = settings.demo || health[a] !== undefined;
      lamp.dataset.ready = !known ? 'checking' : settings.demo ? 'true' : String(!!h.ready);
      lamp.title = !known ? tr('lamp.checking') : settings.demo ? tr('lamp.demo') : h.ready ? tr('lamp.ready', { name: NAME[a], version: h.version || '' }) : h.problem || tr('head.not_ready');
    }
    renderQuota();
    if (setupDlg.open) renderSetup();
  }

  // ----------------------------------------------------------------- model + quota
  let quota = null;
  let statusTimer = 0;
  const cap = (w) => w.charAt(0).toUpperCase() + w.slice(1);
  function prettyModel(id) {
    if (!id) return '';
    let m = /^claude-([a-z]+)-(\d+)-(\d+)/.exec(id);
    if (m) return `${cap(m[1])} ${m[2]}.${m[3]}`;
    m = /^claude-([a-z]+)-(\d+)$/.exec(id);
    if (m) return `${cap(m[1])} ${m[2]}`;
    m = /^claude-([a-z]+)$/.exec(id) || /^(opus|sonnet|haiku|fable)$/.exec(id);
    if (m) return cap(m[1]);
    m = /^gpt-([\d.]+)(?:-([a-z]+))?/i.exec(id);
    if (m) return `GPT-${m[1]}${m[2] ? ' ' + cap(m[2]) : ''}`;
    m = /^gemini-([\d.]+)-([a-z]+)(?:-(low|medium|high))?/i.exec(id);
    if (m) return `Gemini ${m[1]} ${cap(m[2])}${m[3] ? ` (${LEVEL_TR[m[3]]})` : ''}`;
    m = /^grok-([\d.]+)(.*)$/i.exec(id);
    if (m) return `Grok ${m[1]}${/fast/.test(m[2]) ? ' Fast' : ''}`;
    return id;
  }
  // Console headers already say whose model it is: "Gemini 3.8 Flash (yüksek)" -> "3.8 Flash".
  function shortModel(a, id) {
    let t = prettyModel(id).replace(/\s*\([^)]*\)$/, '');
    if (t.startsWith(`${NAME[a]} `)) t = t.slice(NAME[a].length + 1);
    return t;
  }
  function ago(ts) {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return tr('ago.now');
    if (s < 3600) return tr('ago.min', { n: Math.round(s / 60) });
    if (s < 86400) return tr('ago.hour', { n: Math.round(s / 3600) });
    return tr('ago.day', { n: Math.round(s / 86400) });
  }
  function when(ts) {
    const d = new Date(ts * 1000);
    const sameDay = d.toDateString() === new Date().toDateString();
    return sameDay ? d.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleString(LOCALE, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
  // Quota windows arrive with Turkish ids from the server ("5 saat", "hafta", "hafta (Opus)", "3 gün").
  function windowName(label) {
    const w = String(label || '');
    if (window.I18N.lang === 'tr') return w;
    return w.replace(/^5 saat/, '5h').replace(/^hafta/, 'week').replace(/^(\d+) gün/, '$1 days').replace(/^gün/, 'day')
      .replace(/^ay/, 'month').replace(/^(\d+) saat/, '$1h').replace(/^(\d+) dk/, '$1 min');
  }
  let statusLate = 0;
  function refreshStatus() {
    const load = async () => {
      try { quota = await api('/api/status'); renderQuota(); } catch (e) { /* server restarting */ }
    };
    clearTimeout(statusTimer);
    statusTimer = setTimeout(load, 600);
    // Claude's /usage is re-read in the background after each job; pick it up a few seconds later.
    clearTimeout(statusLate);
    statusLate = setTimeout(load, 9000);
  }
  // Model name plus effort bars: the current job's effort while busy, otherwise the configured mode.
  function renderModel(a) {
    const q = (quota && quota[a]) || {};
    const ag = S.agents[a];
    const modelEl = $(`#model-${a}`);
    // While a job runs, its own model (chosen per job); otherwise the last one seen.
    const live = ag && (ag.job || ag.chat) && ag.model;
    const auto = (settings.modelMode || 'auto') === 'auto' && !settings[`${a}Model`];
    // Nothing seen yet: in auto mode name the model ordinary work will use.
    const planned = !live && !q.model && auto && modelTiers[a] ? modelTiers[a].standard : null;
    const shownModel = live ? ag.model : q.model || planned;
    modelEl.textContent = shortModel(a, shownModel) || (a !== 'codex' ? tr('model.first_job') : '');
    const mode = settings.effortMode || 'auto';
    const busy = ag && (ag.job || ag.chat) && ag.effort;
    const shown = busy ? ag.effort : mode === 'cli' ? q.effort : mode !== 'auto' ? mode : null;
    const level = { minimal: 1, low: 1, medium: 2, high: 3, xhigh: 4, max: 4 }[shown];
    if (level) {
      const bars = el('span', 'effort');
      for (let i = 1; i <= 4; i++) bars.append(el('i', i <= level ? 'on' : ''));
      modelEl.append(bars);
    } else if (mode === 'auto') {
      modelEl.append(el('span', 'effort-auto', tr('effort.auto_short')));
    }
    const effortLine = busy ? tr('effort.job', { level: LEVEL_TR[ag.effort] || ag.effort })
      : mode === 'auto' ? tr('effort.auto')
        : mode === 'cli' ? tr('effort.cli', { level: q.effort ? ` (${LEVEL_TR[q.effort] || q.effort})` : '' }) : tr('effort.always', { level: LEVEL_TR[mode] });
    const modelLine = (settings.modelMode || 'auto') === 'auto' && !settings[`${a}Model`] ? tr('model.auto_line') : '';
    modelEl.title = shownModel ? `Model: ${shownModel}${live ? tr('model.this_job') : ''}\n${modelLine ? modelLine + '\n' : ''}${effortLine}${q.plan ? `\nPlan: ${q.plan}` : ''}`
      : `${tr('model.unseen', { name: NAME[a] })}\n${effortLine}`;
  }

  function renderQuota() {
    if (!quota) return;
    for (const a of AGENTS) {
      const q = quota[a] || {};
      renderModel(a);
      const box = $(`#quota-${a}`);
      box.innerHTML = '';
      const windows = q.windows || [];
      let minLeft = null;
      if (!windows.length && q.spend) {
        // No quota reading (Grok): show what PixelCrew has spent through it instead.
        const chip = el('span', 'spend');
        chip.append(el('b', '', `$${q.spend['5h'].toFixed(2)}`), document.createTextNode(tr('spend.5h')),
          el('i', '', '·'), el('b', '', `$${q.spend.week.toFixed(2)}`), document.createTextNode(tr('spend.week')));
        chip.title = tr('spend.tip', { why: NO_QUOTA[a], h5: q.spend['5h'].toFixed(2), week: q.spend.week.toFixed(2) });
        box.append(chip);
      } else if (!windows.length) box.append(battery(null, 'limit', NO_QUOTA[a]));
      for (const w of windows) {
        const left = Math.max(0, 100 - (w.used || 0));
        minLeft = minLeft === null ? left : Math.min(minLeft, left);
        const period = window.I18N.has(`period.${w.label}`) ? tr(`period.${w.label}`) : tr('period.other', { label: windowName(w.label) });
        const parts = [];
        if (w.rolled) {
          parts.push(tr('quota.rolled', { period, when: when(w.resets_at) }));
          parts.push(tr('quota.next'));
        } else {
          parts.push(tr('quota.used', { period, used: w.used, left }));
          if (w.resets_at) parts.push(tr('quota.resets', { when: when(w.resets_at) }));
          if (w.seen_at) parts.push(tr('quota.seen', { ago: ago(w.seen_at) }));
        }
        if (w.status === 'rejected') parts.push(tr('quota.rejected'));
        const stale = w.rolled || (w.seen_at && Date.now() / 1000 - w.seen_at > 3 * 3600);
        box.append(battery(left, windowName(w.label), parts.join('\n'), stale));
      }
      office.setQuota(a, minLeft);
      const lamp = $(`#lamp-${a}`);
      const h = health[a] || {};
      if (h.ready && !settings.demo) lamp.title = tr('lamp.ready_quota', { name: NAME[a], model: q.model ? `, ${prettyModel(q.model)}` : '', left: minLeft !== null ? tr('lamp.left', { left: minLeft }) : '' });
    }
  }
  const NO_QUOTA = keyed('noquota', AGENTS);
  function battery(left, label, tip, stale) {
    const wrap = el('span', 'batt');
    wrap.title = tip;
    if (left === null) wrap.classList.add('unknown');
    else if (left <= 20) wrap.classList.add('low');
    else if (left <= 50) wrap.classList.add('mid');
    if (stale) wrap.classList.add('stale');
    const body = el('span', 'batt-body');
    const fill = el('i');
    fill.style.width = `${left === null ? 0 : left}%`;
    body.append(fill);
    wrap.append(body, el('span', 'batt-txt', left === null ? '?' : pct(left)), el('span', 'batt-lbl', label));
    return wrap;
  }
  setInterval(refreshStatus, 60000);

  // ----------------------------------------------------------------- stream
  function connect() {
    if (es) es.close();
    es = new EventSource('/api/stream');
    es.onmessage = (msg) => {
      let ev;
      try { ev = JSON.parse(msg.data); } catch (e) { return; }
      if (ev.kind === 'hello') {
        S = fresh();
        CH = freshChat();
        clearFeeds();
        office.reset();
        for (const e of ev.events) apply(e, false);
        applyTeam();
        office.snap();
        markAll();
        schedule();
        return;
      }
      if (!replay) apply(ev, true);
    };
    es.onerror = () => { /* EventSource reconnects on its own; the hello resync covers the gap. */ };
  }

  // ----------------------------------------------------------------- wiring
  $('#composer').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (mode === 'ask') return ask();
    const prompt = $('#prompt').value.trim();
    if (!prompt) { $('#prompt').focus(); toast(tr('toast.need_task'), true); return; }
    if (!settings.demo && !teamOf(settings.team || DEFAULT_TEAM).some((a) => health[a] && health[a].ready)) {
      toast(tr('toast.no_ready'), true);
      openSetup();
      return;
    }
    $('#startBtn').disabled = true;
    try {
      await api('/api/run', { prompt });
      $('#prompt').value = '';
      fitPrompt();
      selectTab('tasks');
    } catch (err) {
      toast(err.message, true);
      $('#startBtn').disabled = false;
    }
  });
  async function ask() {
    const question = $('#prompt').value.trim();
    if (!question) { $('#prompt').focus(); toast(tr('toast.need_question'), true); return; }
    const pick = ($('input[name="to"]:checked') || {}).value || 'both';
    const to = pick === 'both' ? teamOf(settings.team || DEFAULT_TEAM) : [pick];
    $('#startBtn').disabled = true;
    try {
      await api('/api/ask', { question, to });
      $('#prompt').value = '';
      fitPrompt();
      selectTab('chat');
    } catch (err) {
      toast(err.message, true);
      $('#startBtn').disabled = false;
    }
  }
  for (const r of $$('input[name="mode"]')) {
    r.addEventListener('change', () => {
      mode = r.value;
      try { localStorage.setItem('pixelcrew.mode', mode); } catch (e) { /* ignore */ }
      if (mode === 'ask') selectTab('chat');
      schedule();
      $('#prompt').focus();
    });
  }
  $('#newChatBtn').addEventListener('click', async () => {
    try { await api('/api/chat/reset', {}); } catch (err) { toast(err.message, true); }
  });

  // The prompt grows with its content, up to the CSS max-height.
  function fitPrompt() {
    const p = $('#prompt');
    p.style.height = 'auto';
    p.style.height = `${Math.min(p.scrollHeight + 2, 180)}px`;
  }
  $('#prompt').addEventListener('input', fitPrompt);

  $('#prompt').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); $('#composer').requestSubmit(); }
  });
  $('#stopBtn').addEventListener('click', async () => {
    try {
      if (mode === 'ask') await api('/api/chat/cancel', {});
      else await api('/api/cancel', { runId: S.run && S.run.id });
    } catch (err) { toast(err.message, true); }
  });
  $('#approveBtn').addEventListener('click', async () => {
    try { await api('/api/approve', { runId: S.run.id, assignees: overrides, efforts: effortOverrides }); } catch (err) { toast(err.message, true); }
  });
  $('#rejectBtn').addEventListener('click', async () => {
    try { await api('/api/cancel', { runId: S.run && S.run.id }); } catch (err) { toast(err.message, true); }
  });

  for (const inp of $$('[data-setting]')) {
    inp.addEventListener('change', async () => {
      try {
        const data = await api('/api/settings', { [inp.dataset.setting]: inp.checked });
        settings = data.settings;
        renderSettingsUI(); renderLamps();
        dirty.phases = dirty.heads = true; schedule();
      } catch (err) { toast(err.message, true); inp.checked = !inp.checked; }
    });
  }

  // settings dialog
  const sd = $('#settingsDialog');
  function openSettings() {
    const f = $('#settingsForm');
    for (const r of f.querySelectorAll('[name="permissions"]')) r.checked = r.value === settings.permissions;
    f.fixRounds.value = String(settings.fixRounds);
    f.jobTimeoutMin.value = String(settings.jobTimeoutMin);
    f.claudeModel.value = settings.claudeModel || '';
    f.codexModel.value = settings.codexModel || '';
    f.language.value = settings.language || 'auto';
    f.autoUpdate.checked = settings.autoUpdate !== false;
    f.geminiModel.value = settings.geminiModel || '';
    f.grokModel.value = settings.grokModel || '';
    f.everyAgentTask.checked = settings.everyAgentTask !== false;
    f.modelMode.value = settings.modelMode || 'auto';
    for (const a of AGENTS) f[`role-${a}`].value = (settings.roles || {})[a] || (a === 'claude' || a === 'codex' ? 'senior' : 'worker');
    const chosen = settings.team || DEFAULT_TEAM;
    for (const box of f.querySelectorAll('[name="team"]')) {
      box.checked = chosen.includes(box.value);
      const h = health[box.value] || {};
      const opt = box.closest('.team-opt');
      opt.dataset.ready = settings.demo ? 'true' : health[box.value] === undefined ? 'checking' : String(!!h.ready);
      opt.title = h.ready ? tr('team.ready', { name: NAME[box.value], version: h.version ? ` (${h.version})` : '' }) : (h.problem || '');
      const small = opt.querySelector('small');
      small.textContent = h.ready || settings.demo ? small.dataset.vendor : setupPill(box.value)[0];
    }
    f.effortMode.value = settings.effortMode || 'auto';
    f.quotaBalance.checked = settings.quotaBalance !== false;
    for (const o of f.quotaCritical.options) o.textContent = pct(o.value);
    f.quotaCritical.value = String(settings.quotaCritical || 15);
    f.finalCheck.checked = settings.finalCheck !== false;
    $('#settingsError').hidden = true;
    renderTierTable();
    sd.showModal();
  }
  $('#settingsBtn').addEventListener('click', openSettings);
  // categories on the left switch the pane on the right
  function showPane(name) {
    for (const b of $$('.set-nav button')) b.classList.toggle('on', b.dataset.pane === name);
    for (const p of $$('.set-pane')) p.hidden = p.dataset.pane !== name;
  }
  for (const b of $$('.set-nav button')) b.addEventListener('click', () => showPane(b.dataset.pane));
  for (const r of $$('.set-row.radio-row')) {
    r.addEventListener('click', (e) => { const i = r.querySelector('input'); if (e.target !== i) i.checked = true; });
  }
  // Which model each tier uses, per agent; a pinned model replaces the whole row.
  function renderTierTable() {
    const f = $('#settingsForm');
    const box = $('#tierTable');
    box.innerHTML = '';
    box.hidden = f.modelMode.value !== 'auto';
    if (box.hidden) return;
    const head = el('div', 'tier-row head');
    head.append(el('span', '', ''), el('span', '', TIER_NAME.strong), el('span', '', TIER_NAME.standard), el('span', '', TIER_NAME.fast));
    box.append(head);
    for (const a of AGENTS) {
      if (!f.querySelector(`[name="team"][value="${a}"]`).checked) continue;
      const row = el('div', 'tier-row');
      row.dataset.agent = a;
      row.append(el('b', '', NAME[a]));
      const pinned = (f[`${a}Model`].value || '').trim();
      const t = modelTiers[a] || {};
      if (pinned) {
        const all = el('span', 'pinned', tr('team.pinned', { model: pinned }));
        row.append(all);
      } else {
        for (const k of ['strong', 'standard', 'fast']) {
          const c = el('span', '', prettyModel(t[k]) || '-');
          c.title = t[k] || '';
          row.append(c);
        }
      }
      box.append(row);
    }
  }
  for (const n of ['modelMode', 'claudeModel', 'codexModel', 'geminiModel', 'grokModel']) {
    $('#settingsForm').elements[n].addEventListener('input', renderTierTable);
  }
  for (const b of $$('#settingsForm [name="team"]')) b.addEventListener('change', renderTierTable);

  // workspace picker
  const wd = $('#workspaceDialog');
  function renderWorkspaceDialog() {
    const ws = splitPath(settings.workspace);
    $('#wsCurrentName').textContent = ws.name;
    $('#wsCurrentPath').textContent = settings.workspace || '';
    const recents = (settings.recentWorkspaces || []).filter((p) => p !== settings.workspace);
    $('#recentBox').hidden = !recents.length;
    const list = $('#recentList');
    list.innerHTML = '';
    for (const p of recents) {
      const r = splitPath(p);
      const b = el('button');
      b.type = 'button';
      b.title = p;
      const txt = el('span');
      txt.append(el('b', '', r.name), el('small', '', `\u200E${r.parent}\u200E`));
      b.append(el('span', 'ws-icon'), txt);
      b.addEventListener('click', () => useWorkspace(p, false));
      const li = el('li'); li.append(b); list.append(li);
    }
    $('#wsError').hidden = true;
  }
  async function useWorkspace(path, create) {
    try {
      const data = await api('/api/settings', { workspace: path, createWorkspace: create });
      settings = data.settings;
      renderSettingsUI();
      wd.close();
      toast(tr('ws.changed', { name: splitPath(settings.workspace).name }));
    } catch (err) {
      const box = $('#wsError'); box.textContent = err.message; box.hidden = false;
    }
  }
  $('#workspaceBtn').addEventListener('click', () => { renderWorkspaceDialog(); $('#wsManual').value = ''; wd.showModal(); });
  $('#pickFolderBtn').addEventListener('click', async () => {
    const btn = $('#pickFolderBtn');
    btn.disabled = true; btn.textContent = tr('ws.picking');
    try {
      if (native) {
        const path = await nativeAsk({ type: 'pickFolder', start: settings.workspace });
        if (path) await useWorkspace(path, false);
      } else {
        const res = await api('/api/workspace/pick', {});
        if (res.path) await useWorkspace(res.path, false);
      }
    } catch (err) {
      const box = $('#wsError'); box.textContent = err.message; box.hidden = false;
    }
    btn.disabled = false; btn.textContent = tr('ws.pick');
  });
  $('#wsManualBtn').addEventListener('click', () => {
    const v = $('#wsManual').value.trim();
    if (v) useWorkspace(v, true);
  });
  $('#wsManual').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); $('#wsManualBtn').click(); }
  });

  // example prompts in empty states
  for (const b of $$('.examples button')) {
    b.addEventListener('click', () => {
      mode = b.parentElement.dataset.mode === 'ask' ? 'ask' : 'task';
      try { localStorage.setItem('pixelcrew.mode', mode); } catch (e) { /* ignore */ }
      $('#prompt').value = b.textContent;
      fitPrompt();
      schedule();
      $('#prompt').focus();
    });
  }
  $('#settingsForm').addEventListener('submit', async (e) => {
    if (e.submitter && e.submitter.value !== 'save') return;
    e.preventDefault();
    const f = e.target;
    const patch = {
      permissions: (f.querySelector('[name="permissions"]:checked') || {}).value || 'safe',
      fixRounds: Number(f.fixRounds.value), jobTimeoutMin: Number(f.jobTimeoutMin.value),
      claudeModel: f.claudeModel.value, codexModel: f.codexModel.value,
      geminiModel: f.geminiModel.value.trim(), grokModel: f.grokModel.value.trim(),
      language: f.language.value, autoUpdate: f.autoUpdate.checked,
      team: [...f.querySelectorAll('[name="team"]:checked')].map((b) => b.value),
      everyAgentTask: f.everyAgentTask.checked, modelMode: f.modelMode.value,
      roles: Object.fromEntries(AGENTS.map((a) => [a, f[`role-${a}`].value])),
      effortMode: f.effortMode.value, finalCheck: f.finalCheck.checked,
      quotaBalance: f.quotaBalance.checked, quotaCritical: Number(f.quotaCritical.value),
    };
    if (!patch.team.length) {
      const box = $('#settingsError');
      box.textContent = tr('team.empty'); box.hidden = false;
      return;
    }
    try {
      const data = await api('/api/settings', patch);
      if ((settings.language || 'auto') !== data.settings.language) {
        nativePost({ type: 'language', lang: data.language });
        location.reload();
        return;
      }
      settings = data.settings;
      renderSettingsUI();
      applyTeam();
      renderQuota();
      dirty.tasks = dirty.phases = true; schedule();
      sd.close();
      toast(tr('toast.saved'));
    } catch (err) {
      const box = $('#settingsError');
      box.textContent = err.message; box.hidden = false;
    }
  });

  // lamps
  const ld = $('#lampDialog');
  let lampAgent = 'claude';
  function openLamp(a) {
    lampAgent = a;
    const h = health[a] || {};
    $('#lampTitle').textContent = tr('lamp.status', { name: NAME[a] });
    const body = $('#lampBody');
    body.innerHTML = '';
    const info = el('div', 'lamp-info');
    if (h.ready) {
      info.append(el('p', '', tr('lamp.is_ready', { name: NAME[a] })));
      if (h.version) info.append(el('p', '', tr('lamp.version', { v: h.version })));
      if (h.auth) info.append(el('p', '', tr('lamp.auth', { v: h.auth })));
    } else {
      info.append(el('p', '', h.problem || tr('lamp.unknown')));
      info.append(el('p', '', tr('lamp.fallback')));
    }
    if (h.bin) { const p = el('p'); p.append(el('code', '', h.bin)); info.append(p); }
    body.append(info);
    ld.showModal();
  }
  for (const a of AGENTS) $(`#lamp-${a}`).addEventListener('click', () => openLamp(a));
  $('#recheckBtn').addEventListener('click', async () => {
    $('#recheckBtn').disabled = true;
    try { await loadState(true); ld.close(); openLamp(lampAgent); } catch (err) { toast(err.message, true); }
    $('#recheckBtn').disabled = false;
  });


  // ------------------------------------------------------------------ setup
  // First-run screen (and Settings > Sign-in & API keys): per agent, install the CLI,
  // sign in with the subscription or store an API key, and pick the team.
  const VENDOR = { claude: 'Anthropic', codex: 'OpenAI', gemini: 'Google', grok: 'xAI' };
  const TOOL = { claude: 'Claude Code', codex: 'Codex CLI', gemini: 'Antigravity CLI', grok: 'Grok Build' };
  const setupDlg = $('#setupDialog');
  let keychainOK = true;
  let setupShown = false;
  let setupPollTimer = 0;
  let setupPollUntil = 0;
  function setupPill(a) {
    const h = health[a];
    if (!h) return [tr('setup.pill.checking'), 'checking'];
    if (h.ready) return [tr('setup.pill.ready'), 'ready'];
    if (!h.installed) return [tr('setup.pill.missing'), 'missing'];
    if (h.mode === 'key') return [tr('setup.pill.key'), 'todo'];
    if (h.fix) return [tr('setup.pill.login'), 'todo'];
    return [tr('setup.pill.problem'), 'todo'];
  }
  function actionBtn(label, onClick, cls) {
    const b = el('button', `btn small ${cls || ''}`, label);
    b.type = 'button';
    b.addEventListener('click', async () => {
      b.disabled = true;
      try { await onClick(); } catch (err) { toast(err.message, true); }
      b.disabled = false;
    });
    return b;
  }
  function link(label, href) {
    const a = el('a', 'setup-link', label);
    a.href = href; a.target = '_blank'; a.rel = 'noopener';
    return a;
  }
  function renderSetup() {
    const grid = $('#setupGrid');
    grid.innerHTML = '';
    const chosen = settings.team || DEFAULT_TEAM;
    for (const a of AGENTS) {
      const h = health[a] || {};
      const known = health[a] !== undefined;
      const mode = (settings.auth || {})[a] || 'account';
      const [pillText, pillCls] = setupPill(a);
      const card = el('section', 'setup-card');
      card.dataset.agent = a;
      card.dataset.state = pillCls;

      const head = el('header', 'setup-card-head');
      const title = el('div', 'setup-name');
      title.append(el('span', 'dot'), el('b', '', NAME[a]), el('small', '', VENDOR[a]));
      head.append(title, el('span', `setup-pill ${pillCls}`, pillText));
      card.append(head);

      const row = el('div', 'setup-row');
      const seg = el('div', 'seg');
      seg.setAttribute('role', 'group');
      for (const m of ['account', 'key']) {
        const b = el('button', m === mode ? 'on' : '', tr(`setup.mode.${m}`));
        b.type = 'button';
        b.setAttribute('aria-pressed', String(m === mode));
        b.addEventListener('click', () => { if (m !== mode) setAuth(a, m); });
        seg.append(b);
      }
      const teamLbl = el('label', 'check setup-team');
      const teamBox = document.createElement('input');
      teamBox.type = 'checkbox';
      teamBox.checked = chosen.includes(a);
      teamBox.addEventListener('change', () => saveTeam(a, teamBox));
      teamLbl.append(teamBox, el('span', '', tr('setup.in_team')));
      row.append(seg, teamLbl);
      card.append(row);

      const body = el('div', 'setup-body');
      if (known && !h.installed) {
        body.append(el('p', '', tr('setup.missing', { tool: TOOL[a] })));
        const acts = el('div', 'setup-actions');
        if (h.canInstall) acts.append(actionBtn(tr('setup.install'), () => terminal(a, 'install'), 'primary'));
        if (h.docs) acts.append(link(tr('setup.install.guide'), h.docs));
        body.append(acts);
      }
      if (mode === 'account') {
        if (h.installed && h.ready) {
          body.append(el('p', 'ok-line', [tr('setup.signed_in'), h.auth, h.version].filter(Boolean).join(' · ')));
        } else if (h.installed) {
          body.append(el('p', '', h.fix ? tr('setup.login.help', { vendor: VENDOR[a] }) : (h.problem || '')));
          if (h.fix) {
            const acts = el('div', 'setup-actions');
            acts.append(actionBtn(tr('setup.login'), () => terminal(a, 'login'), 'primary'));
            body.append(acts);
          }
        }
      } else {
        if (h.hasKey) {
          const saved = el('div', 'setup-actions');
          saved.append(el('span', 'ok-line', tr('setup.key.saved')),
            actionBtn(tr('setup.key.remove'), () => saveKey(a, ''), 'ghost'));
          body.append(saved);
        } else if (h.ready) body.append(el('p', 'ok-line', tr('setup.key.env')));
        const form = el('div', 'key-form');
        const input = document.createElement('input');
        input.type = 'password';
        input.autocomplete = 'off';
        input.spellcheck = false;
        input.placeholder = tr('setup.key.placeholder', { vendor: VENDOR[a] });
        input.disabled = !keychainOK;
        const save = actionBtn(h.hasKey ? tr('setup.key.replace') : tr('setup.key.save'), async () => {
          const key = input.value.trim();
          if (!key) { input.focus(); return; }
          await saveKey(a, key);
          input.value = '';
        }, 'primary');
        save.disabled = !keychainOK;
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); save.click(); } });
        form.append(input, save);
        body.append(form);
        const foot = el('p', 'setup-small');
        foot.append(document.createTextNode(`${tr('setup.key.billing', { vendor: VENDOR[a] })} `), link(tr('setup.key.get'), h.keyPage || '#'));
        body.append(foot);
        if (known && !h.installed) body.append(el('p', 'setup-small', tr('setup.key.cli_needed', { tool: TOOL[a] })));
      }
      card.append(body);
      grid.append(card);
    }
    const anyReady = AGENTS.some((a) => health[a] && health[a].ready);
    const note = $('#setupNote');
    note.textContent = `${keychainOK ? tr('setup.keys_note') : tr('setup.no_keychain')}${anyReady ? '' : ` ${tr('setup.demo_hint')}`}`;
    $('#setupLanguage').value = settings.language || 'auto';
  }
  function openSetup() {
    $('#setupError').hidden = true;
    renderSetup();
    if (!setupDlg.open) setupDlg.showModal();
  }
  async function setAuth(a, mode) {
    try {
      const data = await api('/api/settings', { auth: Object.assign({}, settings.auth || {}, { [a]: mode }) });
      settings = data.settings;
      renderSetup();
      const h = await api('/api/health');
      health = h.health;
      renderLamps();
    } catch (err) { toast(err.message, true); }
  }
  async function saveTeam(a, box) {
    const chosen = new Set(settings.team || DEFAULT_TEAM);
    if (box.checked) chosen.add(a); else chosen.delete(a);
    if (!chosen.size) { box.checked = true; toast(tr('setup.need_one'), true); return; }
    try {
      const data = await api('/api/settings', { team: AGENTS.filter((x) => chosen.has(x)) });
      settings = data.settings;
      applyTeam(); renderLamps();
      dirty.heads = dirty.phases = true; schedule();
    } catch (err) { box.checked = !box.checked; toast(err.message, true); }
  }
  async function saveKey(a, key) {
    const data = await api('/api/keys', { agent: a, key });
    settings = data.settings;
    health = data.health;
    renderLamps();
    toast(tr(key ? 'setup.key.saved_toast' : 'setup.key.removed_toast', { name: NAME[a] }));
  }
  async function terminal(a, action) {
    await api('/api/terminal', { agent: a, action });
    toast(tr('setup.terminal.opened'));
    // Watch for the install / sign-in to land while the user works in Terminal.
    setupPollUntil = Date.now() + 4 * 60 * 1000;
    clearTimeout(setupPollTimer);
    setupPollTimer = setTimeout(pollSetup, 5000);
  }
  async function pollSetup() {
    if (!setupDlg.open || Date.now() > setupPollUntil) return;
    try { await loadState(true); } catch (e) { /* server busy; try again */ }
    setupPollTimer = setTimeout(pollSetup, 6000);
  }
  async function setLanguage(language) {
    const data = await api('/api/settings', { language });
    nativePost({ type: 'language', lang: data.language });
    location.reload();
  }
  $('#setupLanguage').addEventListener('change', (e) => { setLanguage(e.target.value).catch((err) => toast(err.message, true)); });
  $('#setupRecheckBtn').addEventListener('click', async () => {
    const b = $('#setupRecheckBtn');
    b.disabled = true;
    try { await loadState(true); } catch (err) { toast(err.message, true); }
    b.disabled = false;
  });
  setupDlg.addEventListener('close', async () => {
    clearTimeout(setupPollTimer);
    if (settings.onboarded) return;
    try { settings = (await api('/api/settings', { onboarded: true })).settings; } catch (e) { /* asked again next time */ }
  });
  $('#openSetupBtn').addEventListener('click', () => { sd.close(); openSetup(); });
  $('#lampSetupBtn').addEventListener('click', () => { ld.close(); openSetup(); });

  // ------------------------------------------------------------------ updates
  // The macOS app checks GitHub Releases and pushes its state here; the bar offers the next step.
  let updateState = { state: 'idle' };
  let barDismissed = false;
  function renderUpdate(u) {
    const prev = updateState;
    updateState = u = u || { state: 'idle' };
    u.inFlight = ['downloading', 'verifying'].includes(prev.state);
    if (u.state !== prev.state) barDismissed = false;
    // With Settings open its Version row shows the result; otherwise a toast does.
    if (u.state === 'upToDate' && u.userAsked && !sd.open) toast(tr('update.upToDate', { current: u.current || '' }));
    paintUpdate();
  }
  // Settings > General > Version: a status line and one button for the next step.
  function paintUpdateRow(u, vars) {
    if (u.current) $('#appVersion').textContent = u.current;
    const status = $('#updateStatus'), btn = $('#checkUpdateBtn');
    const row = {
      checking: [tr('update.checking'), '', tr('update.checkNow'), null],
      upToDate: [tr('update.upToDate', vars), 'good', tr('update.checkNow'), 'check'],
      available: [`${tr('update.available', vars)}.`, '', tr('update.install'), 'install'],
      downloading: [tr('update.downloading', vars), '', tr('update.install'), null],
      verifying: [tr('update.verifying'), '', tr('update.install'), null],
      ready: [`${tr('update.ready', vars)}. ${tr('update.ready.detail')}`, 'good', tr('update.restart'), 'restart'],
      manual: [tr('update.manual.detail'), '', tr('update.checkNow'), 'check'],
      failed: [`${tr('update.failed')}: ${u.error || ''}`, 'failed', tr('update.retry'), 'check'],
    }[u.state] || ['', '', tr('update.checkNow'), 'check'];
    status.textContent = row[0];
    status.className = row[1];
    btn.textContent = row[2];
    btn.disabled = !row[3];
    btn.dataset.action = row[3] || '';
    btn.classList.toggle('primary', row[3] === 'install' || row[3] === 'restart');
  }
  function paintUpdate() {
    const u = updateState;
    const bar = $('#updateBar');
    const vars = { version: u.version || '', current: u.current || '', error: u.error || '' };
    paintUpdateRow(u, vars);
    // An open dialog makes the page behind it inert, so the bar waits until it closes.
    const modalOpen = $$('dialog').some((d) => d.open);
    const show = !modalOpen && !barDismissed && {
      checking: u.userAsked, available: true, downloading: true, verifying: true, ready: true, manual: true,
      failed: u.userAsked || u.inFlight,
    }[u.state];
    layer(bar, !!show);
    if (!show) return;
    bar.innerHTML = '';
    bar.className = `update-bar ${['checking', 'downloading', 'verifying'].includes(u.state) ? 'busy' : ''} ${u.state === 'failed' ? 'failed' : ''}`;
    const mark = el('span', 'update-mark');
    for (const c of ['var(--grok)', 'var(--claude)', 'var(--gemini)', 'var(--codex)']) { const i = el('i'); i.style.background = c; mark.append(i); }
    const copy = el('span', 'update-copy');
    copy.append(el('b', '', tr(`update.${u.state}`, vars)));
    const detail = u.state === 'failed' ? u.error : window.I18N.has(`update.${u.state}.detail`) ? tr(`update.${u.state}.detail`, vars) : '';
    if (detail) { const d = el('small', '', detail); d.title = detail; copy.append(d); }
    const actions = el('span', 'update-actions');
    bar.append(mark, copy, actions);
    const send = (action) => nativePost({ type: 'update', action });
    const button = (label, cls, onClick) => {
      const b = el('button', `btn small ${cls}`, label);
      b.type = 'button';
      b.addEventListener('click', onClick);
      actions.append(b);
    };
    if (u.state === 'available') {
      button(tr('update.install'), 'primary', () => send('install'));
      if (u.page) {
        const a = el('a', 'setup-link', tr('update.notes'));
        a.href = u.page; a.target = '_blank'; a.rel = 'noopener';
        actions.append(a);
      }
      button(tr('update.skip'), 'ghost', () => send('skip'));
      button(tr('update.later'), 'ghost', () => send('later'));
    } else if (u.state === 'ready') {
      button(tr('update.restart'), 'primary', () => send('restart'));
      button(tr('update.later'), 'ghost', () => { barDismissed = true; paintUpdate(); });
    } else if (u.state === 'manual') {
      button(tr('update.later'), 'ghost', () => send('later'));
    } else if (u.state === 'failed') {
      button(tr('update.retry'), '', () => send('check'));
      button(tr('update.later'), 'ghost', () => send('later'));
    }
  }
  $('#checkUpdateBtn').addEventListener('click', (e) => {
    const action = e.currentTarget.dataset.action;
    if (action) nativePost({ type: 'update', action });
  });
  // The bar steps aside while any dialog is open (Settings' Version row shows the same) and comes back after.
  const dialogWatch = new MutationObserver(paintUpdate);
  for (const d of $$('dialog')) dialogWatch.observe(d, { attributes: true, attributeFilter: ['open'] });

  // history
  const hd = $('#historyDialog');
  $('#historyBtn').addEventListener('click', async () => {
    const list = $('#historyList');
    list.innerHTML = '';
    try {
      const { runs } = await api('/api/runs');
      if (!runs.length) list.append(el('li', 'empty', tr('hist.empty')));
      for (const r of runs) {
        const li = el('li');
        const b = el('button');
        b.type = 'button';
        const d = new Date(r.created * 1000);
        b.append(
          el('time', '', d.toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })),
          el('span', 'h-prompt', `${r.demo ? '[demo] ' : ''}${r.prompt}`),
          el('span', `h-status ${r.status}`, window.I18N.has(`hist.${r.status}`) ? tr(`hist.${r.status}`) : r.status),
        );
        b.addEventListener('click', () => { hd.close(); openReplay(r.id); });
        li.append(b);
        list.append(li);
      }
    } catch (err) { list.append(el('li', 'empty', err.message)); }
    hd.showModal();
  });

  async function openReplay(id) {
    try {
      const { events } = await api(`/api/runs/${encodeURIComponent(id)}/events`);
      replay = true;
      if (es) { es.close(); es = null; }
      S = fresh();
      clearFeeds();
      office.reset();
      for (const e of events) apply(e, false);
      office.snap();
      markAll();
      selectTab(S.finished ? 'summary' : 'tasks');
      schedule();
    } catch (err) { toast(err.message, true); }
  }
  $('#backLiveBtn').addEventListener('click', () => {
    replay = false;
    connect();
    markAll(); schedule();
  });

  // menu commands from the macOS app
  Object.assign(menuActions, {
    newTask() { mode = 'task'; schedule(); $('#prompt').focus(); },
    ask() { mode = 'ask'; selectTab('chat'); schedule(); $('#prompt').focus(); },
    settings() { openSettings(); },
    setup() { openSetup(); },
    workspace() { $('#workspaceBtn').click(); },
    history() { $('#historyBtn').click(); },
    stop() { if (!$('#stopBtn').hidden) $('#stopBtn').click(); },
  });

  // ------------------------------------------------------------------ boot
  clearFeeds();
  loadState(false).then(refreshStatus).catch((err) => toast(err.message, true));
  connect();
  schedule();
})();
