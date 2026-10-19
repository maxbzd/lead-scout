// Точка входа: вход по паролю, вкладки, живые обновления, общий обработчик нажатий.

import { api, on } from './api.js';
import { S, cache, loadProfile, loadStatus } from './state.js';
import { $, $$, closeSheet, debounce, html, I, onPopState, paint, sheetOpen, toast } from './ui.js';
import { chatActions } from './views/chats.js';
import { connectActions } from './views/connect.js';
import { detailActions, openOrder, priceActions } from './views/detail.js';
import { feedActions, onNewOrders, refreshFeed, renderFeed, updateScanPill } from './views/feed.js';
import { onProfileInput, profileActions, renderProfile } from './views/profile.js';
import { renderStats, statsActions } from './views/stats.js';

const TABS = ['feed', 'stats', 'me'];

// ------------------------------------------------------------------ вход ----
function showLogin(message = '') {
  closeSheet();
  $('#app').hidden = true;
  $('#tabbar').hidden = true;
  $('#boot').hidden = true;
  const box = $('#login');
  box.hidden = false;
  paint(box, html`
    <form class="login-card" id="login-form" autocomplete="on">
      <div class="orb sm" aria-hidden="true"><div class="core"></div></div>
      <h1>Scout</h1>
      <p class="lead-p">Личный скаут заказов. Введите пароль, чтобы войти.</p>
      <input type="text" name="username" value="scout" autocomplete="username" hidden>
      <label class="lbl" for="pw">Пароль</label>
      <input class="inp" id="pw" type="password" autocomplete="current-password" required data-autofocus>
      <p class="form-err" id="login-err" role="alert">${message}</p>
      <button class="btn pri full" type="submit" id="login-go">Войти</button>
      <p class="mono note-sm">Пароль лежит в файле <b>data/password.txt</b> на вашем сервере или задан в настройке LS_PASSWORD.</p>
    </form>`);
  setTimeout(() => $('#pw')?.focus(), 80);
}

async function submitLogin(e) {
  e.preventDefault();
  const btn = $('#login-go');
  const err = $('#login-err');
  err.textContent = '';
  btn.disabled = true;
  try {
    await api('/login', { method: 'POST', body: { password: $('#pw').value } });
    $('#login').hidden = true;
    await start();
  } catch (x) {
    err.textContent = x.message;
    btn.disabled = false;
    $('#pw').select();
  }
}

// ------------------------------------------------------------- вкладки ----
async function show(tab, { push = true } = {}) {
  if (!TABS.includes(tab)) tab = 'feed';
  S.tab = tab;
  $$('.tab').forEach((t) => t.setAttribute('aria-selected', String(t.dataset.v === tab)));
  $('#search-btn').hidden = tab !== 'feed';
  if (tab !== 'feed') $('#sbar').hidden = true;
  if (push && !location.hash.startsWith('#/o/')) history.replaceState(null, '', tab === 'feed' ? location.pathname : `#/${tab}`);
  window.scrollTo(0, 0);
  if (tab === 'feed') {
    renderFeed();
    await refreshFeed({ anim: true });
  } else if (tab === 'stats') {
    await renderStats();
  } else {
    await renderProfile();
  }
}

function routeFromHash() {
  const h = location.hash;
  const m = h.match(/^#\/o\/(\d+)/);
  if (m) return { tab: 'feed', order: +m[1] };
  const t = h.replace(/^#\//, '');
  return { tab: TABS.includes(t) ? t : 'feed' };
}

// ------------------------------------------------------------ живое ----
let es = null;
let freshTimer = null;

function connectStream() {
  if (es) return;
  es = new EventSource('/api/stream');
  es.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch { return; }
    handleEvent(ev);
  };
  es.onopen = () => { if (!S.online) { S.online = true; refreshSoft(); } };
  es.onerror = () => { /* браузер переподключится сам */ };
}

function handleEvent(ev) {
  if (ev.type === 'scan') {
    if (S.status) S.status.scan.running = ev.running;
    updateScanPill();
    if (!ev.running) refreshSoft().then(() => ev.ids?.length && onNewOrders(ev.ids));
  } else if (ev.type === 'orders') {
    if (!sheetOpen()) {
      clearTimeout(freshTimer);
      freshTimer = setTimeout(refreshSoft, 400);
    }
  } else if (ev.type === 'status' || ev.type === 'chats') {
    loadStatus().then(() => { updateScanPill(); if (S.tab === 'feed' && !sheetOpen()) refreshFeed(); }).catch(() => {});
  }
}

async function refreshSoft() {
  await loadStatus().catch(() => {});
  updateScanPill();
  if (sheetOpen()) return;
  if (S.tab === 'feed') await refreshFeed();
  else if (S.tab === 'stats') { S.stats = null; await renderStats(); }
}

// ------------------------------------------------------ нажатия и ввод ----
const ACTIONS = {
  ...feedActions, ...detailActions, ...priceActions, ...connectActions, ...chatActions, ...profileActions, ...statsActions,
  close: () => closeSheet(),
  tab: (el) => show(el.dataset.v),
  veil: (el, e) => { if (e.target === el) closeSheet(); },
  search: (el) => {
    const bar = $('#sbar');
    bar.hidden = !bar.hidden;
    el.setAttribute('aria-pressed', String(!bar.hidden));
    if (!bar.hidden) $('#q').focus();
    else if (S.q) { S.q = ''; $('#q').value = ''; refreshFeed(); }
  },
  newbar: (el) => {
    el.classList.remove('on');
    if (el.dataset.id) openOrder(+el.dataset.id);
  },
};

function onClick(e) {
  const el = e.target.closest('[data-act]');
  if (!el || el.disabled) return;
  const fn = ACTIONS[el.dataset.act];
  if (!fn) return;
  if (el.tagName === 'A') e.preventDefault();
  Promise.resolve(fn(el, e)).catch((x) => toast(x?.message || 'Не получилось. Попробуйте ещё раз'));
}

function onKey(e) {
  if (e.key === 'Escape' && sheetOpen()) { closeSheet(); return; }
  const t = e.target;
  if (e.key === 'Enter' && !e.shiftKey && t.tagName === 'INPUT') {
    const named = t.dataset.enter;
    if (named && ACTIONS[named]) { e.preventDefault(); ACTIONS[named](t, e); return; }
    if (t.closest('#sheet') && t.type !== 'range') {
      const go = $('#sheet .btn.pri[data-act]');
      if (go) { e.preventDefault(); go.click(); }
    }
  } else if ((e.key === 'Enter' || e.key === ' ') && t.matches?.('.row1[data-act]')) {
    e.preventDefault();
    t.click();
  }
}

const search = debounce(() => { if (S.tab === 'feed') refreshFeed({ anim: true }); }, 280);

function onInput(e) {
  const t = e.target;
  if (t.id === 'q') { S.q = t.value; search(); return; }
  if (t.dataset.bind) onProfileInput(t);
}

// Свайп вниз за верх шторки закрывает её
function enableSwipeClose() {
  const sheet = $('#sheet');
  let y0 = null;
  let dy = 0;
  sheet.addEventListener('touchstart', (e) => {
    y0 = sheet.scrollTop <= 0 && e.touches.length === 1 ? e.touches[0].clientY : null;
    dy = 0;
  }, { passive: true });
  sheet.addEventListener('touchmove', (e) => {
    if (y0 === null) return;
    dy = e.touches[0].clientY - y0;
    if (dy > 0) sheet.style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  sheet.addEventListener('touchend', () => {
    if (y0 === null) return;
    sheet.style.transform = '';
    if (dy > 110) closeSheet();
    y0 = null;
  });
}

// -------------------------------------------------------------- запуск ----
let started = false;

async function start() {
  try {
    try {
      const { authed } = await api('/session');
      if (!authed) return showLogin();
    } catch (e) {
      if (e.status !== 0 || !cache.read('status')) throw e; // без связи открываем то, что сохранилось
      S.online = false;
    }
    await loadStatus();
  } catch (e) {
    if (e.status === 401) return showLogin();
    $('#boot').hidden = true;
    toast(e.message);
    return;
  }
  loadProfile().catch(() => {});
  $('#boot').hidden = true;
  $('#login').hidden = true;
  $('#app').hidden = false;
  $('#tabbar').hidden = false;
  const r = routeFromHash();
  S.tab = r.tab;
  await show(r.tab, { push: false });
  updateScanPill();
  connectStream();
  if (r.order) {
    history.replaceState(null, '', location.pathname);
    openOrder(r.order);
  }
  started = true;
}

function boot() {
  $$('[data-i]').forEach((el) => { el.outerHTML = String(I[el.dataset.i]); });
  document.addEventListener('click', onClick);
  document.addEventListener('keydown', onKey);
  document.addEventListener('input', onInput);
  document.addEventListener('change', onInput);
  document.addEventListener('submit', (e) => { if (e.target.id === 'login-form') submitLogin(e); });
  on('unauth', () => { cache.clear(); if (es) { es.close(); es = null; } showLogin('Сессия закончилась. Войдите снова.'); });
  window.addEventListener('online', () => { S.online = true; if (started) refreshSoft(); });
  window.addEventListener('offline', () => { S.online = false; if (started && S.tab === 'feed') refreshFeed(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && started) refreshSoft(); });
  window.addEventListener('hashchange', () => {
    const r = routeFromHash();
    if (r.order && started) {
      history.replaceState(null, '', location.pathname);
      openOrder(r.order);
    }
  });
  window.addEventListener('popstate', onPopState);
  enableSwipeClose();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
  start();
}

boot();
