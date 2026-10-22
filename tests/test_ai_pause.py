"""Пауза ИИ: ручная и автоматическая, когда в ленте накопилось много неразобранного."""

import httpx
import pytest

from leadscout import ai_control, store
from leadscout.llm.claude import FallbackLLM
from leadscout.llm.openai_compat import OpenAICompatLLM
from leadscout.pipeline import Pipeline

from .conftest import H, make_raw
from .test_claude_llm import TEXT
from .test_free_ai import GOOD, answer

OTHER = "Нужен специалист по n8n для автоматизации заявок в amoCRM. Бюджет 40 000 руб, срок 5 дней. @crm_owner"


def ai_pipe(db, settings):
    calls = []

    def handler(req):
        calls.append(1)
        return answer(GOOD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    m = OpenAICompatLLM("https://x.example/v1", "k-12345678", "m", db=db, client=client, rpm=6000)
    return Pipeline(db, FallbackLLM(m), settings), calls


async def test_manual_pause_stops_background_ai(db, settings, chat_id):
    pipe, calls = ai_pipe(db, settings)
    await pipe.process(make_raw(chat_id, TEXT))
    ai_control.save(db, {"paused": True})
    pipe.reset_ai_state()
    assert await pipe.upgrade_with_ai() == 0 and calls == []
    assert ai_control.state(db)["reason"] == "manual"
    ai_control.save(db, {"paused": False})
    pipe.reset_ai_state()
    assert await pipe.upgrade_with_ai() == 1 and len(calls) == 1


async def test_auto_pause_when_backlog_is_big_and_resume_after_review(db, settings, chat_id):
    pipe, calls = ai_pipe(db, settings)
    ai_control.save(db, {"auto": True, "limit": 5})
    oids = []
    texts = [TEXT, OTHER, "Требуется промпт-инженер для GPT-ассистента поддержки. Оплата 50 000 руб, срок неделя. @support_lead",
             "Нужен чат-бот с GPT для записи в салон красоты. Бюджет 30 000 руб, срок 4 дня. @salon_two",
             "Ищу разработчика Telegram-бота с интеграцией OpenAI. 60 000 руб, срок 10 дней. @bot_client",
             "Нужен парсер каналов и RAG по базе знаний на Python. Бюджет 90 000 руб, срок 3 недели. @rag_owner"]
    for i, t in enumerate(texts):
        oids.append(await pipe.process(make_raw(chat_id, t, author=f"u{i}", author_id=100 + i)))
    st = ai_control.state(db)
    assert st["backlog"] >= 5 and st["reason"] == "backlog" and not st["active"]
    pipe.reset_ai_state()
    assert await pipe.upgrade_with_ai() == 0 and calls == []
    for oid in oids[:3]:  # разобрали несколько заказов
        store.set_feedback(db, oid, "down", "other")
        store.set_state(db, oid, "hidden")
    pipe.reset_ai_state()
    assert ai_control.state(db)["active"] and await pipe.upgrade_with_ai() >= 1


async def test_pause_blocks_ai_during_ingest_for_claude_style_llm(db, settings, chat_id):
    class Boom:
        name = "claude"
        deferred = False

        async def extract(self, *a):
            raise AssertionError("ИИ на паузе не должен вызываться")

        score = draft = extract

    pipe = Pipeline(db, Boom(), settings)
    ai_control.save(db, {"paused": True})
    pipe.reset_ai_state()
    assert await pipe.process(make_raw(chat_id, TEXT))


def test_auto_can_be_turned_off(db):
    ai_control.save(db, {"auto": False, "limit": 5})
    assert ai_control.state(db)["active"] is True


def test_api_control(authed):
    assert authed.get("/api/status").json()["llm"]["control"] is None  # без ИИ управлять нечем
    bad = authed.put("/api/ai/control", json={"limit": 1}, headers=H)
    assert bad.status_code == 422
    ok = authed.put("/api/ai/control", json={"paused": True, "limit": 80}, headers=H)
    assert ok.status_code == 200


@pytest.mark.parametrize("limit", [5, 500])
def test_limit_bounds(db, limit):
    assert ai_control.save(db, {"limit": limit})["limit"] == limit
