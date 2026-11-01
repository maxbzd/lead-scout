import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

let proc;
const PORT = 8799;
const BASE = `http://localhost:${PORT}`;
const mf = { dispatchFetch: (url, opts) => fetch(url.replace('http://scout.test', BASE), opts) };
let cookie = '';
const H = { 'X-Requested-With': 'leadscout', 'Content-Type': 'application/json' };
const SYNC = { Authorization: 'Bearer sync-token-123', 'Content-Type': 'application/json' };

const call = (path, opts = {}) => mf.dispatchFetch(`http://scout.test${path}`, { ...opts, headers: { ...(opts.headers || {}), ...(cookie ? { Cookie: cookie } : {}) } });
const api = async (path, method = 'GET', body) => {
  const r = await call(`/api${path}`, { method, headers: method === 'GET' ? {} : H, body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: r.status, data: await r.json().catch(() => null), headers: r.headers };
};
const sync = async (path, method = 'GET', body) => {
  const r = await mf.dispatchFetch(`http://scout.test/sync${path}`, { method, headers: SYNC, body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: r.status, data: await r.json().catch(() => null) };
};

function order(id, over = {}) {
  const base = {
    id, title: `Заказ ${id}`, verdict: 'match', score: 80, hot: false, state: 'new', feedback: null, reason: null, offered: false, stage: null,
    posted_at: new Date(Date.now() - 10 * 60000).toISOString(), budget: { rub_max: 40000, label: '40 000 ₽' }, age_label: '10 мин', price_offered: null, ...over,
  };
  return {
    id, verdict: base.verdict, score: base.score, hot: base.hot, state: base.state, feedback: base.feedback, offered: base.offered, stage: base.stage,
    posted_at: base.posted_at, budget: 40000, fixed: true, search: `${base.title} телеграм-бот для салона`,
    card: base, detail: { ...base, text: 'исходный текст', fit: [], flags: [], draft: null,
      cloud_drafts: { warm: { price: 45000, label: '45 000 ₽', texts: ['Здравствуйте! Беру за 45 000 ₽.', 'Добрый день! Готов за 45 000 ₽.'] } } },
  };
}

before(async () => {
  // настоящий локальный запуск Cloudflare (workerd) с чистой базой во временной папке
  const store = mkdtempSync(join(tmpdir(), 'scout-d1-'));
  proc = spawn('npx', ['wrangler', 'dev', '--local', '--port', String(PORT), '--persist-to', store,
    '--var', 'APP_PASSWORD:secret-pw', '--var', 'SYNC_TOKEN:sync-token-123'], { cwd: new URL('..', import.meta.url).pathname, env: { ...process.env, HOME: store }, detached: true });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('wrangler не запустился за 90 секунд')), 90000);
    const onData = (d) => { if (String(d).includes('Ready on')) { clearTimeout(timer); resolve(); } };
    proc.stdout.on('data', onData);
    proc.stderr.on('data', onData);
    proc.on('exit', (code) => reject(new Error(`wrangler завершился с кодом ${code}`)));
  });
});

after(() => {
  try { process.kill(-proc.pid, 'SIGTERM'); } catch { /* уже завершился */ } // убиваем всю группу: иначе wrangler и workerd остаются висеть
});

test('без входа API закрыт, вход по паролю выдаёт cookie', async () => {
  assert.equal((await api('/orders')).status, 401);
  assert.deepEqual((await api('/session')).data, { authed: false });
  const bad = await api('/login', 'POST', { password: 'wrong' });
  assert.equal(bad.status, 401);
  const good = await call('/api/login', { method: 'POST', headers: H, body: JSON.stringify({ password: 'secret-pw' }) });
  assert.equal(good.status, 200);
  const set = good.headers.get('set-cookie');
  assert.match(set, /HttpOnly/);
  assert.match(set, /SameSite=Lax/);
  cookie = set.split(';')[0];
  assert.deepEqual((await api('/session')).data, { authed: true });
});

test('запись без заголовка X-Requested-With отклоняется', async () => {
  const r = await call('/api/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  assert.equal(r.status, 403);
});

test('синхронизация закрыта без токена', async () => {
  const r = await mf.dispatchFetch('http://scout.test/sync/ping');
  assert.equal(r.status, 401);
  const wrong = await mf.dispatchFetch('http://scout.test/sync/ping', { headers: { Authorization: 'Bearer nope' } });
  assert.equal(wrong.status, 401);
  assert.equal((await sync('/ping')).status, 200);
});

test('компьютер присылает данные, сайт их показывает с фильтрами, поиском и счётчиками', async () => {
  const kv = {
    status: { demo: false, scan: { running: false, last: null, next_in: null, every_min: 0 }, llm: { mode: 'ai', control: { paused: false, auto: true, limit: 40, backlog: 3, active: true } }, ready: [], tz: 'Europe/Moscow', public_url: '', local: false },
    stats: { accuracy: { current: null, n: 0, series: [] }, suggestions: [{ id: 7, kind: 'raise_min', text: 'Поднять минимум?' }], tiles: [], hours: { total: 0 }, funnel: [], chats: [], learned: 0, log: [] },
    profile: { profile: { skills: ['Telegram-боты'], tone: 'warm' }, presets: { free: 20000 } },
    notif: { instant: true },
    chats: { items: [{ id: 1, title: 'Чат' }] },
    feed_meta: { total_read: 321 },
  };
  const res = await sync('/push', 'POST', {
    orders: [order(1, { score: 95, hot: true }), order(2, { score: 60, verdict: 'maybe' }), order(3, { verdict: 'scam', score: 30 }), order(4, { state: 'saved' })],
    discarded: [{ id: 900, posted_at: new Date().toISOString(), search: 'болтовня про claude', item: { id: 900, kind: 'message', text: 'болтовня про claude', chat: 'Чат', age_label: '5 мин' } }],
    kv,
  });
  assert.equal(res.data.ok, true);

  const all = (await api('/orders')).data;
  assert.deepEqual(all.items.map((o) => o.id), [1, 4, 2]); // по убыванию совпадения, скам не показан
  assert.equal(all.counts.all, 3);
  assert.equal(all.counts.hot, 1);
  assert.equal(all.counts.saved, 1);
  assert.equal(all.counts.rejected, 1);
  assert.equal(all.counts.discarded, 1);
  assert.equal(all.hot_sum, 40000);
  assert.equal(all.total_read, 321);
  assert.equal((await api('/orders?filter=hot')).data.items.length, 1);
  assert.deepEqual((await api('/orders?filter=rejected')).data.items.map((o) => o.id), [3]);
  assert.deepEqual((await api('/orders?sort=new')).data.items.length, 3);
  assert.deepEqual((await api('/orders?q=' + encodeURIComponent('Заказ 2'))).data.items.map((o) => o.id), [2]);
  assert.equal((await api('/orders?q=%25')).data.items.length, 0); // «%» ищется буквально
  assert.equal((await api('/orders?filter=discarded')).data.items[0].id, 900);

  const st = (await api('/status')).data;
  assert.equal(st.cloud.hosted, true);
  assert.equal(st.cloud.online, true);
  assert.equal(st.llm.mode, 'ai');
  assert.equal((await api('/stats')).data.suggestions.length, 1);
  assert.equal((await api('/profile')).data.profile.tone, 'warm');
  assert.equal((await api('/chats')).data.items.length, 1);
});

test('возраст пересчитывается на лету', async () => {
  const o = (await api('/orders?filter=all')).data.items.find((x) => x.id === 1);
  assert.match(o.age_label, /мин/);
  assert.equal(o.fresh, true);
});

test('оценка, закладка, отклик и этап отвечают сразу и попадают в очередь для компьютера', async () => {
  let r = await api('/orders/2/feedback', 'POST', { kind: 'down', reason: 'budget' });
  assert.equal(r.data.order.feedback, 'down');
  assert.equal(r.data.order.state, 'hidden');
  assert.equal((await api('/orders')).data.counts.all, 2); // скрытый пропал из ленты
  assert.equal((await api('/orders?filter=rated')).data.items.length, 1);

  r = await api('/orders/3/feedback', 'POST', { kind: 'up' }); // 👍 на отсеянном возвращает его в ленту
  assert.equal(r.data.order.verdict, 'maybe');
  assert.ok(r.data.order.score >= 50);

  r = await api('/orders/1/save', 'POST', { saved: true });
  assert.equal(r.data.order.state, 'saved');
  r = await api('/orders/1/offer', 'POST', { done: true, price: 42000 });
  assert.equal(r.data.order.offered, true);
  assert.equal(r.data.order.stage, 'wait');
  r = await api('/orders/1/stage', 'POST', { stage: 'work', amount: 41000 });
  assert.equal(r.data.order.stage, 'work');
  assert.equal(r.data.order.price_offered, 41000);
  assert.equal((await api('/orders/1/stage', 'POST', { stage: 'bad' })).status, 422);
  assert.equal((await api('/orders/999/save', 'POST', { saved: true })).status, 404);

  const cmds = (await sync('/commands')).data.items;
  assert.deepEqual(cmds.map((c) => c.path), ['/orders/2/feedback', '/orders/3/feedback', '/orders/1/save', '/orders/1/offer', '/orders/1/stage']);
  assert.deepEqual(cmds[0].body, { kind: 'down', reason: 'budget' });
  assert.equal((await api('/status')).data.cloud.pending, 5);

  await sync('/ack', 'POST', { done: cmds.map((c) => c.id) });
  assert.equal((await sync('/commands')).data.items.length, 0);
  assert.equal((await api('/status')).data.cloud.pending, 0);
});

test('ошибка выполнения на компьютере сохраняется и не зацикливается', async () => {
  await api('/orders/4/save', 'POST', { saved: false });
  const [cmd] = (await sync('/commands')).data.items;
  await sync('/ack', 'POST', { errors: { [cmd.id]: 'не получилось' } });
  assert.equal((await sync('/commands')).data.items.length, 0);
});

test('черновик берётся из готовых, цена и вариант меняются', async () => {
  let r = await api('/orders/1/draft', 'POST', { tone: 'warm', variant: 0 });
  assert.equal(r.data.text, 'Здравствуйте! Беру за 45 000 ₽.');
  r = await api('/orders/1/draft', 'POST', { tone: 'warm', variant: 1 });
  assert.match(r.data.text, /Добрый день/);
  r = await api('/orders/1/draft', 'POST', { tone: 'warm', variant: 0, price: 50000 });
  assert.equal(r.data.text, 'Здравствуйте! Беру за 50 000 ₽.');
  assert.equal(r.data.price, 50000);
  r = await api('/orders/1/draft', 'POST', { tone: 'formal', variant: 0 });
  assert.equal(r.status, 409); // для этого тона черновика ещё нет
  const d = (await api('/orders/1')).data;
  assert.equal(d.cloud_drafts, undefined);
});

test('профиль и настройки сохраняются сразу, повторные изменения заменяют предыдущие в очереди', async () => {
  await api('/profile', 'PUT', { skills: ['A'], tone: 'short' });
  await api('/profile', 'PUT', { skills: ['A', 'B'], tone: 'formal' });
  assert.equal((await api('/profile')).data.profile.tone, 'formal');
  const cmds = (await sync('/commands')).data.items.filter((c) => c.path === '/profile');
  assert.equal(cmds.length, 1);
  assert.deepEqual(cmds[0].body.skills, ['A', 'B']);

  const ai = await api('/ai/control', 'PUT', { paused: true });
  assert.equal(ai.data.llm.control.paused, true);
  const sc = await api('/settings/scan', 'PUT', { minutes: 60 });
  assert.equal(sc.data.scan.every_min, 60);
});

test('проверка чатов и возврат из «Отброшено» ставятся в очередь', async () => {
  await sync('/ack', 'POST', { done: (await sync('/commands')).data.items.map((c) => c.id) });
  const s1 = await api('/scan', 'POST', {});
  assert.equal(s1.data.queued, true);
  await api('/scan', 'POST', {}); // повторная кнопка не плодит очередь
  const p = await api('/discarded/900/promote', 'POST', {});
  assert.equal(p.data.queued, true);
  assert.equal((await api('/orders?filter=discarded')).data.items.length, 0);
  const cmds = (await sync('/commands')).data.items;
  assert.deepEqual(cmds.map((c) => c.path), ['/scan', '/discarded/900/promote']);
});

test('удаление: выбранные, всё во вкладке (кроме отложенного) и отброшенные идут в очередь для компьютера', async () => {
  await sync('/ack', 'POST', { done: (await sync('/commands')).data.items.map((c) => c.id) });
  await sync('/push', 'POST', {
    orders: [order(50), order(51), order(52), order(53, { state: 'saved' }), order(54, { offered: true })],
    discarded: [
      { id: 950, posted_at: new Date().toISOString(), search: 'мусор один', item: { id: 950, kind: 'message', text: 'мусор один', chat: 'Чат', age_label: '1 мин' } },
      { id: 951, posted_at: new Date().toISOString(), search: 'мусор два', item: { id: 951, kind: 'message', text: 'мусор два', chat: 'Чат', age_label: '1 мин' } },
    ],
  });
  const before = (await api('/orders')).data.counts.all;
  const one = await api('/orders/delete', 'POST', { ids: [50, 50, 'x', -3, 999999], notorder: true });
  assert.equal(one.data.deleted, 2); // повторы и мусор отброшены, несуществующий номер ставится в очередь, но на сайте ничего не удаляет
  assert.equal((await api('/orders')).data.counts.all, before - 1);
  const cmds = (await sync('/commands')).data.items;
  assert.equal(cmds.length, 1);
  assert.equal(cmds[0].path, '/orders/delete');
  assert.deepEqual(cmds[0].body, { ids: [50, 999999], notorder: true });

  const all = await api('/orders/delete', 'POST', { all: true, filter: 'all' });
  const left = (await api('/orders?filter=all')).data.items.map((o) => o.id).sort();
  assert.ok(all.data.deleted >= 1);
  assert.ok(left.includes(53) && left.includes(54), 'отложенное и то, на что откликнулись, не удаляется');
  assert.ok(!left.includes(51) && !left.includes(52));

  const d = await api('/discarded/delete', 'POST', { ids: [950] });
  assert.equal(d.data.deleted, 1);
  assert.equal((await api('/orders?filter=discarded')).data.items.some((x) => x.id === 950), false);
  const dAll = await api('/discarded/delete', 'POST', { all: true });
  assert.ok(dAll.data.deleted >= 1);
  assert.equal((await api('/orders?filter=discarded')).data.items.length, 0);
  const paths = (await sync('/commands')).data.items.map((c) => c.path);
  assert.deepEqual(paths.filter((x) => x.endsWith('/delete')), ['/orders/delete', '/orders/delete', '/discarded/delete', '/discarded/delete']);
});

test('подключения и чаты меняются только на компьютере', async () => {
  for (const [path, method] of [['/connect/telegram/api', 'POST'], ['/connect/ai', 'POST'], ['/chats', 'POST'], ['/demo', 'POST'], ['/profile/import', 'POST']]) {
    const r = await api(path, method, {});
    assert.equal(r.status, 400, path);
    assert.match(r.data.detail, /компьютере/);
  }
});

test('поток событий меняется после новой отправки данных', async () => {
  const first = await call('/api/stream');
  assert.equal(first.headers.get('content-type'), 'text/event-stream');
  const text = await first.text();
  assert.match(text, /type":"orders/);
  const id = text.match(/\nid: (\d+)/)[1];
  const again = await mf.dispatchFetch('http://scout.test/api/stream', { headers: { Cookie: cookie, 'Last-Event-ID': id } });
  assert.doesNotMatch(await again.text(), /type":"orders/);
  await sync('/push', 'POST', { orders: [], kv: {} });
  const third = await mf.dispatchFetch('http://scout.test/api/stream', { headers: { Cookie: cookie, 'Last-Event-ID': id } });
  assert.match(await third.text(), /type":"orders/);
});

test('выход отзывает cookie, вход ограничен по числу неудачных попыток', async () => {
  const old = cookie;
  assert.equal((await api('/logout', 'POST', {})).status, 200);
  cookie = old;
  assert.deepEqual((await api('/session')).data, { authed: false });
  cookie = '';
  let last;
  for (let i = 0; i < 9; i++) last = await api('/login', 'POST', { password: 'x' + i });
  assert.equal(last.status, 429);
  const stillBlocked = await api('/login', 'POST', { password: 'secret-pw' });
  assert.equal(stillBlocked.status, 429);
});
