import { LANGS, pickLanguage, setLanguage, t } from './i18n.js';
import { esc, renderMarkdown } from './markdown.js';

const $ = s => document.querySelector(s);
const api = (path, body) => fetch('/api/' + path, body === undefined ? {} :
  { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json());
const ICON = { clarify: '?', correct: '✗', confirm: '✓', add: '+', answer: 'A' };
const SVG = {
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 4.8v14.4a1 1 0 0 0 1.5.86l12-7.2a1 1 0 0 0 0-1.72l-12-7.2A1 1 0 0 0 6 4.8z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2.5"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  expand: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17 17 7"/><path d="M8 7h9v9"/></svg>',
  pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16h14v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>',
};

let state = { listening: false, settings: {}, boot: null, metricsOpen: false };

// ----------------------------------------------------------------- helpers
const fmt = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
const clock = ts => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const sec = v => v == null ? '—' : v < 10 ? v.toFixed(1) : Math.round(v);
const debounce = (fn, ms) => { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; };

function status(text) { $('#status').textContent = text; }

// ----------------------------------------------------------------- thread language
// Value is the English language name sent to the LLM prompt; label is the native name.
const THREAD_LANGS = [['Ukrainian', 'Українська'], ['English', 'English'], ['Russian', 'Русский'],
  ['Polish', 'Polski'], ['German', 'Deutsch'], ['Spanish', 'Español'], ['French', 'Français'],
  ['Italian', 'Italiano'], ['Portuguese', 'Português'], ['Czech', 'Čeština'], ['Turkish', 'Türkçe']];
function fillThreadLangs() {
  for (const sel of [$('#threadLang'), $('#threadLangSetting')]) {
    const cur = sel.value || state.settings.notes_language || 'auto';
    sel.innerHTML = '';
    sel.add(new Option(t('notes_auto'), 'auto'));
    for (const [v, label] of THREAD_LANGS) sel.add(new Option(label, v));
    sel.value = cur;
  }
}
$('#threadLang').onchange = async e => {
  const r = await api('settings', { notes_language: e.target.value });
  state.settings = r.settings;
  api('refresh', {});                     // immediately refresh gist and notes in the new language
};

const PRESETS = ['auto', 'interview', 'lecture', 'meeting', 'media'];
function fillPresets() {
  for (const sel of [$('#preset'), $('#presetSetting')]) {
    const cur = sel.value || state.settings.preset || 'auto';
    sel.innerHTML = '';
    for (const p of PRESETS) sel.add(new Option(t('preset_' + p), p));
    sel.value = PRESETS.includes(cur) ? cur : 'auto';
  }
}
$('#preset').onchange = async e => {
  const p = e.target.value;
  const payload = { preset: p };
  if (p === 'media') payload.answer_questions = false;
  else if (p === 'interview' || p === 'lecture' || p === 'meeting') payload.answer_questions = true;
  const r = await api('settings', payload);
  state.settings = r.settings;
  setQA(r.settings.answer_questions);
  $('#preset').value = r.settings.preset || 'auto';
  api('refresh', {});
};

// ----------------------------------------------------------------- i18n
function applyLanguage(setting) {
  const code = setLanguage(pickLanguage(setting));
  if ($('#uiLang')) $('#uiLang').value = setting && setting !== 'auto' ? setting : code;
  setListening(state.listening);
  fillThreadLangs();
  fillPresets();
  localStorage.setItem('lt.lang', setting || 'auto');
}
for (const sel of [$('#uiLang'), $('#uiLangSetting')].filter(Boolean)) {
  if (sel.id === 'uiLangSetting') sel.add(new Option('auto', 'auto'));
  for (const [code, name] of Object.entries(LANGS)) sel.add(new Option(name, code));
}
if ($('#uiLang')) $('#uiLang').onchange = e => { applyLanguage(e.target.value); api('settings', { ui_language: e.target.value }); };

// ----------------------------------------------------------------- thread rendering
function syncPinnedBox() {
  const p = $('#pinned');
  p.hidden = p.children.length === 0;
  document.querySelectorAll('#topics .topic').forEach(tp => {
    if (tp.dataset.placeholder === '1') {
      const hasNotes = tp.querySelector('.notes')?.children.length > 0;
      const hasBrief = !tp.querySelector('.brief')?.hidden;
      tp.hidden = !hasNotes && !hasBrief;
    }
  });
}
function topicEl(id, title, revive = false) {
  $('#empty')?.remove();
  let el = document.getElementById('topic-' + id);
  if (el && (!revive || el === $('#topics').firstElementChild)) return el;
  document.querySelectorAll('.topic').forEach(x => x.classList.add('old'));
  if (el) { el.classList.remove('old'); $('#topics').prepend(el); return el; }
  el = document.createElement('section');
  el.className = 'topic'; el.id = 'topic-' + id;
  if (!title || title === '—') el.dataset.placeholder = '1';
  const expBtn = title && title !== '—'
    ? `<button type="button" class="topic-exp" title="${esc(t('expand_topic'))}" aria-label="${esc(t('expand_topic'))}">${SVG.expand}</button>` : '';
  el.innerHTML = `<h2><span class="dot"></span><span>${esc(title)}</span>${expBtn}</h2><div class="brief" hidden></div><div class="notes"></div>`;
  const btn = el.querySelector('.topic-exp');
  if (btn) btn.onclick = () => api('ask', { question: title });
  $('#topics').prepend(el);
  return el;
}
function setBrief(id, items) {
  const el = document.getElementById('topic-' + id);
  if (!el || !items?.length) return;
  const b = el.querySelector('.brief');
  b.hidden = false;
  b.innerHTML = '<ul>' + items.map(i => `<li>${fmt(i)}</li>`).join('') + '</ul>';
}
async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = Object.assign(document.createElement('textarea'), { value: text });
    document.body.append(ta); ta.select(); document.execCommand('copy'); ta.remove();
  }
  const prev = btn.innerHTML;
  btn.innerHTML = SVG.check; btn.classList.add('ok'); btn.title = t('copied');
  setTimeout(() => { btn.innerHTML = prev; btn.classList.remove('ok'); btn.title = t('copy'); }, 1500);
}
function noteRawMd(n) {
  return (n.question ? `**${n.question}**\n\n` : '') + (n.text || '');
}
function addNote(id, n, fresh = true) {
  if (n.id != null && document.getElementById('note-' + n.id)) {
    updateNote(n);
    return;
  }
  const el = topicEl(id, '—');
  const d = document.createElement('div');
  d.className = `note ${n.kind}` + (fresh ? ' fresh' : '') + (n.streaming ? ' streaming' : '') + (n.pinned ? ' pinned' : '');
  if (n.id != null) d.id = 'note-' + n.id;
  d.dataset.topic = String(id);
  d.dataset.raw = noteRawMd(n);
  d.dataset.t = String(n.t ?? (Date.now() / 1000));
  const lat = n.e2e != null ? ` · ${sec(n.e2e)} s` : '';
  const body = n.kind === 'answer'
    ? `<div class="qa-head"><div class="qa-q">${esc(n.question || '')}</div></div>` +
      `<div class="md">${renderMarkdown(n.text)}</div>`
    : fmt(n.text);
  const acts = n.id != null ? (
    '<div class="note-acts">' +
    (n.kind === 'answer'
      ? `<button type="button" class="note-act act-copy" title="${esc(t('copy'))}">${SVG.copy}</button>`
      : `<button type="button" class="note-act act-exp" title="${esc(t('expand_note'))}">${SVG.expand}</button>`) +
    `<button type="button" class="note-act act-pin${n.pinned ? ' on' : ''}" title="${esc(t(n.pinned ? 'unpin_note' : 'pin_note'))}">${SVG.pin}</button>` +
    `<button type="button" class="note-act act-del del" title="${esc(t('dismiss_note'))}">${SVG.close}</button>` +
    '</div>'
  ) : '';
  d.innerHTML = `<div class="ic" title="${esc(t('kind_' + n.kind))}">${ICON[n.kind] || '+'}</div>` +
    `<div class="t">${body}</div><div class="meta"><span class="meta-t">${clock(Number(d.dataset.t))}${lat}</span>${acts}</div>`;
  const copyBtn = d.querySelector('.act-copy');
  if (copyBtn) copyBtn.onclick = () => copyText(d.dataset.raw || '', copyBtn);
  const expBtn = d.querySelector('.act-exp');
  if (expBtn) expBtn.onclick = () => api('ask', { question: String(n.text || '').replace(/\*\*/g, '') });
  const pinBtn = d.querySelector('.act-pin');
  if (pinBtn) pinBtn.onclick = () => api('note/pin', { id: n.id });
  const delBtn = d.querySelector('.act-del');
  if (delBtn) delBtn.onclick = () => api('note/dismiss', { id: n.id });
  if (n.pinned) {
    $('#pinned').prepend(d);
  } else {
    el.querySelector('.notes').prepend(d);
  }
  syncPinnedBox();
  updateCounts();
}
function updateNote(ev) {
  const d = ev.id != null ? document.getElementById('note-' + ev.id) : null;
  if (!d) return;
  if (ev.question != null) {
    const qEl = d.querySelector('.qa-q');
    if (qEl) qEl.textContent = ev.question;
  }
  if (ev.text != null) {
    const mdEl = d.querySelector('.md');
    if (mdEl) mdEl.innerHTML = renderMarkdown(ev.text);
    d.dataset.raw = noteRawMd({ question: ev.question ?? d.querySelector('.qa-q')?.textContent, text: ev.text });
  }
  if (ev.streaming !== undefined) d.classList.toggle('streaming', !!ev.streaming);
  if (ev.e2e != null) {
    const mt = d.querySelector('.meta-t');
    if (mt) mt.textContent = `${clock(Number(d.dataset.t))} · ${sec(ev.e2e)} s`;
  }
}
function applyPin(id, pinned) {
  const d = document.getElementById('note-' + id);
  if (!d) return;
  d.classList.toggle('pinned', !!pinned);
  const btn = d.querySelector('.act-pin');
  if (btn) {
    btn.classList.toggle('on', !!pinned);
    btn.title = t(pinned ? 'unpin_note' : 'pin_note');
  }
  if (pinned) {
    $('#pinned').prepend(d);
  } else {
    const tp = document.getElementById('topic-' + d.dataset.topic);
    if (tp) tp.querySelector('.notes').prepend(d);
  }
  syncPinnedBox();
}
function retractNote(id) {
  document.getElementById('note-' + id)?.remove();
  syncPinnedBox();
  updateCounts();
}
// ----------------------------------------------------------------- note-kind filters (legend)
const KINDS = ['clarify', 'correct', 'confirm', 'add', 'answer'];
function updateCounts() {
  for (const k of KINDS)
    document.querySelector(`.kf[data-kind="${k}"] i`).textContent = document.querySelectorAll(`#thread .note.${k}`).length;
}
function setKindVisible(kind, on) {
  document.body.classList.toggle('hide-' + kind, !on);
  document.querySelector(`.kf[data-kind="${kind}"]`).setAttribute('aria-pressed', on);
}
const hidden = new Set(JSON.parse(localStorage.getItem('lt.hiddenKinds') || '[]'));
hidden.forEach(k => setKindVisible(k, false));
$('#legend').onclick = e => {
  const b = e.target.closest('.kf'); if (!b) return;
  const k = b.dataset.kind, on = hidden.has(k);
  on ? hidden.delete(k) : hidden.add(k);
  setKindVisible(k, on);
  localStorage.setItem('lt.hiddenKinds', JSON.stringify([...hidden]));
};

const live = [];
function renderLive() {
  const el = $('#live');
  const expanded = el.classList.contains('expanded');
  const rows = expanded ? live : live.slice(-3);
  el.innerHTML = rows.map(x => `<div><b>${esc(x.spk)}</b> ${esc(expanded ? x.text : x.text.slice(-220))}</div>`).join('');
  if (expanded) el.scrollTop = el.scrollHeight;
}
function showLine(ev) {
  if (ev.merged && live.length && live.at(-1).spk === ev.spk) live.at(-1).text = ev.text;
  else live.push({ spk: ev.spk, text: ev.text });
  while (live.length > 15) live.shift();
  renderLive();
}
$('#live').onclick = () => { $('#live').classList.toggle('expanded'); renderLive(); };
function setGist(text) {
  if ($('#gistText').textContent === text) return;
  $('#gistText').textContent = text;
  const g = $('#gist'); g.classList.remove('fresh'); void g.offsetWidth; g.classList.add('fresh');
}
const TYPE_ICON = { tech_interview: '💼', tech_talk: '💻', lecture: '🎓', podcast: '🎙️', news: '📰', film: '🎬',
  cartoon: '🧸', series: '📺', sports: '⚽', meeting: '👥', chat: '💬', music: '🎵', other: '🔎' };
function setProfile(p) {
  if (!p || !p.type) { $('#profile').hidden = true; return; }
  $('#profile').hidden = false;
  $('#profileIcon').textContent = TYPE_ICON[p.type] || '🔎';
  $('#profileType').textContent = t('ct_' + p.type);
  $('#profileTitle').textContent = p.title || '';
  $('#profileWho').textContent = p.participants || '';
  $('#profile').title = [p.languages, p.focus].filter(Boolean).join(' — ');
  state.profile = p;
}
function setListening(on) {
  state.listening = on;
  if (state.profile) setProfile(state.profile);    // re-translate content type label after UI language change
  const b = $('#btnListen');
  $('#listenIc').innerHTML = on ? SVG.stop : SVG.play;
  $('#listenLbl').textContent = on ? t('stop') : t('listen');
  b.classList.toggle('on', on); b.classList.toggle('primary', !on);
  if (!on) $('#lv').style.width = 0;
}
function resetView() {
  $('#pinned').innerHTML = '';
  syncPinnedBox();
  $('#topics').innerHTML = `<div class="empty" id="empty">${t('emptyReset')}</div>`;
  $('#gistText').textContent = '—'; live.length = 0; $('#live').innerHTML = ''; setProfile(null); updateCounts();
}

// ----------------------------------------------------------------- history panel
let sessionsCache = [], activeSession = null;
function fmtSize(b) { return b < 1024 ? `${b} B` : `${(b / 1024).toFixed(1)} KB`; }
function renderHistoryList() {
  const q = ($('#histSearch').value || '').trim().toLowerCase();
  const list = sessionsCache.filter(s => !q || s.name.toLowerCase().includes(q) || (s.title || '').toLowerCase().includes(q));
  const ul = $('#histList');
  if (!list.length) {
    ul.innerHTML = `<li class="hint" style="padding:8px">${esc(t('h_empty'))}</li>`;
    return;
  }
  ul.innerHTML = list.map(s =>
    `<li class="hist-item${activeSession?.name === s.name ? ' active' : ''}" data-name="${esc(s.name)}">` +
    `<b>${esc(s.name.replace(/\.md$/, '').replace('_', ' '))} · ${fmtSize(s.size)}</b>` +
    `<span>${esc(s.title || '—')}</span></li>`).join('');
}
async function loadHistory() {
  const r = await api('sessions');
  sessionsCache = r.sessions || [];
  renderHistoryList();
}
async function openSession(name) {
  const r = await api('sessions/' + encodeURIComponent(name));
  if (!r || r.error) return;
  activeSession = r;
  $('#histView').hidden = false;
  $('#histName').textContent = r.name;
  $('#histBody').innerHTML = renderMarkdown(r.content || '');
  renderHistoryList();
}
$('#histSearch').oninput = renderHistoryList;
$('#histList').onclick = e => {
  const li = e.target.closest('.hist-item');
  if (li) openSession(li.dataset.name);
};
$('#btnHistCopy').onclick = () => activeSession && copyText(activeSession.content || '', $('#btnHistCopy'));
$('#btnHistDl').onclick = () => {
  if (!activeSession) return;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([activeSession.content || ''], { type: 'text/markdown;charset=utf-8' }));
  a.download = activeSession.name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};
$('#btnHistDel').onclick = async () => {
  if (!activeSession || !confirm(t('confirm_delete', { name: activeSession.name }))) return;
  await api('sessions/delete', { name: activeSession.name });
  activeSession = null;
  $('#histView').hidden = true;
  loadHistory();
};
$('#btnHistory').onclick = () => {
  const open = $('#history').hidden;
  if (open && state.metricsOpen) {
    state.metricsOpen = false;
    $('#metrics').hidden = true;
    $('#btnMetrics').setAttribute('aria-expanded', false);
    localStorage.setItem('lt.metrics', '');
  }
  $('#history').hidden = !open;
  $('#btnHistory').setAttribute('aria-expanded', open);
  if (open) loadHistory();
};

// ----------------------------------------------------------------- metrics panel
function renderMetrics(m) {
  if (!state.metricsOpen) return;
  const tm = m.timings || {};
  const pp = k => tm[k] && tm[k].count ? `${sec(tm[k].p50)} / ${sec(tm[k].p95)}` : '—';
  const c = m.counters || {};
  const total = o => Object.values(o || {}).reduce((a, b) => a + b, 0);
  const tiles = [
    ['m_e2e', pp('e2e_note'), (tm.e2e_note?.p50 ?? 0) > 6],
    ['m_qa', pp('e2e_qa'), (tm.e2e_qa?.p50 ?? 0) > 6],
    ['m_stt', pp('stt_lag'), (tm.stt_lag?.p50 ?? 0) > 4],
    ['m_llm', pp('llm_note')], ['m_brief', pp('brief')],
    ['m_rate', m.notes_per_min ?? 0], ['m_notes', total(c.notes)],
    ['m_topics', total(c.topics)], ['m_requests', total(c.llm_requests)],
    ['m_queue', m.gauges?.stt_queue ?? 0, (m.gauges?.stt_queue ?? 0) >= 3],
    ['m_errors', total(c.stt_errors) + total(c.llm_errors), total(c.stt_errors) + total(c.llm_errors) > 0],
    ['m_uptime', fmtUptime(m.uptime)],
  ];
  $('#tiles').innerHTML = tiles.map(([k, v, warn]) =>
    `<div class="tile${warn ? ' warn' : ''}"><div class="v">${esc(v)}</div><div class="l">${esc(t(k))}</div></div>`).join('');
  const rej = c.notes_rejected || {};
  $('#rejected').innerHTML = Object.entries(rej).map(([k, v]) =>
    `<li><span>${esc(t('r_' + k.replace('reason=', '')))}</span><b>${v}</b></li>`).join('') || '<li><span>—</span></li>';
}
function fmtUptime(s) {
  if (s == null) return '—';
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return h ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
}
$('#btnMetrics').onclick = async () => {
  state.metricsOpen = !state.metricsOpen;
  if (state.metricsOpen && !$('#history').hidden) {
    $('#history').hidden = true;
    $('#btnHistory').setAttribute('aria-expanded', false);
  }
  $('#metrics').hidden = !state.metricsOpen;
  $('#btnMetrics').setAttribute('aria-expanded', state.metricsOpen);
  localStorage.setItem('lt.metrics', state.metricsOpen ? '1' : '');
  if (state.metricsOpen) renderMetrics(await api('metrics'));
};

// ----------------------------------------------------------------- window controls (KDE)
const pushWindow = debounce(() => api('settings', {
  window_opacity: $('#opacity').value / 100, keep_above: $('#btnPin').getAttribute('aria-pressed') === 'true' }), 150);
$('#opacity').oninput = pushWindow;
$('#btnPin').onclick = () => {
  const b = $('#btnPin'); b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') !== 'true'); pushWindow();
};

// ----------------------------------------------------------------- events
async function syncState() {
  const s = await api('state');
  state.settings = s.settings; state.boot = s.boot;
  setListening(s.listening);
  setQA(s.settings.answer_questions);
  $('#threadLang').value = s.settings.notes_language || 'auto';
  $('#preset').value = s.settings.preset || 'auto';
  if (s.gist) setGist(s.gist);
  setProfile(s.profile);
  for (const tp of s.topics) { topicEl(tp.id, tp.title); setBrief(tp.id, tp.brief); tp.notes.forEach(n => addNote(tp.id, n, false)); }
  live.length = 0;
  s.transcript.slice(-15).forEach(x => live.push({ spk: x.spk, text: x.text }));
  renderLive();
  return s;
}

function connect() {
  let disconnected = false;
  const es = new EventSource('/api/events');
  es.onerror = () => { disconnected = true; status(t('reconnecting')); };
  es.onmessage = e => {
    const ev = JSON.parse(e.data);
    switch (ev.type) {
      case 'hello':                         // reload on server restart; otherwise resync missed events
        if (state.boot && state.boot !== ev.boot) { location.reload(); break; }
        state.boot = ev.boot;
        if (disconnected) { disconnected = false; status(''); syncState(); }
        break;
      case 'line': showLine(ev); status(t('st_stt', { lag: sec(ev.lag) })); break;
      case 'gist': setGist(ev.text); break;
      case 'profile': setProfile(ev.profile); break;
      case 'topic': topicEl(ev.id, ev.title, !!ev.revived); break;
      case 'brief': setBrief(ev.topic, ev.items); break;
      case 'note': addNote(ev.topic, ev); break;
      case 'note_update': updateNote(ev); break;
      case 'note_pin': applyPin(ev.id, ev.pinned); break;
      case 'retract': retractNote(ev.id); break;
      case 'tick': status(t('st_tick', { took: sec(ev.took), result: t('res_' + ev.result) })); break;
      case 'levels': $('#lv').style.width = (ev.level * 100) + '%'; break;
      case 'listening': setListening(ev.on); break;
      case 'settings':
        state.settings = ev.settings;
        setQA(ev.settings.answer_questions);
        $('#preset').value = ev.settings.preset || 'auto';
        break;
      case 'metrics': renderMetrics(ev); break;
      case 'status': status(t(ev.code, ev.params || {})); break;
      case 'reset': resetView(); break;
    }
  };
}

// ----------------------------------------------------------------- buttons & shortcuts
function setQA(on) { $('#btnQA').setAttribute('aria-pressed', !!on); }
$('#btnQA').onclick = async () => {
  const next = !state.settings.answer_questions;
  const r = await api('settings', { answer_questions: next });
  state.settings = r.settings;
  setQA(r.settings.answer_questions);
  if (next) api('refresh', {});
};
$('#askForm').onsubmit = async e => {
  e.preventDefault();
  const inp = $('#askInput');
  const q = inp.value.trim();
  inp.value = '';
  await api('ask', q ? { question: q } : {});
};
$('#btnSummary').onclick = () => api('summary', {});
$('#btnListen').onclick = () => api(state.listening ? 'stop' : 'start', {});
$('#btnNow').onclick = () => api('refresh', {});
$('#btnReset').onclick = () => api('reset', {});

document.addEventListener('keydown', e => {
  if (dlg.open) return;
  const inField = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '');
  if (e.key === 'Escape') {
    if (inField) document.activeElement.blur();
    else if (!$('#history').hidden) $('#btnHistory').click();
    else if (state.metricsOpen) $('#btnMetrics').click();
    return;
  }
  if (!inField && !e.altKey && !e.ctrlKey && !e.metaKey && e.key === '/') {
    e.preventDefault();
    $('#askInput').focus();
    return;
  }
  if (!e.altKey || e.ctrlKey || e.metaKey) return;
  const k = e.key.toLowerCase();
  if (k === 'l') { e.preventDefault(); $('#btnListen').click(); }
  else if (k === 'q') { e.preventDefault(); $('#btnQA').click(); }
  else if (k === 'a') { e.preventDefault(); api('ask', {}); }
  else if (k === 's') { e.preventDefault(); $('#btnSummary').click(); }
  else if (k === 'h') { e.preventDefault(); $('#btnHistory').click(); }
});

// ----------------------------------------------------------------- settings dialog
const dlg = $('#dlg'), form = $('#form');
const SECRETS = ['llm_api_key', 'stt_token'];
function fillDeviceSelect(sel, list, current) {
  sel.innerHTML = '';
  sel.add(new Option(t('dev_default'), ''));
  for (const d of list || []) sel.add(new Option(d.label, d.name));
  if (current && ![...sel.options].some(o => o.value === current)) sel.add(new Option(current, current));
  sel.value = current || '';
}
$('#btnSettings').onclick = async () => {
  const [st, devs] = await Promise.all([api('state'), api('audio-devices').catch(() => ({ monitors: [], mics: [] }))]);
  const s = st.settings;
  state.settings = s;
  fillDeviceSelect($('#monDev'), devs.monitors, s.monitor_device);
  fillDeviceSelect($('#micDev'), devs.mics, s.mic_device);
  for (const el of form.elements) {
    if (!el.name) continue;
    if (SECRETS.includes(el.name)) { el.value = ''; el.placeholder = s[el.name + '_set'] ? t('secret_set') : ''; continue; }
    el.value = String(s[el.name] ?? '');
  }
  dlg.showModal();
};
dlg.addEventListener('close', async () => {
  if (dlg.returnValue !== 'save') return;
  const b = {};
  for (const el of form.elements) if (el.name) b[el.name] = el.value;
  const r = await api('settings', b);
  state.settings = r.settings;
  applyLanguage(r.settings.ui_language);
  $('#threadLang').value = r.settings.notes_language;
  $('#preset').value = r.settings.preset || 'auto';
  setQA(r.settings.answer_questions);
  status(t('saved'));
});

// ----------------------------------------------------------------- start
(async () => {
  applyLanguage(localStorage.getItem('lt.lang') || 'auto');
  const s = await syncState();
  applyLanguage(s.settings.ui_language);
  $('#threadLang').value = s.settings.notes_language || 'auto';
  $('#preset').value = s.settings.preset || 'auto';
  $('#winctl').hidden = !s.window_control;
  $('#winNa').hidden = s.window_control;
  $('#opacity').value = Math.round((s.settings.window_opacity ?? 1) * 100);
  $('#btnPin').setAttribute('aria-pressed', !!s.settings.keep_above);
  if (localStorage.getItem('lt.metrics')) $('#btnMetrics').click();
  if (!s.settings.llm_model) status(t('no_model'));
  connect();
})();
