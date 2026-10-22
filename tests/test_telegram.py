"""Чтение Telegram без сети: подставные клиент и сообщения."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon import errors, types

from leadscout import store
from leadscout.sources import telegram as tg
from leadscout.sources.telegram import (
    TelegramProblem,
    TelegramReader,
    describe_error,
    message_to_raw,
    sender_info,
)
from leadscout.util import iso


def user(**kw):
    base = {"id": 7, "first_name": "Иван", "last_name": "К", "username": "ivan_k", "bot": False}
    base.update(kw)
    return types.User(**base)


def msg(i, text, minutes_ago=5, sender_id=7, **kw):
    m = SimpleNamespace(
        id=i, message=text, date=datetime.now(UTC) - timedelta(minutes=minutes_ago), sender_id=sender_id,
        action=kw.get("action"), reply_to=kw.get("reply_to"), replies=kw.get("replies"),
    )

    async def get_sender():
        return user()

    m.get_sender = get_sender
    return m


class FakeClient:
    def __init__(self, messages=None, error=None):
        self.messages = messages or []
        self.error = error
        self.calls = []

    connected = True

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connected = True

    def iter_messages(self, ent, limit=None, min_id=None, reverse=False, offset_date=None):
        self.calls.append({"limit": limit, "min_id": min_id, "reverse": reverse, "offset_date": offset_date})
        items = list(self.messages)
        if min_id is not None:
            items = [m for m in items if m.id > min_id]
        if not reverse:
            items = sorted(items, key=lambda m: m.id, reverse=True)
        else:
            items = sorted(items, key=lambda m: m.id)
        error = self.error

        async def gen():
            if error:
                raise error
            for m in items[:limit]:
                yield m

        return gen()

    async def get_entity(self, ref):
        return SimpleNamespace(id=ref if isinstance(ref, int) else 1)

    async def get_messages(self, ent, limit=1):
        return sorted(self.messages, key=lambda m: m.id, reverse=True)[:limit]


def reader(db, client):
    r = TelegramReader(db, 1, "h", "")
    r.client = client
    r.state = "ready"
    return r


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fast(_):
        return None

    monkeypatch.setattr(tg.asyncio, "sleep", fast)


def chat_row(db, **kw):
    cid = store.add_chat(db, "Фриланс", username="frl_chat", tg_id=-1001234567890, **kw)
    return db.one("SELECT * FROM chats WHERE id=?", (cid,))


def test_sender_info_marks_bots_and_scam():
    assert sender_info(user())["username"] == "ivan_k"
    b = sender_info(user(bot=True, scam=True))
    assert b["username"] is None and b["flags"]["bot"] and b["flags"]["scam"]
    assert sender_info(None)["id"] is None


def test_message_to_raw(db):
    row = chat_row(db)
    raw = message_to_raw(msg(5, "Нужен бот"), row, user())
    assert raw.text == "Нужен бот" and raw.author_username == "ivan_k"
    assert raw.link == "https://t.me/frl_chat/5"
    assert raw.posted_at.tzinfo is not None
    assert message_to_raw(msg(6, "   "), row, user()) is None  # картинки без подписи пропускаем
    reply = msg(7, "Беру", reply_to=SimpleNamespace(reply_to_msg_id=5), replies=SimpleNamespace(replies=3))
    r = message_to_raw(reply, row, user())
    assert r.reply_to == 5 and r.replies_count == 3


def test_private_chat_link_uses_short_id(db):
    cid = store.add_chat(db, "Закрытый", tg_id=-1001234567890)
    row = db.one("SELECT * FROM chats WHERE id=?", (cid,))
    assert store.chat_link(row, 9) == "https://t.me/c/1234567890/9"


async def test_first_scan_takes_recent_messages_oldest_first(db):
    row = chat_row(db)
    client = FakeClient([msg(1, "старое", minutes_ago=60 * 24 * 10), msg(2, "раз", 50), msg(3, "два", 10),
                         msg(4, "служебное", 5, action=object())])
    out = await reader(db, client)._fetch_chat(row)
    assert [m.text for m in out] == ["раз", "два"]  # старше 72 часов и служебные отброшены, порядок от старых к новым
    assert client.calls[0]["limit"] == tg.FIRST_SCAN_LIMIT and client.calls[0]["min_id"] is None


async def test_catchup_reads_only_after_cursor(db):
    row = chat_row(db)
    db.run("UPDATE chats SET last_msg_id=2 WHERE id=?", (row["id"],))
    row = db.one("SELECT * FROM chats WHERE id=?", (row["id"],))
    client = FakeClient([msg(1, "a"), msg(2, "b"), msg(3, "c"), msg(4, "d")])
    out = await reader(db, client)._fetch_chat(row)
    assert [m.tg_msg_id for m in out] == [3, 4]
    assert client.calls[0]["min_id"] == 2 and client.calls[0]["reverse"] is True


async def test_empty_first_scan_sets_cursor(db):
    row = chat_row(db)
    client = FakeClient([msg(9, "очень старое", minutes_ago=60 * 24 * 30)])
    out = await reader(db, client)._fetch_chat(row)
    assert out == []
    assert db.one("SELECT last_msg_id FROM chats WHERE id=?", (row["id"],))["last_msg_id"] == 9


async def test_fetch_records_chat_errors_and_continues(db):
    bad = chat_row(db)
    ok_id = store.add_chat(db, "Другой", username="other_chat", tg_id=-1009999999999)
    client = FakeClient([msg(1, "привет", 3)])
    r = reader(db, client)
    orig = r._fetch_chat

    async def flaky(row):
        if row["id"] == bad["id"]:
            raise errors.ChannelPrivateError(request=None)
        return await orig(row)

    r._fetch_chat = flaky
    out = await r.fetch()
    assert [m.chat_id for m in out] == [ok_id]
    assert "Нет доступа" in db.one("SELECT last_error FROM chats WHERE id=?", (bad["id"],))["last_error"]


async def test_fetch_stops_when_account_logged_out(db):
    chat_row(db)
    r = reader(db, FakeClient())

    async def lost(row):
        raise errors.AuthKeyUnregisteredError(request=None)

    r._fetch_chat = lost
    assert await r.fetch() == []
    assert r.state == "unauthorized" and "Войдите заново" in r.error


async def test_fetch_when_not_ready_is_empty(db):
    r = TelegramReader(db, 1, "h", "")
    assert await r.fetch() == []


async def test_add_by_link_rejects_garbage_and_users(db):
    r = reader(db, FakeClient())
    with pytest.raises(TelegramProblem, match="Не похоже на ссылку"):
        await r.add_by_link("привет как дела")

    async def get_user(ref):
        return user()

    r.client.get_entity = get_user
    with pytest.raises(TelegramProblem, match="личный аккаунт"):
        await r.add_by_link("https://t.me/ivan_k")


async def test_add_by_link_public_channel(db):
    ent = types.Channel(id=1234567890, title="Заказы", photo=types.ChatPhotoEmpty(), date=datetime.now(UTC),
                        username="zakazy_ai", participants_count=900, access_hash=1)

    class C:
        async def __call__(self, req):
            return None

        async def get_entity(self, ref):
            return ent

    r = reader(db, C())
    cid = await r.add_by_link("@zakazy_ai")
    row = db.one("SELECT * FROM chats WHERE id=?", (cid,))
    assert row["title"] == "Заказы" and row["username"] == "zakazy_ai" and row["members"] == 900
    assert await r.add_by_link("https://t.me/zakazy_ai") == cid  # повторное добавление не плодит дубли


async def test_add_by_link_translates_telegram_errors(db):
    class C:
        async def get_entity(self, ref):
            raise errors.UsernameNotOccupiedError(request=None)

    r = reader(db, C())
    with pytest.raises(TelegramProblem, match="Такого чата"):
        await r.add_by_link("@nobody_here")


def test_describe_error_texts():
    assert "подождать 12" in describe_error(errors.FloodWaitError(request=None, capture=12))
    assert "Нет доступа" in describe_error(errors.ChannelPrivateError(request=None))
    assert "недействительна" in describe_error(errors.InviteHashExpiredError(request=None))
    assert "Ошибка Telegram" in describe_error(RuntimeError("x"))


async def test_login_normalises_phone_and_maps_errors(monkeypatch):
    calls = {}

    class FakeTL:
        session = SimpleNamespace(save=lambda: "SESSION")

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def send_code_request(self, phone):
            calls["phone"] = phone
            return SimpleNamespace(phone_code_hash="H")

        async def sign_in(self, phone=None, code=None, phone_code_hash=None, password=None):
            if password == "bad":
                raise errors.PasswordHashInvalidError(request=None)
            if code == "00000":
                raise errors.PhoneCodeInvalidError(request=None)
            if code == "22222":
                raise errors.SessionPasswordNeededError(request=None)

    monkeypatch.setattr(tg, "_client", lambda *a, **k: FakeTL())
    login = tg.TelegramLogin(1, "h")
    await login.send_code("8 (999) 123-45-67")
    assert calls["phone"].startswith("+") and login.code_hash == "H"
    with pytest.raises(tg.LoginError, match="Код неверный"):
        await login.sign_in_code("0 0 0 0 0")
    assert await login.sign_in_code("22222") == "password"
    assert await login.sign_in_code("12345") == "ok"
    with pytest.raises(tg.LoginError, match="Пароль"):
        await login.sign_in_password("bad")
    assert login.session_string() == "SESSION"


async def test_dead_connection_is_revived_before_fetching(db):
    chat_row(db)
    client = FakeClient([msg(1, "привет", 3)])
    client.connected = False
    r = reader(db, client)
    await r.fetch()
    assert client.connected is True

    async def fail():
        raise OSError("нет сети")

    client.connected = False
    client.connect = fail
    assert await r.fetch() == [] and r.state == "error"  # дальше сработает автоматическое переподключение


async def test_cursor_moves_over_media_without_text(db):
    row = chat_row(db)
    db.run("UPDATE chats SET last_msg_id=100 WHERE id=?", (row["id"],))
    row = db.one("SELECT * FROM chats WHERE id=?", (row["id"],))
    media = [msg(i, "", 3) for i in range(101, 1101)]  # 1000 сообщений-картинок
    r = reader(db, FakeClient(media))
    assert await r._fetch_chat(row) == [] and r.seen[row["id"]] == 1100
    r.commit_cursors()
    assert db.one("SELECT last_msg_id FROM chats WHERE id=?", (row["id"],))["last_msg_id"] == 1100


async def test_own_messages_are_not_orders(db):
    row = chat_row(db)
    mine = msg(5, "Ищу разработчика Telegram-бота, бюджет 30 000 руб, срок неделя", 3)
    mine.out = True
    other = msg(6, "Нужен бот, бюджет 20 000 руб", 2)
    r = reader(db, FakeClient([mine, other]))
    out = await r._fetch_chat(row)
    assert [m.tg_msg_id for m in out] == [6]


async def test_stale_chat_reads_from_recent_date_not_old_cursor(db):
    row = chat_row(db)
    db.run("UPDATE chats SET last_msg_id=50, last_scan_at=? WHERE id=?",
           (iso(datetime.now(UTC) - timedelta(days=3)), row["id"]))
    row = db.one("SELECT * FROM chats WHERE id=?", (row["id"],))
    client = FakeClient([msg(60, "свежее", 3)])
    await reader(db, client)._fetch_chat(row)
    assert client.calls[0]["offset_date"] is not None and client.calls[0]["min_id"] is None


def test_hidden_link_becomes_contact():
    row = {"id": 1, "username": "x_chat", "tg_id": None}
    m = msg(9, "Нужен бот, писать сюда. Бюджет 20 000 руб", 3)
    m.entities = [types.MessageEntityTextUrl(offset=18, length=4, url="https://t.me/real_client")]
    raw = message_to_raw(m, row, user())
    assert "https://t.me/real_client" in raw.text


async def test_empty_login_code_is_rejected():
    login = tg.TelegramLogin(1, "h")
    login.client = object()
    with pytest.raises(tg.LoginError, match="цифрами"):
        await login.sign_in_code("abc")
