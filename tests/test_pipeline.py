import pytest

from leadscout import learning, store
from leadscout.profile import Profile, save_profile

from .conftest import make_raw

BOT = "Нужен телеграм-бот с GPT для записи клиентов в салон красоты. Бюджет 45 000 ₽, срок 7 дней. Писать @salon_admin"
N8N = "Требуется разработчик для настройки автоворонки в n8n: заявки из Telegram в amoCRM. Бюджет 30 000 ₽, нужно за 3 дня"
CHEAP = "Нужен парсер Telegram-каналов в таблицу, срок 2 дня, бюджет 6 000 ₽. Пишите в личку"
SCAM = "Ищем исполнителей для написания текстов! Доход до 300 000 в месяц без опыта. Для старта внесите залог 5 000 ₽"
SEEKER = "Ищу заказы по разработке телеграм-ботов и автоматизации. Делаю на заказ быстро, моё портфолио в профиле"
CHAT = "Всем привет! Кто-нибудь пробовал новую версию Python? Расскажите, как впечатления, интересно мнение"


@pytest.mark.asyncio
async def test_good_order_is_scored_and_saved(pipe, db, chat_id):
    oid = await pipe.process(make_raw(chat_id, BOT))
    assert oid
    o = store.get_order(db, oid)
    assert o["verdict"] == "match" and o["score"] >= 70
    assert o["budget_rub_max"] == 45000 and o["deadline_days"] == 7
    assert o["contact_value"] == "@salon_admin"
    assert o["rec_min"] and o["rec_max"] and o["rec_min"] < 45000 < o["rec_max"] + 5000
    assert store.get_draft(db, oid, "warm", 0)["text"]


@pytest.mark.asyncio
async def test_noise_is_ignored(pipe, db, chat_id):
    for t in (SEEKER, CHAT):
        assert await pipe.process(make_raw(chat_id, t)) is None
    assert db.scalar("SELECT COUNT(*) FROM orders") == 0


@pytest.mark.asyncio
async def test_scam_detected(pipe, db, chat_id):
    oid = await pipe.process(make_raw(chat_id, SCAM))
    o = store.get_order(db, oid)
    assert o["verdict"] == "scam" and o["score"] <= 35
    assert any(f["level"] == "high" for f in o["red_flags"])


@pytest.mark.asyncio
async def test_below_minimum_is_skipped(pipe, db, chat_id):
    save_profile(db, Profile(recall=False))  # строгий режим
    oid = await pipe.process(make_raw(chat_id, CHEAP))
    o = store.get_order(db, oid)
    assert o["verdict"] == "skip"
    assert "минимума" in o["why"]


@pytest.mark.asyncio
async def test_stop_word(pipe, db, chat_id):
    text = BOT + " Работа бесплатно, ради портфолио"
    save_profile(db, Profile(stop_words=["бесплатно"], recall=False))
    oid = await pipe.process(make_raw(chat_id, text))
    assert store.get_order(db, oid)["verdict"] in {"skip", "scam"}


@pytest.mark.asyncio
async def test_duplicates_across_chats_merge(pipe, db, chat_id):
    other = store.add_chat(db, "Фриланс · Боты", username="frl_bots")
    a = await pipe.process(make_raw(chat_id, BOT))
    b = await pipe.process(make_raw(other, "СРОЧНО! " + BOT.replace("45 000", "45000")))
    assert a and b is None
    o = store.get_order(db, a)
    assert o["dup_count"] == 1
    assert len(store.order_sources(db, a)) == 2


@pytest.mark.asyncio
async def test_same_message_twice_is_ignored(pipe, db, chat_id):
    raw = make_raw(chat_id, BOT, tg_msg_id=999)
    assert await pipe.process(raw)
    assert await pipe.process(raw) is None


@pytest.mark.asyncio
async def test_replies_count_as_bids(pipe, db, chat_id):
    raw = make_raw(chat_id, BOT, tg_msg_id=500, author_id=1)
    oid = await pipe.process(raw)
    await pipe.process(make_raw(chat_id, "Могу сделать, пишите мне в личку", author_id=2, reply_to=500))
    await pipe.process(make_raw(chat_id, "Беру, опыт 3 года, примеры есть", author_id=3, reply_to=500))
    await pipe.process(make_raw(chat_id, "Уточню: оплата по этапам", author_id=1, reply_to=500))  # автор заказа
    assert store.get_order(db, oid)["bids"] == 2


@pytest.mark.asyncio
async def test_feedback_changes_learning(pipe, db, chat_id):
    oid = await pipe.process(make_raw(chat_id, BOT))
    store.set_feedback(db, oid, "down", "budget")
    learned = learning.learned(db)
    assert learned.examples and not learned.examples[0].liked
    assert "telegram" not in learned.skill_weights, "«низкий бюджет» не значит «навык не мой»"
    store.set_feedback(db, oid, "down", "profile")
    assert learning.learned(db).skill_weights["telegram"] < 0
    store.set_feedback(db, oid, None)
    assert learning.learned(db).examples == []


@pytest.mark.asyncio
async def test_rescore_after_profile_change(pipe, db, chat_id):
    oid = await pipe.process(make_raw(chat_id, N8N))
    assert store.get_order(db, oid)["verdict"] in {"match", "maybe"}
    save_profile(db, Profile(min_budget=50000, recall=False))
    await pipe.rescore_recent()
    assert store.get_order(db, oid)["verdict"] == "skip"
    save_profile(db, Profile(min_budget=50000, recall=True))
    await pipe.rescore_recent()
    o = store.get_order(db, oid)
    assert o["verdict"] == "maybe" and "на всякий случай" in o["why"]


@pytest.mark.asyncio
async def test_draft_tones_and_variants_differ(pipe, db, chat_id):
    oid = await pipe.process(make_raw(chat_id, BOT))
    texts = set()
    for tone in ("short", "warm", "formal"):
        for v in (0, 1):
            d = await pipe.make_draft(oid, tone, v)
            texts.add(d["text"])
            assert len(d["text"]) > 40
    assert len(texts) == 6
    assert len((await pipe.make_draft(oid, "short", 0))["text"]) < 260
