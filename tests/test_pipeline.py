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
    save_profile(db, Profile(recall=False, ignore_budget=False))  # строгий режим с порогом цены
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
    save_profile(db, Profile(min_budget=50000, recall=False, ignore_budget=False))
    await pipe.rescore_recent()
    assert store.get_order(db, oid)["verdict"] == "skip"
    save_profile(db, Profile(min_budget=50000, recall=True, ignore_budget=False))
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


async def test_by_default_the_price_does_not_matter(db, pipe, chat_id):
    """Цена не важна: заказ за 3 000 ₽ и заказ без цены показываются и оцениваются так же, как дорогой."""
    save_profile(db, Profile(recall=False))
    assert Profile().ignore_budget is True
    cheap = await pipe.process(make_raw(chat_id, "Нужен телеграм-бот с GPT для записи клиентов, оплата 3 000 ₽, срок 3 дня. @c_cheap1", author="c1", author_id=1))
    free = await pipe.process(make_raw(chat_id, "Требуется специалист по n8n и amoCRM: связать заявки с таблицей, цену обсудим в личке. @c_free22", author="c2", author_id=2))
    rich = await pipe.process(make_raw(chat_id, "Нужен телеграм-бот с GPT для записи клиентов, оплата 90 000 ₽, срок 3 дня. @c_rich33", author="c3", author_id=3))
    rows = {k: store.get_order(db, v) for k, v in {"cheap": cheap, "free": free, "rich": rich}.items()}
    assert all(r["verdict"] in {"match", "maybe"} for r in rows.values()), {k: r["verdict"] for k, r in rows.items()}
    assert abs(rows["cheap"]["score"] - rows["rich"]["score"]) <= 4, "цена не двигает оценку"
    assert "ниже" not in (rows["cheap"]["why"] or "")


async def test_any_site_request_is_shown_but_an_explicit_constructor_is_not(db, pipe, chat_id):
    save_profile(db, Profile(recall=False))
    plain = await pipe.process(make_raw(chat_id, "Нужен сайт-визитка для стоматологии: 5 страниц, форма заявки, адаптив. Срок две недели. @dent_owner", author="s1", author_id=1))
    shop = await pipe.process(make_raw(chat_id, "Требуется разработчик интернет-магазина, нужно сделать каталог и корзину. Пишите @shop_owner9", author="s2", author_id=2))
    tilda = await pipe.process(make_raw(chat_id, "Нужен лендинг на Тильде для онлайн-курса, макет есть, срок 3 дня. @tilda_owner", author="s3", author_id=3))
    wix = await pipe.process(make_raw(chat_id, "Нужен сайт на Wix для студии йоги, нужно собрать 4 страницы. @yoga_owner", author="s4", author_id=4))
    verdict = {k: store.get_order(db, v)["verdict"] for k, v in {"plain": plain, "shop": shop, "tilda": tilda, "wix": wix}.items()}
    assert verdict["plain"] in {"match", "maybe"} and verdict["shop"] in {"match", "maybe"}, verdict
    assert verdict["tilda"] == "skip" and verdict["wix"] == "skip", verdict


def test_untouched_old_defaults_are_upgraded_but_your_edits_are_kept(db):
    from leadscout.profile import DEFAULT_CRITERIA, DEFAULT_SKILLS, LEGACY_DEFAULTS, load_profile

    db.set_setting("profile", {"skills": LEGACY_DEFAULTS["skills"][1], "stop_words": LEGACY_DEFAULTS["stop_words"][0], "criteria": LEGACY_DEFAULTS["criteria"][0]})
    p = load_profile(db)
    assert p.skills == DEFAULT_SKILLS and "тильд" in p.stop_words and p.criteria == DEFAULT_CRITERIA
    db.set_setting("profile", {"skills": ["Свой навык"], "stop_words": ["моё"], "criteria": "Мои слова"})
    p = load_profile(db)
    assert p.skills == ["Свой навык"] and p.stop_words == ["моё"] and p.criteria == "Мои слова"
