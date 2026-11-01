"""Удаление заказов: по одному, выбранных, всё во вкладке; отброшенные; «не заказ» учит ИИ."""

import asyncio

import pytest

from leadscout import classify, store
from leadscout.cloud import allowed

from .conftest import H


@pytest.fixture()
def app(authed):
    authed.post("/api/scan", headers=H)
    return authed


def ids_of(c, flt="all"):
    return [o["id"] for o in c.get(f"/api/orders?filter={flt}").json()["items"]]


def test_selected_orders_are_deleted_and_do_not_come_back(app):
    ids = ids_of(app)
    assert len(ids) >= 4
    r = app.post("/api/orders/delete", json={"ids": ids[:3]}, headers=H)
    assert r.json() == {"deleted": 3}
    left = ids_of(app)
    assert not set(ids[:3]) & set(left) and len(left) == len(ids) - 3
    rt = app.app.state.rt
    assert rt.db.scalar("SELECT COUNT(*) FROM messages WHERE status='deleted'") == 3
    # то же сообщение при повторной проверке заказом не становится
    row = rt.db.one("SELECT * FROM messages WHERE status='deleted' LIMIT 1")
    raw = store.raw_from_row(row)
    assert asyncio.run(rt.pipeline.process(raw)) is None
    assert app.get("/api/orders?filter=discarded").json()["items"] is not None
    assert row["id"] not in [m["id"] for m in app.get("/api/orders?filter=discarded").json()["items"]]


def test_unknown_and_repeated_ids_are_harmless(app):
    ids = ids_of(app)
    assert app.post("/api/orders/delete", json={"ids": [999999]}, headers=H).json() == {"deleted": 0}
    assert app.post("/api/orders/delete", json={"ids": [ids[0], ids[0]]}, headers=H).json()["deleted"] == 1
    assert app.post("/api/orders/delete", json={"ids": [ids[0]]}, headers=H).json() == {"deleted": 0}


def test_delete_all_in_tab_keeps_saved_and_offered(app):
    ids = ids_of(app)
    app.post(f"/api/orders/{ids[0]}/save", json={"saved": True}, headers=H)
    app.post(f"/api/orders/{ids[1]}/offer", json={"done": True}, headers=H)
    r = app.post("/api/orders/delete", json={"all": True, "filter": "all"}, headers=H)
    assert r.json()["deleted"] == len(ids) - 2
    assert sorted(ids_of(app)) == sorted(ids[:2])


def test_delete_everything_in_the_saved_tab_only_when_asked_there(app):
    ids = ids_of(app)
    app.post(f"/api/orders/{ids[0]}/save", json={"saved": True}, headers=H)
    assert app.post("/api/orders/delete", json={"all": True, "filter": "saved"}, headers=H).json()["deleted"] == 1
    assert ids[0] not in ids_of(app)


def test_delete_all_respects_the_search(app):
    all_ids = ids_of(app)
    n = len(app.get("/api/orders?filter=all&q=telegram").json()["items"])
    assert 0 < n < len(all_ids)
    r = app.post("/api/orders/delete", json={"all": True, "filter": "all", "q": "telegram"}, headers=H)
    assert r.json()["deleted"] == n
    assert len(ids_of(app)) == len(all_ids) - n


def test_not_an_order_deletion_teaches_the_ai(app):
    ids = ids_of(app)
    app.post("/api/orders/delete", json={"ids": ids[:2], "notorder": True}, headers=H)
    _, no = classify.examples(app.app.state.rt.db)
    assert len(no) == 2
    app.post("/api/orders/delete", json={"ids": ids[2:3]}, headers=H)
    assert len(classify.examples(app.app.state.rt.db)[1]) == 2  # обычное удаление ИИ ничему не учит


def test_discarded_messages_can_be_deleted_one_by_one_or_all(app):
    items = app.get("/api/orders?filter=discarded").json()["items"]
    assert len(items) >= 3
    assert app.post("/api/discarded/delete", json={"ids": [items[0]["id"]]}, headers=H).json() == {"deleted": 1}
    left = app.get("/api/orders?filter=discarded").json()["items"]
    assert len(left) == len(items) - 1
    assert app.post("/api/discarded/delete", json={"all": True}, headers=H).json()["deleted"] == len(left)
    assert app.get("/api/orders?filter=discarded").json()["items"] == []
    assert app.post("/api/discarded/delete", json={"ids": [items[0]["id"]]}, headers=H).json() == {"deleted": 0}


def test_deletion_needs_a_login_and_the_csrf_header(client):
    assert client.post("/api/orders/delete", json={"ids": [1]}, headers=H).status_code == 401
    client.post("/api/login", json={"password": "secret-pw"}, headers=H)
    assert client.post("/api/orders/delete", json={"ids": [1]}).status_code == 403


def test_the_site_may_delete_through_the_queue():
    assert allowed("POST", "/orders/delete") and allowed("POST", "/discarded/delete")
    assert not allowed("DELETE", "/orders/delete") and not allowed("POST", "/orders/delete/all")
