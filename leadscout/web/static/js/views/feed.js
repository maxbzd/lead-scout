// Лента заказов.

import { get, post } from '../api.js';
import { S, loadFeed, loadStatus, replaceCard } from '../state.js';
import { $, copyText, esc, html, I, openLink, paint, plural, raw, ring, safeUrl, toast, money } from '../ui.js';
import { askWhy, giveFeedback, openOrder } from './detail.js';
import { openConnect } from './connect.js';
import { openChats } from './chats.js';

const FILTERS = [
  ['all', 'Все'], ['hot', 'Горячие'], ['saved', 'Отложенные'], ['rated', 'Оценённые'], ['offered', 'Предложил цену'], ['rejected', 'Отсеянные'], ['discarded', 'Отброшено'],
];
const SORTS = [['score', 'Совпадение'], ['new', 'Свежие'], ['budget', 'Бюджет']];

export function renderFeed() {
  const view = $('#view');
  paint(view, html`
    <section class="view">
      <div id="banners"></div>
      <div class="hero" id="hero"></div>
      <div class="scan"><span class="mono" id="scan-note"></span><div class="bar"><i></i></div></div>
      <div class="chips" id="filters" role="group" aria-label="Фильтр"></div>
      <div class="sortrow"><span class="mono" id="shown"></span><div class="seg" id="sort" role="group" aria-label="Сортировка"></div></div>
      <div class="list anim" id="list" aria-live="polite"></div>
    </section>`);
  paintAll(true);
}

export function paintAll(anim = false) {
  if (!$('#hero')) return;
  paintBanners();
  paintHero();
  paintChips();
  paintList(anim);
}

function paintBanners() {
  const st = S.status;
  const out = [];
  if (!S.online) out.push(html`<div class="banner warn">${I.alert}<span>Нет связи. Показываю то, что сохранилось на телефоне.</span></div>`);
  if (st?.demo) {
    out.push(html`<div class="banner"><span class="b-dot"></span><span><b>Демо.</b> Заказы тестовые.</span><button type="button" data-act="connect-tg">Подключить Telegram</button></div>`);
  } else if (st && st.telegram.state !== 'ready') {
    const msg = st.telegram.state === 'unauthorized' ? 'Telegram разлогинил аккаунт. Войдите заново.' : st.telegram.error || 'Бот пока не читает ваши чаты.';
    out.push(html`<div class="banner warn">${I.alert}<span>${msg}</span><button type="button" data-act="connect-tg">${st.telegram.state === 'unauthorized' ? 'Войти' : 'Подключить'}</button></div>`);
  } else if (st && st.ready.find((r) => r.id === 'chats' && !r.ok)) {
    out.push(html`<div class="banner"><span class="b-dot"></span><span>Выберите чаты, которые читать.</span><button type="button" data-act="open-chats">Выбрать</button></div>`);
  }
  paint($('#banners'), out);
}

function paintHero() {
  const f = S.feed;
  const st = S.status;
  const all = f?.counts.all ?? 0;
  const hot = f?.counts.hot ?? 0;
  const read = f?.total_read ?? 0;
  const never = !st?.scan.last;
  const date = new Date().toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });
  let cap = 'заказов для вас';
  if (read) cap = `заказов для вас из ${read} ${plural(read, 'сообщения', 'сообщений', 'сообщений')}`;
  if (never && !all) cap = 'бот ещё не проверял чаты';
  const meta = all ? `${hot} ${plural(hot, 'горячий', 'горячих', 'горячих')}${f.hot_sum ? ` · на ${money(f.hot_sum)}` : ''}` : '';
  paint($('#hero'), html`
    <div>
      <div class="mono">${date}</div>
      <div class="num">${all}</div>
      <p class="cap">${cap}</p>
      <div class="meta"><span class="mono">${meta}</span></div>
    </div>
    <div class="orb" aria-hidden="true"><div class="core"></div></div>`);
  const sc = st?.scan;
  $('#scan-note').textContent = sc?.running ? 'проверяю чаты…' : read ? `прочитано ${read} за неделю` : 'ждёт первой проверки';
}

export function paintChips() {
  const c = S.feed?.counts || {};
  paint($('#filters'), FILTERS.map(([id, label]) => raw(`<button class="chip" type="button" data-act="filter" data-v="${id}" aria-pressed="${S.filter === id}">${esc(label)}<b>${c[id] ?? 0}</b></button>`)));
  paint($('#sort'), SORTS.map(([id, label]) => raw(`<button type="button" data-act="sort" data-v="${id}" aria-pressed="${S.sort === id}">${esc(label)}</button>`)));
}

function ageHtml(o) {
  return html`<span class="age${o.fresh ? ' fresh' : ''}"><i></i>${o.age_label || ''}</span>`;
}

export function cardHtml(o, i = 0) {
  const tags = o.skills.slice(0, 4).map((s) => html`<span class="tag${s.hit ? ' hit' : ''}">${s.label}</span>`);
  const rate = o.rate_label && !o.flag ? html`<span class="tag rate${o.rate_day < 5000 ? ' low' : ''}">${o.rate_label}</span>` : '';
  const dup = o.dups ? html`<span class="dup">${o.dups === 1 ? 'ещё в 1 чате' : `ещё в ${o.dups} чатах`}</span>` : '';
  let go;
  if (o.verdict === 'scam' || o.verdict === 'skip') go = html`<button class="go done" type="button" data-act="open" data-id="${o.id}">Разобрать</button>`;
  else if (o.offered) go = html`<button class="go done" type="button" data-act="open" data-id="${o.id}">Предложено</button>`;
  else go = html`<button class="go" type="button" data-act="quick-offer" data-id="${o.id}">${I.send}Предложить</button>`;
  return html`
    <article class="card${o.hot ? ' hot' : ''}${o.verdict === 'scam' ? ' scam' : ''}" data-id="${o.id}" style="--i:${i}">
      <div class="meta">${ageHtml(o)}<span>${o.source}</span>${dup}${o.demo ? html`<span class="demo-tag">демо</span>` : ''}</div>
      <div class="row1" data-act="open" data-id="${o.id}" role="button" tabindex="0">
        ${ring(o.score, 56, 4.5)}
        <div><h3>${o.title}</h3><p class="co"><b>${o.budget.label}</b> · ${o.deadline_label}</p></div>
      </div>
      <div class="tags">${tags}${rate}${o.bids ? html`<span class="tag bid">ответов: ${o.bids}</span>` : ''}</div>
      ${o.why ? html`<p class="why">${I.spark}<span>${o.why}</span></p>` : ''}
      ${o.flag ? html`<div class="flag">${I.alert}${o.flag}</div>` : ''}
      <div class="foot"><div class="acts">
        <button class="ib bm" type="button" data-act="bm" data-id="${o.id}" aria-pressed="${o.state === 'saved'}" aria-label="Отложить">${I.bm}</button>
        <button class="ib up" type="button" data-act="up" data-id="${o.id}" aria-pressed="${o.feedback === 'up'}" aria-label="Подходит">${I.up}</button>
        <button class="ib dn" type="button" data-act="dn" data-id="${o.id}" aria-pressed="${o.feedback === 'down'}" aria-label="Не подходит">${I.up}</button>
      </div>${go}</div>
    </article>`;
}

function emptyState() {
  const st = S.status;
  if (S.q) return html`<div class="empty"><div class="t">Ничего не нашлось</div>По запросу «${S.q}» заказов нет.</div>`;
  if (S.filter !== 'all') {
    const msg = {
      hot: 'Горячие заказы (от 90%) появятся, как только бот найдёт идеальное совпадение.',
      saved: 'Отложите заказ закладкой, и он будет здесь.',
      rated: 'Ставьте 👍 и 👎 под заказами: так бот учится.',
      offered: 'Здесь будут заказы, на которые вы уже откликнулись.',
      rejected: 'Здесь будет то, что бот отсеял: мошенничество, мало денег, не ваш профиль.',
      discarded: 'Сюда попадает всё, что бот не счёл заказом: болтовня, вакансии, реклама. Пока ничего.',
    }[S.filter];
    return html`<div class="empty"><div class="t">Пока пусто</div>${msg}</div>`;
  }
  if (st && !st.demo && st.telegram.state !== 'ready') {
    return html`<div class="empty"><div class="t">Бот ждёт ваш Telegram</div>Подключите аккаунт, и я начну читать чаты с заказами.<br><button class="btn pri" type="button" data-act="connect-tg" style="margin-top:16px">Подключить Telegram</button></div>`;
  }
  return html`<div class="empty"><div class="t">Здесь пока тихо</div>Новые подходящие заказы появятся после ближайшей проверки. Можно запустить её сейчас.<br><button class="btn" type="button" data-act="scan" style="margin-top:16px">Проверить сейчас</button></div>`;
}

function discardedHtml(m) {
  const link = safeUrl(m.link);
  return html`<article class="card muted-card" data-id="${m.id}">
    <div class="meta"><span class="age"><i></i>${m.age_label}</span><span>${m.chat}</span>${m.demo ? html`<span class="demo-tag">демо</span>` : ''}</div>
    <p class="raw-text">${m.text}</p>
    ${m.author ? html`<p class="mono note-sm" style="margin-top:8px">${m.author}</p>` : ''}
    <div class="foot"><span class="mono">бот не счёл заказом</span><div class="acts">
      ${link ? html`<a class="ib" href="${link}" target="_blank" rel="noopener noreferrer" aria-label="Открыть в Telegram">${I.link}</a>` : ''}
      <button class="go" type="button" data-act="promote" data-id="${m.id}">${I.plus}Это заказ</button></div></div>
  </article>`;
}

export function paintList(anim = false) {
  const list = $('#list');
  if (!list) return;
  const items = S.feed?.items || [];
  list.classList.toggle('anim', anim);
  $('#shown').textContent = S.feed ? `показано ${items.length}` : '';
  const draw = S.filter === 'discarded' ? (o) => discardedHtml(o) : cardHtml;
  paint(list, items.length ? items.map((o, i) => draw(o, i)) : emptyState());
}

export async function refreshFeed({ anim = false } = {}) {
  try {
    await loadFeed();
  } catch (e) {
    toast(e.message);
  }
  paintAll(anim);
}

// ------------------------------------------------------------- проверка ----
let scanning = false;
export async function runScan() {
  if (scanning) return;
  scanning = true;
  const note = $('#scan-note');
  if (note) note.textContent = 'проверяю чаты…';
  const pill = $('#scan-txt');
  if (pill) pill.textContent = 'сканирую…';
  try {
    const r = await post('/scan');
    if (r.skipped) toast('Проверка уже идёт');
    else if (r.error) toast(r.error);
    else if (r.orders) toast(`Нашёл новых: ${r.orders}`);
    else toast('Новых заказов нет');
    await loadStatus();
    await refreshFeed({ anim: !!r.orders });
  } catch (e) {
    toast(e.message);
  } finally {
    scanning = false;
    updateScanPill();
  }
}

export function updateScanPill() {
  const pill = $('#scan-txt');
  if (!pill) return;
  const last = S.status?.scan.last?.at;
  if (S.status?.scan.running) { pill.textContent = 'сканирую…'; return; }
  if (!last) { pill.textContent = 'ещё не сканировал'; return; }
  const d = new Date(last);
  pill.textContent = `скан ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

// -------------------------------------------------------------- действия ----
const byId = (id) => S.feed?.items.find((o) => o.id === +id);

async function quickOffer(id) {
  const o = byId(id);
  if (!o) return;
  if (!o.draft || !o.contact_url) return openOrder(id, { focus: 'draft' });
  // копирование и открытие чата делаем сразу, пока длится нажатие: иначе браузер заблокирует переход
  const copied = copyText(o.draft.text);
  const opened = openLink(o.contact_url);
  const ok = await copied;
  try {
    const r = await post(`/orders/${id}/offer`, { done: true, price: o.draft.price || undefined });
    replaceCard({ ...o, ...r.order, draft: o.draft, contact_url: o.contact_url });
    paintList();
    loadFeed().then(paintAll).catch(() => {});
  } catch (e) {
    toast(e.message);
    return;
  }
  toast(ok ? (opened ? 'Текст скопирован. Вставьте его в чате.' : 'Текст скопирован.') : 'Не получилось скопировать. Откройте заказ и скопируйте текст там.', {
    action: 'Отменить', onAction: async () => { await post(`/orders/${id}/offer`, { done: false }); refreshFeed(); },
  });
}

export const feedActions = {
  filter: async (el) => { S.filter = el.dataset.v; await refreshFeed({ anim: true }); window.scrollTo(0, 0); },
  sort: async (el) => { S.sort = el.dataset.v; await refreshFeed({ anim: true }); },
  open: (el) => openOrder(+el.dataset.id),
  'quick-offer': (el) => quickOffer(+el.dataset.id),
  scan: () => runScan(),
  bm: async (el) => {
    const id = +el.dataset.id;
    const o = byId(id);
    if (!o) return;
    const saved = o.state !== 'saved';
    try {
      const r = await post(`/orders/${id}/save`, { saved });
      replaceCard({ ...o, ...r.order, draft: o.draft, contact_url: o.contact_url });
      paintList();
      toast(saved ? 'Отложено. Найдёте в фильтре «Отложенные».' : 'Убрано из отложенных');
      loadFeed().then(() => { paintChips(); }).catch(() => {});
    } catch (e) { toast(e.message); }
  },
  up: async (el) => {
    const id = +el.dataset.id;
    const o = byId(id);
    const kind = o?.feedback === 'up' ? 'clear' : 'up';
    await giveFeedback(id, kind);
  },
  dn: (el) => askWhy(+el.dataset.id),
  'connect-tg': () => openConnect('telegram'),
  'open-chats': () => openChats(),
  promote: async (el) => {
    el.disabled = true;
    try {
      const r = await post(`/discarded/${el.dataset.id}/promote`);
      toast('Добавил в ленту. Оцените его: так бот научится.', { action: 'Открыть', onAction: () => openOrder(r.order.id) });
      await refreshFeed();
    } catch (e) {
      toast(e.message);
      el.disabled = false;
    }
  },
};

export async function onNewOrders(ids) {
  if (!S.feed || !ids?.length) return;
  const cards = [];
  for (const id of ids.slice(0, 4)) {
    try { cards.push(await get(`/orders/${id}`)); } catch { /* пропускаем */ }
  }
  const good = cards.filter((c) => c.verdict === 'match' || c.verdict === 'maybe').sort((a, b) => b.score - a.score);
  if (!good.length) return;
  const best = good[0];
  const bar = $('#newbar');
  if (!bar) return;
  paint($('#nb-title'), html`Новый заказ · ${best.score}%`);
  paint($('#nb-sub'), html`${best.title} · ${best.budget.label}`);
  bar.dataset.id = best.id;
  bar.classList.add('on');
  clearTimeout(onNewOrders.t);
  onNewOrders.t = setTimeout(() => bar.classList.remove('on'), 12000);
}
