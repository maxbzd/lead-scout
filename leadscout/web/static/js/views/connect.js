// Подключения: Telegram-аккаунт, бот, ключ Claude. Каждое по шагам, простыми словами.

import { del, get, post, put } from '../api.js';
import { S, loadStatus } from '../state.js';
import { $, closeSheet, esc, html, I, openSheet, raw, setSheet, sheetHead, toast } from '../ui.js';
import { openChats } from './chats.js';
import { paintAll, refreshFeed } from './feed.js';
import { renderProfile } from './profile.js';

let C = null; // {kind, step, error, busy, phone}
let pollTimer = null;

const stopPoll = () => { clearInterval(pollTimer); pollTimer = null; };

export async function openConnect(kind) {
  await loadStatus().catch(() => {});
  C = { kind, step: firstStep(kind), error: '', busy: false, phone: '', presets: null, provider: '', models: null };
  if (C.step === 'ai-pick') await loadPresets();
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
  if (kind === 'cloud') return st.cloud?.connected ? 'cloud-done' : 'cloud-form';
  return st.llm.mode !== 'rules' ? 'ai-done' : 'ai-pick';
}

async function loadPresets() {
  try { C.presets = (await get('/connect/ai/providers')).items; } catch (e) { C.presets = []; C.error = e.message; }
}

export async function setAiControl(patch) {
  const r = await put('/ai/control', patch);
  S.status.llm = r.llm;
  return r.llm;
}

const preset = () => C.presets?.find((p) => p.id === C.provider);

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
        <p class="mono note-sm">Ключи хранятся только у вас, в зашифрованном виде.</p>`;
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
    case 'cloud-form':
      return html`${sheetHead('Облачный сайт')}
        <p class="lead-p">Свой сайт, который открывается с телефона из любого места. Заказы и ваши оценки хранятся в облаке, а читает чаты и думает эта программа на компьютере.</p>
        <ol class="howto">
          <li>Разверните облако по инструкции <b>docs/ЗАПУСК.md</b>, раздел про облачный сайт (это один раз, бесплатно).</li>
          <li>Вставьте адрес сайта вида <b>https://scout.имя.workers.dev</b>.</li>
          <li>Вставьте ключ синхронизации: тот же, что вы задали в облаке под именем <b>SYNC_TOKEN</b>.</li>
        </ol>
        ${field('cloud-url', 'Адрес сайта', { ph: 'https://scout.ваше-имя.workers.dev', auto: true })}
        ${field('cloud-token', 'Ключ синхронизации', { type: 'password' })}
        ${err()}
        <div class="btnrow one"><button class="btn pri" type="button" data-act="cloud-go" ${busyAttr()}>Подключить</button></div>
        <p class="mono note-sm">Пока программа на компьютере закрыта, сайт показывает последние данные, а ваши действия ждут в очереди.</p>`;
    case 'cloud-done': {
      const cl = st.cloud;
      return html`${sheetHead('Облачный сайт подключён')}
        <div class="okbox">${I.check}<div><b>${cl.url}</b><p>${cl.error ? cl.error : cl.last_ok ? 'Данные отправлены. Действия с сайта выполняются, пока эта программа открыта.' : 'Отправляю первые данные…'}</p></div></div>
        ${cl.source === 'env' ? html`<p class="capnote">Адрес задан в файле .env, поэтому отключить его отсюда нельзя.</p>` : ''}
        <div class="btnrow"><a class="btn pri" href="${cl.url}" target="_blank" rel="noopener noreferrer">Открыть сайт</a>${cl.source === 'env' ? html`<button class="btn" type="button" data-act="close">Готово</button>` : html`<button class="btn danger" type="button" data-act="cloud-off">Отключить</button>`}</div>`;
    }
    case 'ai-pick':
      return html`${sheetHead('ИИ-режим')}
        <p class="lead-p">Сейчас бот работает по простым правилам: этого хватает, чтобы начать. ИИ лучше понимает смысл сообщений, точнее оценивает заказы и пишет живые отклики. Выберите сервис.</p>
        <div class="picks">
          ${(C.presets || []).map((p) => html`<button class="pick" type="button" data-act="ai-choose" data-v="${p.id}"><b>${p.label}</b><span>${p.note}</span></button>`)}
          <button class="pick" type="button" data-act="ai-choose" data-v="claude"><b>Claude (платно, точнее всех)</b><span>Лучшее качество отборов и текстов. Оплата по расходу, смотрите «Аналитику».</span></button>
        </div>
        ${err()}`;
    case 'ai-free': {
      const p = preset();
      if (!p) return '';
      return html`${sheetHead(p.label)}
        <p class="lead-p">${p.note}</p>
        <ol class="howto">
          ${p.key_url ? html`<li>Откройте <a href="${p.key_url}" target="_blank" rel="noopener noreferrer">страницу ключей</a>, войдите и создайте ключ.</li>` : html`<li>Укажите адрес сервиса и название модели.</li>`}
          <li>Вставьте ключ ниже${p.needs_key ? '' : ' (для своей модели он не нужен)'}.</li>
          <li>Нажмите проверку: приложение сделает один пробный запрос.</li>
        </ol>
        ${p.needs_key ? html`<label class="lbl" for="ai-free-key">Ключ (можно несколько, по одному в строке)</label><textarea class="inp secret" id="ai-free-key" rows="2" placeholder="Вставьте ключ" autocomplete="off" autocapitalize="off" spellcheck="false" data-autofocus></textarea>` : ''}
        ${p.id === 'custom' || p.id === 'ollama' ? field('ai-free-url', 'Адрес сервиса', { type: 'url', ph: 'https://…/v1', value: p.base_url }) : ''}
        ${field('ai-free-model', 'Модель', { ph: p.model || 'название модели', value: p.model })}
        ${C.models?.length ? html`<div class="chips wrap">${C.models.slice(0, 24).map((m) => raw(`<button class="chip" type="button" data-act="ai-model-pick" data-v="${esc(m)}">${esc(m)}</button>`))}</div>` : html`<button class="linkish" type="button" data-act="ai-models">Показать доступные модели</button>`}
        <p class="mono note-sm">Названия моделей у сервисов меняются. Если проверка пишет «модель не найдена», возьмите актуальное имя на странице сервиса.</p>
        ${p.rpd < 100000 ? html`<p class="capnote">Дневной лимит этого тарифа примерно ${p.rpd} запросов. Когда он кончится, бот до утра работает по правилам.</p>` : ''}
        ${p.id === 'gemini' ? html`<p class="capnote">Важно: на бесплатном тарифе Google может использовать присланные тексты для улучшения своих продуктов. Читайте условия сервиса, в чатах бывают чужие данные.</p>` : ''}
        ${err()}
        <div class="btnrow"><button class="btn" type="button" data-act="ai-back">Назад</button><button class="btn pri" type="button" data-act="ai-free-go" ${busyAttr()}>Проверить и подключить</button></div>`;
    }
    case 'ai-key':
      return html`${sheetHead('ИИ-режим (Claude)')}
        <p class="lead-p">С ключом Claude бот понимает смысл сообщений, точнее оценивает заказы и пишет живые отклики.</p>
        <ol class="howto">
          <li>Откройте <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener noreferrer">console.anthropic.com</a>, войдите и создайте ключ (Create Key).</li>
          <li>Пополните баланс на несколько долларов. Расход зависит от числа чатов: начните с 5–10 чатов, а точную сумму смотрите в «Аналитике».</li>
          <li>Вставьте ключ сюда. Он начинается с <b>sk-ant-</b>.</li>
        </ol>
        ${field('ai-key', 'Ключ Claude', { type: 'password', ph: 'sk-ant-…', auto: true })}
        ${err()}
        <div class="btnrow"><button class="btn" type="button" data-act="ai-back">Назад</button><button class="btn pri" type="button" data-act="ai-key" ${busyAttr()}>Проверить и подключить</button></div>`;
    case 'ai-done': {
      const l = st.llm;
      const u = l.usage;
      const free = l.mode === 'ai';
      return html`${sheetHead('ИИ-режим включён')}
        <div class="okbox">${I.check}<div><b>${free ? `Подключено: ${l.model}` : 'Claude подключён'}</b><p>${l.error ? html`<span class="warn-t">Сейчас есть проблема: ${l.error}. Пока работаю по простым правилам.</span>` : 'Бот понимает смысл заказов и пишет отклики.'}</p>
          ${free ? html`<p>Сегодня запросов: ${l.used_today} из ${l.limit_today}. Когда лимит кончится, бот до утра работает по правилам.</p>` : u ? html`<p>В этом месяце потрачено ≈ $${(u.usd || 0).toFixed(2)} (${u.calls} запросов).</p>` : ''}</div></div>
        ${free ? keysBlock(l) : ''}
        ${controlBlock(l.control)}
        <div class="btnrow">${l.source === 'app' ? html`<button class="btn danger" type="button" data-act="${free ? 'ai-free-off' : 'ai-off'}">Отключить</button>` : ''}<button class="btn pri" type="button" data-act="close">Готово</button></div>`;
    }
    default:
      return '';
  }
}

const KEY_STATE = { ok: 'работает', limit: 'лимит на сегодня', blocked: 'ждёт', bad: 'не подошёл' };

function keysBlock(l) {
  const keys = l.keys || [];
  const editable = l.source === 'app';
  return html`<div class="sec"><span class="mono">Ключи (${keys.length})</span>
    <p class="capnote" style="margin-top:8px">Запросы распределяются между ключами поровну, а когда у одного кончился лимит, бот берёт другой. Лимит считает сам сервис на проект: ключи из одного проекта делят один лимит, поэтому создавайте ключи в разных проектах.</p>
    ${keys.map((k, i) => html`<div class="sw-row"><div><div class="t">${k.mask} <span class="st ${k.state === 'ok' ? 'ok' : 'off'}">${KEY_STATE[k.state] || k.state}</span></div><div class="s">сегодня ${k.used} из ${k.limit}</div></div>${editable ? html`<button class="btn small" type="button" data-act="ai-key-del" data-i="${i}">Убрать</button>` : ''}</div>`)}
    ${editable ? html`<label class="lbl" for="ai-add-key">Добавить ещё ключ</label><div class="row-inp"><input class="inp secret" id="ai-add-key" type="text" placeholder="Вставьте ключ" autocomplete="off" spellcheck="false"><button class="btn small" type="button" data-act="ai-key-add" ${C.busy ? raw('disabled') : ''}>Добавить</button></div>` : html`<p class="capnote">Ключи заданы в файле .env: менять их нужно там.</p>`}
    ${err()}
  </div>`;
}

function controlBlock(c) {
  if (!c) return '';
  const state = c.reason === 'manual' ? 'ИИ на паузе: вы поставили её вручную.' : c.reason === 'backlog' ? `ИИ на паузе сам: в ленте уже ${c.backlog} неразобранных заказов.` : `Неразобранных заказов в ленте: ${c.backlog}.`;
  return html`<div class="sec"><span class="mono">Пауза, чтобы не тратить запросы</span>
    <p class="capnote" style="margin-top:8px">${state} На паузе бот читает чаты и оценивает заказы бесплатными правилами. Черновики по вашему нажатию пишутся как обычно.</p>
    <div class="sw-row"><div><div class="t">Пауза ИИ</div><div class="s">Выключить ИИ, пока вы не включите обратно</div></div><button class="sw" role="switch" aria-checked="${!!c.paused}" data-act="ai-pause" aria-label="Пауза ИИ"></button></div>
    <div class="sw-row"><div><div class="t">Пауза, когда накопилось много</div><div class="s">Включится сама и выключится, когда вы разберёте заказы</div></div><button class="sw" role="switch" aria-checked="${!!c.auto}" data-act="ai-auto" aria-label="Автопауза"></button></div>
    ${c.auto ? html`<div class="mono" style="margin-top:6px">неразобранных, при которых ставить на паузу</div><div class="chips wrap">${[20, 40, 80, 150].map((n) => raw(`<button class="chip${c.limit === n ? ' on' : ''}" type="button" data-act="ai-limit" data-v="${n}">${n}</button>`))}</div>` : ''}
  </div>`;
}

function rerender(step) {
  if (!C) return;
  const same = !step || step === C.step;
  // после ошибки введённое не должно пропадать: запоминаем поля и возвращаем их на место
  const kept = same ? Array.from(document.querySelectorAll('#sheet input[id]')).map((i) => [i.id, i.value]) : [];
  if (step) C.step = step;
  setSheet(view());
  for (const [id, value] of kept) {
    const el = document.getElementById(id);
    if (el && !el.value) el.value = value;
  }
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
  'cloud-go': () => run(async () => {
    await post('/connect/cloud', { url: val('cloud-url'), token: val('cloud-token') });
    await loadStatus();
    toast('Облако подключено. Отправляю данные.');
    rerender('cloud-done');
  }),
  'cloud-off': () => run(async () => {
    await del('/connect/cloud');
    await loadStatus();
    rerender('cloud-form');
  }),
  'ai-pause': () => run(async () => { await setAiControl({ paused: !S.status.llm.control.paused }); rerender('ai-done'); }),
  'ai-auto': () => run(async () => { await setAiControl({ auto: !S.status.llm.control.auto }); rerender('ai-done'); }),
  'ai-limit': (el) => run(async () => { await setAiControl({ limit: +el.dataset.v }); rerender('ai-done'); }),
  'ai-models': () => run(async () => {
    const r = await post('/connect/ai/models', { provider: C.provider, key: val('ai-free-key'), base_url: val('ai-free-url') });
    C.models = r.items;
    if (!r.items.length) throw new Error('Сервис не вернул список моделей');
    rerender();
    toast(`Доступно моделей: ${r.items.length}. Выберите в поле «Модель».`);
  }),
  'ai-model-pick': (el) => { document.getElementById('ai-free-model').value = el.dataset.v; },
  'ai-choose': async (el) => {
    C.error = '';
    if (el.dataset.v === 'claude') return rerender('ai-key');
    C.provider = el.dataset.v;
    rerender('ai-free');
  },
  'ai-back': async () => { C.error = ''; if (!C.presets) await loadPresets(); rerender('ai-pick'); },
  'ai-free-go': () => run(async () => {
    const p = preset();
    await post('/connect/ai', { provider: p.id, key: val('ai-free-key'), model: val('ai-free-model'), base_url: val('ai-free-url') });
    await loadStatus();
    toast('ИИ подключён');
    rerender('ai-done');
  }),
  'ai-key-add': () => run(async () => {
    await post('/connect/ai/keys', { key: val('ai-add-key') });
    await loadStatus();
    toast('Ключ добавлен');
    rerender('ai-done');
  }),
  'ai-key-del': (el) => run(async () => {
    await del(`/connect/ai/keys/${+el.dataset.i}`);
    await loadStatus();
    rerender(S.status.llm.mode === 'rules' ? 'ai-pick' : 'ai-done');
  }),
  'ai-free-off': () => run(async () => {
    await del('/connect/ai');
    await loadStatus();
    await loadPresets();
    rerender('ai-pick');
  }),
  'ai-off': () => run(async () => {
    await del('/connect/claude');
    await loadStatus();
    await loadPresets();
    rerender('ai-pick');
  }),
};

async function telegramDone() {
  await loadStatus();
  toast('Telegram подключён. Теперь выберите чаты.');
  rerender('tg-done');
  refreshFeed();
}

