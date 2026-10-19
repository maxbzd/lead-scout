"""Runtime без сети: вход в Telegram, подключение бота, проверка чатов, живые сообщения."""

import asyncio

import pytest

from leadscout import runtime as rtm
from leadscout import store
from leadscout.config import Settings
from leadscout.runtime import Runtime
from leadscout.sources.telegram import LoginError, TelegramProblem

from .conftest import make_raw


class FakeLogin:
    instances: list = []

    def __init__(self, api_id, api_hash):
        self.api_id, self.api_hash = api_id, api_hash
        self.closed = False
        self.password_needed = False
        FakeLogin.instances.append(self)

    async def send_code(self, phone):
        if "bad" in phone:
            raise LoginError("Номер не подошёл")

    async def sign_in_code(self, code):
        return "password" if self.password_needed else "ok"

    async def sign_in_password(self, password):
        if password != "ok":
            raise LoginError("Пароль двухфакторной защиты неверный")

    def session_string(self):
        return "SESSION-STRING"

    async def close(self):
        self.closed = True


class FakeReader:
    name = "telegram"

    def __init__(self, db, api_id, api_hash, session):
        self.db, self.session = db, session
        self.state, self.error, self.me = "off", None, None
        self.listener = None
        self.stopped = False
        self.batches = []

    async def start(self):
        self.state, self.me = "ready", {"name": "Тест", "username": "t", "id": 1}

    async def stop(self):
        self.stopped = True
        self.state = "off"

    def status(self):
        return {"state": self.state, "error": self.error, "me": self.me}

    def refresh_map(self):
        pass

    async def fetch(self):
        return self.batches.pop(0) if self.batches else []


@pytest.fixture()
def rt(tmp_path, monkeypatch):
    FakeLogin.instances.clear()
    monkeypatch.setattr(rtm, "TelegramLogin", FakeLogin)
    monkeypatch.setattr(rtm, "TelegramReader", FakeReader)

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(rtm, "revoke_session", noop)
    s = Settings(data_dir=tmp_path, password="pw", demo=True, run_worker=False)
    return Runtime(s)


async def test_save_api_validates(rt):
    with pytest.raises(LoginError, match="число"):
        await rt.tg_save_api("abc", "x" * 32)
    with pytest.raises(LoginError, match="32"):
        await rt.tg_save_api("123", "short")
    await rt.tg_save_api("123", "a" * 32)
    assert rt.tg_status()["has_api"]


async def test_full_login_replaces_demo_with_real_reader(rt):
    await rt.start()
    await rt.scan()
    assert rt.demo_enabled() and rt.db.scalar("SELECT COUNT(*) FROM chats WHERE is_demo=1") > 0
    with pytest.raises(LoginError, match="api_id"):
        await rt.tg_login_start("+79991234567")
    await rt.tg_save_api("123", "a" * 32)
    with pytest.raises(LoginError, match="Номер"):
        await rt.tg_login_start("bad")
    assert rt.tg_status()["logging_in"] is False
    await rt.tg_login_start("+79991234567")
    assert await rt.tg_login_code("12345") == "ok"
    assert rt.secrets.get("tg_session") == "SESSION-STRING"
    assert isinstance(rt.source, FakeReader) and rt.source.session == "SESSION-STRING"
    assert rt.db.scalar("SELECT COUNT(*) FROM chats WHERE is_demo=1") == 0, "демо-данные убираются"
    assert rt.demo_enabled() is False
    assert FakeLogin.instances[-1].closed
    with pytest.raises(TelegramProblem):
        await rt.set_demo(True)
    raw = rt.db.scalar("SELECT value FROM settings WHERE key='secret:tg_session'")
    assert raw and "SESSION-STRING" not in str(raw), "сессия лежит в базе только в зашифрованном виде"


async def test_two_factor_and_expired_login(rt):
    await rt.tg_save_api("123", "a" * 32)
    await rt.tg_login_start("+79991234567")
    FakeLogin.instances[-1].password_needed = True
    assert await rt.tg_login_code("1") == "password"
    with pytest.raises(LoginError, match="неверный"):
        await rt.tg_login_password("nope")
    await rt.tg_login_password("ok")
    assert rt.secrets.has("tg_session")
    with pytest.raises(LoginError, match="устарел"):
        await rt.tg_login_code("1")  # после входа сессия входа закрыта


async def test_login_expires_after_15_minutes(rt, monkeypatch):
    await rt.tg_save_api("123", "a" * 32)
    await rt.tg_login_start("+79991234567")
    rt._login_started -= 1000
    with pytest.raises(LoginError, match="устарел"):
        await rt.tg_login_code("1")


async def test_logout_removes_session(rt):
    await rt.tg_save_api("123", "a" * 32)
    await rt.tg_login_start("+79991234567")
    await rt.tg_login_code("1")
    await rt.tg_logout()
    assert not rt.secrets.has("tg_session")
    assert not isinstance(rt.source, FakeReader)


async def test_scan_survives_source_failure_and_records_it(rt):
    await rt.start()

    class Boom:
        name = "x"

        async def fetch(self):
            raise RuntimeError("сеть пропала")

        async def stop(self):
            pass

    rt.source = Boom()
    res = await rt.scan()
    assert res["error"] == "RuntimeError" and rt.scanning is False
    assert rt.db.one("SELECT error FROM scan_runs ORDER BY id DESC")["error"] == "RuntimeError"


async def test_scan_without_source_and_concurrent_skip(rt):
    rt.source = None
    assert "Telegram" in (await rt.scan())["error"]

    class Slow:
        name = "slow"

        async def fetch(self):
            await asyncio.sleep(0.2)
            return []

        async def stop(self):
            pass

    rt.source = Slow()
    first = asyncio.create_task(rt.scan())
    await asyncio.sleep(0.05)
    assert await rt.scan() == {"skipped": True}
    await first


async def test_scan_processes_messages_and_publishes(rt):
    await rt.start()
    await rt.set_demo(False)
    reader = FakeReader(rt.db, 1, "h", "s")
    rt.source = reader
    cid = store.add_chat(rt.db, "Чат", username="chatx", tg_id=-1001)
    reader.batches = [[
        make_raw(cid, "Нужен разработчик Telegram-бота с интеграцией OpenAI. Бюджет 40 000 руб, срок 5 дней. @client_a", tg_msg_id=11),
        make_raw(cid, "Продам вечные ссылки, пишите в лс", tg_msg_id=12, author="spam"),
    ]]
    events = []

    async def listen():
        async for ev in rt.bus.subscribe():
            events.append(ev["type"])

    t = asyncio.create_task(listen())
    await asyncio.sleep(0)
    res = await rt.scan()
    await asyncio.sleep(0.05)
    t.cancel()
    assert res["messages"] == 2 and res["orders"] == 1
    assert {"scan", "orders"} <= set(events)
    assert rt.db.one("SELECT last_msg_id FROM chats WHERE id=?", (cid,))["last_msg_id"] == 12


async def test_live_message_is_processed_once(rt):
    await rt.start()
    cid = store.add_chat(rt.db, "Чат", username="chaty", tg_id=-1002)
    raw = make_raw(cid, "Ищу исполнителя: ИИ-ассистент для сайта на GPT, бюджет 60 000 ₽, срок 2 недели. @client_b", tg_msg_id=50)
    await rt.ingest_one(raw)
    await rt.ingest_one(raw)  # то же сообщение пришло и вживую, и при проверке
    assert rt.db.scalar("SELECT COUNT(*) FROM orders") >= 1
    assert rt.db.scalar("SELECT COUNT(*) FROM messages WHERE tg_msg_id=50") == 1


async def test_connect_bot_rejects_garbage_token(rt):
    with pytest.raises(rtm.BotError, match="не похоже"):
        await rt.connect_bot("hello")


async def test_connect_claude_rejects_garbage_key(rt):
    with pytest.raises(rtm.LLMUnavailable, match="sk-ant"):
        await rt.connect_claude("nope")


async def test_slow_telegram_does_not_block_startup(rt, monkeypatch):
    class Hang(FakeReader):
        async def start(self):
            await asyncio.sleep(60)

    monkeypatch.setattr(rtm, "TelegramReader", Hang)
    monkeypatch.setattr(rtm, "START_TIMEOUT", 0.05)
    await rt.tg_save_api("123", "a" * 32)
    rt.secrets.set("tg_session", "S")
    await rt.start()
    assert rt.tg_status()["state"] == "error" and "не отвечает" in rt.tg_status()["error"]
