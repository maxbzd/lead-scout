"""Регрессии по независимому аудиту: потеря сообщений, курсоры, оценки, выход из приложения."""

import asyncio
from datetime import timedelta

import pytest

from leadscout import learning, store
from leadscout.config import Settings
from leadscout.runtime import Runtime
from leadscout.util import iso, now_utc

from .conftest import H, make_raw

ORDER = "Нужен телеграм-бот с GPT для записи клиентов в салон. Бюджет 45 000 ₽, срок 7 дней. Писать @salon_admin"


@pytest.fixture()
def rt(tmp_path):
    return Runtime(Settings(data_dir=tmp_path, password="pw", demo=False, run_worker=False))


async def test_failed_message_is_retried_and_cursor_not_moved(rt, monkeypatch):
    cid = store.add_chat(rt.db, "Чат", username="chat_a", tg_id=-1001)
    raw = make_raw(cid, ORDER, tg_msg_id=500)
    real_save = rt.pipeline._save

    def boom(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(rt.pipeline, "_save", boom)
    assert await rt._ingest([raw]) == []
    assert rt.db.one("SELECT last_msg_id FROM chats WHERE id=?", (cid,))["last_msg_id"] == 0
    assert rt.db.one("SELECT status FROM messages WHERE tg_msg_id=500")["status"] == "new"

    monkeypatch.setattr(rt.pipeline, "_save", real_save)
    rt.db.run("UPDATE messages SET fetched_at=? WHERE tg_msg_id=500", (iso(now_utc() - timedelta(minutes=10)),))
    assert len(await rt.pipeline.retry_stuck()) == 1
    assert rt.db.scalar("SELECT COUNT(*) FROM orders") == 1


async def test_same_message_processed_once_concurrently(rt):
    cid = store.add_chat(rt.db, "Чат", username="chat_b", tg_id=-1002)
    raw = make_raw(cid, ORDER, tg_msg_id=7)
    await asyncio.gather(rt.pipeline.process(raw), rt.pipeline.process(raw))
    assert rt.db.scalar("SELECT COUNT(*) FROM orders") == 1


async def test_live_message_does_not_move_cursor(rt):
    cid = store.add_chat(rt.db, "Чат", username="chat_c", tg_id=-1003)
    rt.db.run("UPDATE chats SET last_msg_id=100 WHERE id=?", (cid,))
    await rt.ingest_one(make_raw(cid, ORDER, tg_msg_id=190))
    assert rt.db.one("SELECT last_msg_id FROM chats WHERE id=?", (cid,))["last_msg_id"] == 100


def test_feedback_has_single_event_per_order(db, pipe):
    cid = store.add_chat(db, "Чат", username="chat_d")

    async def go():
        return await pipe.process(make_raw(cid, ORDER))

    oid = asyncio.run(go())
    store.set_feedback(db, oid, "up")
    store.set_feedback(db, oid, "up")
    store.set_feedback(db, oid, "down", "budget")
    assert db.scalar("SELECT COUNT(*) FROM feedback_events WHERE order_id=?", (oid,)) == 1
    store.set_feedback(db, oid, None)
    assert db.scalar("SELECT COUNT(*) FROM feedback_events WHERE order_id=?", (oid,)) == 0
    assert learning.learned(db).examples == []


def test_readding_disabled_chat_enables_it(db):
    cid = store.add_chat(db, "Чат", username="chat_e", enabled=False)
    assert store.add_chat(db, "Чат", username="chat_e") == cid
    assert db.one("SELECT enabled FROM chats WHERE id=?", (cid,))["enabled"] == 1


def test_logout_revokes_cookie(client):
    assert client.post("/api/login", json={"password": "secret-pw"}, headers=H).status_code == 200
    cookie = client.cookies.get("ls_session")
    assert client.get("/api/session").json() == {"authed": True}
    client.post("/api/logout", headers=H)
    client.cookies.set("ls_session", cookie)  # украденная cookie
    assert client.get("/api/session").json() == {"authed": False}


def test_rate_limit_ignores_spoofed_forwarded_for(client):
    codes = [client.post("/api/login", json={"password": "bad"}, headers={**H, "X-Forwarded-For": f"1.2.3.{i}"}).status_code
             for i in range(10)]
    assert codes[-1] == 429


def test_search_treats_percent_literally(authed):
    r = authed.get("/api/orders", params={"q": "%", "filter": "all"})
    assert r.status_code == 200 and r.json()["items"] == []


async def test_bids_refresh_price_hint(pipe, db, chat_id):
    oid = await pipe.process(make_raw(chat_id, ORDER, tg_msg_id=1))
    before = db.one("SELECT rec_note, rec_max FROM orders WHERE id=?", (oid,))
    assert "Откликов пока нет" in before["rec_note"]
    for i in range(7):
        await pipe.process(make_raw(chat_id, f"Беру, пишите в лс {i}", tg_msg_id=10 + i, author=f"w{i}", author_id=1000 + i, reply_to=1))
    after = db.one("SELECT rec_note, rec_max FROM orders WHERE id=?", (oid,))
    assert "Откликов уже 7" in after["rec_note"] and after["rec_max"] < before["rec_max"]


async def test_raise_min_suggestion_really_filters_those_orders(pipe, db, chat_id):
    from leadscout.profile import load_profile

    texts = [
        "Нужен телеграм-бот для записи в барбершоп с оплатой через ЮKassa. Бюджет 30 000 ₽, срок неделя. @shop_a1",
        "Требуется промпт-инженер: настроить GPT-ассистента для интернет-магазина одежды. 30 000 руб, срок 5 дней. @shop_b2",
        "Ищу специалиста по n8n, нужно связать amoCRM с таблицами и уведомлениями. Оплата 30 000 ₽, срок 4 дня. @shop_c3",
    ]
    for i, t in enumerate(texts):
        oid = await pipe.process(make_raw(chat_id, t, author=f"shop{i}", author_id=500 + i))
        assert oid
        store.set_feedback(db, oid, "down", "budget")
    learning.refresh_suggestions(db, load_profile(db))
    sug = db.all("SELECT payload FROM insights WHERE kind='raise_min'")
    assert sug, "должно появиться предложение поднять минимум"
    assert sug[0]["payload"]["min_budget"] > 30000  # строго выше: иначе такие заказы порог не отсечёт
