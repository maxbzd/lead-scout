// Облачная часть Scout (Cloudflare Worker + база D1).
//
// Сайт и данные живут здесь, а читает чаты и думает (ИИ) программа на вашем компьютере: она присылает результаты
// на /sync/push и забирает ваши действия с /sync/commands. Если компьютер выключен, сайт показывает последнее
// присланное, а ваши действия ждут в очереди и выполнятся, когда он включится.

import schema from '../schema.sql';

const COOKIE = 'ls_session';
const MAX_AGE = 60 * 60 * 24 * 30;
const FAIL_WINDOW = 600;
const FAIL_LIMIT = 8;
const ONLINE_SECONDS = 90;

const SECURITY = {
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'no-referrer',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
};

const enc = new TextEncoder();
const now = () => Math.floor(Date.now() / 1000);
const iso = () => new Date().toISOString().replace(/\.\d+Z$/, 'Z');

class HttpError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...SECURITY, ...extra },
  });
}

// ------------------------------------------------------------------ защита ----

async function sha256(text) {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', enc.encode(text)));
}

async function safeEqual(a, b) {
  const [x, y] = await Promise.all([sha256(a), sha256(b)]);
  return crypto.subtle.timingSafeEqual(x, y);
}

async function hmac(env, payload) {
  const key = await crypto.subtle.importKey('raw', await sha256('cookie:' + env.SYNC_TOKEN), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = new Uint8Array(await crypto.subtle.sign('HMAC', key, enc.encode(payload)));
  return [...sig].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function issue(env) {
  const nonce = [...crypto.getRandomValues(new Uint8Array(8))].map((b) => b.toString(16).padStart(2, '0')).join('');
  const payload = `${now() + MAX_AGE}.${nonce}`;
  return `${payload}.${await hmac(env, payload)}`;
}

function cookieOf(req) {
  const m = (req.headers.get('Cookie') || '').match(new RegExp(`(?:^|;\\s*)${COOKIE}=([^;]+)`));
  return m ? m[1] : null;
}

async function revoked(env) {
  return (await kvGet(env, 'revoked', [])) || [];
}

async function validSession(env, req) {
  const token = cookieOf(req);
  if (!token) return false;
  const parts = token.split('.');
  if (parts.length !== 3) return false;
  const [exp, nonce, sig] = parts;
  if (!(await safeEqual(sig, await hmac(env, `${exp}.${nonce}`)))) return false;
  if (!(Number(exp) > now())) return false;
  return !(await revoked(env)).includes(nonce);
}

async function validSync(env, req) {
  const header = req.headers.get('Authorization') || '';
  if (!header.startsWith('Bearer ') || !env.SYNC_TOKEN) return false;
  return safeEqual(header.slice(7), env.SYNC_TOKEN);
}

// ---------------------------------------------------------------- хранилище ----

async function kvGet(env, key, fallback = null) {
  const row = await env.DB.prepare('SELECT v FROM kv WHERE k=?').bind(key).first();
  if (!row) return fallback;
  try {
    return JSON.parse(row.v);
  } catch {
    return fallback;
  }
}

const kvSet = (env, key, value) =>
  env.DB.prepare('INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v').bind(key, JSON.stringify(value));

async function readBody(req, max = 8_000_000) {
  const text = await req.text();
  if (text.length > max) throw new HttpError(413, 'Слишком большой запрос');
  if (!text) return {};
  try {
    const data = JSON.parse(text);
    return data && typeof data === 'object' ? data : {};
  } catch {
    throw new HttpError(400, 'Запрос не разобрался');
  }
}

// ------------------------------------------------------------------- форматы ----

function fmtAge(m) {
  if (m == null) return '';
  if (m < 1) return 'только что';
  if (m < 60) return `${m} мин`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  if (h < 24) return `${h} ч` + (rest && h < 6 ? ` ${rest} мин` : '');
  return `${Math.floor(h / 24)} дн`;
}

function fmtMoney(n) {
  return `${Math.round(n)}`.replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' ₽';
}

// Возраст в карточках считаем на лету: данные могли быть присланы давно
function freshen(card) {
  if (!card || !card.posted_at) return card;
  const m = Math.max(0, Math.round((Date.now() - Date.parse(card.posted_at)) / 60000));
  card.age_min = m;
  card.age_label = fmtAge(m);
  card.fresh = m < 30;
  return card;
}

function parse(text, fallback = null) {
  try {
    return JSON.parse(text);
  } catch {
    return fallback;
  }
}

const FILTERS = {
  all: "verdict IN ('match','maybe') AND state!='hidden'",
  hot: "verdict='match' AND score>=90 AND state!='hidden'",
  saved: "state='saved'",
  rated: 'feedback IS NOT NULL',
  offered: 'offered=1',
  rejected: "verdict IN ('skip','scam') AND state!='hidden'",
};
const ORDER_BY = {
  score: 'score DESC, posted_at DESC',
  new: 'posted_at DESC',
  budget: 'budget DESC, score DESC',
};

const likeEscape = (s) => s.toLowerCase().replace(/\\/g, '\\\\').replace(/%/g, '\\%').replace(/_/g, '\\_');

// --------------------------------------------------------------- действия ----

// Те же правила, что на компьютере (store.set_feedback и др.): сайт отвечает сразу, а компьютер потом подтверждает.
function applyAction(card, action, arg = {}) {
  const c = card;
  if (action === 'feedback') {
    if (arg.kind === 'up') {
      c.feedback = 'up';
      c.reason = null;
      if (c.state === 'hidden') c.state = 'new';
      if (c.verdict === 'skip' || c.verdict === 'scam') {
        c.verdict = 'maybe';
        c.score = Math.max(c.score, 50);
      }
    } else if (arg.kind === 'down') {
      c.feedback = 'down';
      c.reason = arg.reason || 'other';
      c.state = 'hidden';
    } else {
      c.feedback = null;
      c.reason = null;
      if (c.state === 'hidden') c.state = 'new';
    }
  } else if (action === 'save') {
    c.state = arg.saved ? 'saved' : 'new';
  } else if (action === 'offer') {
    if (arg.done === false) {
      c.offered = false;
      c.stage = null;
      c.price_offered = null;
    } else {
      c.offered = true;
      c.stage = c.stage || 'wait';
      if (arg.price != null) c.price_offered = arg.price;
    }
  } else if (action === 'stage') {
    c.stage = arg.stage;
    if (arg.stage === 'work') c.price_offered = arg.amount ?? c.price_offered ?? c.budget?.rub_max ?? null;
  } else if (action === 'restore') {
    c.feedback = null;
    c.reason = null;
    c.state = 'new';
  }
  c.hot = c.verdict === 'match' && c.score >= 90;
  return c;
}

async function enqueue(env, method, path, body, { replace = false } = {}) {
  const stmts = [];
  if (replace) stmts.push(env.DB.prepare("DELETE FROM commands WHERE status='new' AND method=? AND path=?").bind(method, path));
  stmts.push(env.DB.prepare('INSERT INTO commands(method,path,body,created_at) VALUES(?,?,?,?)').bind(method, path, body == null ? null : JSON.stringify(body), now()));
  await env.DB.batch(stmts);
}

// D1 принимает не больше 100 значений в запросе: удаляем порциями
async function deleteRows(env, table, ids) {
  for (let i = 0; i < ids.length; i += 90) {
    const part = ids.slice(i, i + 90);
    await env.DB.prepare(`DELETE FROM ${table} WHERE id IN (${part.map(() => '?').join(',')})`).bind(...part).run();
  }
}

async function deleteMany(env, table, body) {
  let ids;
  if (body.all) {
    const text = String(body.q || '').trim();
    const args = [];
    let sql;
    if (table === 'orders') {
      const filter = FILTERS[body.filter] ? body.filter : 'all';
      sql = `SELECT id FROM orders WHERE ${FILTERS[filter]}`;
      if (!['saved', 'offered', 'rated'].includes(filter)) sql += " AND state!='saved' AND offered=0 AND stage IS NULL"; // отложенное и то, на что откликнулись, остаётся
      if (text) { sql += " AND search LIKE ? ESCAPE '\\'"; args.push(`%${likeEscape(text)}%`); }
    } else {
      sql = 'SELECT id FROM discarded';
      if (text) { sql += " WHERE search LIKE ? ESCAPE '\\'"; args.push(`%${likeEscape(text)}%`); }
    }
    ids = (await env.DB.prepare(sql).bind(...args).all()).results.map((r) => r.id);
  } else {
    ids = [...new Set((Array.isArray(body.ids) ? body.ids : []).filter((x) => Number.isInteger(x) && x > 0))].slice(0, 2000);
  }
  await deleteRows(env, table, ids);
  for (let i = 0; i < ids.length; i += 400) {
    await enqueue(env, 'POST', `/${table}/delete`, { ids: ids.slice(i, i + 400), notorder: !!body.notorder });
  }
  return ids.length;
}

async function mutateOrder(env, id, action, arg, command) {
  const row = await env.DB.prepare('SELECT * FROM orders WHERE id=?').bind(id).first();
  if (!row) throw new HttpError(404, 'Заказ не найден');
  const card = applyAction(parse(row.card, {}), action, arg);
  const detail = applyAction(parse(row.detail, {}), action, arg);
  await env.DB.batch([
    env.DB.prepare('UPDATE orders SET verdict=?, score=?, hot=?, state=?, feedback=?, offered=?, stage=?, card=?, detail=? WHERE id=?')
      .bind(card.verdict, card.score, card.hot ? 1 : 0, card.state, card.feedback ?? null, card.offered ? 1 : 0, card.stage ?? null,
        JSON.stringify(card), JSON.stringify(detail), id),
    env.DB.prepare('INSERT INTO commands(method,path,body,created_at) VALUES(?,?,?,?)').bind('POST', command.path, JSON.stringify(command.body ?? {}), now()),
  ]);
  return freshen(card);
}

// ---------------------------------------------------------------- маршруты ----

const NOT_HERE = 'Это настраивается в программе на компьютере. Здесь только просмотр и оценки.';

async function emptyStatus(env) {
  return {
    demo: false,
    scan: { running: false, last: null, next_in: null, every_min: 0 },
    telegram: { state: 'off', error: null, me: null, has_api: false, source: 'none', logging_in: false },
    bot: { state: 'off', error: null, username: null, paired: false, pair_link: null, source: 'none' },
    llm: { mode: 'rules', error: null, usage: null, source: 'none', provider: null, control: null },
    ready: [],
    tz: 'Europe/Moscow',
    public_url: '',
    local: false,
  };
}

async function cloudInfo(env) {
  const last = await kvGet(env, 'last_sync');
  const pending = await env.DB.prepare("SELECT COUNT(*) AS n FROM commands WHERE status='new'").first();
  const age = last ? now() - Number(last.ts) : null;
  return { hosted: true, last_sync: last ? last.at : null, online: age != null && age < ONLINE_SECONDS, pending: pending?.n || 0 };
}

async function listOrders(env, url) {
  const q = url.searchParams;
  const filter = q.get('filter') || 'all';
  const sort = q.get('sort') || 'score';
  const limit = Math.max(1, Math.min(200, Number(q.get('limit')) || 80));
  const text = (q.get('q') || '').trim();
  const counts = await countsOf(env);
  const meta = (await kvGet(env, 'feed_meta', {})) || {};
  let items;
  if (filter === 'discarded') {
    let sql = 'SELECT item FROM discarded';
    const args = [];
    if (text) {
      sql += " WHERE search LIKE ? ESCAPE '\\'";
      args.push(`%${likeEscape(text)}%`);
    }
    sql += ' ORDER BY posted_at DESC LIMIT ?';
    const { results } = await env.DB.prepare(sql).bind(...args, limit).all();
    items = results.map((r) => parse(r.item, null)).filter(Boolean);
  } else {
    let sql = `SELECT card FROM orders WHERE ${FILTERS[filter] || FILTERS.all}`;
    const args = [];
    if (text) {
      sql += " AND search LIKE ? ESCAPE '\\'";
      args.push(`%${likeEscape(text)}%`);
    }
    sql += ` ORDER BY ${ORDER_BY[sort] || ORDER_BY.score} LIMIT ?`;
    const { results } = await env.DB.prepare(sql).bind(...args, limit).all();
    items = results.map((r) => freshen(parse(r.card, null))).filter(Boolean);
  }
  return { items, counts, hot_sum: counts._hot_sum, total_read: meta.total_read || 0, ...(filter === 'discarded' ? { kind: 'messages' } : {}) };
}

async function countsOf(env) {
  const parts = Object.entries(FILTERS).map(([k, w]) => `SUM(CASE WHEN ${w} THEN 1 ELSE 0 END) AS "${k}"`).join(', ');
  const row = (await env.DB.prepare(`SELECT ${parts}, SUM(CASE WHEN hot=1 AND fixed=1 AND state!='hidden' THEN budget ELSE 0 END) AS hot_sum FROM orders`).first()) || {};
  const disc = await env.DB.prepare('SELECT COUNT(*) AS n FROM discarded').first();
  const counts = {};
  for (const k of Object.keys(FILTERS)) counts[k] = row[k] || 0;
  counts.discarded = disc?.n || 0;
  Object.defineProperty(counts, '_hot_sum', { value: row.hot_sum || 0, enumerable: false });
  return counts;
}

async function orderDetail(env, id) {
  const row = await env.DB.prepare('SELECT detail FROM orders WHERE id=?').bind(id).first();
  if (!row) throw new HttpError(404, 'Заказ не найден');
  return freshen(parse(row.detail, {}));
}

async function draftFor(env, id, body) {
  const detail = await orderDetail(env, id);
  const drafts = detail.cloud_drafts || {};
  const tone = ['short', 'warm', 'formal'].includes(body.tone) ? body.tone : 'warm';
  const d = drafts[tone];
  if (!d || !d.texts || !d.texts.length) {
    throw new HttpError(409, 'Черновик пока не готов: он появится, когда включится программа на вашем компьютере');
  }
  const variant = Math.max(0, Number(body.variant) || 0);
  let text = d.texts[variant % d.texts.length];
  let price = d.price ?? null;
  if (body.price != null && d.price != null && Number(body.price) !== Number(d.price)) {
    // цена в тексте заменяется простой подстановкой: полный пересчёт делает компьютер
    text = text.split(d.label).join(fmtMoney(Number(body.price)));
    price = Number(body.price);
  } else if (body.price != null) {
    price = Number(body.price);
  }
  return { text, price, tone, variant };
}

async function updateStatusLlm(env, mutate) {
  const status = (await kvGet(env, 'status')) || (await emptyStatus(env));
  mutate(status);
  await kvSet(env, 'status', status).run();
  return status;
}

async function handleApi(req, env, url, ip) {
  const path = url.pathname.replace(/^\/api/, '');
  const method = req.method;

  // публичные
  if (path === '/health' && method === 'GET') return json({ ok: true });
  if (path === '/session' && method === 'GET') return json({ authed: await validSession(env, req) });
  if (path === '/login' && method === 'POST') {
    const since = now() - FAIL_WINDOW;
    const fails = await env.DB.prepare('SELECT COUNT(*) AS n, MIN(at) AS first FROM fails WHERE ip=? AND at>?').bind(ip, since).first();
    if ((fails?.n || 0) >= FAIL_LIMIT) {
      const wait = Math.max(1, FAIL_WINDOW - (now() - fails.first));
      return json({ detail: `Слишком много попыток. Подождите ${Math.floor(wait / 60) + 1} мин.` }, 429, { 'Retry-After': String(wait) });
    }
    const body = await readBody(req, 10_000);
    if (!env.APP_PASSWORD || !(await safeEqual(String(body.password || ''), env.APP_PASSWORD))) {
      await env.DB.batch([
        env.DB.prepare('INSERT INTO fails(ip, at) VALUES(?,?)').bind(ip, now()),
        env.DB.prepare('DELETE FROM fails WHERE at<?').bind(now() - FAIL_WINDOW * 3),
      ]);
      return json({ detail: 'Пароль не подошёл' }, 401);
    }
    await env.DB.prepare('DELETE FROM fails WHERE ip=?').bind(ip).run();
    const cookie = `${COOKIE}=${await issue(env)}; Max-Age=${MAX_AGE}; Path=/; HttpOnly; Secure; SameSite=Lax`;
    return json({ ok: true }, 200, { 'Set-Cookie': cookie });
  }
  if (path === '/logout' && method === 'POST') {
    const token = cookieOf(req);
    if (token && token.split('.').length === 3) {
      const list = await revoked(env);
      list.push(token.split('.')[1]);
      await kvSet(env, 'revoked', list.slice(-300)).run();
    }
    return json({ ok: true }, 200, { 'Set-Cookie': `${COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax` });
  }

  // дальше только после входа
  if (!(await validSession(env, req))) return json({ detail: 'Нужно войти' }, 401);
  if (method !== 'GET' && req.headers.get('X-Requested-With') !== 'leadscout') return json({ detail: 'Запрос отклонён' }, 403);

  if (path === '/status' && method === 'GET') {
    const status = (await kvGet(env, 'status')) || (await emptyStatus(env));
    status.cloud = await cloudInfo(env);
    status.local = false;
    return json(status);
  }
  if (path === '/orders' && method === 'GET') return json(await listOrders(env, url));

  let m = path.match(/^\/orders\/(\d{1,15})$/);
  if (m && method === 'GET') {
    const d = await orderDetail(env, Number(m[1]));
    delete d.cloud_drafts;
    return json(d);
  }
  m = path.match(/^\/orders\/(\d{1,15})\/(draft|feedback|save|offer|stage|restore)$/);
  if (m && method === 'POST') {
    const id = Number(m[1]);
    const body = await readBody(req, 20_000);
    const act = m[2];
    if (act === 'draft') return json(await draftFor(env, id, body));
    const command = { path: `/orders/${id}/${act}`, body };
    if (act === 'feedback' && !['up', 'down', 'clear'].includes(body.kind)) throw new HttpError(422, 'Неверная оценка');
    if (act === 'stage' && !['wait', 'ans', 'work', 'no'].includes(body.stage)) throw new HttpError(422, 'Неверный этап');
    const card = await mutateOrder(env, id, act, body, command);
    return json(act === 'feedback' ? { order: card, learned: 0 } : { order: card });
  }
  m = path.match(/^\/(orders|discarded)\/delete$/);
  if (m && method === 'POST') return json({ deleted: await deleteMany(env, m[1], await readBody(req, 100_000)) });
  m = path.match(/^\/discarded\/(\d{1,15})\/promote$/);
  if (m && method === 'POST') {
    const id = Number(m[1]);
    await env.DB.batch([
      env.DB.prepare('DELETE FROM discarded WHERE id=?').bind(id),
      env.DB.prepare('INSERT INTO commands(method,path,body,created_at) VALUES(?,?,?,?)').bind('POST', `/discarded/${id}/promote`, '{}', now()),
    ]);
    return json({ queued: true, order: null });
  }
  if (path === '/scan' && method === 'POST') {
    const pending = await env.DB.prepare("SELECT 1 AS x FROM commands WHERE status='new' AND path='/scan' LIMIT 1").first();
    if (!pending) await enqueue(env, 'POST', '/scan', {});
    return json({ skipped: false, orders: 0, messages: 0, error: null, ids: [], queued: true });
  }
  if (path === '/stats' && method === 'GET') {
    const stats = await kvGet(env, 'stats');
    if (!stats) throw new HttpError(503, 'Данных пока нет. Запустите программу на компьютере: она пришлёт их сюда');
    return json(stats);
  }
  if (path === '/profile' && method === 'GET') {
    const data = await kvGet(env, 'profile');
    if (!data) throw new HttpError(503, 'Профиль пока не получен. Запустите программу на компьютере');
    return json(data);
  }
  if (path === '/profile' && method === 'PUT') {
    const body = await readBody(req, 200_000);
    const cur = (await kvGet(env, 'profile')) || { presets: {} };
    await env.DB.batch([
      kvSet(env, 'profile', { ...cur, profile: body }),
      env.DB.prepare("DELETE FROM commands WHERE status='new' AND method='PUT' AND path='/profile'"),
      env.DB.prepare('INSERT INTO commands(method,path,body,created_at) VALUES(?,?,?,?)').bind('PUT', '/profile', JSON.stringify(body), now()),
    ]);
    return json({ profile: body });
  }
  if (path === '/notifications' && method === 'GET') {
    const data = await kvGet(env, 'notif');
    if (!data) throw new HttpError(503, 'Настройки пока не получены. Запустите программу на компьютере');
    return json(data);
  }
  if (path === '/notifications' && method === 'PUT') {
    const body = await readBody(req, 20_000);
    await env.DB.batch([
      kvSet(env, 'notif', body),
      env.DB.prepare("DELETE FROM commands WHERE status='new' AND method='PUT' AND path='/notifications'"),
      env.DB.prepare('INSERT INTO commands(method,path,body,created_at) VALUES(?,?,?,?)').bind('PUT', '/notifications', JSON.stringify(body), now()),
    ]);
    return json(body);
  }
  if (path === '/chats' && method === 'GET') return json((await kvGet(env, 'chats')) || { items: [] });
  m = path.match(/^\/suggestions\/(\d{1,15})$/);
  if (m && method === 'POST') {
    const body = await readBody(req, 2_000);
    await enqueue(env, 'POST', path, body);
    const stats = await kvGet(env, 'stats');
    if (stats && stats.suggestions) {
      stats.suggestions = stats.suggestions.filter((s) => s.id !== Number(m[1]));
      await kvSet(env, 'stats', stats).run();
    }
    return json({ ok: true });
  }
  if (path === '/ai/control' && method === 'PUT') {
    const body = await readBody(req, 2_000);
    await enqueue(env, 'PUT', '/ai/control', body, { replace: true });
    const status = await updateStatusLlm(env, (s) => {
      if (s.llm && s.llm.control) Object.assign(s.llm.control, body);
    });
    return json({ llm: status.llm });
  }
  if (path === '/settings/scan' && method === 'PUT') {
    const body = await readBody(req, 2_000);
    await enqueue(env, 'PUT', '/settings/scan', body, { replace: true });
    const status = await updateStatusLlm(env, (s) => {
      if (s.scan && Number.isInteger(body.minutes)) s.scan.every_min = body.minutes;
    });
    return json({ scan: status.scan });
  }
  if (path === '/stream' && method === 'GET') {
    const v = (await kvGet(env, 'version', 0)) || 0;
    const seen = Number(req.headers.get('Last-Event-ID') || -1);
    const event = seen === v ? ': без изменений\n\n' : `id: ${v}\ndata: ${JSON.stringify({ type: 'orders', ids: [] })}\n\ndata: ${JSON.stringify({ type: 'status' })}\n\n`;
    return new Response(`retry: 15000\nid: ${v}\n\n${event}`, {
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', ...SECURITY },
    });
  }
  if (/^\/(connect|telegram|demo|profile\/(import|rescore))/.test(path) || /^\/chats/.test(path)) {
    throw new HttpError(400, NOT_HERE);
  }
  throw new HttpError(404, 'Не найдено');
}

// ----------------------------------------------------------- синхронизация ----

const ORDER_SQL = 'INSERT INTO orders(id,verdict,score,hot,state,feedback,offered,stage,posted_at,budget,fixed,search,card,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
  + 'ON CONFLICT(id) DO UPDATE SET verdict=excluded.verdict, score=excluded.score, hot=excluded.hot, state=excluded.state, feedback=excluded.feedback, '
  + 'offered=excluded.offered, stage=excluded.stage, posted_at=excluded.posted_at, budget=excluded.budget, fixed=excluded.fixed, search=excluded.search, '
  + 'card=excluded.card, detail=excluded.detail';

async function handleSync(req, env, url) {
  const path = url.pathname.replace(/^\/sync/, '');
  const stamp = kvSet(env, 'last_sync', { at: iso(), ts: now() });

  if (path === '/ping' && req.method === 'GET') {
    await stamp.run();
    return json({ ok: true, now: iso() });
  }
  if (path === '/commands' && req.method === 'GET') {
    await stamp.run();
    const { results } = await env.DB.prepare("SELECT id, method, path, body FROM commands WHERE status='new' ORDER BY id LIMIT 50").all();
    return json({ items: results.map((r) => ({ id: r.id, method: r.method, path: r.path, body: parse(r.body, {}) })) });
  }
  if (path === '/ack' && req.method === 'POST') {
    const body = await readBody(req, 200_000);
    const stmts = [];
    for (const id of body.done || []) stmts.push(env.DB.prepare("UPDATE commands SET status='done' WHERE id=?").bind(Number(id)));
    for (const [id, err] of Object.entries(body.errors || {})) {
      stmts.push(env.DB.prepare("UPDATE commands SET status='failed', error=? WHERE id=?").bind(String(err).slice(0, 300), Number(id)));
    }
    stmts.push(env.DB.prepare("DELETE FROM commands WHERE status!='new' AND created_at<?").bind(now() - 3 * 86400));
    stmts.push(stamp);
    await env.DB.batch(stmts);
    return json({ ok: true });
  }
  if (path === '/push' && req.method === 'POST') {
    const body = await readBody(req, 20_000_000);
    const stmts = [];
    for (const o of body.orders || []) {
      stmts.push(env.DB.prepare(ORDER_SQL).bind(
        Number(o.id), String(o.verdict), Number(o.score), o.hot ? 1 : 0, String(o.state), o.feedback ?? null, o.offered ? 1 : 0, o.stage ?? null,
        String(o.posted_at), Number(o.budget) || 0, o.fixed ? 1 : 0, String(o.search || '').toLowerCase().slice(0, 4000),
        JSON.stringify(o.card), JSON.stringify(o.detail)));
    }
    for (const id of body.remove || []) stmts.push(env.DB.prepare('DELETE FROM orders WHERE id=?').bind(Number(id)));
    for (const d of body.discarded || []) {
      stmts.push(env.DB.prepare('INSERT INTO discarded(id,posted_at,search,item) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET item=excluded.item, search=excluded.search')
        .bind(Number(d.id), String(d.posted_at), String(d.search || '').toLowerCase().slice(0, 2000), JSON.stringify(d.item)));
    }
    for (const id of body.discarded_remove || []) stmts.push(env.DB.prepare('DELETE FROM discarded WHERE id=?').bind(Number(id)));
    for (const [k, v] of Object.entries(body.kv || {})) {
      if (['status', 'stats', 'profile', 'notif', 'chats', 'feed_meta'].includes(k)) stmts.push(kvSet(env, k, v));
    }
    const ver = ((await kvGet(env, 'version', 0)) || 0) + 1;
    stmts.push(kvSet(env, 'version', ver));
    stmts.push(stamp);
    if (stmts.length > 0) await env.DB.batch(stmts);
    return json({ ok: true, version: ver });
  }
  throw new HttpError(404, 'Не найдено');
}

// ------------------------------------------------------------------- вход ----

let ready = null;

// Таблицы создаются сами при первом обращении: отдельной настройки базы не нужно
function ensureSchema(env) {
  ready ||= (async () => {
    const statements = schema.split(/;\s*\n/).map((x) => x.replace(/--.*$/gm, '').replace(/\s+/g, ' ').trim()).filter(Boolean);
    await env.DB.batch(statements.map((x) => env.DB.prepare(x)));
  })().catch((e) => {
    ready = null;
    throw e;
  });
  return ready;
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const ip = req.headers.get('CF-Connecting-IP') || 'local';
    try {
      if (url.pathname.startsWith('/sync/') || url.pathname.startsWith('/api/')) await ensureSchema(env);
      if (url.pathname.startsWith('/sync/')) {
        if (!(await validSync(env, req))) return json({ detail: 'Нет доступа' }, 401);
        return await handleSync(req, env, url);
      }
      if (url.pathname.startsWith('/api/')) return await handleApi(req, env, url, ip);
      return env.ASSETS.fetch(req);
    } catch (e) {
      if (e instanceof HttpError) return json({ detail: e.message }, e.status);
      console.error('worker error', e && e.stack ? e.stack : e);
      return json({ detail: 'Что-то пошло не так на сервере. Попробуйте ещё раз' }, 500);
    }
  },
};

export { applyAction, fmtAge, fmtMoney };
