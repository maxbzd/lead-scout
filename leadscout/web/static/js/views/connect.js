// Подключения: Telegram-аккаунт, бот, ключ Claude. Каждое по шагам, простыми словами.

import { del, post } from '../api.js';
import { S, loadStatus } from '../state.js';
import { $, closeSheet, html, I, openSheet, raw, setSheet, sheetHead, toast } from '../ui.js';
import { openChats } from './chats.js';
import { paintAll, refreshFeed } from './feed.js';
import { renderProfile } from './profile.js';

let C = null; // {kind, step, error, busy, phone}
let pollTimer = null;

const stopPoll = () => { clearInterval(pollTimer); pollTimer = null; };

export async function openConnect(kind) {
  await loadStatus().catch(() => {});
  C = { kind, step: firstStep(kind), error: '', busy: false, phone: '' };
  openSheet(view(), { onClose: () => { stopPoll(); C = null; renderProfileSafe(); } });
  if (C.step === 'pair') startPoll();
  focusInput();
}

const renderProfileSafe = () => { try { if (S.tab === 'me') renderProfile(); paintAll(); } catch { /* вкладка ещё не открыта */ } };

function firstStep(kind) {
  const st = S.status;
  if (kind === 'telegram') {
    if (st.telegram.state === 'ready') return 'tg-done';
    return st.telegram.has_api ? 'tg-phone' : 'tg-api';
  }
  if (kind === 'bot') {
    if (st.bot.state === 'ready') return st.bot.paired ? 'bot-done' : 'pair';
    return 'bot-token';
  }
  return st.llm.mode === 'claude' ? 'ai-done' : 'ai-key';
}

function focusInput() {
  setTimeout(() => document.querySelector('#sheet [data-autofocus]')?.focus(), 80);
}

const err = () => (C.error ? html`<p class="form-err" role="alert">${C.error}</p>` : '');
const busyAttr = () => (C.busy ? raw(' disabled') : '');

function field(id, label, { type = 'text', ph = '', mode = '', value = '', auto = false } = {}) {
  return html`<label class="lbl" for="${id}">${label}</label><input class="inp" id="${id}" type="${type}" placeholder="${ph}" ${mode ? raw(`inputmode="${mode}"`) : ''} value="${value}" autocomplete="off" autocapitalize="off" spellcheck="false" ${auto ? raw('data-autofocus') : ''}>`;
}

function steps(n, total) {
  return html`<div class="dots" aria-label="Шаг ${n} из ${total}">${Array.from({ length: total }, (_, i) => html`<i class="${i < n ? 'on' : ''}"></i>`)}</div>`;
}

function view() {
  const st = S.status;
  switch (C.step) {
    case 'tg-api':
      return html`${sheetHead('Подключаем Telegram', 'шаг 1 из 3')}${steps(1, 3)}
        <p class="lead-p">Бот будет читать чаты с заказами из вашего аккаунта, как будто вы сами их открыли. Он ничего не пишет и не отправляет от вашего имени.</p>
        <ol class="howto">
          <li>Откройте <a href="https://my.telegram.org/apps" target="_blank" rel="noopener noreferrer">my.telegram.org/apps</a> и войдите по номеру телефона.</li>
          <li>Нажмите «Create application». Название любое, например Scout. Остальное можно не заполнять.</li>
          <li>Скопируйте сюда два значения: <b>App api_id</b> (число) и <b>App api_hash</b> (длинный код).</li>
        </ol>
        ${field('api-id', 'api_id', { mode: 'numeric', ph: '1234567', auto: true })}
        ${field('api-hash', 'api_hash', { ph: '32 символа из букв и цифр' })}
        ${err()}
        <div class="btnrow one"><button class="btn pri" type="button" data-act="tg-api" ${busyAttr()}>Дальше</button></div>
        <p class="mono note-sm">Ключи хранятся на вашем сервере в зашифрованном виде.</p>`;
    case 'tg-phone':
      return html`${sheetHead('Подключаем Telegram', 'шаг 2 из 3')}${steps(2, 3)}
        <p class="lead-p">Введите номер, на который зарегистрирован Telegram. Код придёт в само приложение Telegram, в чат «Telegram».</p>
        ${field('phone', 'Номер телефона', { type: 'tel', mode: 'tel', ph: '+7 999 123-45-67', value: C.phone, auto: true })}
        ${err()}
        <div class="btnrow one"><button class="btn pri" type="button" data-act="tg-phone" ${busyAttr()}>Получить код</button></div>
        <button class="linkish center" type="button" data-act="tg-reset-api">Изменить api_id и api_hash</button>`;
    case 'tg-code':
      return html`${sheetHead('Код из Telegram', 'шаг 3 из 3')}${steps(3, 3)}
        <p class="lead-p">Мы отправили код на ${C.phone}. Откройте Telegram и найдите сообщение от «Telegram».</p>
        ${field('code', 'Код', { mode: 'numeric', ph: '12345', auto: true })}
        ${err()}
        <div class="btnrow one"><button class="btn pri" type="button" data-act="tg-code" ${busyAttr()}>Войти</button></div>
        <button class="linkish center" type="button" data-act="tg-back">Ввести другой номер</button>`;
    case 'tg-password':
      return html`${sheetHead('Пароль Telegram', 'шаг 3 из 3')}${steps(3, 3)}
        <p class="lead-p">У вас включена двухфакторная защита. Введите облачный пароль, который вы задавали в настройках Telegram.</p>
        ${field('tg-pass', 'Пароль', { type: 'password', auto: true })}
        ${err()}
        <div class="btnrow one"><button class="btn pri" type="button" data-act="tg-password" ${busyAttr()}>Войти</button></div>`;
    case 'tg-done': {
      const me = st.telegram.me;
      return html`${sheetHead('Telegram подключён')}
        <div class="okbox">${I.check}<div><b>${me?.name || 'Аккаунт'}</b>${me?.username ? html`<span> @${me.username}</span>` : ''}<p>Бот читает чаты, которые вы выберете. Если Telegram покажет новое устройство «Lead Scout», так и должно быть.</p></div></div>
        ${st.telegram.source === 'env' ? html`<p class="capnote">Вход задан на сервере (в файле .env), поэтому выйти отсюда нельзя.</p>` : ''}
        <div class="btnrow"><button class="btn pri" type="button" data-act="goto-chats">Выбрать чаты</button>${st.telegram.source === 'app' ? html`<button class="btn danger" type="button" data-act="tg-logout">Выйти</button>` : html`<button class="btn" type="button" data-act="close">Готово</button>`}</div>`;
    }
    case 'bot-token':
      return html`${sheetHead('Бот в Telegram', 'шаг 1 из 2')}${steps(1, 2)}
        <p class="lead-p">Бот будет присылать вам свежие заказы и утреннюю сводку, а кнопки «подходит» и «не то» работают прямо в чате.</p>
        <ol class="howto">
          <li>Откройте <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer">@BotFather</a> в Telegram и отправьте ему <b>/newbot</b>.</li>
          <li>Придумайте имя бота и его адрес (он должен заканчиваться на bot).</li>
          <li>BotFather пришлёт токен: длинная строка с двоеточием. Вставьте её сюда.</li>
        </ol>
        ${field('bot-token', 'Токен бота', { ph: '123456789:AA…', auto: true })}
        ${err()}
        <div class="btnrow one"><button class="btn pri" type="button" data-act="bot-token" ${busyAttr()}>Дальше</button></div>`;
    case 'pair': {
      const b = st.bot;
      return html`${sheetHead('Бот в Telegram', 'шаг 2 из 2')}${steps(2, 2)}
        <p class="lead-p">Бот @${b.username} создан. Осталось сказать ему, что вы его хозяин: откройте его и нажмите Start.</p>
        ${b.pair_link ? html`<div class="btnrow one"><a class="btn pri" href="${b.pair_link}" target="_blank" rel="noopener noreferrer">${I.tg}Открыть бота</a></div>` : ''}
        <p class="capnote center"><span class="pulse-dot"></span>Жду, когда вы нажмёте Start…</p>
        ${err()}`;
    }
    case 'bot-done': {
      const b = st.bot;
      return html`${sheetHead('Бот подключён')}
        <div class="okbox">${I.check}<div><b>@${b.username}</b><p>Присылает свежие заказы, утреннюю сводку и принимает ваши оценки кнопками. Команды: /digest, /stats, /chats, /add, /pause.</p></div></div>
        <div class="btnrow">${b.pair_link ? '' : html`<a class="btn" href="https://t.me/${b.username}" target="_blank" rel="noopener noreferrer">Открыть бота</a>`}${b.source === 'app' ? html`<button class="btn danger" type="button" data-act="bot-off">Отключить</button>` : html`<button class="btn" type="button" data-act="close">Готово</button>`}</div>`;
    }
    case 'ai-key':
      return html`${sheetHead('ИИ-режим (Claude)')}
        <p class="lead-p">Сейчас бот работает по простым правилам: этого хватает, чтобы начать. С ключом Claude он понимает смысл сообщений, точнее оценивает заказы и пишет живые отклики.</p>
        <ol class="howto">
          <li>Откройте <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener noreferrer">console.anthropic.com</a>, войдите и создайте ключ (Create Key).</li>
          <li>Пополните баланс на несколько долларов. Бот тратит примерно 3–10 $ в месяц.</li>
          <li>Вставьте ключ сюда. Он начинается с <b>sk-ant-</b>.</li>
        </ol>
        ${field('ai-key', 'Ключ Claude', { type: 'password', ph: 'sk-ant-…', auto: true })}
        ${err()}
        <div class="btnrow one"><button class="btn pri" type="button" data-act="ai-key" ${busyAttr()}>Проверить и подключить</button></div>`;
    case 'ai-done': {
      const l = st.llm;
      const u = l.usage;
      return html`${sheetHead('ИИ-режим включён')}
        <div class="okbox">${I.check}<div><b>Claude подключён</b><p>${l.error ? html`<span class="warn-t">Сейчас есть проблема: ${l.error}. Пока работаю по простым правилам.</span>` : 'Бот понимает смысл заказов и пишет отклики.'}</p>${u ? html`<p>В этом месяце потрачено ≈ $${(u.usd || 0).toFixed(2)} (${u.calls} запросов).</p>` : ''}</div></div>
        <div class="btnrow">${l.source === 'app' ? html`<button class="btn danger" type="button" data-act="ai-off">Отключить</button>` : ''}<button class="btn pri" type="button" data-act="close">Готово</button></div>`;
    }
    default:
      return '';
  }
}

function rerender(step) {
  if (!C) return;
  if (step) C.step = step;
  setSheet(view());
  focusInput();
}

async function run(fn) {
  if (!C || C.busy) return;
  C.busy = true;
  C.error = '';
  setBusy(true);
  try {
    await fn();
  } catch (e) {
    if (C) { C.error = e.message; rerender(); }
  } finally {
    if (C) C.busy = false;
    setBusy(false);
  }
}

function setBusy(on) {
  document.querySelectorAll('#sheet .btn.pri').forEach((b) => { b.disabled = on; });
}

const val = (id) => ($(`#${id}`)?.value || '').trim();

function startPoll() {
  stopPoll();
  pollTimer = setInterval(async () => {
    try {
      await loadStatus();
      if (C && C.kind === 'bot' && S.status.bot.paired) {
        stopPoll();
        toast('Бот подключён. Загляните в Telegram.');
        rerender('bot-done');
      }
    } catch { /* ждём дальше */ }
  }, 2500);
}

export const connectActions = {
  'tg-api': () => run(async () => {
    await post('/connect/telegram/api', { api_id: val('api-id'), api_hash: val('api-hash') });
    await loadStatus();
    rerender('tg-phone');
  }),
  'tg-phone': () => run(async () => {
    C.phone = val('phone');
    await post('/connect/telegram/phone', { phone: C.phone });
    rerender('tg-code');
  }),
  'tg-code': () => run(async () => {
    const r = await post('/connect/telegram/code', { code: val('code') });
    if (r.step === 'password') return rerender('tg-password');
    await telegramDone();
  }),
  'tg-password': () => run(async () => {
    await post('/connect/telegram/password', { password: $('#tg-pass').value });
    await telegramDone();
  }),
  'tg-back': () => rerender('tg-phone'),
  'tg-reset-api': () => rerender('tg-api'),
  'tg-logout': () => run(async () => {
    await del('/connect/telegram');
    await loadStatus();
    toast('Вышли из Telegram. Бот больше не читает чаты.');
    rerender('tg-api');
    refreshFeed();
  }),
  'goto-chats': () => { closeSheet(); setTimeout(openChats, 120); },
  'bot-token': () => run(async () => {
    await post('/connect/bot', { token: val('bot-token') });
    await loadStatus();
    if (S.status.bot.paired) return rerender('bot-done');
    rerender('pair');
    startPoll();
  }),
  'bot-off': () => run(async () => {
    await del('/connect/bot');
    await loadStatus();
    rerender('bot-token');
  }),
  'ai-key': () => run(async () => {
    await post('/connect/claude', { key: val('ai-key') });
    await loadStatus();
    toast('Claude подключён');
    rerender('ai-done');
  }),
  'ai-off': () => run(async () => {
    await del('/connect/claude');
    await loadStatus();
    rerender('ai-key');
  }),
};

async function telegramDone() {
  await loadStatus();
  toast('Telegram подключён. Теперь выберите чаты.');
  rerender('tg-done');
  refreshFeed();
}

