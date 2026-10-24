"""Связь с облачным сайтом: отправка заказов, выполнение действий с сайта, защита от лишнего. Облако подменено."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from leadscout import store
from leadscout.cloud import ALLOWED, CloudError, allowed, normalize_url
from leadscout.config import Settings
from leadscout.web.app import create_app

from .conftest import H, make_raw

TOKEN = "t" * 32


class FakeCloud:
    """Минимальный двойник облака: хранит присланное и отдаёт очередь команд."""

    def __init__(self):
        self.orders: dict[int, dict] = {}
        self.discarded: dict[int, dict] = {}
        self.kv: dict[str, dict] = {}
        self.commands: list[dict] = []
        self.acks: list[dict] = []
        self.pushes = 0
        self.fail = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(self.fail, json={})
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={})
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/sync/ping":
            return httpx.Response(200, json={"ok": True})
        if path == "/sync/commands":
            return httpx.Response(200, json={"items": [c for c in self.commands if c["id"] not in self.done_ids()]})
        if path == "/sync/ack":
            self.acks.append(body)
            return httpx.Response(200, json={"ok": True})
        if path == "/sync/push":
            self.pushes += 1
            for o in body.get("orders", []):
                self.orders[o["id"]] = o
            for i in body.get("remove", []):
                self.orders.pop(i, None)
            for d in body.get("discarded", []):
                self.discarded[d["id"]] = d
            for i in body.get("discarded_remove", []):
                self.discarded.pop(i, None)
            self.kv.update(body.get("kv", {}))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={})

    def done_ids(self) -> set[int]:
        out: set[int] = set()
        for a in self.acks:
            out |= set(a.get("done", [])) | {int(k) for k in a.get("errors", {})}
        return out


@pytest.fixture()
def cloud():
    return FakeCloud()


@pytest.fixture()
def local(tmp_path, cloud):
    s = Settings(data_dir=tmp_path, local=True, demo=True, run_worker=False, host="127.0.0.1")
    with TestClient(create_app(s), base_url="http://localhost:8000") as c:
        c.app.state.rt.cloud.transport = httpx.MockTransport(cloud.handler)
        yield c


def connect(local):
    r = local.post("/api/connect/cloud", json={"url": "https://scout.example.workers.dev", "token": TOKEN}, headers=H)
    assert r.status_code == 200, r.text
    return local.app.state.rt


def cycle(local, force=False):
    local.portal.call(local.app.state.rt.cloud.cycle, force)


def test_url_must_be_https_or_localhost():
    assert normalize_url("https://a.workers.dev/") == "https://a.workers.dev"
    assert normalize_url("http://localhost:8788") == "http://localhost:8788"
    for bad in ("", "http://evil.example.com", "ftp://x.y", "scout.workers.dev"):
        with pytest.raises(CloudError):
            normalize_url(bad)


def test_only_listed_actions_may_come_from_the_site():
    assert allowed("POST", "/orders/5/feedback") and allowed("PUT", "/profile") and allowed("POST", "/scan")
    for method, path in [("POST", "/connect/telegram/code"), ("DELETE", "/connect/bot"), ("POST", "/connect/cloud"),
                         ("POST", "/orders/5/draft"), ("GET", "/orders"), ("POST", "/orders/5/../x"), ("PUT", "/profile/x"),
                         ("DELETE", "/chats/1"), ("POST", "/orders/abc/save")]:
        assert not allowed(method, path), (method, path)
    assert len(ALLOWED) < 10


def test_connect_checks_token_and_stores_it_encrypted(local, cloud):
    r = local.post("/api/connect/cloud", json={"url": "https://scout.example.workers.dev", "token": "x" * 32}, headers=H)
    assert r.status_code == 400 and "Ключ" in r.json()["detail"]
    assert not local.get("/api/status").json()["cloud"]["connected"]
    rt = connect(local)
    st = local.get("/api/status").json()["cloud"]
    assert st["connected"] and st["url"] == "https://scout.example.workers.dev"
    raw = rt.db.scalar("SELECT value FROM settings WHERE key='secret:cloud_token'")
    assert raw and TOKEN not in raw
    assert local.delete("/api/connect/cloud", headers=H).json()["cloud"]["connected"] is False
    assert rt.secrets.get("cloud_token") is None


def test_connect_rejects_unreachable_and_bad_url(local, cloud):
    cloud.fail = 500
    r = local.post("/api/connect/cloud", json={"url": "https://scout.example.workers.dev", "token": TOKEN}, headers=H)
    assert r.status_code == 400 and "500" in r.json()["detail"]
    r = local.post("/api/connect/cloud", json={"url": "http://evil.example.com", "token": TOKEN}, headers=H)
    assert r.status_code == 400


def test_nothing_is_sent_until_connected(local, cloud):
    local.post("/api/scan", headers=H)
    cycle(local)
    assert cloud.pushes == 0


def test_orders_cards_drafts_and_status_reach_the_cloud(local, cloud):
    rt = connect(local)
    local.post("/api/scan", headers=H)
    cycle(local)
    assert rt.cloud.last_error is None and rt.cloud.last_ok
    mine = local.get("/api/orders").json()
    assert len(cloud.orders) == mine["counts"]["all"] + mine["counts"]["rejected"] > 0  # отсеянное как мошенничество тоже видно на сайте
    sample = next(iter(cloud.orders.values()))
    assert sample["card"]["title"] and sample["detail"]["text"] and sample["search"]
    drafts = sample["detail"]["cloud_drafts"]
    if sample["verdict"] != "scam":
        assert set(drafts) == {"short", "warm", "formal"} and len(drafts["warm"]["texts"]) == 2
    assert cloud.discarded, "отброшенное тоже уходит в облако"
    assert {"status", "stats", "profile", "notif", "chats", "feed_meta"} <= set(cloud.kv)
    assert "cloud" not in cloud.kv["status"]


def test_unchanged_orders_are_not_sent_twice_but_changed_ones_are(local, cloud):
    rt = connect(local)
    local.post("/api/scan", headers=H)
    cycle(local)
    first = cloud.pushes
    cycle(local)
    cycle(local, force=True)
    assert cloud.pushes == first, "без изменений ничего не отправляется"
    oid = next(iter(cloud.orders))
    local.post(f"/api/orders/{oid}/save", json={"saved": True}, headers=H)
    cycle(local)
    assert cloud.pushes > first
    assert cloud.orders[oid]["state"] == "saved"
    assert rt.cloud.last_error is None


def test_site_actions_are_applied_on_the_computer(local, cloud):
    rt = connect(local)
    local.post("/api/scan", headers=H)
    cycle(local)
    ids = sorted(cloud.orders)
    a, b = ids[0], ids[1]
    cloud.commands = [
        {"id": 1, "method": "POST", "path": f"/orders/{a}/save", "body": {"saved": True}},
        {"id": 2, "method": "POST", "path": f"/orders/{b}/feedback", "body": {"kind": "up"}},
        {"id": 3, "method": "PUT", "path": "/notifications", "body": {**local.get("/api/notifications").json(), "digest": False}},
    ]
    cycle(local)
    assert rt.db.one("SELECT state FROM orders WHERE id=?", (a,))["state"] == "saved"
    assert rt.db.one("SELECT feedback FROM orders WHERE id=?", (b,))["feedback"] == "up"
    assert local.get("/api/notifications").json()["digest"] is False
    ack = cloud.acks[-1]
    assert sorted(ack["done"]) == [1, 2, 3] and not ack["errors"]
    assert cloud.orders[a]["state"] == "saved", "итог с компьютера перезаписывает предположение сайта"
    n = rt.cloud.applied
    cycle(local)
    assert rt.cloud.applied == n, "выполненное не повторяется"


def test_forbidden_and_broken_commands_are_reported_not_executed(local, cloud):
    rt = connect(local)
    local.post("/api/scan", headers=H)
    cycle(local)
    oid = next(iter(cloud.orders))
    cloud.commands = [
        {"id": 10, "method": "DELETE", "path": "/connect/telegram", "body": {}},
        {"id": 11, "method": "POST", "path": "/connect/cloud", "body": {"url": "https://evil.example.com", "token": "e" * 32}},
        {"id": 12, "method": "POST", "path": f"/orders/{oid}/feedback", "body": {"kind": "bogus"}},
        {"id": 13, "method": "POST", "path": f"/orders/{oid}/save", "body": {"saved": True}},
    ]
    cycle(local)
    ack = cloud.acks[-1]
    assert ack["done"] == [13]
    assert set(ack["errors"]) == {"10", "11", "12"}
    assert rt.cloud.url == "https://scout.example.workers.dev", "адрес облака сайт изменить не может"


def test_cloud_down_is_survivable(local, cloud):
    rt = connect(local)
    local.post("/api/scan", headers=H)
    cloud.fail = 503
    cycle(local)
    assert rt.cloud.last_error and "503" in rt.cloud.last_error
    assert local.get("/api/orders").status_code == 200
    cloud.fail = None
    cycle(local)
    assert rt.cloud.last_error is None and cloud.orders


def test_orders_that_disappear_are_removed_from_the_cloud(local, cloud):
    rt = connect(local)
    local.post("/api/scan", headers=H)
    cycle(local)
    gone = next(iter(cloud.orders))
    rt.db.run("DELETE FROM orders WHERE id=?", (gone,))
    cycle(local)
    assert gone not in cloud.orders


def test_sync_requests_do_not_keep_the_window_alive(local, cloud):
    rt = connect(local)
    before = local.app.state.last_request
    local.post("/api/scan", headers=H)
    mid = local.app.state.last_request
    assert mid >= before
    cycle(local)
    assert local.app.state.last_request == mid, "служебные запросы не считаются открытым окном"
    assert rt.cloud.last_ok


def test_scan_command_from_the_site_runs_a_scan(local, cloud):
    rt = connect(local)
    cycle(local)
    cloud.commands = [{"id": 5, "method": "POST", "path": "/scan", "body": {}}]
    cycle(local)
    assert cloud.acks[-1]["done"] == [5]
    assert rt.last_scan is not None


def test_message_pipeline_still_fine_with_cloud_attached(local, cloud, chat_id):
    rt = connect(local)
    raw = make_raw(store.add_chat(rt.db, "X", username="x", members=10), "Нужен Telegram-бот с ИИ, бюджет 50000 руб, срок неделя. @cust")
    local.portal.call(rt._ingest, [raw])
    cycle(local)
    assert any("Telegram" in o["card"]["title"] or "бот" in o["card"]["title"].lower() for o in cloud.orders.values())
