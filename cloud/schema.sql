-- Облачная база Scout (Cloudflare D1). Хранит только копию того, что прислал ваш компьютер, и очередь ваших действий.

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY,
  verdict TEXT NOT NULL,
  score INTEGER NOT NULL,
  hot INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'new',
  feedback TEXT,
  offered INTEGER NOT NULL DEFAULT 0,
  stage TEXT,
  posted_at TEXT NOT NULL,
  budget REAL NOT NULL DEFAULT 0,
  fixed INTEGER NOT NULL DEFAULT 0,
  search TEXT NOT NULL DEFAULT '',
  card TEXT NOT NULL,
  detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_feed ON orders(verdict, state, score);
CREATE INDEX IF NOT EXISTS orders_posted ON orders(posted_at);

CREATE TABLE IF NOT EXISTS discarded (
  id INTEGER PRIMARY KEY,
  posted_at TEXT NOT NULL,
  search TEXT NOT NULL DEFAULT '',
  item TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  body TEXT,
  status TEXT NOT NULL DEFAULT 'new',
  error TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS commands_status ON commands(status, id);

CREATE TABLE IF NOT EXISTS fails (
  ip TEXT NOT NULL,
  at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS fails_ip ON fails(ip, at);
