import httpx
import pytest

from leadscout import store
from leadscout.bot import BotApi, BotError, BotService
from leadscout.notify import Notifier
from leadscout.profile import Notifications, save_notifications

from .conftest import make_raw

BOT = "Нужен телеграм-бот с GPT для записи клиентов в салон красоты. Бюджет 45 000 ₽, срок 7 дней. Писать @salon_admin"
N8N = "Требуется разработчик для настройки автоворонки в n8n: заявки из Telegram в amoCRM. Бюджет 30 000 ₽, нужно за 3 дня. @orbita_studio"
OWNER = 777


class FakeApi:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call(self, method, **params):
        self.calls.append((method, params))
        if method == "getMe":
            return {"username": "scout_bot", "id": 1}
        return {}

    async def close(self):
        pass

    def sent(self, method="sendMessage"):
        return [p for m, p in self.calls if m == method]


@pytest.fixture()
def bot(db, settings, pipe):
    settings.public_url = "https://scout.example.com"
    api = FakeApi()
    svc = BotService(api, db, settings, pipe, owner_id=OWNER)
    return svc, api


def msg(text, uid=OWNER):
    return {"update_id": 1, "message": {"text": text, "from": {"id": uid}, "chat": {"id": uid}}}


def cb(data, uid=OWNER):
    return {"update_id": 2, "callback_query": {"id": "cb1", "data": data, "from": {"id": uid},
                                               "message": {"message_id": 55, "chat": {"id": uid}}}}


async def make_order(pipe, chat_id, text=BOT, **kw):
    return await pipe.process(make_raw(chat_id, text, **kw))


@pytest.mark.asyncio
async def test_pairing_binds_owner(db, settings, pipe):
    api = FakeApi()
    svc = BotService(api, db, settings, pipe, owner_id=None)
    await svc.setup()
    code = db.get_setting("bot_pair")
    assert svc.pair_link() == f"https://t.me/scout_bot?start={code}"
    await svc.handle_update(msg("/start wrongcode", uid=1))
    assert not svc.paired
    await svc.handle_update(msg(f"/start {code}", uid=42))
    assert svc.owner_id == 42 and db.get_setting("owner_id") == 42 and db.get_setting("bot_pair") is None
    assert "подключён" in api.sent()[-1]["text"]
    # чужому дальше не отвечаем по делу
    await svc.handle_update(msg("/digest", uid=99))
    assert "личный" in api.sent()[-1]["text"]


@pytest.mark.asyncio
async def test_card_has_buttons_and_html(bot, pipe, chat_id, db):
    svc, api = bot
    oid = await make_order(pipe, chat_id)
    row = store.get_order(db, oid)
    await svc.send_card(row, "Новый заказ")
    sent = api.sent()[-1]
    assert sent["chat_id"] == OWNER and sent["parse_mode"] == "HTML"
    assert "Новый заказ" in sent["text"] and "<b>" in sent["text"] and "45" in sent["text"]
    flat = [b for r in sent["reply_markup"]["inline_keyboard"] for b in r]
    data = {b.get("callback_data") for b in flat}
    assert {f"offer:{oid}", f"fb:up:{oid}", f"fb:dn:{oid}"} <= data
    urls = {b.get("url") for b in flat}
    assert f"https://scout.example.com/#/o/{oid}" in urls and "https://t.me/salon_admin" in urls
    assert all(len(b.get("callback_data", "")) <= 64 for b in flat)


@pytest.mark.asyncio
async def test_feedback_buttons(bot, pipe, chat_id, db):
    svc, api = bot
    oid = await make_order(pipe, chat_id)
    await svc.handle_update(cb(f"fb:up:{oid}"))
    assert store.get_order(db, oid)["feedback"] == "up"
    await svc.handle_update(cb(f"fb:dn:{oid}"))
    markup = api.sent("editMessageReplyMarkup")[-1]["reply_markup"]["inline_keyboard"]
    codes = [r[0]["callback_data"] for r in markup]
    assert f"why:budget:{oid}" in codes
    await svc.handle_update(cb(f"why:budget:{oid}"))
    row = store.get_order(db, oid)
    assert row["feedback"] == "down" and row["feedback_reason"] == "budget" and row["state"] == "hidden"
    assert api.sent("editMessageText")


@pytest.mark.asyncio
async def test_offer_flow(bot, pipe, chat_id, db):
    svc, api = bot
    oid = await make_order(pipe, chat_id)
    await svc.handle_update(cb(f"offer:{oid}"))
    out = api.sent()[-1]
    assert "<code>" in out["text"] and "Черновик предложения" in out["text"]
    first_buttons = [b for r in out["reply_markup"]["inline_keyboard"] for b in r]
    assert any(b.get("url") == "https://t.me/salon_admin" for b in first_buttons)
    await svc.handle_update(cb(f"redo:1:{oid}"))
    assert api.sent()[-1]["text"] != out["text"]
    await svc.handle_update(cb(f"sent:{oid}"))
    row = store.get_order(db, oid)
    assert row["offered_at"] and row["stage"] == "wait"


@pytest.mark.asyncio
async def test_strangers_cannot_press_buttons(bot, pipe, chat_id, db):
    svc, api = bot
    oid = await make_order(pipe, chat_id)
    await svc.handle_update(cb(f"fb:up:{oid}", uid=5))
    assert store.get_order(db, oid)["feedback"] is None
    assert api.sent("answerCallbackQuery")[-1]["text"] == "Это личный бот"


@pytest.mark.asyncio
async def test_commands(bot, pipe, chat_id, db):
    svc, api = bot
    await make_order(pipe, chat_id)
    await svc.handle_update(msg("/digest"))
    assert "Лучшие заказы" in api.sent()[-1]["text"] and api.sent()[-1]["reply_markup"]["inline_keyboard"]
    await svc.handle_update(msg("/stats"))
    assert "Итоги недели" in api.sent()[-1]["text"]
    await svc.handle_update(msg("/chats"))
    assert "AI Заказы" in api.sent()[-1]["text"]
    await svc.handle_update(msg("/pause"))
    assert "выключены" in api.sent()[-1]["text"]
    from leadscout.profile import load_notifications
    assert load_notifications(db).paused
    await svc.handle_update(msg("/resume"))
    assert not load_notifications(db).paused


@pytest.mark.asyncio
async def test_add_command(bot, pipe, db):
    svc, api = bot
    added = []

    async def add(ref):
        added.append(ref)
        return 1

    svc.add_chat = add
    await svc.handle_update(msg("/add https://t.me/some_chat"))
    assert added == ["https://t.me/some_chat"] and "Чат добавлен" in api.sent()[-1]["text"]

    async def boom(ref):
        raise RuntimeError("Нет такого чата")

    svc.add_chat = boom
    await svc.handle_update(msg("/add https://t.me/zzz"))
    assert "Нет такого чата" in api.sent()[-1]["text"]


@pytest.mark.asyncio
async def test_html_is_escaped(bot, pipe, chat_id, db):
    svc, api = bot
    oid = await make_order(pipe, chat_id, text=BOT + " <script>alert(1)</script> & прочее")
    await svc.send_card(store.get_order(db, oid))
    text = api.sent()[-1]["text"]
    assert "<script>" not in text


# ---------------------------------------------------------------- notifier --


def _around_now(db, hours: int = 1) -> tuple[str, str]:
    from datetime import timedelta

    from leadscout.profile import load_profile
    from leadscout.util import local_now

    cur = local_now(load_profile(db).tz)
    return (cur - timedelta(hours=hours)).strftime("%H:%M"), (cur + timedelta(hours=hours)).strftime("%H:%M")


def day_mode(db, **kw):
    save_notifications(db, Notifications(quiet=False, digest_time="00:00", **kw))


def night_mode(db, **kw):
    start, end = _around_now(db)
    save_notifications(db, Notifications(quiet=True, quiet_start=start, quiet_end=end, digest_time="00:00", **kw))


@pytest.mark.asyncio
async def test_instant_alert_for_matches_and_maybes(bot, pipe, chat_id, db, settings):
    svc, api = bot
    n = Notifier(db, settings, lambda: svc)
    day_mode(db)
    hot = await make_order(pipe, chat_id, BOT, minutes_ago=5)
    mid = await make_order(pipe, chat_id, "Нужен парсер Telegram-каналов в таблицу на Python, бюджет 25 000 ₽, срок 5 дней. @parser_guy", minutes_ago=30)
    row = store.get_order(db, hot)
    assert row["score"] >= 90
    sent = await n.after_scan([hot, mid])
    assert sent == 2 and len(api.sent()) == 2  # присылаем и «подойдёт», и «возможно подойдёт»
    assert store.get_order(db, hot)["alert_state"] == "sent"
    assert await n.after_scan([hot]) == 0  # второй раз не шлём


@pytest.mark.asyncio
async def test_quiet_hours_queue_then_digest(bot, pipe, chat_id, db, settings):
    svc, api = bot
    n = Notifier(db, settings, lambda: svc)
    night_mode(db)
    oid = await make_order(pipe, chat_id, BOT, minutes_ago=5)
    assert await n.after_scan([oid]) == 0
    assert store.get_order(db, oid)["alert_state"] == "queued" and not api.sent()
    assert n.digest_due()
    assert await n.send_digest() == 1
    assert "Доброе утро" in api.sent()[-1]["text"] or "Свежие" in api.sent()[-1]["text"]
    assert not n.digest_due()
    assert await n.send_digest() == 0  # повторно ничего нового


@pytest.mark.asyncio
async def test_big_orders_break_quiet_hours(bot, pipe, chat_id, db, settings):
    svc, api = bot
    n = Notifier(db, settings, lambda: svc)
    big = "Нужен Telegram-бот с RAG по базе знаний и LLM для юрфирмы. Бюджет 150 000 ₽, срок 10 дней. @bigfirm"
    night_mode(db)
    oid = await make_order(pipe, chat_id, big, minutes_ago=3)
    assert store.get_order(db, oid)["score"] >= 90
    assert await n.after_scan([oid]) == 1


@pytest.mark.asyncio
async def test_paused_and_unpaired_send_nothing(bot, pipe, chat_id, db, settings):
    svc, api = bot
    n = Notifier(db, settings, lambda: svc)
    oid = await make_order(pipe, chat_id, BOT, minutes_ago=5)
    day_mode(db, paused=True)
    assert await n.after_scan([oid]) == 0
    day_mode(db)
    svc.owner_id = None
    assert await n.after_scan([oid]) == 0
    assert not api.sent()


@pytest.mark.asyncio
async def test_botapi_error_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
        return httpx.Response(200, json={"ok": True, "result": {"x": 1}})

    api = BotApi("123:abc", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(BotError) as e:
        await api.call("getMe")
    assert "Токен" in str(e.value) and e.value.code == 401
    assert await api.call("sendMessage", chat_id=1, text="x", reply_markup=None) == {"x": 1}
