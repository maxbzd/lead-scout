import assert from 'node:assert/strict';
import { test } from 'node:test';
import { checkPassword, currentDatabaseId, findDatabase, findUrl, newPassword, newToken, PLACEHOLDER_ID, setDatabaseId } from '../scripts/lib.mjs';

const TOML = 'name = "scout"\n[[d1_databases]]\nbinding = "DB"\ndatabase_name = "scout"\ndatabase_id = "00000000-0000-0000-0000-000000000000"\n';

test('находит базу по имени в ответе wrangler', () => {
  const list = JSON.stringify([{ uuid: 'aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb', name: 'other' }, { uuid: 'cccccccc-1111-2222-3333-dddddddddddd', name: 'scout' }]);
  assert.equal(findDatabase(list, 'scout'), 'cccccccc-1111-2222-3333-dddddddddddd');
  assert.equal(findDatabase(list, 'nope'), null);
  assert.equal(findDatabase('не json', 'scout'), null);
  assert.equal(findDatabase('{}', 'scout'), null);
});

test('id базы вписывается в wrangler.toml и проверяется', () => {
  assert.equal(currentDatabaseId(TOML), PLACEHOLDER_ID);
  const id = 'cccccccc-1111-2222-3333-dddddddddddd';
  const out = setDatabaseId(TOML, id);
  assert.equal(currentDatabaseId(out), id);
  assert.ok(out.includes('binding = "DB"'));
  assert.throws(() => setDatabaseId(TOML, '"; rm -rf /'));
  assert.throws(() => setDatabaseId('name = "x"', id));
});

test('адрес сайта берётся из вывода публикации', () => {
  assert.equal(findUrl('Uploaded scout\nDeployed scout triggers\n  https://scout.ivan-k.workers.dev\nCurrent Version ID: 1'), 'https://scout.ivan-k.workers.dev');
  assert.equal(findUrl('ничего'), null);
});

test('пароль и ключ случайные и достаточно длинные', () => {
  assert.match(newToken(), /^[0-9a-f]{48}$/);
  assert.notEqual(newToken(), newToken());
  const p = newPassword();
  assert.equal(p.length, 14);
  assert.doesNotMatch(p, /[0Ol1I]/);
  assert.equal(checkPassword('short'), 'Пароль должен быть не короче 8 символов');
  assert.equal(checkPassword('long-enough'), '');
});
