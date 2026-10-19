// Какие чаты читает бот.

import { del, get, patch, post } from '../api.js';
import { S, loadChats, loadStatus } from '../state.js';
import { $, ago, esc, html, I, openSheet, plural, setSheet, sheetHead, toast } from '../ui.js';
import { openConnect } from './connect.js';
import { paintAll } from './feed.js';
import { renderProfile } from './profile.js';

let CH = null; // {dialogs, loading, confirmDel, error}

export async function openChats() {
  CH = { dialogs: null, loading: false, confirmDel: null, error: '' };
  try { await Promise.all([loadChats(), loadStatus()]); } catch (e) { toast(e.message); return; }
  openSheet(view(), { onClose: () => { CH = null; loadStatus().then(() => { if (S.tab === 'me') renderProfile(); paintAll(); }).catch(() => {}); } });
}

function chatRow(c) {
  const meta = [];
  if (c.members) meta.push(`${new Intl.NumberFormat('ru-RU').format(c.members)} участников`);
  if (c.found) meta.push(`подошло ${c.found}`);
  if (c.last_scan_at) meta.push(`проверен ${ago(c.last_scan_at)}`);
  return html`<div class="sw-row">
    <div><div class="t">${c.title}${c.is_demo ? html` <span class="demo-tag">демо</span>` : ''}</div>
      <div class="s">${meta.join(' · ') || 'ещё не проверялся'}</div>
      ${c.last_error ? html`<div class="s bad-t">${c.last_error}</div>` : ''}
      <div class="row-act">${CH.confirmDel === c.id
        ? html`<button type="button" class="linkish bad-t" data-act="chat-del-yes" data-id="${c.id}">Точно удалить вместе с заказами</button> <button type="button" class="linkish" data-act="chat-del-no">Нет</button>`
        : html`<button type="button" class="linkish" data-act="chat-del" data-id="${c.id}">Удалить</button>`}</div></div>
    <button class="sw" role="switch" aria-checked="${!!c.enabled}" data-act="chat-toggle" data-id="${c.id}" aria-label="Читать ${esc(c.title)}"></button>
  </div>`;
}

function dialogsBlock() {
  const tg = S.status.telegram;
  if (tg.state !== 'ready') return '';
  if (CH.dialogs === null) {
    return html`<div class="sec"><span class="mono">Из ваших чатов в Telegram</span>
      <button class="btn" type="button" data-act="chat-dialogs" style="width:100%;margin-top:10px">${CH.loading ? 'Загружаю…' : 'Показать мои чаты'}</button></div>`;
  }
  const rest = CH.dialogs;
  return html`<div class="sec"><span class="mono">Из ваших чатов в Telegram</span>
    ${rest.length ? html`<div class="dlg">${rest.map((d) => html`<div class="sw-row"><div><div class="t">${d.title}</div><div class="s">${d.kind === 'channel' ? 'канал' : 'группа'}${d.username ? ' · @' + d.username : ''}</div></div>
      ${d.added ? html`<span class="added">${I.check}</span>` : html`<button class="btn small" type="button" data-act="chat-add-dialog" data-id="${d.tg_id}">Добавить</button>`}</div>`)}</div>` : html`<p class="capnote">В вашем аккаунте нет групп и каналов.</p>`}</div>`;
}

function view() {
  const items = S.chats?.items || [];
  const tg = S.status.telegram;
  return html`${sheetHead('Чаты с заказами', `${items.filter((c) => c.enabled).length} ${plural(items.filter((c) => c.enabled).length, 'включён', 'включено', 'включено')}`)}
    ${tg.state !== 'ready' && !S.status.demo ? html`<div class="banner warn" style="margin-top:12px">${I.alert}<span>Сначала подключите Telegram, иначе читать нечего.</span><button type="button" data-act="connect-tg-from-chats">Подключить</button></div>` : ''}
    ${S.status.demo ? html`<div class="banner" style="margin-top:12px"><span class="b-dot"></span><span>Это демо-чаты. Настоящие появятся после подключения Telegram.</span></div>` : ''}
    <div class="chat-list">${items.length ? items.map(chatRow) : html`<p class="capnote" style="margin-top:14px">Пока ни одного чата. Добавьте первый ниже.</p>`}</div>
    ${dialogsBlock()}
    <div class="sec"><span class="mono">Добавить по ссылке</span>
      <div class="row-inp"><input class="inp" id="chat-link" type="url" inputmode="url" placeholder="https://t.me/имя_чата" autocomplete="off" autocapitalize="off" spellcheck="false"><button class="btn pri small" type="button" data-act="chat-add">Добавить</button></div>
      ${CH.error ? html`<p class="form-err" role="alert">${CH.error}</p>` : ''}
      <p class="mono note-sm">Подойдёт и закрытый чат по приглашению. Бот вступит в него от вашего имени и только читает.</p></div>`;
}

function redraw() { if (CH) setSheet(view()); }

export const chatActions = {
  'chat-toggle': async (el) => {
    const id = +el.dataset.id;
    const cur = S.chats.items.find((c) => c.id === id);
    try {
      const r = await patch(`/chats/${id}`, { enabled: !cur.enabled });
      S.chats.items = r.items;
      redraw();
    } catch (e) { toast(e.message); }
  },
  'chat-del': (el) => { CH.confirmDel = +el.dataset.id; redraw(); },
  'chat-del-no': () => { CH.confirmDel = null; redraw(); },
  'chat-del-yes': async (el) => {
    try {
      const r = await del(`/chats/${el.dataset.id}`);
      S.chats.items = r.items;
      CH.confirmDel = null;
      redraw();
      toast('Чат удалён');
    } catch (e) { toast(e.message); }
  },
  'chat-add': async () => {
    const link = ($('#chat-link').value || '').trim();
    if (!link) return;
    CH.error = '';
    const btn = document.querySelector('[data-act="chat-add"]');
    btn.disabled = true;
    try {
      const r = await post('/chats', { link });
      S.chats.items = r.items;
      toast('Чат добавлен. Начинаю читать.');
    } catch (e) { CH.error = e.message; }
    redraw();
  },
  'chat-dialogs': async () => {
    CH.loading = true;
    redraw();
    try { CH.dialogs = (await get('/telegram/dialogs')).items; } catch (e) { CH.error = e.message; }
    CH.loading = false;
    redraw();
  },
  'chat-add-dialog': async (el) => {
    el.disabled = true;
    try {
      const r = await post('/chats/from-dialog', { tg_id: +el.dataset.id });
      S.chats.items = r.items;
      const d = CH.dialogs?.find((x) => x.tg_id === +el.dataset.id);
      if (d) d.added = true;
      toast('Чат добавлен. Начинаю читать.');
    } catch (e) { toast(e.message); el.disabled = false; }
    redraw();
  },
  'connect-tg-from-chats': () => { openConnect('telegram'); },
};

