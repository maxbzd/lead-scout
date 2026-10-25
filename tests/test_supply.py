"""Объявления исполнителей (#помогу, «я делаю сайты») и правила чата не должны попадать в ленту заказов."""

import pytest

from leadscout import extract as ex

RULES = """Правила публикации в чате ✅

❗️ Объявления публикуются только через бота — @mari_pro_vakansii_bot
Писать в чат напрямую нельзя. Отправьте объявление боту — он проверит его и опубликует здесь за вас.

1️⃣ — оформляйте объявление строго по образцу
2️⃣ — первым должен идти тег #ищу или #помогу
3️⃣ — от 2 до 7 #хештегов в объявлении
4️⃣ — 1 сутки = 1 объявление (не более)
5️⃣ — бот публикует объявления с 10:00 до 20:00 мск
6️⃣ — чат только для вакансий и резюме, отзывы на специалистов пишите сюда
7️⃣ — редактировать отправленный текст нельзя — бот удалит объявление

🌿 Обязательно оставляйте контактные данные в объявлении, иначе люди до вас не смогут дописаться

❌ Не попадайтесь на мошенников — прочитайте гайд по безопасности"""

TECH = """#помогу #техническийспециалист #геткурс #salebot #чатбот

Привет, меня зовут Никита, я – технический специалист.

Навожу порядок в настройках, чтобы проект работал как система, а не как набор случайных решений и когда тех-часть живёт по принципу «ну вроде работает» — я делаю, чтобы точно работало.

Я работаю с экспертами, онлайн-школами и продюсерами: выстраиваю техническую систему запусков и автоворонок так, чтобы она работала стабильно и без нервов.

Что могу закрыть:

— Полная настройка платформы GetCourse
— Автоматизированные воронки в различных мессенджерах
— Боты под любой сценарий
— Интеграции оплат, CRM, систем уведомлений(e-mail, SMS, обзвоны)
— Проведение вебинаров под ключ в связках Bizon365, OBS, Youtube, Zoom, GetCourse
— Аудит технической части проекта и устранение узких мест

Работаю быстро, без «ожидания неделями», думаю о результате и предлагаю решения, а не жду идеальное ТЗ.

Берусь как за проектную работу, так и за разовые задачи.

Если хотите, чтобы техничка в вашем проекте перестала быть проблемой — напишите мне в лс @Marakulin_technic

👤 Автор объявления: @marakulin_technic

🔻 Прочтите правила безопасности перед откликом. Админ чата не несёт ответственности за содержание объявлений."""

SITES = """#помогу #сайт #лендинг #сайтподключ #инфобизнес

🌟Привет, я Софья, делаю сайты любой сложности с личной админкой

🔥Главное отличие: сначала собираю тестовую версию вашего сайта и показываю её до оплаты. Платите, только если нравится, потом вносим любые правки до запуска без доплат.

✨️Что получаете:
○ Уникальный дизайн для вас, не шаблон
○ Админка: цены, тексты, фото и отзывы можете потом легко редактировать сами, если понадобится, без моей помощи
○ Заявки с сайта приходят в админку (Можем настроить уведомления в телеграм без доплаты)

⚡️Уникальный одностраничный сайт (с личной админкой) — от 25000₽, срок выполнения до 2 дней.
Подробнее в полном прайсе👇🏻

Полный прайс: https://creatorweb.ru/prices
Мои работы: https://creatorweb.ru/cases

Связаться: @sofia_chernenko

👤 Автор объявления: @c_sofia

🔻 Прочтите правила безопасности перед откликом. Админ чата не несёт ответственности за содержание объявлений."""

# настоящие заказы из такого же чата (с тем же хвостом про правила безопасности)
ORDERS = [
    """#ищу #чатбот #telegram

Нужен специалист, который сделает Telegram-бота для записи клиентов в барбершоп: меню услуг, выбор мастера, напоминания. Бюджет 40 000 ₽, срок до 10 дней.

Контакты: @barbershop_owner

👤 Автор объявления: @barbershop_owner

🔻 Прочтите правила безопасности перед откликом. Админ чата не несёт ответственности за содержание объявлений.""",
    "Ищу разработчика на проект: нужно настроить интеграцию n8n с amoCRM, бюджет 30000, срок 3 дня. Пишите в лс @ivan_k",
    "Привет, меня зовут Анна, у меня интернет-магазин. Нужен бот с ИИ для ответов клиентам в Telegram, бюджет 50 000 ₽. @anna_shop",
]


@pytest.mark.parametrize("text", [TECH, SITES])
def test_service_offers_are_not_orders_even_when_long_and_full_of_project_words(text):
    assert ex.is_supply(text)
    assert not ex.looks_like_order(text)
    assert not ex.maybe_order(text)


def test_chat_rules_are_not_an_order():
    assert ex.is_chat_rules(RULES)
    assert not ex.looks_like_order(RULES)
    assert not ex.maybe_order(RULES)


@pytest.mark.parametrize("text", ORDERS)
def test_real_orders_survive(text):
    assert not ex.is_supply(text)
    assert not ex.is_chat_rules(text)
    assert ex.looks_like_order(text)
    assert ex.maybe_order(text)


def test_pipeline_sends_the_three_messages_to_discarded_and_keeps_orders(pipe, chat_id, db):
    import asyncio

    from .conftest import make_raw

    async def go():
        out = []
        for i, t in enumerate([RULES, TECH, SITES, *ORDERS]):
            out.append(await pipe.process(make_raw(chat_id, t, author=f"u{i}", author_id=100 + i)))
        return out

    ids = asyncio.run(go())
    assert ids[:3] == [None, None, None]
    assert all(i for i in ids[3:])
    assert db.scalar("SELECT COUNT(*) FROM messages WHERE status='noise'") == 3


def test_old_offers_already_in_the_feed_are_cleaned_up_but_touched_ones_stay(pipe, chat_id, db):
    import asyncio

    from leadscout import store

    from .conftest import make_raw

    async def go():
        a = await pipe.process(make_raw(chat_id, ORDERS[1], author="a1", author_id=1))
        b = await pipe.process(make_raw(chat_id, ORDERS[2], author="a2", author_id=2))
        return a, b

    good, other = asyncio.run(go())
    # имитируем старую версию: объявление исполнителя когда-то прошло как заказ
    for i, text in enumerate([TECH, SITES]):
        mid, _ = store.insert_message(db, make_raw(chat_id, text, author=f"s{i}", author_id=50 + i))
        db.run("INSERT INTO orders(message_id,title,verdict,score,posted_at,created_at,state) SELECT ?, 'старое', 'maybe', 50, posted_at, posted_at, 'new' FROM messages WHERE id=?", (mid, mid))
    saved_mid = db.scalar("SELECT message_id FROM orders WHERE title='старое' ORDER BY id LIMIT 1")
    db.run("UPDATE orders SET state='saved' WHERE message_id=?", (saved_mid,))
    assert pipe.purge_non_orders() == 1
    assert db.scalar("SELECT COUNT(*) FROM orders WHERE title='старое'") == 1  # сохранённое вами осталось
    assert db.scalar("SELECT COUNT(*) FROM orders WHERE id IN (?,?)", (good, other)) == 2
