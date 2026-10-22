"""Регрессии по второму аудиту: разбор текста, дедупликация, расход ИИ, уведомления."""

import asyncio

import httpx
import pytest

from leadscout import extract as ex
from leadscout import learning, store
from leadscout.bot import BotError, BotService
from leadscout.llm.claude import FallbackLLM
from leadscout.llm.openai_compat import OpenAICompatLLM
from leadscout.notify import Notifier
from leadscout.pipeline import Pipeline
from leadscout.profile import Notifications, Profile, save_notifications
from leadscout.scoring import recommend_price
from leadscout.taxonomy import skill_ids

from .conftest import make_raw
from .test_bot import OWNER, FakeApi
from .test_free_ai import GOOD, answer


@pytest.mark.parametrize(("text", "lo", "hi", "cur", "period"), [
    ("за 15000", 15000, 15000, "RUB", "fixed"),
    ("Бюджет 2000", 2000, 2000, "RUB", "fixed"),
    ("ЗП 100 000", 100000, 100000, "RUB", "fixed"),
    ("Предлагаю 5000", 5000, 5000, "RUB", "fixed"),
    ("7 тр", 7000, 7000, "RUB", "fixed"),
    ("7тр", 7000, 7000, "RUB", "fixed"),
    ("1.500.000 руб", 1500000, 1500000, "RUB", "fixed"),
    ("300 баксов", 300, 300, "USD", "fixed"),
    ("300 у.е.", 300, 300, "USD", "fixed"),
    ("2000 руб за 1 час", 2000, 2000, "RUB", "hour"),
    ("Бюджет 30к, поддержка 2 часа в день", 30000, 30000, "RUB", "fixed"),
    ("до 30000", 30000, 30000, "RUB", "fixed"),
    ("от 20 до 40к", 20000, 40000, "RUB", "fixed"),
])
def test_budget_forms(text, lo, hi, cur, period):
    b = ex.parse_budget(text)
    assert (b.min, b.max, b.currency, b.period) == (lo, hi, cur, period)


@pytest.mark.parametrize("text", [
    "Нужен чат-бот на GPT. Бюджет договорной, тел. 8 900 123 45 67",
    "Бюджет обсудим, пишите на +7 999 111 22 33",
    "Хочу сделать сайт в 2024 году",
])
def test_phone_and_year_are_not_budget(text):
    assert not ex.parse_budget(text).known


def test_deadline_fraction_and_scam_false_positives():
    assert ex.parse_deadline_days("срок: 1,5 месяца") == 45
    for t in ("Бюджет 30к. В стоимость входит внесение 2 правок", "консультации по вкладам и депозитам банка", "покупатель должен оплатить доступ к API"):
        assert not [f for f in ex.scam_flags(t) if f.level == "high"], t
    assert [f.code for f in ex.scam_flags("сначала внесите депозит за доступ к ТЗ")]


@pytest.mark.parametrize("text", [
    "Ищу исполнителя для чат-бота на ChatGPT. Бюджет 30к. Присылайте резюме и примеры работ",
    "Нужен бот на Claude для анализа резюме кандидатов. Бюджет 50к",
])
def test_word_resume_does_not_kill_an_order(text):
    assert ex.looks_like_order(text) or ex.maybe_order(text)


def test_vacancy_still_excluded_and_skills_dictionary():
    assert not ex.looks_like_order("Ищем Python-разработчика в команду, full-time, ЗП от 200к. Присылайте резюме")
    for t in ("Нужно подключить чат гпт к сайту", "подключить DeepSeek к нашей CRM", "нужен специалист по машинному обучению"):
        assert "llm" in skill_ids(t), t


def test_mentioned_handle_is_not_the_contact():
    assert ex.parse_contact("Нужен бот как у @competitor_shop", "real_author").value == "@real_author"
    assert ex.parse_contact("Нужен бот. Писать @real_client", "author_x").value == "@real_client"


def test_price_without_budget_is_capped():
    from leadscout.models import OrderView

    v = OrderView(title="t", summary="", text="", skills=["llm"], budget_min=None, budget_max=None, currency="RUB",
                  period="fixed", budget_rub_min=None, budget_rub_max=None, deadline_days=90, age_minutes=10)
    lo, hi, _ = recommend_price(v, Profile(day_rate=6000))
    assert hi <= 6000 * 10 * 1.15 + 1000


async def test_other_reason_does_not_penalize_skills(pipe, db, chat_id):
    oid = await pipe.process(make_raw(chat_id, "Нужен телеграм-бот с GPT для записи клиентов. Бюджет 45 000 ₽, срок 7 дней. @salon_a1"))
    store.set_feedback(db, oid, "down", "other")
    assert "telegram" not in learning.learned(db).skill_weights


# ---- пайплайн ----------------------------------------------------------------

def ai_pipe(db, settings, handler=None):
    calls = []

    def default(req):
        calls.append(1)
        return answer(GOOD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler or default))
    m = OpenAICompatLLM("https://x.example/v1", "k-12345678", "m", db=db, client=client, rpm=6000)
    return Pipeline(db, FallbackLLM(m), settings), calls


@pytest.mark.parametrize("text", [
    "Нужен ИИ-бот для клиники, кто сделает?",
    "Нужен человек, который разбирается в ИИ",
    "помогите с ботом на gpt. кто сможет? лс",
])
async def test_ai_connected_does_not_hurt_recall(db, settings, chat_id, text):
    pipe, _ = ai_pipe(db, settings)
    assert await pipe.process(make_raw(chat_id, text, author_id=5)), text


async def test_crosspost_costs_one_ai_run(db, settings):
    texts_chats = [store.add_chat(db, f"Чат {i}", username=f"chat_cp_{i}") for i in range(3)]
    pipe, calls = ai_pipe(db, settings)
    text = "Нужен телеграм-бот с GPT для записи клиентов. Бюджет 45 000 ₽, срок 7 дней. Писать @salon_x1"
    res = await asyncio.gather(*(pipe.process(make_raw(c, text, author_id=1)) for c in texts_chats))
    assert sum(1 for r in res if r) == 1 and db.scalar("SELECT COUNT(*) FROM orders") == 1
    assert db.one("SELECT dup_count FROM orders")["dup_count"] == 2


async def test_obvious_scam_does_not_spend_tokens(db, settings, chat_id):
    class Boom:
        name = "claude"
        deferred = False

        async def extract(self, *a):
            raise AssertionError("ИИ не нужен для очевидного обмана")

        score = draft = extract

    pipe = Pipeline(db, Boom(), settings)
    oid = await pipe.process(make_raw(chat_id, "Нужен бот для казино, оплата 100000 руб, сначала внесите депозит за доступ к ТЗ"))
    assert oid and store.get_order(db, oid)["verdict"] == "scam"


async def test_different_price_means_different_order(pipe, db, chat_id):
    a = await pipe.process(make_raw(chat_id, "Нужен телеграм-бот с GPT для записи клиентов в салон. Бюджет 20 000 ₽, срок 7 дней. @owner_a", tg_msg_id=1))
    b = await pipe.process(make_raw(chat_id, "Нужен телеграм-бот с GPT для записи клиентов в салон. Бюджет 50 000 ₽, срок 7 дней. @owner_a", tg_msg_id=2))
    assert a and b and a != b


async def test_reply_that_is_a_new_order_is_not_swallowed(pipe, db, chat_id):
    parent = await pipe.process(make_raw(chat_id, "Нужен телеграм-бот с GPT для салона. Бюджет 45 000 ₽, срок 7 дней. @owner_p", tg_msg_id=10, author_id=1))
    reply = await pipe.process(make_raw(chat_id, "А мне нужен ИИ-бот для клиники, бюджет 60к, срок 10 дней, писать @other_cust", tg_msg_id=11, author_id=2, reply_to=10))
    assert parent and reply


async def test_failing_ai_is_backed_off_and_never_raises(db, settings, chat_id):
    calls = []

    def junk(req):
        calls.append(1)
        return answer({"is_order": True, "score": "high", "verdict": "match", "why": "x", "fit": [{"ok": True, "text": "a"}], "red_flags": True})

    pipe, _ = ai_pipe(db, settings, junk)
    await pipe.process(make_raw(chat_id, "Нужен телеграм-бот с GPT для записи клиентов. Бюджет 45 000 ₽, срок 7 дней. @salon_z"))
    await pipe.upgrade_with_ai()
    first = len(calls)
    for _ in range(3):
        await pipe.upgrade_with_ai()
    assert len(calls) == first, "неудачный заказ ждёт, а не бьёт по лимиту каждые 20 секунд"


async def test_rescue_orders_from_retry_are_returned(db, settings, chat_id):
    pipe = Pipeline(db, __import__("leadscout.llm.rules", fromlist=["RuleLLM"]).RuleLLM(), settings)
    raw = make_raw(chat_id, "Нужен телеграм-бот с GPT для записи клиентов. Бюджет 45 000 ₽, срок 7 дней. @salon_q")
    store.insert_message(db, raw)
    db.run("UPDATE messages SET fetched_at='2020-01-01T00:00:00Z'")
    assert len(await pipe.retry_stuck()) == 1


# ---- уведомления -------------------------------------------------------------

async def make_orders(pipe, chat_id, n):
    ids = []
    for i in range(n):
        ids.append(await pipe.process(make_raw(
            chat_id, f"Нужен телеграм-бот с GPT номер {i} для компании «Ромашка{i}» с интеграцией amoCRM{i * 7}. Бюджет {40 + i} 000 ₽, срок 7 дней. @cust_{i}x",
            tg_msg_id=100 + i, author=f"a{i}", author_id=1000 + i)))
    return ids


@pytest.fixture()
def bot2(db, settings, pipe):
    settings.public_url = "https://scout.example.com"
    api = FakeApi()
    return BotService(api, db, settings, pipe, owner_id=OWNER), api


async def test_digest_sends_everything_in_chunks(bot2, db, settings, pipe, chat_id):
    svc, api = bot2
    save_notifications(db, Notifications(quiet=False, digest_time="00:00"))
    ids = await make_orders(pipe, chat_id, 20)
    assert all(ids)
    n = Notifier(db, settings, lambda: svc)
    assert await n.send_digest() == 20
    assert len(api.sent()) == 2
    assert db.scalar("SELECT COUNT(*) FROM orders WHERE alert_state='digested'") == 20


async def test_demo_orders_never_reach_the_owner(bot2, db, settings, pipe):
    svc, api = bot2
    save_notifications(db, Notifications(quiet=False))
    demo_chat = store.add_chat(db, "Демо", username="demo_chat_x", is_demo=True)
    ids = await make_orders(pipe, demo_chat, 2)
    n = Notifier(db, settings, lambda: svc)
    assert await n.after_scan(ids) == 0 and await n.send_digest() == 0 and api.sent() == []


async def test_failed_alert_is_retried(bot2, db, settings, pipe, chat_id):
    svc, api = bot2
    save_notifications(db, Notifications(quiet=False))
    (oid,) = await make_orders(pipe, chat_id, 1)
    real = svc.api.call
    state = {"fail": True}

    async def flaky(method, **kw):
        if state["fail"] and method == "sendMessage":
            raise BotError("сеть", 500)
        return await real(method, **kw)

    svc.api.call = flaky
    n = Notifier(db, settings, lambda: svc)
    assert await n.after_scan([oid]) == 0
    assert store.get_order(db, oid)["alert_state"] == "retry"
    state["fail"] = False
    assert await n.retry_failed() == 1 and store.get_order(db, oid)["alert_state"] == "sent"


async def test_bad_button_url_falls_back_to_plain_card(bot2, db, pipe, chat_id):
    svc, api = bot2
    (oid,) = await make_orders(pipe, chat_id, 1)
    db.run("UPDATE orders SET contact_url='https://t.me/user (в лс)' WHERE id=?", (oid,))
    row = store.get_order(db, oid)
    assert not any(b.get("text") == "Написать" for r in svc.card_keyboard(row)["inline_keyboard"] for b in r)
    real = svc.api.call
    seen = []

    async def reject_urls(method, **kw):
        seen.append(method)
        if method == "sendMessage" and "url" in str(kw.get("reply_markup")) and len(seen) == 1:
            raise BotError("BUTTON_URL_INVALID", 400)
        return await real(method, **kw)

    svc.api.call = reject_urls
    await svc.send_card(row)
    assert seen.count("sendMessage") == 2


async def test_stale_callback_does_not_cancel_the_action(bot2, db, pipe, chat_id):
    svc, api = bot2
    (oid,) = await make_orders(pipe, chat_id, 1)
    real = svc.api.call

    async def stale(method, **kw):
        if method == "answerCallbackQuery":
            raise BotError("query is too old", 400)
        return await real(method, **kw)

    svc.api.call = stale
    await svc.handle_update({"update_id": 2, "callback_query": {"id": "c", "data": f"fb:up:{oid}", "from": {"id": OWNER},
                                                                 "message": {"message_id": 5, "chat": {"id": OWNER}}}})
    assert store.get_order(db, oid)["feedback"] == "up"
