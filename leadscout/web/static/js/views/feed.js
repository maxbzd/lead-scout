// Лента заказов.

import { get, post } from '../api.js';
import { S, loadFeed, loadStatus, replaceCard } from '../state.js';
import { $, closeSheet, copyText, esc, html, I, openLink, openSheet, paint, plural, raw, ring, safeUrl, sheetHead, toast, money } from '../ui.js';
import { askWhy, giveFeedback, openOrder } from './detail.js';
import { openConnect, setAiControl } from './connect.js';
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
      <div id="manual-cta"></div>
      <div class="chips" id="filters" role="group" aria-label="Фильтр"></div>
      <div class="sortrow"><span class="mono" id="shown"></span><button class="linkish pick-btn" type="button" id="pick-btn" data-act="pick-toggle">Выбрать</button><div class="seg" id="sort" role="group" aria-label="Сортировка"></div></div>
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
  const cl = st?.cloud;
  if (cl?.hosted) {
    const at = cl.last_sync ? new Date(cl.last_sync) : null;
    const when = at ? `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}` : null;
    if (!cl.last_sync) out.push(html`<div class="banner warn">${I.alert}<span>Компьютер ещё не присылал данных. Запустите программу Scout на компьютере.</span></div>`);
    else if (!cl.online) out.push(html`<div class="banner warn">${I.alert}<span><b>Компьютер не в сети.</b> Данные на ${when}.${cl.pending ? ` Ваших действий в очереди: ${cl.pending}, они выполнятся при запуске программы.` : ' Оценки и закладки сохранятся и дойдут до него позже.'}</span></div>`);
    else if (cl.pending) out.push(html`<div class="banner"><span class="b-dot"></span><span>Передаю на компьютер: ${cl.pending}</span></div>`);
  }
  const ai = st?.llm;
  if (ai && ai.mode !== 'rules' && ai.control && !ai.control.active) {
    const manual = ai.control.reason === 'manual';
    out.push(html`<div class="banner"><span class="b-dot"></span><span><b>ИИ на паузе.</b> ${manual ? 'Вы поставили её вручную.' : `Накопилось ${ai.control.backlog} неразобранных заказов, разберите их.`} Оценивает бесплатно правилами.</span><button type="button" data-act="ai-resume">${manual ? 'Включить' : 'Всё равно включить'}</button></div>`);
  }
  if (cl?.hosted) {
    // подключения на сайте только показываем: менять их можно в программе на компьютере
  } else if (st && st.telegram.state === 'unauthorized') {
    out.push(html`<div class="banner warn">${I.alert}<span>Telegram разлогинил аккаунт. Войдите заново.</span><button type="button" data-act="connect-tg">Войти</button></div>`);
  } else if (st && st.telegram.state === 'error' && st.telegram.error) {
    out.push(html`<div class="banner warn">${I.alert}<span>${st.telegram.error}</span><button type="button" data-act="connect-tg">Подробнее</button></div>`);
  } else {
    const step = nextSetupStep(st);
    if (step && !setupSnoozed()) {
      out.push(html`<div class="banner setup"><span class="b-dot"></span><span><b>Настройка: шаг ${step.n} из ${step.of}. ${step.title}.</b> ${step.text}</span><button type="button" data-act="${step.act}">${step.btn}</button><button type="button" class="later" data-act="setup-later">Позже</button></div>`);
    }
  }
  paint($('#banners'), out);
}

// Первая настройка по порядку: ИИ, Telegram, чаты, бот на телефон, сайт на телефон. Показываем только первый невыполненный шаг.
function nextSetupStep(st) {
  if (!st) return null;
  const chatsOk = !!st.ready.find((r) => r.id === 'chats' && r.ok);
  const steps = [
    [st.llm.mode !== 'rules', 'Подключите ИИ', 'Он сам отличает настоящие заказы от болтовни и объявлений исполнителей. Подойдёт бесплатный Gemini.', 'connect-ai', 'Подключить'],
    [st.telegram.state === 'ready', 'Подключите Telegram', st.demo ? 'Сейчас показаны тестовые заказы.' : 'Без этого бот не читает ваши чаты.', 'connect-tg', 'Подключить'],
    [chatsOk, 'Выберите чаты', 'Бот читает только те, что вы отметите.', 'open-chats', 'Выбрать'],
    [!!st.bot.paired, 'Подключите бота', 'Он присылает свежие заказы на телефон.', 'connect-bot', 'Подключить'],
  ];
  if (st.local) steps.push([!!st.cloud?.connected, 'Сайт для телефона', 'Заказы и ваши оценки будут храниться в облаке и открываться с телефона.', 'connect-cloud', 'Подключить']);
  const at = steps.findIndex((s) => !s[0]);
  if (at < 0) return null;
  const [, title, text, act, btn] = steps[at];
  return { n: at + 1, of: steps.length, title, text, act, btn };
}

const SNOOZE = 'ls:setup-snooze';
function setupSnoozed() {
  try { return Date.now() < +(localStorage.getItem(SNOOZE) || 0); } catch { return false; }
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
  paint($('#manual-cta'), sc && sc.every_min === 0 ? html`<button class="btn pri full" type="button" data-act="scan" style="margin-top:14px">${I.search}${sc.running ? 'Проверяю…' : 'Проверить чаты'}</button>` : '');
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
      <i class="pickmark" aria-hidden="true"></i>
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
    <i class="pickmark" aria-hidden="true"></i>
    <div class="meta"><span class="age"><i></i>${m.age_label}</span><span>${m.chat}</span>${m.demo ? html`<span class="demo-tag">демо</span>` : ''}</div>
    <p class="raw-text">${m.text}</p>
    ${m.author ? html`<p class="mono note-sm" style="margin-top:8px">${m.author}</p>` : ''}
    <div class="foot"><span class="mono">${m.why || 'не похоже на заказ'}</span><div class="acts">
      ${link ? html`<a class="ib" href="${link}" target="_blank" rel="noopener noreferrer" aria-label="Открыть в Telegram">${I.link}</a>` : ''}
      <button class="go" type="button" data-act="promote" data-id="${m.id}">${I.plus}Это заказ</button></div></div>
  </article>`;
}

let feedError = '';

export function paintList(anim = false) {
  const list = $('#list');
  if (!list) return;
  const items = S.feed?.items || [];
  if (!S.feed && feedError) {
    paint(list, html`<div class="empty"><div class="t">Не получилось загрузить</div>${feedError}<br><button class="btn" type="button" data-act="retry-feed" style="margin-top:16px">Повторить</button></div>`);
    $('#shown').textContent = '';
    return;
  }
  list.classList.toggle('anim', anim);
  $('#shown').textContent = S.feed ? `показано ${items.length}` : '';
  const draw = S.filter === 'discarded' ? (o) => discardedHtml(o) : cardHtml;
  paint(list, items.length ? items.map((o, i) => draw(o, i)) : emptyState());
  markSel();
  markPicked();
}

export async function refreshFeed({ anim = false } = {}) {
  try {
    await loadFeed();
    feedError = '';
  } catch (e) {
    S.feed = null; // иначе под новым фильтром остались бы карточки от старого
    feedError = e.message;
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
    if (r.queued) toast('Команда ушла на компьютер. Он проверит чаты, как только программа запущена.');
    else if (r.skipped) toast('Проверка уже идёт');
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

let installing = false;
export function setInstalling(on) { installing = on; paintUpdateBar(); }

// Полоса вверху: новая версия готова, одна кнопка. Видна на любой вкладке.
export function paintUpdateBar() {
  const bar = $('#upbar');
  const u = S.status?.update;
  if (!bar) return;
  const show = !!(u?.available && u.can_install) || installing;
  bar.hidden = !show;
  if (!show) return;
  paint(bar, installing
    ? html`<span><b>Устанавливаю обновление…</b> Окно закроется и откроется само.</span><button type="button" disabled>Подождите</button>`
    : html`<span><b>Есть новая версия ${u.latest}.</b> Нажмите, и программа обновится сама, данные сохранятся.</span><button type="button" data-act="update-install">Обновить</button>`);
}

// ---------------------------------------------------- выбор и удаление ----
let pick = null; // Set номеров выбранных карточек; null: обычный режим
export const pickMode = () => pick !== null;

function shownIds() {
  return (S.feed?.items || []).map((o) => o.id);
}

export function markPicked() {
  const list = $('#list');
  if (list) list.classList.toggle('picking', pick !== null);
  document.querySelectorAll('.card[data-id]').forEach((c) => c.classList.toggle('picked', pick !== null && pick.has(+c.dataset.id)));
  const btn = $('#pick-btn');
  if (btn) btn.textContent = pick ? 'Готово' : 'Выбрать';
  const bar = $('#selbar');
  if (!bar) return;
  bar.hidden = pick === null;
  if (pick === null) return;
  const total = S.feed?.counts?.[S.filter] ?? shownIds().length;
  const disc = S.filter === 'discarded';
  paint(bar, html`<div class="selrow"><b>${pick.size ? `Выбрано ${pick.size}` : 'Отметьте карточки'}</b><span class="mono">показано ${shownIds().length}</span>
      <button type="button" class="linkish" data-act="pick-all">${pick.size === shownIds().length && pick.size ? 'Снять' : 'Все'}</button></div>
    <div class="selrow btns">
      ${disc ? '' : html`<button type="button" class="btn small" data-act="pick-notorder" ${pick.size ? '' : raw('disabled')}>Это не заказ</button>`}
      <button type="button" class="btn small danger" data-act="pick-del" ${pick.size ? '' : raw('disabled')}>Удалить</button>
      <button type="button" class="linkish" data-act="pick-everything">Всё во вкладке${typeof total === 'number' ? ` (${total})` : ''}</button>
    </div>`);
}

export function startPick(on = pick === null) {
  pick = on ? new Set() : null;
  markPicked();
}

export function togglePick(id) {
  if (pick === null) startPick(true);
  pick.has(id) ? pick.delete(id) : pick.add(id);
  markPicked();
}

// В режиме выбора нажатие на карточку отмечает её, а не открывает
export function handlePick(e) {
  if (pick === null) return false;
  const card = e.target.closest('.card[data-id]');
  if (!card || e.target.closest('a')) return false;
  e.preventDefault();
  e.stopPropagation();
  togglePick(+card.dataset.id);
  return true;
}

function confirmDelete({ title, text, notorder, run }) {
  askDelete = { notorder, run };
  openSheet(html`${sheetHead(title)}<p class="lead-p">${text}</p>
    <div class="btnrow"><button class="btn" type="button" data-act="close">Отмена</button><button class="btn danger" type="button" data-act="pick-confirm" data-autofocus>${notorder ? 'Это не заказ, удалить' : 'Удалить'}</button></div>`);
}
let askDelete = null;

async function doDelete(body) {
  const disc = S.filter === 'discarded';
  const r = await post(disc ? '/discarded/delete' : '/orders/delete', body);
  startPick(false);
  toast(r.deleted ? `Удалено: ${r.deleted}` : 'Ничего не удалено');
  await refreshFeed();
}

export const pickActions = {
  'pick-toggle': () => startPick(),
  'pick-all': () => {
    const ids = shownIds();
    pick = pick && pick.size === ids.length ? new Set() : new Set(ids);
    markPicked();
  },
  'pick-del': () => {
    const ids = [...pick];
    confirmDelete({ title: `Удалить ${ids.length} ${plural(ids.length, 'заказ', 'заказа', 'заказов')}?`, text: 'Это нельзя отменить. Сообщения из чатов заново в ленту не вернутся.', run: () => doDelete({ ids }) });
  },
  'pick-notorder': () => {
    const ids = [...pick];
    confirmDelete({ title: `Это не заказы (${ids.length})?`, text: 'Они будут удалены, а ИИ запомнит их как примеры того, что заказом не является.', notorder: true, run: () => doDelete({ ids, notorder: true }) });
  },
  'pick-everything': () => {
    const n = S.feed?.counts?.[S.filter];
    const name = (FILTERS.find(([id]) => id === S.filter) || [, 'ленте'])[1];
    const keep = ['saved', 'offered', 'rated', 'discarded'].includes(S.filter) ? '' : ' Отложенные и те, на которые вы откликнулись, останутся.';
    confirmDelete({ title: `Удалить всё во вкладке «${name}»?`, text: `${typeof n === 'number' ? `Будет удалено заказов: ${n}. ` : ''}Это нельзя отменить.${keep}`, run: () => doDelete({ all: true, filter: S.filter, q: S.q }) });
  },
  'pick-confirm': async (el) => {
    const job = askDelete;
    if (!job) return;
    el.disabled = true;
    closeSheet();
    askDelete = null;
    await job.run();
  },
};

// Выбранная карточка (клавиши j/k на компьютере)
let selId = null;
export function markSel() {
  document.querySelectorAll('.card.sel').forEach((c) => c.classList.remove('sel'));
  if (selId == null) return;
  const el = document.querySelector(`.card[data-id="${selId}"]`);
  if (el) el.classList.add('sel');
}
export function moveSel(delta) {
  const cards = [...document.querySelectorAll('.card[data-id]')];
  if (!cards.length) return null;
  const at = cards.findIndex((c) => +c.dataset.id === selId);
  const next = cards[Math.max(0, Math.min(cards.length - 1, at < 0 ? 0 : at + delta))];
  selId = +next.dataset.id;
  markSel();
  next.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  return selId;
}
export const currentSel = () => selId;

export function updateScanPill() {
  paintUpdateBar();
  const pill = $('#scan-txt');
  if (!pill) return;
  const last = S.status?.scan.last?.at;
  const pr = S.status?.scan.progress;
  if (S.status?.scan.running) { pill.textContent = pr ? `ИИ читает ${pr[0]}/${pr[1]}` : 'сканирую…'; return; }
  if (!last) { pill.textContent = 'ещё не сканировал'; return; }
  const d = new Date(last);
  pill.textContent = `скан ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

// -------------------------------------------------------------- действия ----
const byId = (id) => S.feed?.items.find((o) => o.id === +id);

const offering = new Set();

async function quickOffer(id) {
  if (offering.has(id)) return; // двойной тап не должен отправлять два отклика
  offering.add(id);
  try { await quickOfferNow(id); } finally { offering.delete(id); }
}

async function quickOfferNow(id) {
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
  ...pickActions,
  'setup-later': () => {
    try { localStorage.setItem(SNOOZE, String(Date.now() + 24 * 3600 * 1000)); } catch { /* без памяти подсказка вернётся */ }
    paintBanners();
    toast('Напомню завтра. Всё можно настроить в Профиле.');
  },
  filter: async (el) => { startPick(false); S.filter = el.dataset.v; await refreshFeed({ anim: true }); window.scrollTo(0, 0); },
  sort: async (el) => { S.sort = el.dataset.v; await refreshFeed({ anim: true }); },
  open: (el) => openOrder(+el.dataset.id),
  'quick-offer': (el) => quickOffer(+el.dataset.id),
  scan: () => runScan(),
  'retry-feed': () => refreshFeed({ anim: true }),
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
  'ai-resume': async () => {
    try {
      const c = S.status.llm.control;
      await setAiControl(c.reason === 'manual' ? { paused: false } : { auto: false });
      toast('ИИ снова работает');
      paintAll();
    } catch (e) { toast(e.message); }
  },
  promote: async (el) => {
    el.disabled = true;
    try {
      const r = await post(`/discarded/${el.dataset.id}/promote`);
      if (r.queued) toast('Принял. Заказ появится в ленте, когда компьютер обработает команду.');
      else toast('Добавил в ленту. Оцените его: так бот научится.', { action: 'Открыть', onAction: () => openOrder(r.order.id) });
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
