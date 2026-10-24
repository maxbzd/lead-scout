// Мастер развёртывания: `npm run setup` делает всё сам: вход в Cloudflare, база, секреты, публикация сайта.
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { createInterface } from 'node:readline/promises';
import { fileURLToPath } from 'node:url';
import { PLACEHOLDER_ID, checkPassword, currentDatabaseId, findDatabase, findUrl, newPassword, newToken, setDatabaseId } from './lib.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const toml = join(root, 'wrangler.toml');
const secretsFile = join(root, '.scout-secrets.json');
const DB_NAME = 'scout';

const wrangler = (args, { capture = false } = {}) => {
  const r = spawnSync('npx', ['wrangler', ...args], { cwd: root, encoding: 'utf8', stdio: capture ? ['inherit', 'pipe', 'pipe'] : ['inherit', 'inherit', 'inherit'] });
  return { ok: r.status === 0, out: `${r.stdout || ''}${r.stderr || ''}`, stdout: r.stdout || '' };
};

const step = (n, text) => console.log(`\n[${n}/5] ${text}`);
const fail = (text) => { console.error(`\nОшибка: ${text}`); process.exit(1); };

async function main() {
  console.log('Scout: установка облачного сайта на Cloudflare (бесплатный тариф подходит).');

  step(1, 'Вход в Cloudflare');
  if (!wrangler(['whoami', '--json'], { capture: true }).ok) {
    console.log('Сейчас откроется браузер: войдите в Cloudflare (или создайте бесплатный аккаунт) и разрешите доступ.');
    if (!wrangler(['login']).ok) fail('Не удалось войти в Cloudflare. Запустите установку ещё раз.');
  } else console.log('Вы уже вошли.');

  step(2, 'Подготовка сайта');
  if (spawnSync('node', ['scripts/prepare.mjs'], { cwd: root, stdio: 'inherit' }).status !== 0) fail('Не удалось подготовить файлы сайта.');

  step(3, 'База данных');
  let text = readFileSync(toml, 'utf8');
  if (currentDatabaseId(text) === PLACEHOLDER_ID) {
    let id = findDatabase(wrangler(['d1', 'list', '--json'], { capture: true }).stdout, DB_NAME);
    if (!id) {
      if (!wrangler(['d1', 'create', DB_NAME]).ok) fail('Не удалось создать базу. Если она уже есть в Cloudflare, удалите её в панели или впишите её id в cloud/wrangler.toml.');
      id = findDatabase(wrangler(['d1', 'list', '--json'], { capture: true }).stdout, DB_NAME);
    }
    if (!id) fail('База создана, но я не смог найти её идентификатор. Впишите его в cloud/wrangler.toml (строка database_id).');
    text = setDatabaseId(text, id);
    writeFileSync(toml, text);
    console.log('База готова.');
  } else console.log('База уже настроена.');

  step(4, 'Пароль и ключ синхронизации');
  let secrets = {};
  if (existsSync(secretsFile)) {
    try { secrets = JSON.parse(readFileSync(secretsFile, 'utf8')); } catch { secrets = {}; }
  }
  if (!secrets.SYNC_TOKEN) secrets.SYNC_TOKEN = newToken();
  if (!secrets.APP_PASSWORD) {
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    const answer = (await rl.question('Придумайте пароль для входа на сайт (от 8 символов) или нажмите Enter, и я придумаю сам: ')).trim();
    rl.close();
    if (answer) {
      const bad = checkPassword(answer);
      if (bad) fail(bad);
      secrets.APP_PASSWORD = answer;
    } else secrets.APP_PASSWORD = newPassword();
  }
  writeFileSync(secretsFile, JSON.stringify(secrets));
  try { chmodSync(secretsFile, 0o600); } catch { /* на Windows права не нужны */ }

  step(5, 'Публикация сайта');
  const dep = wrangler(['deploy', '--secrets-file', secretsFile], { capture: true });
  process.stdout.write(dep.out);
  if (!dep.ok) fail('Публикация не удалась. Прочитайте сообщение выше и запустите установку ещё раз.');
  const url = findUrl(dep.out);

  console.log('\n==================== ГОТОВО ====================');
  console.log(`Адрес сайта:          ${url || '(смотрите строку со словом workers.dev выше)'}`);
  console.log(`Пароль для входа:     ${secrets.APP_PASSWORD}`);
  console.log(`Ключ синхронизации:   ${secrets.SYNC_TOKEN}`);
  console.log('\nДальше в программе Scout на компьютере: Профиль → Подключения → «Облачный сайт»,');
  console.log('вставьте адрес и ключ синхронизации. Пароль и ключ сохранены в cloud/.scout-secrets.json (не показывайте его никому).');
}

main().catch((e) => fail(e.message));
