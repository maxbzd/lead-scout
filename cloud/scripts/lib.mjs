// Мелкие чистые функции мастера развёртывания: их можно проверить тестами без аккаунта Cloudflare.
import { randomBytes } from 'node:crypto';

export const PLACEHOLDER_ID = '00000000-0000-0000-0000-000000000000';

export function findDatabase(listJson, name) {
  let items;
  try { items = JSON.parse(listJson); } catch { return null; }
  if (!Array.isArray(items)) return null;
  const hit = items.find((d) => d && d.name === name);
  return hit ? hit.uuid || hit.database_id || hit.id || null : null;
}

export function setDatabaseId(toml, id) {
  if (!/^[0-9a-f-]{36}$/i.test(id)) throw new Error('Некорректный идентификатор базы');
  if (!/database_id\s*=\s*"[^"]*"/.test(toml)) throw new Error('В wrangler.toml нет строки database_id');
  return toml.replace(/database_id\s*=\s*"[^"]*"/, `database_id = "${id}"`);
}

export function currentDatabaseId(toml) {
  const m = toml.match(/database_id\s*=\s*"([^"]*)"/);
  return m ? m[1] : null;
}

export function findUrl(text) {
  const m = String(text).match(/https:\/\/[a-z0-9-]+(?:\.[a-z0-9-]+)*\.workers\.dev/i);
  return m ? m[0] : null;
}

export function newToken() {
  return randomBytes(24).toString('hex');
}

export function newPassword() {
  // без похожих символов, чтобы пароль можно было набрать с телефона
  const alphabet = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const bytes = randomBytes(14);
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join('');
}

export function checkPassword(pw) {
  return typeof pw === 'string' && pw.length >= 8 ? '' : 'Пароль должен быть не короче 8 символов';
}
