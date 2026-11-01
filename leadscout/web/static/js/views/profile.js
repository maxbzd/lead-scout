// Профиль: навыки, деньги, портфолио, уведомления, подключения.

import { post, put } from '../api.js';
import { S, cache, loadNotif, loadProfile, loadStatus } from '../state.js';
import { $, closeSheet, debounce, esc, html, I, money, openSheet, paint, plural, raw, ring, sheetHead, toast } from '../ui.js';
import { openChats } from './chats.js';
import { openConnect, setAiControl } from './connect.js';
import { refreshFeed } from './feed.js';

const PAY = [['fixed', 'Фикс за проект'], ['hourly', 'Почасовая'], ['retainer', 'Абонемент']];
const CAP = [['free', 'Свободен'], ['busy', 'Немного занят'], ['full', 'Занят']];
const TONES = [['short', 'Коротко'], ['warm', 'Тепло'], ['formal', 'Деловой']];
const ZONES = [
  ['Europe/Kaliningrad', 'Калининград'], ['Europe/Moscow', 'Москва'], ['Europe/Samara', 'Самара'], ['Asia/Yekaterinburg', 'Екатеринбург'],
  ['Asia/Omsk', 'Омск'], ['Asia/Novosibirsk', 'Новосибирск'], ['Asia/Krasnoyarsk', 'Красноярск'], ['Asia/Irkutsk', 'Иркутск'],
  ['Asia/Yakutsk', 'Якутск'], ['Asia/Vladivostok', 'Владивосток'], ['Asia/Kamchatka', 'Камчатка'], ['Europe/Minsk', 'Минск'],
  ['Europe/Kyiv', 'Киев'], ['Asia/Almaty', 'Алматы'], ['Asia/Tbilisi', 'Тбилиси'], ['Asia/Yerevan', 'Ереван'], ['Europe/Berlin', 'Берлин'],
  ['Europe/London', 'Лондон'], ['UTC', 'UTC'],
];

let deferredInstall = null;
window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); deferredInstall = e; });

const standalone = () => window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
const isIos = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

export async function renderProfile() {
  const view = $('#view');
  if (!S.profile || !S.notif) {
    paint(view, html`<section class="view"><div class="skeleton" style="height:200px;margin-top:14px"></div></section>`);
    try { await Promise.all([loadProfile(), loadNotif(), loadStatus()]); } catch (e) { toast(e.message); return; }
    if (S.tab !== 'me') return;
  } else {
    loadStatus().then(() => paintReady()).catch(() => {});
  }
  const p = S.profile;
  const hosted = !!S.status.cloud?.hosted;
  const demoToggle = !hosted && S.status.telegram.state !== 'ready';
  paint(view, html`<section class="view"><div class="set" style="margin-top:14px">
    <div class="glass" id="ready"></div>

    <div class="glass">
      <div class="sw-row"><div><div class="t">Ничего не пропускать</div><div class="s">Сомнительные заказы тоже показываю с пометкой. Всё, что бот отбросил, лежит в ленте в разделе «Отброшено».</div></div>
        <button class="sw" id="recall-sw" role="switch" aria-checked="${!!p.recall}" data-act="recall-toggle" aria-label="Ничего не пропускать"></button></div>
    </div>

    <div class="glass field">
      <div class="card-head"><div class="mono">моя загрузка</div><span class="saved" id="saved">сохранено</span></div>
      <div class="seg" id="cap" style="margin:0"></div>
      <p class="capnote" id="cap-note"></p>
      <div class="mono" style="margin-top:8px">что умеете</div>
      <div class="chips wrap" id="skills"></div>
      <div class="row-inp"><input class="inp" id="skill-in" type="text" maxlength="60" placeholder="Например: парсинг, Make, дизайн" data-enter="skill-add" autocomplete="off"><button class="btn small" type="button" data-act="skill-add">Добавить</button></div>
      <div class="mono" style="margin-top:8px">как берёте оплату</div>
      <div class="chips wrap" id="pay"></div>
      <div class="mono" style="margin-top:8px">бюджет заказа от <span id="min-v" class="ink"></span></div>
      <input id="min" type="range" min="5" max="150" step="5" aria-label="Минимальный бюджет заказа, тысяч рублей" data-bind="min">
      <div class="mono" style="margin-top:4px">ваша ставка за день <span id="rate-v" class="ink"></span></div>
      <input id="rate" type="range" min="1" max="30" step="0.5" aria-label="Ставка за день, тысяч рублей" data-bind="rate">
      <div class="mono" style="margin-top:8px">стоп-слова</div>
      <div class="chips wrap" id="stops"></div>
      <div class="row-inp"><input class="inp" id="stop-in" type="text" maxlength="60" placeholder="Заказы с этим словом не показывать" data-enter="stop-add" autocomplete="off"><button class="btn small" type="button" data-act="stop-add">Добавить</button></div>
    </div>

    <div class="glass field">
      <div class="mono">портфолио для откликов</div>
      <div class="cases" id="cases"></div>
      <label class="mono" for="bio" style="margin-top:6px">о себе в двух строках</label>
      <textarea id="bio" class="pos" maxlength="600" data-bind="bio" placeholder="Что вы делаете и чем отличаетесь. Бот использует это в откликах.">${p.bio}</textarea>
      <div class="mono" style="margin-top:6px">тон откликов</div>
      <div class="seg" id="tone-seg" style="margin:0"></div>
      <div class="btnrow"><button class="btn" type="button" data-act="case-add">Добавить кейс</button><button class="btn" type="button" data-act="import">Собрать из текста</button></div>
    </div>

    <div class="glass">
      <div class="card-head"><div class="mono">чаты</div></div>
      <p class="capnote" id="chats-note"></p>
      ${hosted ? '' : html`<button class="btn" style="width:100%;margin-top:10px" type="button" data-act="open-chats">Управлять чатами</button>`}
      <div class="mono" style="margin-top:14px">как часто проверять</div>
      <div class="seg" id="scan-seg" style="margin:8px 0 0"></div>
      <p class="capnote" style="margin-top:8px">Реже проверка, меньше нагрузка и расход ИИ. Сообщения не теряются: бот каждый раз читает всё накопленное.</p>
    </div>

    <div class="glass" id="notif"></div>

    <div class="glass">
      <div class="mono" style="margin-bottom:6px">подключения</div>
      <div id="conns"></div>
      ${demoToggle ? html`<div class="sw-row"><div><div class="t">Показывать тестовые заказы</div><div class="s">Демо, пока Telegram не подключён</div></div><button class="sw" role="switch" aria-checked="${!!S.status.demo}" data-act="demo-toggle" aria-label="Демо-режим"></button></div>` : ''}
    </div>

    <div class="glass field">
      <div class="mono">часовой пояс</div>
      <select id="tz" class="inp" data-bind="tz" aria-label="Часовой пояс">${ZONES.map(([z, name]) => raw(`<option value="${z}"${p.tz === z ? ' selected' : ''}>${esc(name)}</option>`))}</select>
    </div>

    <div class="glass" id="install"></div>
    ${S.status.local ? html`<div class="glass field" id="update"></div>` : ''}
    ${S.status.local ? html`<button class="btn" type="button" data-act="quit-app" style="width:100%">Закрыть программу</button><p class="capnote center">${S.status.cloud?.connected ? 'Окно можно закрыть, программа продолжит работать и принимать действия с сайта. Этой кнопкой она останавливается совсем.' : 'Закрыв окно, вы тоже остановите программу через пару минут.'}</p>` : html`<button class="btn" type="button" data-act="logout" style="width:100%">Выйти из приложения</button>`}
    <p class="mono center" style="margin:6px 0 0">Scout 1.0</p>
  </div></section>`);
  paintReady();
  paintSkills();
  paintMoney();
  paintPay();
  paintStops();
  paintCases();
  paintTone();
  paintNotif();
  paintConns();
  paintInstall();
  paintUpdate();
  paintChatsNote();
  paintScanSeg();
}

// ------------------------------------------------------------ готовность ----
function paintReady() {
  const el = $('#ready');
  if (!el || !S.status) return;
  const items = S.status.ready;
  const done = items.filter((i) => i.ok).length;
  const pct = Math.round((done / items.length) * 100);
  const hosted = !!S.status.cloud?.hosted;
  const action = hosted ? { cases: 'case-add' } : { chats: 'open-chats', cases: 'case-add', telegram: 'connect-tg', bot: 'connect-bot', ai: 'connect-ai' };
  const btn = { chats: 'Выбрать', cases: 'Добавить', telegram: 'Подключить', bot: 'Подключить', ai: 'Включить' };
  const title = done === items.length ? 'Бот готов к работе' : S.status.demo ? 'Демо-режим' : 'Бот почти готов';
  paint(el, html`<div class="ready"><div>${ring(pct, 72, 6)}</div><div><div class="mono">${done} из ${items.length}</div><h3 class="ready-t">${title}</h3></div></div>
    <div class="todo">${items.map((i) => html`<div><span class="${i.ok ? 'okc' : 'nc'}">${i.ok ? I.check : I.alert}</span><span>${i.label}</span>${!i.ok && action[i.id] ? html`<button class="mini" type="button" data-act="${action[i.id]}">${btn[i.id]}</button>` : html`<span></span>`}</div>`)}</div>`);
}

const SCAN_LABELS = { 0: 'Вручную', 10: '10 мин', 30: '30 мин', 60: '1 час', 120: '2 часа', 360: '6 часов' };

function paintScanSeg() {
  const el = $('#scan-seg');
  if (!el || !S.status) return;
  const cur = S.status.scan.every_min;
  const choices = Object.keys(SCAN_LABELS).map(Number);
  if (!choices.includes(cur)) choices.push(cur);
  paint(el, choices.sort((a, b) => a - b).map((m) => raw(`<button type="button" data-act="scan-every" data-v="${m}" aria-pressed="${m === cur}">${esc(SCAN_LABELS[m] || `${m} мин`)}</button>`)));
}

function paintChatsNote() {
  const el = $('#chats-note');
  if (!el || !S.status) return;
  const on = S.status.ready.find((r) => r.id === 'chats')?.count || 0;
  const every = S.status.scan.every_min;
  const when = every ? `Проверка каждые ${every} мин.` : 'Проверка только по кнопке «Проверить чаты».';
  el.textContent = on ? `Читаю ${on} ${plural(on, 'чат', 'чата', 'чатов')}. ${when}` : 'Пока ни одного чата. Без них бот не найдёт заказов.';
}

// -------------------------------------------------------------- сохранение ----
const flashSaved = debounce(() => { const s = $('#saved'); if (s) { s.classList.add('on'); setTimeout(() => s.classList.remove('on'), 1600); } }, 100);

const saveProfile = debounce(async () => {
  try {
    await put('/profile', S.profile); // ответ не применяем: пока шёл запрос, вы могли успеть напечатать ещё
    flashSaved();
    setTimeout(() => refreshFeed(), 900); // фоновый пересчёт успевает отработать
  } catch (e) { toast(e.message); }
}, 600);

const saveNotif = debounce(async () => {
  try { S.notif = await put('/notifications', S.notif); flashSaved(); } catch (e) { toast(e.message); }
}, 500);

function changed() { saveProfile(); }

// ---------------------------------------------------------------- блоки ----
function chipRm(label, act, i) {
  return raw(`<button class="chip on" type="button" data-act="${act}" data-i="${i}" aria-label="Убрать ${esc(label)}">${esc(label)}<span class="x2">×</span></button>`);
}

function paintSkills() {
  paint($('#skills'), S.profile.skills.length ? S.profile.skills.map((s, i) => chipRm(s, 'skill-rm', i)) : html`<span class="capnote">Добавьте хотя бы один навык, иначе нечего сравнивать.</span>`);
}
function paintStops() {
  paint($('#stops'), S.profile.stop_words.length ? S.profile.stop_words.map((s, i) => chipRm(s, 'stop-rm', i)) : html`<span class="capnote">Пока нет.</span>`);
}
function paintPay() {
  paint($('#pay'), PAY.map(([id, label]) => raw(`<button class="chip${S.profile.pay_types.includes(id) ? ' on' : ''}" type="button" data-act="pay" data-v="${id}">${esc(label)}</button>`)));
}
function paintTone() {
  paint($('#tone-seg'), TONES.map(([id, label]) => raw(`<button type="button" data-act="tone-set" data-v="${id}" aria-pressed="${S.profile.tone === id}">${esc(label)}</button>`)));
}
function paintMoney() {
  const p = S.profile;
  paint($('#cap'), CAP.map(([id, label]) => raw(`<button type="button" data-act="cap" data-v="${id}" aria-pressed="${p.capacity === id}">${esc(label)}</button>`)));
  const min = $('#min');
  const rate = $('#rate');
  min.value = Math.max(5, Math.min(150, Math.round(p.min_budget / 1000)));
  rate.value = Math.max(1, Math.min(30, p.day_rate / 1000));
  sliderFill(min);
  sliderFill(rate);
  $('#min-v').textContent = money(p.min_budget);
  $('#rate-v').textContent = money(p.day_rate);
  $('#cap-note').textContent = `Показываю заказы от ${money(p.min_budget)}. Всё, что дешевле, убираю в «Отсеянные».`;
}
function sliderFill(el) {
  const pc = ((el.value - el.min) / (el.max - el.min)) * 100;
  el.style.background = `linear-gradient(90deg,#8E7BFF ${pc}%,rgba(255,255,255,.12) ${pc}%)`;
}
function paintCases() {
  const cs = S.profile.cases;
  paint($('#cases'), cs.length ? cs.map((c, i) => html`<div><span><b>${c.title}</b>${c.note ? html`<em>${c.note}</em>` : ''}</span><button class="linkish bad-t" type="button" data-act="case-rm" data-i="${i}" aria-label="Удалить кейс">Убрать</button></div>`)
    : html`<p class="capnote">Добавьте 1–3 примера работ. Бот упомянет подходящий в отклике.</p>`);
}

function toggle(label, sub, key, on) {
  return html`<div class="sw-row"><div><div class="t">${label}</div>${sub ? html`<div class="s">${sub}</div>` : ''}</div><button class="sw" role="switch" aria-checked="${on}" data-act="n-toggle" data-k="${key}" aria-label="${label}"></button></div>`;
}

function paintNotif() {
  const n = S.notif;
  paint($('#notif'), html`<div class="mono" style="margin-bottom:4px">уведомления в Telegram</div>
    ${toggle('Сразу присылать новые заказы', 'И подходящие, и «возможно подойдут». Заказы разбирают за минуты', 'instant', n.instant)}
    ${toggle('Не беспокоить ночью', `С ${n.quiet_start} до ${n.quiet_end}, кроме заказов от ${money(n.quiet_override_budget)}`, 'quiet', n.quiet)}
    ${n.quiet ? html`<div class="time-row"><label>С <input class="inp time" type="time" value="${n.quiet_start}" data-bind="quiet_start"></label><label>до <input class="inp time" type="time" value="${n.quiet_end}" data-bind="quiet_end"></label></div>` : ''}
    ${toggle('Утренняя сводка', 'Всё лучшее за ночь одним сообщением', 'digest', n.digest)}
    ${n.digest ? html`<div class="time-row"><label>В <input class="inp time" type="time" value="${n.digest_time}" data-bind="digest_time"></label></div>` : ''}
    ${toggle('Итоги недели', 'По воскресеньям вечером', 'weekly', n.weekly)}
    ${toggle('Пауза', 'Выключить все уведомления. Заказы бот собирает всё равно', 'paused', n.paused)}`);
}

function connRow(id, label, state, sub, ok) {
  const hosted = !!S.status?.cloud?.hosted;
  const btn = hosted ? '' : html`<button class="btn small" type="button" data-act="connect-${id}">${ok ? 'Настроить' : 'Подключить'}</button>`;
  return html`<div class="sw-row"><div><div class="t">${label} <span class="st ${ok ? 'ok' : 'off'}">${state}</span></div><div class="s">${sub}</div></div>${btn}</div>`;
}

function cloudRow(c) {
  const ok = !!c?.connected;
  const sub = !ok ? 'Свой сайт с телефона: данные хранятся в облаке, читает и думает этот компьютер'
    : c.error ? c.error : c.last_ok ? `${c.url.replace(/^https?:\/\//, '')} · обновлено ${new Date(c.last_ok).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}` : 'Подключаюсь…';
  return connRow('cloud', 'Облачный сайт', ok ? (c.error ? 'ошибка' : 'подключён') : 'не подключён', sub, ok && !c.error);
}

export function paintConns() {
  const el = $('#conns');
  const st = S.status;
  if (!el || !st) return;
  const tg = st.telegram;
  const tgOk = tg.state === 'ready';
  const tgSub = tgOk ? `${tg.me?.name || 'Аккаунт'}${tg.me?.username ? ' · @' + tg.me.username : ''}` : tg.state === 'unauthorized' ? 'Нужно войти заново' : tg.error || 'Читает ваши чаты с заказами';
  const b = st.bot;
  const bSub = b.state === 'ready' ? (b.paired ? '@' + b.username : 'Нажмите Start в боте, чтобы закончить') : b.error || 'Присылает заказы и сводку';
  const l = st.llm;
  const lSub = l.mode === 'claude' ? (l.error ? l.error : l.usage ? `Claude · ≈ $${(l.usage.usd || 0).toFixed(2)} в этом месяце` : 'Claude понимает смысл заказов')
    : l.mode === 'ai' ? (l.error ? l.error : `${l.model} · сегодня ${l.used_today} из ${l.limit_today} запросов`) : 'Сейчас простые правила. С ИИ точнее';
  paint(el, [
    connRow('tg', 'Telegram', tgOk ? 'подключён' : tg.state === 'demo' ? 'демо' : 'не подключён', tgSub, tgOk),
    connRow('bot', 'Бот', b.state === 'ready' && b.paired ? 'подключён' : b.state === 'ready' ? 'ждёт Start' : 'не подключён', bSub, b.state === 'ready' && b.paired),
    connRow('ai', 'ИИ', l.mode === 'rules' ? 'выключен' : l.control && !l.control.active ? 'на паузе' : 'включён', lSub, l.mode !== 'rules'),
    st.local ? cloudRow(st.cloud) : '',
    st.cloud?.hosted ? html`<p class="capnote">Telegram, бот и ИИ подключаются в программе на компьютере. Здесь они только показаны.</p>` : '',
    l.control ? html`<div class="sw-row"><div><div class="t">Пауза ИИ</div><div class="s">${l.control.reason === 'backlog' ? `Включилась сама: неразобранных ${l.control.backlog}` : 'Не тратить запросы, пока вы разбираете заказы'}</div></div><button class="sw" role="switch" aria-checked="${!l.control.active}" data-act="ai-pause-quick" aria-label="Пауза ИИ"></button></div>` : '',
  ]);
}

export function paintUpdate() {
  const el = $('#update');
  const u = S.status?.update;
  if (!el || !u) return;
  const state = u.available ? `Доступна версия ${u.latest}.` : u.error ? u.error : u.checked_at ? 'У вас последняя версия.' : 'Ещё не проверял.';
  const act = u.available && u.can_install ? html`<button class="btn pri small" type="button" data-act="update-install">Обновить</button>`
    : u.available ? html`<span class="capnote">Скачайте свежий установщик с GitHub: из запуска файлом обновить кнопкой нельзя.</span>`
      : html`<button class="btn small" type="button" data-act="update-check">Проверить</button>`;
  const a = S.status.autostart;
  const auto = a?.supported ? html`<div class="sw-row"><div><div class="t">Запускать вместе с Windows</div><div class="s">Программа стартует в фоне без окна: проверяет чаты по расписанию и принимает действия с телефона. Откроете её значком</div></div><button class="sw" role="switch" aria-checked="${a.enabled}" data-act="autostart-toggle" aria-label="Запускать вместе с Windows"></button></div>` : '';
  paint(el, html`<div class="mono">программа на компьютере</div>
    <div class="sw-row"><div><div class="t">Scout ${u.current}</div><div class="s">${state}</div></div>${act}</div>
    ${auto}
    <div class="sw-row"><div><div class="t">Копия данных</div><div class="s">Все заказы, оценки и настройки одним файлом. Ключи и пароли в копию не попадают</div></div><a class="btn small" href="/api/backup" download>Сохранить</a></div>`);
}

function paintInstall() {
  const el = $('#install');
  if (!el) return;
  if (standalone()) {
    paint(el, html`<div class="hint2"><div class="ic">${I.check}</div><div>Scout установлен на этот телефон и открывается как обычное приложение.</div></div>`);
  } else if (deferredInstall) {
    paint(el, html`<div class="hint2"><div class="ic">${I.down}</div><div>Установите Scout на телефон: он откроется как обычное приложение.<br><button class="btn small" type="button" data-act="install" style="margin-top:10px">Установить</button></div></div>`);
  } else if (isIos()) {
    paint(el, html`<div class="hint2"><div class="ic">${I.down}</div><div>Чтобы Scout стал приложением: в Safari нажмите «Поделиться» и выберите «На экран Домой».</div></div>`);
  } else {
    paint(el, html`<div class="hint2"><div class="ic">${I.down}</div><div>Откройте меню браузера и выберите «Установить приложение» или «Добавить на главный экран».</div></div>`);
  }
}

// ----------------------------------------------------------------- шторки ----
function openCaseSheet() {
  openSheet(html`${sheetHead('Пример работы', 'портфолио')}
    <label class="lbl" for="case-t">Что сделали</label><input class="inp" id="case-t" maxlength="120" placeholder="Бот записи для салона красоты" data-autofocus>
    <label class="lbl" for="case-n">Результат (необязательно)</label><input class="inp" id="case-n" maxlength="200" placeholder="+30% записей за месяц">
    <label class="lbl" for="case-u">Ссылка (необязательно)</label><input class="inp" id="case-u" maxlength="300" type="url" inputmode="url" placeholder="https://…">
    <div class="btnrow"><button class="btn" type="button" data-act="close">Отмена</button><button class="btn pri" type="button" data-act="case-save">Сохранить</button></div>`);
}

function openImportSheet() {
  openSheet(html`${sheetHead('Собрать профиль из текста', 'быстрый старт')}
    <p class="lead-p">Вставьте резюме, описание услуг или любой текст о себе. Бот выберет навыки, ставку и примеры работ, а вы проверите.</p>
    <textarea class="pos" id="imp-t" rows="7" placeholder="Делаю телеграм-ботов и автоматизации на n8n…" maxlength="8000" data-autofocus></textarea>
    <div id="imp-out"></div>
    <div class="btnrow one"><button class="btn pri" type="button" data-act="imp-go">Разобрать</button></div>`);
}

function previewPatch(patch, smart) {
  const rows = [];
  if (patch.skills?.length) rows.push(html`<div><span class="mono">навыки</span><p>${patch.skills.join(', ')}</p></div>`);
  if (patch.day_rate) rows.push(html`<div><span class="mono">ставка за день</span><p>${money(patch.day_rate)}</p></div>`);
  if (patch.min_budget) rows.push(html`<div><span class="mono">минимальный заказ</span><p>${money(patch.min_budget)}</p></div>`);
  if (patch.bio) rows.push(html`<div><span class="mono">о себе</span><p>${patch.bio}</p></div>`);
  if (patch.cases?.length) rows.push(html`<div><span class="mono">примеры работ</span><p>${patch.cases.map((c) => c.title).join('; ')}</p></div>`);
  return html`<div class="preview">${rows}${smart ? '' : html`<p class="capnote">Разобрал по простым правилам. С ключом Claude выйдет точнее.</p>`}
    <div class="btnrow"><button class="btn" type="button" data-act="imp-cancel">Отмена</button><button class="btn pri" type="button" data-act="imp-apply">Применить</button></div></div>`;
}

let lastPatch = null;

// --------------------------------------------------------------- действия ----
const norm = (s) => s.trim().replace(/\s+/g, ' ');

function addTo(list, input) {
  const v = norm(input.value);
  if (!v) return false;
  if (list.some((x) => x.toLowerCase() === v.toLowerCase())) { input.value = ''; return false; }
  list.push(v);
  input.value = '';
  return true;
}

export const profileActions = {
  cap: (el) => {
    S.profile.capacity = el.dataset.v;
    S.profile.min_budget = S.presets[el.dataset.v];
    paintMoney();
    changed();
  },
  'ai-pause-quick': async () => {
    const c = S.status.llm.control;
    try {
      await setAiControl(c.reason === 'backlog' ? { auto: false } : { paused: !c.paused });
      paintConns();
      toast(S.status.llm.control.active ? 'ИИ снова работает' : 'ИИ на паузе: расходов нет');
    } catch (e) { toast(e.message); }
  },
  'scan-every': async (el) => {
    try {
      const r = await put('/settings/scan', { minutes: +el.dataset.v });
      S.status.scan = { ...S.status.scan, ...r.scan };
      paintScanSeg();
      paintChatsNote();
      toast(+el.dataset.v === 0 ? 'Проверка только по вашей кнопке' : `Проверяю чаты каждые ${SCAN_LABELS[+el.dataset.v] || el.dataset.v + ' мин'}`);
    } catch (e) { toast(e.message); }
  },
  'recall-toggle': (el) => {
    S.profile.recall = !S.profile.recall;
    el.setAttribute('aria-checked', String(S.profile.recall));
    changed();
  },
  'skill-add': () => { if (addTo(S.profile.skills, $('#skill-in'))) { paintSkills(); changed(); } },
  'skill-rm': (el) => { S.profile.skills.splice(+el.dataset.i, 1); paintSkills(); changed(); },
  'stop-add': () => { if (addTo(S.profile.stop_words, $('#stop-in'))) { paintStops(); changed(); } },
  'stop-rm': (el) => { S.profile.stop_words.splice(+el.dataset.i, 1); paintStops(); changed(); },
  pay: (el) => {
    const v = el.dataset.v;
    const list = S.profile.pay_types;
    const i = list.indexOf(v);
    if (i >= 0) list.splice(i, 1); else list.push(v);
    paintPay();
    changed();
  },
  'tone-set': (el) => { S.profile.tone = el.dataset.v; paintTone(); changed(); },
  'case-add': () => openCaseSheet(),
  'case-save': () => {
    const title = norm($('#case-t').value);
    if (!title) { toast('Напишите, что вы сделали'); return; }
    S.profile.cases.push({ title, note: norm($('#case-n').value), url: $('#case-u').value.trim() });
    closeSheet();
    if (S.tab === 'me') paintCases();
    changed();
    setTimeout(() => loadStatus().then(paintReady).catch(() => {}), 900);
  },
  'case-rm': (el) => { S.profile.cases.splice(+el.dataset.i, 1); paintCases(); changed(); },
  import: () => openImportSheet(),
  'imp-go': async (el) => {
    const text = $('#imp-t').value.trim();
    if (text.length < 20) { toast('Напишите хотя бы пару предложений о себе'); return; }
    el.disabled = true;
    el.textContent = 'Разбираю…';
    try {
      const r = await post('/profile/import', { text });
      lastPatch = r.patch;
      paint($('#imp-out'), previewPatch(r.patch, r.smart));
      el.hidden = true;
    } catch (e) {
      toast(e.message);
      el.disabled = false;
      el.textContent = 'Разобрать';
    }
  },
  'imp-cancel': () => closeSheet(),
  'imp-apply': () => {
    const p = lastPatch || {};
    if (p.skills) S.profile.skills = [...new Set([...S.profile.skills, ...p.skills])].slice(0, 40);
    for (const k of ['day_rate', 'min_budget', 'bio']) if (p[k]) S.profile[k] = p[k];
    if (p.pay_types?.length) S.profile.pay_types = p.pay_types;
    if (p.cases?.length) S.profile.cases = [...S.profile.cases, ...p.cases].slice(0, 12);
    closeSheet();
    renderProfile();
    changed();
    toast('Профиль обновлён');
  },
  'n-toggle': (el) => {
    const k = el.dataset.k;
    S.notif[k] = !S.notif[k];
    paintNotif();
    saveNotif();
  },
  'open-chats': () => openChats(),
  'connect-tg': () => openConnect('telegram'),
  'connect-bot': () => openConnect('bot'),
  'connect-ai': () => openConnect('claude'),
  'connect-cloud': () => openConnect('cloud'),
  'demo-toggle': async () => {
    try {
      await post('/demo', { on: !S.status.demo });
      cache.clear();
      await loadStatus();
      S.feed = null;
      S.stats = null;
      renderProfile();
      refreshFeed();
      toast(S.status.demo ? 'Тестовые заказы включены' : 'Тестовые заказы убраны');
    } catch (e) { toast(e.message); }
  },
  install: async () => {
    if (!deferredInstall) return;
    deferredInstall.prompt();
    await deferredInstall.userChoice.catch(() => {});
    deferredInstall = null;
    paintInstall();
  },
  'autostart-toggle': async () => {
    const r = await put('/autostart', { on: !S.status.autostart.enabled });
    S.status.autostart = r;
    paintUpdate();
    toast(r.enabled ? 'Включено: программа будет стартовать вместе с Windows' : 'Автозапуск выключен');
  },
  'update-check': async () => {
    S.status.update = await post('/update/check');
    paintUpdate();
    toast(S.status.update.available ? `Доступна версия ${S.status.update.latest}` : S.status.update.error || 'У вас последняя версия');
  },
  'update-install': async () => {
    toast('Скачиваю обновление. Программа закроется и откроется заново.');
    await post('/update/install');
    paint($('#view'), html`<section class="view"><div class="glass field" style="margin-top:24px"><h3 class="ready-t">Устанавливаю обновление</h3><p class="lead-p">Окно закроется само, через несколько секунд Scout откроется уже новой версии. Если не открылся, нажмите на значок Scout.</p></div></section>`);
  },
  'quit-app': async () => {
    await post('/quit');
    paint($('#view'), html`<section class="view"><div class="glass field" style="margin-top:24px"><h3 class="ready-t">Программа остановлена</h3><p class="lead-p">Окно можно закрыть. Чтобы запустить снова, нажмите на значок Scout.</p></div></section>`);
  },
  logout: async () => {
    try { await post('/logout'); } catch { /* всё равно выходим */ }
    cache.clear();
    location.hash = '';
    location.reload();
  },
};

// Ползунки, поля и списки
export function onProfileInput(el) {
  const bind = el.dataset.bind;
  if (!bind || !S.profile) return;
  if (bind === 'min') { S.profile.min_budget = Math.round(+el.value) * 1000; sliderFill(el); $('#min-v').textContent = money(S.profile.min_budget); $('#cap-note').textContent = `Показываю заказы от ${money(S.profile.min_budget)}. Всё, что дешевле, убираю в «Отсеянные».`; S.profile.capacity = 'free'; changed(); }
  else if (bind === 'rate') { S.profile.day_rate = Math.round(+el.value * 1000); sliderFill(el); $('#rate-v').textContent = money(S.profile.day_rate); changed(); }
  else if (bind === 'bio') { S.profile.bio = el.value; changed(); }
  else if (bind === 'tz') { S.profile.tz = el.value; changed(); }
  else if (['quiet_start', 'quiet_end', 'digest_time'].includes(bind)) { if (el.value) { S.notif[bind] = el.value; saveNotif(); } }
}

