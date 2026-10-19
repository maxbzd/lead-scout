import pytest
from fastapi.testclient import TestClient

from leadscout.config import Settings
from leadscout.web.app import create_app

H = {"X-Requested-With": "leadscout"}


@pytest.fixture()
def client(tmp_path):
    s = Settings(data_dir=tmp_path, password="secret-pw", demo=True, run_worker=False)
    with TestClient(create_app(s)) as c:
        yield c


@pytest.fixture()
def authed(client):
    r = client.post("/api/login", json={"password": "secret-pw"}, headers=H)
    assert r.status_code == 200
    return client


def test_requires_login(client):
    assert client.get("/api/orders").status_code == 401
    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/session").json() == {"authed": False}


def test_wrong_password_and_rate_limit(client):
    for _ in range(8):
        assert client.post("/api/login", json={"password": "nope"}, headers=H).status_code == 401
    r = client.post("/api/login", json={"password": "secret-pw"}, headers=H)
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_cookie_flags(client):
    r = client.post("/api/login", json={"password": "secret-pw"}, headers=H)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_csrf_header_required_for_writes(authed):
    r = authed.post("/api/scan")
    assert r.status_code == 403
    assert authed.post("/api/scan", headers=H).status_code == 200


def test_security_headers(client):
    r = client.get("/api/health")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def scan(c):
    r = c.post("/api/scan", headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def test_demo_scan_fills_feed(authed):
    res = scan(authed)
    assert res["orders"] >= 6
    feed = authed.get("/api/orders").json()
    assert feed["counts"]["all"] >= 5 and feed["counts"]["hot"] >= 2
    scores = [o["score"] for o in feed["items"]]
    assert scores == sorted(scores, reverse=True)
    top = feed["items"][0]
    assert top["budget"]["label"] and top["deadline_label"] and top["skills"]
    rej = authed.get("/api/orders?filter=rejected").json()
    assert {o["verdict"] for o in rej["items"]} <= {"skip", "scam"}
    assert any(o["verdict"] == "scam" for o in rej["items"])
    # дубль из двух чатов склеен
    salon = [o for o in feed["items"] if "салон" in o["title"].lower() or "салон" in o["summary"].lower()]
    assert salon and salon[0]["dups"] >= 1
    assert salon[0]["bids"] >= 2


def test_manual_scan_brings_new_orders_one_by_one(authed):
    scan(authed)
    before = authed.get("/api/orders").json()["counts"]["all"]
    res = scan(authed)
    assert res["orders"] == 1 and res["ids"]
    assert authed.get("/api/orders").json()["counts"]["all"] == before + 1


def test_search_cyrillic(authed):
    scan(authed)
    r = authed.get("/api/orders", params={"q": "САЛОН"}).json()
    assert len(r["items"]) >= 1
    assert authed.get("/api/orders", params={"q": "зззз"}).json()["items"] == []


def first_id(c):
    return c.get("/api/orders").json()["items"][0]["id"]


def test_detail_and_draft(authed):
    scan(authed)
    oid = first_id(authed)
    d = authed.get(f"/api/orders/{oid}").json()
    assert d["fit"] and d["text"] and d["client"]["signals"] and d["rec"]["note"] is not None
    assert d["contact"]["url"].startswith("https://t.me/")
    assert d["draft"] and len(d["draft"]["text"]) > 40
    r = authed.post(f"/api/orders/{oid}/draft", json={"tone": "short", "variant": 1}, headers=H).json()
    assert r["tone"] == "short" and len(r["text"]) < 260
    r2 = authed.post(f"/api/orders/{oid}/draft", json={"tone": "short", "variant": 1, "price": 50000}, headers=H).json()
    assert "50" in r2["text"]


def test_feedback_hides_and_restores(authed):
    scan(authed)
    oid = first_id(authed)
    r = authed.post(f"/api/orders/{oid}/feedback", json={"kind": "down", "reason": "budget"}, headers=H).json()
    assert r["order"]["state"] == "hidden" and r["order"]["feedback"] == "down" and r["learned"] == 1
    assert oid not in [o["id"] for o in authed.get("/api/orders").json()["items"]]
    assert oid in [o["id"] for o in authed.get("/api/orders?filter=rated").json()["items"]]
    r = authed.post(f"/api/orders/{oid}/feedback", json={"kind": "clear"}, headers=H).json()
    assert r["order"]["state"] == "new" and r["order"]["feedback"] is None
    assert oid in [o["id"] for o in authed.get("/api/orders").json()["items"]]


def test_save_offer_stage_flow(authed):
    scan(authed)
    oid = first_id(authed)
    assert authed.post(f"/api/orders/{oid}/save", json={"saved": True}, headers=H).json()["order"]["state"] == "saved"
    assert oid in [o["id"] for o in authed.get("/api/orders?filter=saved").json()["items"]]
    o = authed.post(f"/api/orders/{oid}/offer", json={"done": True, "price": 46000}, headers=H).json()["order"]
    assert o["offered"] and o["stage"] == "wait" and o["price_offered"] == 46000
    o = authed.post(f"/api/orders/{oid}/stage", json={"stage": "work"}, headers=H).json()["order"]
    assert o["stage"] == "work"
    st = authed.get("/api/stats").json()
    work = next(t for t in st["tiles"] if t["id"] == "work")
    assert work["value"] == "1" and "46" in work["note"]
    assert st["funnel"][-1]["value"] == 1
    o = authed.post(f"/api/orders/{oid}/offer", json={"done": False}, headers=H).json()["order"]
    assert not o["offered"] and o["stage"] is None


def test_stats_shape(authed):
    scan(authed)
    st = authed.get("/api/stats").json()
    assert len(st["tiles"]) == 4 and len(st["days"]["values"]) == 7 and len(st["hours"]["values"]) == 24
    assert st["chats"] and st["log"] and st["accuracy"]["current"] is None  # оценок ещё нет


def test_accuracy_after_feedback(authed):
    scan(authed)
    items = authed.get("/api/orders").json()["items"]
    good = [o for o in items if o["score"] >= 70][:6]
    for i, o in enumerate(good):
        authed.post(f"/api/orders/{o['id']}/feedback", json={"kind": "up" if i < 4 else "down", "reason": "other"}, headers=H)
    acc = authed.get("/api/stats").json()["accuracy"]
    assert acc["n"] == len(good) and acc["current"] == round(100 * 4 / len(good))


def test_profile_update_rescores(authed):
    scan(authed)
    p = authed.get("/api/profile").json()["profile"]
    p["min_budget"] = 60000
    r = authed.put("/api/profile", json=p, headers=H)
    assert r.status_code == 200 and r.json()["profile"]["min_budget"] == 60000
    r = authed.put("/api/profile", json={**p, "tone": "weird"}, headers=H)
    assert r.json()["profile"]["tone"] == "warm"
    r = authed.put("/api/profile", json={**p, "min_budget": -5}, headers=H)
    assert r.status_code == 422


def test_profile_import(authed):
    r = authed.post("/api/profile/import", json={"text": "Делаю телеграм-ботов и автоматизации на n8n. Беру 8000 в день. Минимальный заказ от 25 000 руб"}, headers=H)
    patch = r.json()["patch"]
    assert "Telegram" in patch["skills"] and patch["day_rate"] == 8000 and patch["min_budget"] == 25000


def test_chats_flow(authed):
    scan(authed)
    chats = authed.get("/api/chats").json()["items"]
    assert len(chats) == 5
    cid = chats[0]["id"]
    items = authed.patch(f"/api/chats/{cid}", json={"enabled": False}, headers=H).json()["items"]
    assert next(c for c in items if c["id"] == cid)["enabled"] == 0
    r = authed.post("/api/chats", json={"link": "https://t.me/new_orders_chat"}, headers=H)
    assert r.status_code == 200 and any(c["username"] == "new_orders_chat" for c in r.json()["items"])
    bad = authed.post("/api/chats", json={"link": "not a link at all"}, headers=H)
    assert bad.status_code == 400 and "ссылку" in bad.json()["detail"]


def test_notifications_validation(authed):
    n = authed.get("/api/notifications").json()
    n["digest_time"] = "07:30"
    assert authed.put("/api/notifications", json=n, headers=H).json()["digest_time"] == "07:30"
    n["digest_time"] = "25:99"
    assert authed.put("/api/notifications", json=n, headers=H).status_code == 422


def test_status_readiness_and_connect_errors(authed):
    scan(authed)
    st = authed.get("/api/status").json()
    assert st["demo"] is True and st["llm"]["mode"] == "rules" and st["telegram"]["state"] == "demo"
    assert {r["id"] for r in st["ready"]} == {"chats", "skills", "cases", "telegram", "bot", "ai"}
    r = authed.post("/api/connect/claude", json={"key": "wrong-key-value"}, headers=H)
    assert r.status_code == 400 and "sk-ant" in r.json()["detail"]
    r = authed.post("/api/connect/bot", json={"token": "short-token-without-colon"}, headers=H)
    assert r.status_code == 400
    r = authed.post("/api/connect/telegram/api", json={"api_id": "abc", "api_hash": "x"}, headers=H)
    assert r.status_code == 400
    r = authed.post("/api/connect/telegram/phone", json={"phone": "+79991234567"}, headers=H)
    assert r.status_code == 400 and "api_id" in r.json()["detail"]


def test_suggestion_to_raise_min_budget(authed):
    scan(authed)
    p = authed.get("/api/profile").json()["profile"]
    p["min_budget"] = 5000
    authed.put("/api/profile", json=p, headers=H)
    items = authed.get("/api/orders?filter=all").json()["items"]
    cheap = sorted([o for o in items if o["budget"]["rub_max"] and o["budget"]["period"] == "fixed"], key=lambda o: o["budget"]["rub_max"])[:3]
    for o in cheap:
        authed.post(f"/api/orders/{o['id']}/feedback", json={"kind": "down", "reason": "budget"}, headers=H)
    sug = authed.get("/api/stats").json()["suggestions"]
    assert sug and "минимум" in sug[0]["text"]
    r = authed.post(f"/api/suggestions/{sug[0]['id']}", json={"accept": True}, headers=H)
    assert r.status_code == 200
    assert authed.get("/api/profile").json()["profile"]["min_budget"] > 5000
