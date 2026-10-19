"""Режим «ничего не пропускать»: сомнительное показываем, отброшенное можно просмотреть и вернуть."""

import pytest

from leadscout import store
from leadscout.notify import Notifier
from leadscout.profile import Notifications, Profile, save_notifications, save_profile

from .conftest import H, make_raw
from .test_bot import bot  # noqa: F401  (фикстура подставного бота)

CHATTER = "Ребят, а кто-нибудь пробовал claude для кода? Как впечатления у тех, кто уже перешёл на него?"


async def test_short_order_is_not_lost(pipe, db, chat_id):
    oid = await pipe.process(make_raw(chat_id, "Нужен бот для магазина, 25к, до пятницы"))
    assert oid and store.get_order(db, oid)["verdict"] in {"match", "maybe"}


async def test_strict_mode_drops_it_but_recall_default_keeps_it(pipe, db, chat_id):
    save_profile(db, Profile(recall=False))
    assert await pipe.process(make_raw(chat_id, "Нужен бот для магазина, 25к, до пятницы")) is None
    save_profile(db, Profile(recall=True))
    assert await pipe.process(make_raw(chat_id, "Нужен бот для магазина, 25к, до пятницы", tg_msg_id=9001, author="other", author_id=77))


async def test_question_for_executor_is_an_order(pipe, db, chat_id):
    oid = await pipe.process(make_raw(chat_id, "Подскажите, кто делает чат-ботов с GPT под ключ? Нужно для интернет-магазина, @shop_owner_1"))
    assert oid


async def test_below_minimum_and_stop_word_stay_visible(pipe, db, chat_id):
    save_profile(db, Profile(min_budget=100000, stop_words=["магазин"]))
    oid = await pipe.process(make_raw(chat_id, "Нужен телеграм-бот с GPT для магазина. Бюджет 30 000 ₽, срок неделя. @owner_x"))
    row = store.get_order(db, oid)
    assert row["verdict"] == "maybe" and "на всякий случай" in row["why"]
    assert any(not f["ok"] for f in row["fit"])


async def test_scam_is_still_flagged(pipe, db, chat_id):
    oid = await pipe.process(make_raw(chat_id, "Нужен бот для казино, оплата 100000 руб, сначала внесите депозит за доступ к ТЗ"))
    assert store.get_order(db, oid)["verdict"] == "scam"


def test_discarded_list_and_promote(authed):
    rt = authed.app.state.rt
    cid = store.add_chat(rt.db, "Чат", username="chat_promote")

    async def run():
        return await rt.pipeline.process(make_raw(cid, CHATTER, tg_msg_id=4242, author="talker", author_id=9))

    import asyncio

    assert asyncio.run(run()) is None
    feed = authed.get("/api/orders", params={"filter": "discarded"}).json()
    assert feed["counts"]["discarded"] >= 1
    item = next(i for i in feed["items"] if "claude" in i["text"])
    assert item["kind"] == "message" and item["chat"] == "Чат"
    r = authed.post(f"/api/discarded/{item['id']}/promote", headers=H)
    assert r.status_code == 200
    card = r.json()["order"]
    assert card["verdict"] == "maybe" and "Не уверен" in " ".join(f["text"] for f in authed.get(f"/api/orders/{card['id']}").json()["flags"])
    assert authed.post(f"/api/discarded/{item['id']}/promote", headers=H).status_code == 409
    assert all("claude" not in i["text"] for i in authed.get("/api/orders", params={"filter": "discarded"}).json()["items"])
    assert any(o["id"] == card["id"] for o in authed.get("/api/orders").json()["items"])


async def test_thumbs_up_returns_rejected_order_to_feed(pipe, db, chat_id):
    oid = await pipe.process(make_raw(chat_id, "Нужен бот для казино, оплата 100000 руб, сначала внесите депозит за доступ к ТЗ"))
    assert store.get_order(db, oid)["verdict"] == "scam"
    store.set_feedback(db, oid, "up")
    o = store.get_order(db, oid)
    assert o["verdict"] == "maybe" and o["score"] >= 50


@pytest.mark.asyncio
async def test_digest_includes_maybes_and_mentions_rejected(bot, pipe, chat_id, db, settings):
    svc, api = bot
    save_notifications(db, Notifications(quiet=False, digest_time="00:00"))
    await pipe.process(make_raw(chat_id, "Нужен телеграм-бот с GPT для записи клиентов. Бюджет 45 000 ₽, срок 7 дней. @a_owner1", author_id=1))
    await pipe.process(make_raw(chat_id, "Нужен бот для казино, оплата 100000 руб, сначала внесите депозит за доступ к ТЗ", author="b", author_id=2))
    n = Notifier(db, settings, lambda: svc)
    assert await n.send_digest() >= 1
    text = " ".join(m.get("text", "") for m in api.sent())
    assert "Отсеяно" in text


def test_default_profile_is_in_recall_mode():
    assert Profile().recall is True
    assert Notifications().instant_threshold <= 50
