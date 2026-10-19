// Общее состояние приложения и загрузка данных.

import { get } from './api.js';

export const S = {
  tab: 'feed',
  status: null,
  feed: null,
  filter: 'all',
  sort: 'score',
  q: '',
  stats: null,
  profile: null,
  presets: null,
  notif: null,
  chats: null,
  online: navigator.onLine,
};

const cache = {
  read(key) {
    try { return JSON.parse(localStorage.getItem('ls:' + key)); } catch { return null; }
  },
  write(key, value) {
    try { localStorage.setItem('ls:' + key, JSON.stringify(value)); } catch { /* память переполнена или запрещена */ }
  },
  clear() {
    try { Object.keys(localStorage).filter((k) => k.startsWith('ls:')).forEach((k) => localStorage.removeItem(k)); } catch { /* ничего */ }
  },
};
export { cache };

export async function loadStatus() {
  try {
    S.status = await get('/status');
    cache.write('status', S.status);
  } catch (e) {
    S.status = S.status || cache.read('status');
    if (!S.status) throw e;
  }
  return S.status;
}

let feedSeq = 0;

export async function loadFeed() {
  const my = ++feedSeq;
  const { filter, sort } = S;
  const text = S.q.trim();
  const q = new URLSearchParams({ filter, sort });
  if (text) q.set('q', text);
  const key = `feed:${filter}:${sort}`;
  try {
    const data = await get('/orders?' + q.toString());
    if (my !== feedSeq) return S.feed; // за это время запросили другой список: старый ответ не нужен
    S.feed = data;
    S.online = true;
    if (!text) cache.write(key, data);
  } catch (e) {
    if (my !== feedSeq) return S.feed;
    const old = cache.read(key);
    if (!old) throw e;
    S.feed = old;
    S.online = false;
  }
  return S.feed;
}

export async function loadStats() {
  try {
    S.stats = await get('/stats');
    cache.write('stats', S.stats);
  } catch (e) {
    S.stats = cache.read('stats');
    if (!S.stats) throw e;
  }
  return S.stats;
}

export async function loadProfile() {
  const r = await get('/profile');
  S.profile = r.profile;
  S.presets = r.presets;
  return S.profile;
}

export async function loadNotif() {
  S.notif = await get('/notifications');
  return S.notif;
}

export async function loadChats() {
  S.chats = await get('/chats');
  return S.chats;
}

export function replaceCard(card) {
  if (!S.feed) return;
  const i = S.feed.items.findIndex((o) => o.id === card.id);
  if (i >= 0) S.feed.items[i] = card;
}
