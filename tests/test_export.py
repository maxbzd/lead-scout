"""Выгрузка работы ИИ: подсказки, решения, ваши исправления; контакты скрываются."""

import asyncio

from leadscout import classify, diagnostics

from .conftest import H
from .test_classify import ORDER, QUIET, FakeAI, ingest
from .test_supply import TECH


def test_redaction_hides_people_but_keeps_the_meaning():
    t = "Пишите @ivan_petrov или ivan@mail.ru, телефон +7 (999) 123-45-67, портфолио https://site.ru/me и t.me/ivan_x. Нужен бот за 40 000 ₽"
    out = diagnostics.redact(t)
    for secret in ("ivan_petrov", "ivan@mail.ru", "123-45-67", "site.ru", "ivan_x"):
        assert secret not in out
    assert "Нужен бот за 40 000 ₽" in out and "@ник" in out and "[ссылка]" in out and "[телефон]" in out and "[почта]" in out
    assert diagnostics.redact(None) == ""


def test_export_is_local_only_and_needs_the_header_free_get(client):
    client.post("/api/login", json={"password": "secret-pw"}, headers=H)
    assert client.get("/api/export/ai").status_code == 404


def test_export_shows_prompts_decisions_and_corrections(tmp_path):
    from fastapi.testclient import TestClient

    from leadscout.config import Settings
    from leadscout.web.app import create_app

    s = Settings(data_dir=tmp_path, local=True, demo=False, run_worker=False, host="127.0.0.1")
    with TestClient(create_app(s), base_url="http://localhost:8000") as c:
        rt = c.app.state.rt
        from leadscout import store

        chat = store.add_chat(rt.db, "Чат заказов", username="zakazy", members=100)
        rt.pipeline.llm = FakeAI()
        rt.pipeline.ai_on = lambda: True
        tg = TECH + " @marakulin_technic"
        ids = asyncio.run(ingest(rt.pipeline, chat, [ORDER, QUIET, tg]))
        assert ids[0] and ids[1] is None
        # ошибки бота, исправленные вами
        discarded = c.get("/api/orders?filter=discarded").json()["items"]
        quiet_id = next(m["id"] for m in discarded if "Коллеги" in m["text"])
        c.post(f"/api/discarded/{quiet_id}/promote", headers=H)
        c.post(f"/api/orders/{ids[0]}/feedback", json={"kind": "down", "reason": "notorder"}, headers=H)
        classify.teach(rt.db, "Пример заказа с достаточно длинным текстом для запоминания @someone", True)

        r = c.get("/api/export/ai")
        assert r.status_code == 200 and "scout-ai-report-" in r.headers["content-disposition"]
        d = r.json()
        assert d["контакты_скрыты"] is True and d["программа"]
        assert "заказ" in d["системная_подсказка_чтения"] and "Ты помогаешь фрилансеру" in d["системная_подсказка_оценки"]
        assert d["итоги_решений"].get("ai:order") and d["итоги_решений"].get("user:promote") and d["итоги_решений"].get("rule:offer")
        by_text = {m["text"][:20]: m for m in d["сообщения"]}
        assert any(m["decision"] == "rule:offer" and m["order"] is None for m in d["сообщения"])
        assert any(m["decision"].startswith("user:promote") for m in d["сообщения"])
        assert d["ваши_исправления"]["бот_пропустил_а_вы_вернули"], "возврат из «Отброшено» виден как ошибка бота"
        assert d["ваши_исправления"]["бот_показал_а_вы_сказали_не_заказ"], "«Это не заказ» виден как ложное срабатывание"
        assert by_text
        raw = r.text
        assert "marakulin_technic" not in raw and "someone" not in raw  # ники скрыты
        full = c.get("/api/export/ai?redact=0").text
        assert "someone" in full
        assert len(c.get("/api/export/ai?limit=10").json()["сообщения"]) <= 10
