// Карточка заказа: почему подходит, цена, заказчик, черновик отклика, этапы.

import { get, post } from '../api.js';
import { S, loadFeed, replaceCard } from '../state.js';
import { $, closeSheet, copyText, esc, html, I, money, nbsp, openLink, openSheet, paint, plural, raw, ring, safeUrl, setSheet, sheetHead, sheetOpen, toast } from '../ui.js';
import { paintAll, paintChips, paintList } from './feed.js';

const TONES = [['short', 'Коротко'], ['warm', 'Тепло'], ['formal', 'Деловой']];
const STAGES = [['wait', 'Ждём ответа'], ['ans', 'Заказчик ответил'], ['work', 'Взял в работу'], ['no', 'Отказ']];
const REASONS = [['profile', 'Не мой профиль'], ['budget', 'Низкий бюджет'], ['terms', 'Не те сроки'], ['scam', 'Подозрительно'], ['notorder', 'Это не заказ'], ['other', 'Просто не нравится']];

let D = null; // открытый сейчас заказ

const kRange = (lo, hi) => {
  const k = (n) => (n >= 1000 && n % 500 === 0 ? String(n / 1000).replace('.', ',') : nbsp(n));
  return lo === hi ? `${k(lo)} ${lo >= 1000 ? 'тыс. ' : ''}₽`.replace('  ', ' ') : `${k(lo)}–${k(hi)} тыс. ₽`;
};

function priceBlock(o) {
  if (!o.rec || o.rec.min == null) {
    return o.rec?.note ? html`<div class="sec pr"><span class="mono">Сколько просить</span><p class="note">${o.rec.note}</p></div>` : '';
  }
  const lo = o.rec.min;
  const hi = o.rec.max;
  const budget = o.budget.rub_max ?? o.budget.rub_min;
  const mn = lo * 0.62;
  const mx = hi * 1.38;
  const pc = (x) => Math.max(0, Math.min(100, ((x - mn) / (mx - mn)) * 100));
  return html`<div class="sec pr">
    <span class="mono">Сколько просить</span>
    <p class="lead">Рекомендую <b>${lo >= 10000 ? kRange(lo, hi) : `${money(lo)}–${money(hi)}`}</b></p>
    <div class="track"><div class="range" style="left:${pc(lo).toFixed(1)}%;width:${(pc(hi) - pc(lo)).toFixed(1)}%"></div>${budget ? html`<div class="mk" style="left:${pc(budget).toFixed(1)}%"><em>бюджет</em></div>` : ''}</div>
    <p class="note">${o.rec.note}</p>
  </div>`;
}

function clientBlock(o) {
  const sig = (o.client?.signals || []).slice(0, 3);
  const c = o.contact || {};
  return html`<div class="sec"><span class="mono">Заказчик</span>
    ${sig.length ? html`<div class="clients" style="margin-top:10px">${sig.map((s) => html`<div${s.bad ? raw(' class="bad"') : ''}><span class="mono">${s.label}</span><b>${s.value}</b></div>`)}</div>` : ''}
    <p class="capnote" style="margin-top:10px">${c.value ? html`Связаться: <b>${c.value}</b>` : 'Контакт в тексте не указан. Ответьте на сообщение в чате.'}</p>
  </div>`;
}

function sourcesBlock(o) {
  const links = [];
  if (o.message_link) links.push(html`<a href="${safeUrl(o.message_link)}" target="_blank" rel="noopener noreferrer">Открыть сообщение в Telegram</a>`);
  const extra = (o.sources || []).slice(1).filter((s) => s.chat);
  return html`<div class="sec"><span class="mono">Исходное сообщение</span><div class="post">${o.text}</div>
    <p class="capnote" style="margin-top:8px">${links}${extra.length ? html` · Опубликовано ещё в: ${extra.map((s) => s.chat).join(', ')}` : ''}</p>
    ${o.bids ? html`<p class="capnote">Ответов в чате на это объявление: <b>${o.bids}</b>. Это похоже на отклики других исполнителей.</p>` : ''}</div>`;
}

function stagesBlock(o) {
  return html`<span class="mono">Что дальше</span><div class="seg stage-seg" style="margin-top:10px">${STAGES.map(([id, label]) => raw(`<button type="button" data-act="stage" data-v="${id}" aria-pressed="${o.stage === id}">${esc(label)}</button>`))}</div>
    ${o.stage === 'work' ? html`<p class="capnote">Сумма в аналитике: <b>${money(o.price_offered || o.budget.rub_max || 0)}</b>. <button type="button" class="linkish" data-act="amount">Изменить</button></p>` : ''}`;
}

function draftBlock(o) {
  if (o.verdict === 'scam') return '';
  return html`<div class="sec"><span class="mono">Черновик предложения</span>
    <div class="seg" id="tone" style="margin-top:10px">${TONES.map(([id, label]) => raw(`<button type="button" data-act="tone" data-v="${id}" aria-pressed="${D.tone === id}">${esc(label)}</button>`))}</div>
    <textarea class="draft" id="draft" rows="6" aria-label="Текст предложения" spellcheck="true"></textarea>
    <div class="btnrow"><button class="btn" type="button" data-act="redo">${I.redo}Переписать</button><button class="btn" type="button" data-act="copy">${I.copy}Скопировать</button></div>
    <p class="mono note-sm">Текст можно править. Бот берёт факты и примеры работ из вашего профиля.</p></div>`;
}

function ctaBlock(o) {
  if (o.verdict === 'scam') return html`<div class="cta"><span></span><button class="btn pri" type="button" data-act="skip-order">Скрыть этот заказ</button></div>`;
  const label = o.contact?.url ? (o.offered ? 'Скопировать ещё раз' : 'Откликнуться') : 'Скопировать текст';
  return html`<div class="cta"><button class="price" type="button" data-act="price" aria-label="Изменить цену"><span class="mono">ваша цена</span><b id="price-v">${D.price ? money(D.price) : '—'}</b></button><button class="btn pri" type="button" data-act="cta">${I.send}${label}</button></div>`;
}

function body(o) {
  return html`
    <div class="sh-head"><span class="mono">${o.source}${o.age_label ? ' · ' + o.age_label + ' назад' : ''}${o.demo ? ' · демо' : ''}</span><button class="x" type="button" data-act="close" aria-label="Закрыть">${I.close}</button></div>
    <div class="d-hero">${ring(o.score, 132, 7, true)}<span class="mono">совпадение с вашими навыками</span>
      <h2>${o.title}</h2>
      <p class="co"><b>${o.budget.label}</b> · ${o.deadline_label}</p>
      <p class="mono">${o.rate_label || ''}${o.rate_label && o.dups ? ' · ' : ''}${o.dups ? `ещё в ${o.dups} ${plural(o.dups, 'чате', 'чатах', 'чатах')}` : ''}</p>
    </div>
    ${o.flags?.length ? html`<div class="flags">${o.flags.map((f) => html`<div class="flag">${I.alert}${f.text}</div>`)}</div>` : ''}
    ${priceBlock(o)}
    <div class="sec"><span class="mono">Почему так${(o.scored_by && o.scored_by !== 'rules') ? ' · оценил ИИ' : ''}</span><ul class="fit">${(o.fit || []).map((f) => html`<li class="${f.ok ? 'ok' : 'no'}">${f.ok ? I.check : I.alert}<span>${f.text}</span></li>`)}</ul></div>
    ${clientBlock(o)}
    ${sourcesBlock(o)}
    ${draftBlock(o)}
    <div class="sec stages" id="stages"${o.offered ? '' : raw(' hidden')}>${stagesBlock(o)}</div>
    <div class="sec rate-row"><span class="mono">Оценить заказ</span><div class="rate-btns">
      <button class="btn" type="button" data-act="d-up" aria-pressed="${o.feedback === 'up'}">${I.up}Подходит</button>
      <button class="btn" type="button" data-act="d-dn" aria-pressed="${o.feedback === 'down'}"><span class="flip">${I.up}</span>Не то</button></div></div>
    ${ctaBlock(o)}`;
}

export async function openOrder(id, { focus } = {}) {
  let o;
  try {
    o = await get(`/orders/${id}`);
  } catch (e) {
    toast(e.message);
    return;
  }
  const tone = o.draft?.tone || S.profile?.tone || 'warm';
  const mine = { o, tone, variant: o.draft?.variant || 0, price: o.draft?.price ?? defaultPrice(o), text: o.draft?.text || '', busy: false };
  D = mine;
  openSheet(body(o), { onClose: () => { if (D === mine) D = null; } }); // второй тап по карточке не должен сломать открытую шторку
  const ta = $('#draft');
  if (ta) {
    ta.value = D.text;
    autosize(ta);
    ta.addEventListener('input', () => { D.text = ta.value; autosize(ta); });
  }
  if (o.verdict !== 'scam' && !D.text) await loadDraft();
  if (focus === 'draft') ta?.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

const autosize = (ta) => { ta.style.height = 'auto'; ta.style.height = `${ta.scrollHeight + 4}px`; };

function defaultPrice(o) {
  if (o.rec?.min == null) return null;
  const mid = (o.rec.min + o.rec.max) / 2;
  const step = mid >= 10000 ? 1000 : 500;
  return Math.round(mid / step) * step;
}

async function loadDraft({ force = false } = {}) {
  const ta = $('#draft');
  if (!D || !ta) return;
  const mine = D;
  const seq = (mine.seq = (mine.seq || 0) + 1);
  mine.busy = true;
  ta.classList.add('busy');
  try {
    const r = await post(`/orders/${mine.o.id}/draft`, { tone: mine.tone, variant: mine.variant, price: mine.price ?? undefined, force });
    if (D !== mine || seq !== mine.seq) return; // пока ждали, закрыли заказ или выбрали другой тон: старый ответ не нужен
    D.text = r.text;
    if (r.price != null) D.price = r.price;
    ta.value = r.text;
    autosize(ta);
    const pv = $('#price-v');
    if (pv) pv.textContent = D.price ? money(D.price) : '—';
  } catch (e) {
    toast(e.message);
  } finally {
    if (D === mine && seq === mine.seq) { mine.busy = false; ta.classList.remove('busy'); }
  }
}

function syncCard(card) {
  if (!D) return;
  D.o = { ...D.o, ...card };
  const feedCard = S.feed?.items.find((x) => x.id === card.id);
  if (feedCard) replaceCard({ ...feedCard, ...card });
}

async function refreshLists() {
  try { await loadFeed(); } catch { /* офлайн */ }
  paintChips();
  paintList();
}

export async function giveFeedback(id, kind, reason) {
  try {
    const r = await post(`/orders/${id}/feedback`, { kind, reason });
    const old = S.feed?.items.find((x) => x.id === id);
    if (old) replaceCard({ ...old, ...r.order });
    if (D && D.o.id === id) {
      syncCard(r.order);
      const up = $('[data-act="d-up"]'); const dn = $('[data-act="d-dn"]');
      up?.setAttribute('aria-pressed', String(r.order.feedback === 'up'));
      dn?.setAttribute('aria-pressed', String(r.order.feedback === 'down'));
    }
    if (kind === 'up') toast('Запомнил: похожих станет больше');
    if (kind === 'down') toast('Скрыл. Учту при подборе.', { action: 'Вернуть', onAction: () => giveFeedback(id, 'clear') });
    await refreshLists();
  } catch (e) {
    toast(e.message);
  }
}

export function askWhy(id) {
  const o = S.feed?.items.find((x) => x.id === id) || D?.o;
  const sheetWasOpen = sheetOpen();
  const content = html`${sheetHead('Что не так?', 'учу бота')}
    <p class="capnote" style="margin-top:8px">«${o?.title || ''}». Ответ поможет подбирать точнее.</p>
    <div class="reasons">${REASONS.map(([code, label]) => raw(`<button class="chip" type="button" data-act="reason" data-id="${id}" data-v="${code}">${esc(label)}</button>`))}</div>
    <div style="height:24px"></div>`;
  if (sheetWasOpen) setSheet(content);
  else openSheet(content);
}

export async function chooseReason(id, code) {
  closeSheet();
  await giveFeedback(id, 'down', code);
}

// ------------------------------------------------------------- действия ----
async function sendOffer() {
  const o = D.o;
  const text = $('#draft')?.value || D.text;
  const url = o.contact?.url || o.message_link;
  const copied = copyText(text); // сразу, пока длится нажатие
  const opened = o.contact?.url ? openLink(url) : false;
  const ok = await copied;
  try {
    const r = await post(`/orders/${o.id}/offer`, { done: true, price: D.price || undefined });
    syncCard(r.order);
    if (!D || !$('#stages')) return; // заказ успели закрыть
    $('#stages').hidden = false;
    paint($('#stages'), stagesBlock(D.o));
    $('[data-act="cta"]').lastChild.textContent = 'Скопировать ещё раз';
    paintList();
    loadFeed().then(paintAll).catch(() => {});
  } catch (e) {
    toast(e.message);
    return;
  }
  toast(ok ? (opened ? 'Текст скопирован. Вставьте его в чате заказчика.' : 'Текст скопирован. Откройте чат с заказчиком и вставьте его.') : 'Не получилось скопировать. Выделите текст и скопируйте вручную.');
}

export const detailActions = {
  tone: async (el) => {
    if (!D) return;
    D.tone = el.dataset.v;
    D.variant = 0;
    document.querySelectorAll('#tone button').forEach((b) => b.setAttribute('aria-pressed', String(b === el)));
    await loadDraft();
  },
  redo: async () => { if (D) { D.variant += 1; await loadDraft({ force: true }); } },
  copy: async () => {
    if (!D) return;
    toast((await copyText($('#draft').value)) ? 'Текст скопирован' : 'Не получилось скопировать. Выделите текст и скопируйте вручную.');
  },
  cta: async (el) => {
    if (!D || el.dataset.busy) return;
    el.dataset.busy = '1';
    try { await sendOffer(); } finally { delete el.dataset.busy; }
  },
  stage: async (el) => {
    if (!D) return;
    const stage = el.dataset.v;
    try {
      const r = await post(`/orders/${D.o.id}/stage`, { stage });
      syncCard(r.order);
      paint($('#stages'), stagesBlock(D.o));
      if (stage === 'work') toast(`Заказ в работе на ${money(D.o.price_offered || D.o.budget.rub_max || 0)}. Он попал в аналитику.`);
      loadFeed().then(paintAll).catch(() => {});
    } catch (e) { toast(e.message); }
  },
  price: () => openPrice('price'),
  amount: () => openPrice('amount'),
  'd-up': () => D && giveFeedback(D.o.id, D.o.feedback === 'up' ? 'clear' : 'up'),
  'd-dn': () => D && (D.o.feedback === 'down' ? giveFeedback(D.o.id, 'clear') : askWhy(D.o.id)),
  reason: (el) => chooseReason(+el.dataset.id, el.dataset.v),
  'skip-order': async () => { if (D) { const id = D.o.id; closeSheet(); await giveFeedback(id, 'down', 'scam'); } },
};

// ------------------------------------------------------------------ цена ----
function openPrice(mode) {
  if (!D) return;
  const o = D.o;
  const cur = mode === 'amount' ? (o.price_offered || o.budget.rub_max || D.price || 0) : (D.price || defaultPrice(o) || 0);
  const quick = o.rec?.min != null ? [o.rec.min, Math.round((o.rec.min + o.rec.max) / 2000) * 1000, o.rec.max] : [];
  setSheet(html`${sheetHead(mode === 'amount' ? 'За сколько взяли заказ' : 'Ваша цена', mode === 'amount' ? 'для аналитики' : 'в предложении')}
    <div class="price-box"><input id="price-in" class="inp big-in" type="text" inputmode="numeric" autocomplete="off" value="${cur}" aria-label="Сумма в рублях" data-autofocus><span>₽</span></div>
    ${quick.length ? html`<div class="chips wrap">${quick.map((q) => raw(`<button class="chip" type="button" data-act="price-pick" data-v="${q}">${esc(money(q))}</button>`))}</div>` : ''}
    <div class="btnrow"><button class="btn" type="button" data-act="price-cancel">Назад</button><button class="btn pri" type="button" data-act="price-ok" data-mode="${mode}">Готово</button></div>`);
  setTimeout(() => $('#price-in')?.select(), 60);
}

export const priceActions = {
  'price-pick': (el) => { $('#price-in').value = el.dataset.v; },
  'price-cancel': () => { if (D) reopen(); },
  'price-ok': async (el) => {
    const n = parseInt(($('#price-in').value || '').replace(/\D/g, ''), 10);
    if (!n || n < 0) { toast('Введите сумму цифрами'); return; }
    const id = D.o.id;
    if (el.dataset.mode === 'amount') {
      const r = await post(`/orders/${id}/stage`, { stage: 'work', amount: n });
      syncCard(r.order);
      toast(`Заказ в работе на ${money(n)}`);
      loadFeed().then(paintAll).catch(() => {});
      reopen();
    } else {
      D.price = n;
      reopen();
      await loadDraft({ force: true });
    }
  },
};

function reopen() {
  const o = D.o;
  setSheet(body(o));
  const ta = $('#draft');
  if (ta) {
    ta.value = D.text;
    autosize(ta);
    ta.addEventListener('input', () => { D.text = ta.value; autosize(ta); });
  }
}
