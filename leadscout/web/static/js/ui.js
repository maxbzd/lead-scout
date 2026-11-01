// Общие помощники интерфейса: безопасная вёрстка, значки, всплывашки, шторки.

class Safe {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}

export const raw = (s) => new Safe(String(s));

const ENT = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ENT[c]);

// Шаблон с автоматическим экранированием. Всё, что пришло из чатов, безопасно по умолчанию.
export function html(strings, ...vals) {
  let out = strings[0];
  vals.forEach((v, i) => {
    out += part(v) + strings[i + 1];
  });
  return new Safe(out);
}

function part(v) {
  if (v === null || v === undefined || v === false) return '';
  if (v instanceof Safe) return v.s;
  if (Array.isArray(v)) return v.map(part).join('');
  return esc(v);
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
export const paint = (el, safe) => { el.innerHTML = Array.isArray(safe) ? safe.map(String).join('') : String(safe); };

// Ссылки из заказов пропускаем только если они безопасны.
export function safeUrl(u) {
  try {
    const x = new URL(u);
    if (x.protocol === 'https:' || x.protocol === 'mailto:') return x.href;
  } catch { /* не ссылка */ }
  return '';
}

export function openLink(url) {
  const href = safeUrl(url);
  if (!href) return false;
  const a = document.createElement('a');
  a.href = href;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  document.body.appendChild(a);
  a.click();
  a.remove();
  return true;
}

export const nbsp = (n) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
export const money = (n) => (n === null || n === undefined ? '' : `${nbsp(n)} ₽`);
export const thousands = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n % 1000 ? 1 : 0).replace('.', ',')} тыс. ₽` : money(n));

export function plural(n, one, few, many) {
  const m = Math.abs(n) % 100;
  if (m >= 11 && m <= 14) return many;
  const r = m % 10;
  return r === 1 ? one : r >= 2 && r <= 4 ? few : many;
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export async function copyText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* пробуем запасной способ */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------- значки ----
const svg = (body, extra = '') => raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${body}</svg>`);

export const I = {
  up: svg('<path d="M7 11v9H4.5A1.5 1.5 0 0 1 3 18.5v-6A1.5 1.5 0 0 1 4.5 11H7zm0 0 4-8c1.6 0 2.6 1.1 2.6 2.6V9h5.1a2 2 0 0 1 2 2.3l-1.1 6.7a2 2 0 0 1-2 1.7H7"/>'),
  spark: raw('<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2.5l2 6 6 2-6 2-2 6-2-6-6-2 6-2z"/></svg>'),
  chev: svg('<path d="M9 6l6 6-6 6"/>'),
  check: svg('<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>'),
  alert: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5m0 3.2h.01"/>'),
  close: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
  copy: svg('<rect x="9" y="9" width="11" height="11" rx="2.5"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>'),
  redo: svg('<path d="M20 12a8 8 0 1 1-2.5-5.8M20 4v5h-5"/>'),
  send: svg('<path d="M21 3 10 14M21 3l-7 18-4-7-7-4z"/>'),
  bm: svg('<path d="M7 4h10a1 1 0 0 1 1 1v15l-6-4-6 4V5a1 1 0 0 1 1-1z"/>'),
  search: svg('<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>'),
  feed: svg('<path d="M4 7h16M4 12h16M4 17h10"/>'),
  stats: svg('<path d="M5 20v-8m7 8V4m7 16v-5"/>'),
  me: svg('<circle cx="12" cy="8" r="3.6"/><path d="M5 20a7 7 0 0 1 14 0"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  down: svg('<path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14"/>'),
  trend: svg('<path d="M5 15l5-5 4 4 5-6"/>'),
  link: svg('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>'),
  lock: svg('<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>'),
  trash: svg('<path d="M5 7h14M10 7V4h4v3m-8 0 1 13h10l1-13"/>'),
  tg: svg('<path d="M21 4 3 11l5 2m13-9-3 16-6-5m9-11L8 13m0 0v6l3-3"/>'),
};

// ------------------------------------------------------------- всплывашка ----
let toastTimer;
export function toast(msg, opts = {}) {
  const t = document.getElementById('toast');
  if (!t) return;
  paint(t, html`<span>${msg}</span>${opts.action ? html`<button type="button" id="toast-act">${opts.action}</button>` : ''}`);
  t.classList.toggle('top', !document.getElementById('veil').hidden);
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), opts.ms || 4200);
  if (opts.action) {
    document.getElementById('toast-act').onclick = () => { t.classList.remove('on'); opts.onAction?.(); };
  }
}

// ------------------------------------------------------------------ шторки ----
const stack = [];
let lastFocus = null;

// Пока открыта шторка, остальное приложение недоступно для Tab и экранных читалок
function setBackdropInert(on) {
  for (const id of ['app', 'tabbar']) {
    const el = document.getElementById(id);
    if (el) el.inert = on;
  }
}
let pendingBack = false; // кнопка «назад» закрывает шторку, а не приложение


// На широком экране компьютера карточка заказа открывается справа от списка и не закрывает его
export const isWide = () => window.matchMedia('(min-width: 1200px)').matches;

export function openSheet(content, { onClose, wide = false, docked = false } = {}) {
  const veil = document.getElementById('veil');
  const sheet = document.getElementById('sheet');
  const dock = docked && isWide();
  document.getElementById('toast')?.classList.remove('on');
  if (!veil.hidden) stack.pop()?.onClose?.(); // новая шторка поверх открытой заменяет её
  paint(sheet, html`<div class="grab"></div>${content}`);
  sheet.classList.toggle('wide', wide);
  if (veil.hidden && !dock) lastFocus = document.activeElement;
  veil.classList.toggle('docked', dock);
  veil.hidden = false;
  setBackdropInert(!dock);
  document.body.style.overflow = dock ? '' : 'hidden';
  sheet.scrollTop = 0;
  if (!dock && !pendingBack && !history.state?.sheet) history.pushState({ sheet: 1 }, '');
  stack.push({ onClose });
  const first = sheet.querySelector('[data-autofocus]');
  if (first && !dock) setTimeout(() => first.focus(), 60);
  return sheet;
}

export function setSheet(content) {
  const sheet = document.getElementById('sheet');
  paint(sheet, html`<div class="grab"></div>${content}`);
}

export const sheetHooks = { afterClose: null };

export function closeSheet({ fromPop = false } = {}) {
  const veil = document.getElementById('veil');
  if (veil.hidden) return;
  veil.hidden = true;
  veil.classList.remove('docked');
  setBackdropInert(false);
  document.body.style.overflow = '';
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus?.({ preventScroll: true });
  const top = stack.pop();
  paint(document.getElementById('sheet'), '');
  if (!fromPop && history.state?.sheet) {
    pendingBack = true;
    history.back();
  }
  top?.onClose?.();
  sheetHooks.afterClose?.();
}

// Системная кнопка «назад» или свайп: закрываем шторку
export function onPopState() {
  if (pendingBack) { pendingBack = false; return; }
  if (!document.getElementById('veil').hidden) closeSheet({ fromPop: true });
}

export const sheetOpen = () => !document.getElementById('veil').hidden;

export function sheetHead(title, sub = '') {
  return html`<div class="sh-head"><div>${sub ? html`<div class="mono">${sub}</div>` : ''}<h2 class="sh-title">${title}</h2></div><button class="x" type="button" data-act="close" aria-label="Закрыть">${I.close}</button></div>`;
}

export function ring(m, size, sw, big = false) {
  const r = (size - sw) / 2 - 1;
  const c = 2 * Math.PI * r;
  const cx = size / 2;
  const g = m >= 90 ? 'aurora' : m >= 70 ? 'cy' : 'dim';
  const fs = big ? 44 : size > 60 ? 20 : 17;
  return raw(`<svg class="ring${m >= 90 ? ' hotr' : ''}" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="Совпадение ${m} процентов">` +
    `<circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="rgba(255,255,255,.10)" stroke-width="${sw}"/>` +
    `<circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="url(#${g})" stroke-width="${sw}" stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${(c * (1 - m / 100)).toFixed(1)}" transform="rotate(-90 ${cx} ${cx})"/>` +
    `<text x="${cx}" y="${cx + 1}" text-anchor="middle" dominant-baseline="central" fill="#F2F1FA" font-family="Onest, sans-serif" font-weight="${big ? 200 : 500}" font-size="${fs}" letter-spacing="-0.02em">${m}${big ? '<tspan font-size="18" dx="2" fill="#B9B6D2">%</tspan>' : ''}</text></svg>`);
}

export function chip(label, { on = false, count = null, attrs = '' } = {}) {
  return raw(`<button class="chip" type="button" aria-pressed="${on}" ${attrs}>${esc(label)}${count !== null ? `<b>${count}</b>` : ''}</button>`);
}

export function ago(iso) {
  if (!iso) return '';
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (m < 1) return 'только что';
  if (m < 60) return `${m} мин назад`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} ч назад`;
  return `${Math.floor(h / 24)} дн назад`;
}

export function hhmm(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
