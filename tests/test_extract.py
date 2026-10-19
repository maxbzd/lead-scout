import pytest

from leadscout import extract as ex
from leadscout.taxonomy import profile_keys, skill_ids


@pytest.mark.parametrize(
    "text,lo,hi,cur,period",
    [
        ("Бюджет: 45 000 ₽, срок 7 дней", 45000, 45000, "RUB", "fixed"),
        ("Оплата 30к за проект", 30000, 30000, "RUB", "fixed"),
        ("Бюджет от 30 до 50 тыс. руб", 30000, 50000, "RUB", "fixed"),
        ("30-50к рублей за всё", 30000, 50000, "RUB", "fixed"),
        ("Готов платить $300", 300, 300, "USD", "fixed"),
        ("бюджет 80–120 тыс. ₽ по оценке", 80000, 120000, "RUB", "fixed"),
        ("Ставка 1500 руб/час", 1500, 1500, "RUB", "hour"),
        ("Оплата 120000 в месяц", 120000, 120000, "RUB", "month"),
        ("Цена 12 000р", 12000, 12000, "RUB", "fixed"),
        ("бюджет от 25 000 ₽", 25000, None, "RUB", "fixed"),
        ("Нужно за 5 дней, бюджет 18 000 ₽", 18000, 18000, "RUB", "fixed"),
    ],
)
def test_budget(text, lo, hi, cur, period):
    b = ex.parse_budget(text)
    assert b.min == lo
    assert (b.max if hi is not None else b.max) == (hi if hi is not None else None) or (hi is None and b.max is None)
    assert b.currency == cur
    assert b.period == period


@pytest.mark.parametrize(
    "text",
    [
        "Срок 7 дней, без бюджета",
        "У меня 3 года опыта, 2 проекта",
        "Звоните +7 912 345 67 89",
        "в 2024 году мы запустили",
        "Нужно 10 роликов за 4 дня",
    ],
)
def test_budget_ignores_non_money(text):
    assert not ex.parse_budget(text).known


@pytest.mark.parametrize(
    "text,days",
    [
        ("Срок: 7 дней", 7),
        ("сделать за 3 дня", 3),
        ("в течение 2 недель", 14),
        ("за неделю нужно", 7),
        ("Срочно!", 2),
        ("Нужно за пару дней", 3),
        ("У меня 3 года опыта", None),
        ("Срок 1 месяц", 30),
    ],
)
def test_deadline(text, days):
    assert ex.parse_deadline_days(text) == days


def test_contact_prefers_write_hint():
    c = ex.parse_contact("Бот для салона @lumiere_admin и @other_person. Писать: @lumiere_admin")
    assert c.type == "telegram" and c.value == "@lumiere_admin" and c.url == "https://t.me/lumiere_admin"


def test_contact_email_not_username():
    c = ex.parse_contact("Резюме на hr@lumenlabs.example пишите")
    assert c.type == "email" and c.value == "hr@lumenlabs.example"


def test_contact_falls_back_to_author():
    c = ex.parse_contact("Нужен бот. Подробности в личке", author_username="ivan_petrov")
    assert c.type == "author" and c.url == "https://t.me/ivan_petrov"


def test_contact_tme_link():
    c = ex.parse_contact("Писать сюда https://t.me/studio_orbita подробности")
    assert c.value == "@studio_orbita"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Нужен телеграм-бот для салона красоты. Бюджет 45 000 ₽, срок 7 дней, писать @salon_admin", True),
        ("Ищу заказы по разработке ботов. Мое портфолио: ссылка. Делаю быстро и недорого", False),
        ("Подписывайтесь на канал, скидка 50% по промокоду на курс стартует в понедельник", False),
        ("Нужен спец по n8n, бюджет 30к", False),  # слишком коротко
        ("Требуется разработчик для настройки автоворонки в n8n. Бюджет 30 000 ₽, нужно сделать за 3 дня", True),
        ("Всем привет! Как дела? Что нового в чате сегодня, расскажите пожалуйста подробнее", False),
    ],
)
def test_looks_like_order(text, expected):
    assert ex.looks_like_order(text) is expected


@pytest.mark.parametrize(
    "text,code",
    [
        ("Для старта внесите залог 5 000 ₽, вернём после первого заказа", "deposit"),
        ("Нужно пройти платное обучение, потом трудоустройство", "training"),
        ("Доход до 300 000 в месяц без опыта", "income"),
        ("Нужно сделать бота бесплатно, ради портфолио", "unpaid"),
        ("Нужен специалист для накрутки отзывов", "illegal"),
        ("Тестовое задание бесплатно, потом обсудим оплату", "trial"),
    ],
)
def test_scam_flags(text, code):
    assert code in {f.code for f in ex.scam_flags(text)}


def test_scam_flags_clean():
    assert ex.scam_flags("Нужен бот для записи клиентов. Бюджет 40 000 ₽, оплата 50/50") == []


def test_tg_scam_author_flag():
    flags = ex.scam_flags("Нужен бот", {"scam": True})
    assert flags and flags[0].level == "high"


def test_stop_words_prefix_match():
    assert ex.stop_word_hit("Работа с залогом", ["залог"]) == "залог"
    assert ex.stop_word_hit("Обычный заказ", ["залог"]) is None


def test_similarity_and_hash():
    a = "Нужен телеграм-бот для салона красоты. Бюджет 45 000 ₽, срок 7 дней, писать @salon_admin"
    b = "СРОЧНО! Нужен телеграм бот для салона красоты. Бюджет 45000 ₽ срок 7 дней писать @salon_admin"
    c = "Требуется разработчик для настройки автоворонки в n8n. Бюджет 30 000 ₽"
    assert ex.similarity(a, a) == 1.0
    assert ex.similarity(a, b) > 0.5
    assert ex.similarity(a, c) < 0.2
    assert ex.content_hash(a) == ex.content_hash(a.upper())


def test_title():
    t = ex.make_title("🔥 СРОЧНО! Нужен телеграм-бот для записи клиентов в салон красоты с оплатой\nПодробности...")
    assert t.startswith("Телеграм-бот для записи клиентов")
    assert len(t) <= 80


def test_taxonomy():
    assert {"telegram", "bot", "llm"} <= set(skill_ids("Нужен телеграм-бот с GPT"))
    assert "n8n" in skill_ids("автоворонка в n8n")
    assert "rag" in skill_ids("чат по базе знаний")
    assert "llm" not in skill_ids("сделать сайт на тильде")
    assert {"telegram", "bot"} <= profile_keys(["Telegram-боты"])
    assert "сварка" in profile_keys(["Сварка"])
