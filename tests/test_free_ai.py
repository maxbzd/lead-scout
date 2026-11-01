"""Бесплатный и свой ИИ (OpenAI-совместимый протокол): ответы, лимиты, ошибки. Сеть подменена."""

import json

import httpx
import pytest

from leadscout import runtime as rtm
from leadscout import store
from leadscout.config import Settings
from leadscout.llm import openai_compat as oc
from leadscout.llm.base import LLMUnavailable
from leadscout.llm.claude import FallbackLLM
from leadscout.llm.openai_compat import OpenAICompatLLM, parse_json_loose
from leadscout.llm.rules import rule_extract
from leadscout.models import Learned
from leadscout.profile import Profile
from leadscout.runtime import Runtime

from .conftest import make_raw
from .test_claude_llm import TEXT, view

GOOD = {"is_order": True, "score": 82, "verdict": "match", "why": "Совпали навыки", "fit": [{"ok": True, "text": "Бот и GPT"}], "red_flags": []}


def answer(content, status=200):
    body = {"choices": [{"message": {"content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 120}}
    return httpx.Response(status, json=body)


def llm(handler, db=None, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatLLM("https://api.example.com/v1", "key-12345", "m1", db=db, client=client, rpm=6000, **kw)


def test_loose_json():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Вот ответ: {"a": 2} Надеюсь, помог') == {"a": 2}
    with pytest.raises(LLMUnavailable):
        parse_json_loose("просто текст")


async def test_score_parses_and_applies_hard_rules():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return answer("```json\n" + json.dumps(GOOD) + "\n```")

    m = llm(handler)
    s = await m.score(view(), Profile(recall=False), Learned())
    assert s.score == 82 and s.verdict == "match" and s.scored_by == "ai"
    assert seen["auth"] == "Bearer key-12345" and seen["body"]["model"] == "m1"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    # бюджет ниже минимума важнее мнения модели
    s2 = await m.score(view(), Profile(min_budget=500000, recall=False, ignore_budget=False), Learned())
    assert s2.verdict == "skip"


async def test_not_an_order_gets_doubt_flag():
    m = llm(lambda r: answer({**GOOD, "is_order": False, "score": 10, "verdict": "skip"}))
    s = await m.score(view(), Profile(), Learned())
    assert any(f.code == "doubt" for f in s.flags) and s.verdict == "maybe"  # режим «ничего не пропускать» оставляет в ленте


async def test_extract_makes_no_request():
    calls = []
    m = llm(lambda r: calls.append(r) or answer(GOOD))
    hint = rule_extract(TEXT)
    assert await m.extract(TEXT, hint) is hint and calls == []


async def test_json_mode_unsupported_is_remembered():
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append("response_format" in body)
        return httpx.Response(400, json={"error": "response_format not supported"}) if "response_format" in body else answer(GOOD)

    m = llm(handler)
    await m.score(view(), Profile(), Learned())
    await m.score(view(), Profile(), Learned())
    assert seen == [True, False, False]


@pytest.mark.parametrize(("status", "text"), [(401, "Ключ"), (404, "не найдена"), (500, "ошибкой 500")])
async def test_http_errors_become_unavailable(status, text):
    m = llm(lambda r: httpx.Response(status, json={}))
    with pytest.raises(LLMUnavailable, match=text):
        await m.score(view(), Profile(), Learned())
    assert text in (m.last_error or "")


async def test_rate_limit_blocks_following_calls_without_hitting_network():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "90"}, json={})

    m = llm(handler)
    with pytest.raises(LLMUnavailable):
        await m.score(view(), Profile(), Learned())
    with pytest.raises(LLMUnavailable):
        await m.score(view(), Profile(), Learned())
    assert len(calls) == 1


async def test_daily_limit_switches_to_rules(db):
    calls = []
    m = llm(lambda r: calls.append(1) or answer(GOOD), db=db, rpd=2)
    wrapped = FallbackLLM(m)
    for _ in range(4):
        s = await wrapped.score(view(), Profile(), Learned())
    assert len(calls) == 2 and s.scored_by == "rules" and m.used_today() == 2
    assert "Дневной лимит" in (wrapped.last_error or "")


async def test_draft_is_plain_text():
    m = llm(lambda r: answer("Здравствуйте! Заказ интересен, готов взяться за 45 000 ₽ за неделю. Есть опыт с ботами и GPT."))
    text = await m.draft(view(), Profile(), "warm", 0, 45000)
    assert text.startswith("Здравствуйте")


async def test_ingest_is_instant_and_ai_refines_in_background(db, settings, chat_id):
    from leadscout.pipeline import Pipeline

    calls = []
    m = llm(lambda r: calls.append(json.loads(r.content)["messages"][0]["content"][:30]) or answer(GOOD), db=db)
    pipe = Pipeline(db, FallbackLLM(m), settings)
    oid = await pipe.process(make_raw(chat_id, TEXT))
    assert oid and store.get_order(db, oid)["scored_by"] == "rules" and calls == []  # проверка чатов не ждёт ИИ
    assert await pipe.upgrade_with_ai() == 1
    row = store.get_order(db, oid)
    assert row["scored_by"] == "ai" and row["why"] == "Совпали навыки"
    assert len([c for c in calls if c.startswith("Ты помогаешь")]) == 1  # разбор правилами, оценка одним запросом
    assert await pipe.upgrade_with_ai() == 0  # второй раз тот же заказ не трогаем


async def test_upgrade_stops_when_limit_is_reached(db, settings, chat_id):
    from leadscout.pipeline import Pipeline

    m = llm(lambda r: answer(GOOD), db=db, rpd=1)
    pipe = Pipeline(db, FallbackLLM(m), settings)
    texts = [TEXT, "Нужен специалист по n8n для автоматизации заявок в amoCRM. Бюджет 40 000 руб, срок 5 дней. @crm_owner",
             "Требуется промпт-инженер для GPT-ассистента поддержки. Оплата 50 000 руб, срок неделя. @support_lead"]
    for i, t in enumerate(texts):
        await pipe.process(make_raw(chat_id, t, author=f"a{i}", author_id=10 + i))
    assert await pipe.upgrade_with_ai() == 1  # дальше лимит: остальные остаются с оценкой правил
    assert db.scalar("SELECT COUNT(*) FROM orders WHERE scored_by='rules'") == 2


@pytest.fixture()
def rt(tmp_path, monkeypatch):
    real = httpx.AsyncClient

    def factory(*a, **k):
        def handler(req):
            if "bad-key" in req.headers.get("authorization", ""):
                return httpx.Response(401, json={})
            return answer("ok")

        return real(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(oc.httpx, "AsyncClient", factory)
    return Runtime(Settings(data_dir=tmp_path, password="pw", demo=False, run_worker=False))


async def test_connect_ai_validates_then_saves_and_switches_mode(rt):
    with pytest.raises(LLMUnavailable, match="Неизвестный"):
        await rt.connect_ai("nope", "x" * 20)
    with pytest.raises(LLMUnavailable, match="ключ"):
        await rt.connect_ai("gemini", "short")
    with pytest.raises(LLMUnavailable, match="не подошёл"):
        await rt.connect_ai("gemini", "bad-key-123456")
    assert rt.llm_status()["mode"] == "rules"
    await rt.connect_ai("gemini", "good-key-123456")
    st = rt.llm_status()
    assert st["mode"] == "ai" and st["provider"] == "gemini" and st["limit_today"] == 900
    assert rt.secrets.get("ai_key") == "good-key-123456"
    raw = rt.db.scalar("SELECT value FROM settings WHERE key='secret:ai_key'")
    assert raw and "good-key" not in raw
    rt.disconnect_ai()
    assert rt.llm_status()["mode"] == "rules" and rt.secrets.get("ai_key") is None


async def test_ollama_needs_no_key(rt):
    await rt.connect_ai("ollama", "")
    assert rt.llm_status()["mode"] == "ai"


def test_api_providers_and_status(authed):
    r = authed.get("/api/connect/ai/providers").json()["items"]
    assert {p["id"] for p in r} >= {"gemini", "groq", "openrouter", "ollama", "custom"}
    assert authed.get("/api/status").json()["llm"]["mode"] == "rules"
    bad = authed.post("/api/connect/ai", json={"provider": "gemini", "key": "x"}, headers={"X-Requested-With": "leadscout"})
    assert bad.status_code == 400


_ = rtm  # модуль нужен подмене клиента
