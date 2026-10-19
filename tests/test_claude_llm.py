import json
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest

from leadscout.extract import Contact
from leadscout.llm.base import LLMUnavailable
from leadscout.llm.claude import ClaudeLLM, FallbackLLM
from leadscout.llm.rules import RuleLLM, rule_extract
from leadscout.models import Learned, OrderView
from leadscout.profile import Profile


def resp(data, stop="end_turn", text=None):
    body = text if text is not None else json.dumps(data, ensure_ascii=False)
    return NS(content=[NS(type="thinking", thinking=""), NS(type="text", text=body)], stop_reason=stop,
              usage=NS(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=0))


class FakeMessages:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


class FakeClient:
    def __init__(self, outputs):
        self.messages = FakeMessages(outputs)


def llm_with(outputs, db=None):
    c = FakeClient(outputs)
    return ClaudeLLM("k", "claude-haiku-5-5", "claude-sonnet-5-5", db=db, client=c), c


TEXT = "Нужен телеграм-бот с GPT для записи клиентов. Бюджет 45000 руб, срок 7 дней. Писать @salon_admin"


def view():
    return OrderView(title="Бот для записи", summary="s", text=TEXT, skills=["telegram", "bot", "llm"], budget_min=45000,
                     budget_max=45000, currency="RUB", period="fixed", budget_rub_min=45000, budget_rub_max=45000,
                     deadline_days=7, age_minutes=10)


@pytest.mark.asyncio
async def test_extract_request_shape_and_parse(db):
    out = {"kind": "order", "title": "Телеграм-бот для записи клиентов", "summary": "Бот с GPT", "skills": ["Telegram-бот", "LLM", "Рыбалка"],
           "budget_min": 45000, "budget_max": 45000, "currency": "RUB", "period": "fixed", "deadline_days": 7,
           "contact": "@salon_admin", "red_flags": []}
    llm, c = llm_with([resp(out)], db)
    ext = await llm.extract(TEXT, rule_extract(TEXT))
    call = c.messages.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert "thinking" not in call and "temperature" not in call and "budget_tokens" not in json.dumps(call, default=str)
    assert call["output_config"]["effort"] == "low"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert ext.is_order and ext.title == "Телеграм-бот для записи клиентов"
    assert {"telegram", "bot", "llm"} <= set(ext.skills) and "рыбалка" in ext.skills
    assert ext.budget_max == 45000 and ext.deadline_days == 7
    assert ext.contact == Contact("telegram", "@salon_admin", "https://t.me/salon_admin")
    assert db.get_setting(next(k for k in ["usage:" + __import__("datetime").datetime.now(__import__("datetime").UTC).strftime("%Y-%m")]))["calls"] == 1


@pytest.mark.asyncio
async def test_extract_not_order():
    llm, _ = llm_with([resp({"kind": "offer", "title": "", "summary": "", "skills": [], "budget_min": 0, "budget_max": 0,
                             "currency": "RUB", "period": "fixed", "deadline_days": 0, "contact": "", "red_flags": []})])
    ext = await llm.extract(TEXT, rule_extract(TEXT))
    assert ext.is_order is False


@pytest.mark.asyncio
async def test_extract_falls_back_to_rule_hints_when_zero():
    llm, _ = llm_with([resp({"kind": "order", "title": "", "summary": "", "skills": [], "budget_min": 0, "budget_max": 0,
                             "currency": "RUB", "period": "fixed", "deadline_days": 0, "contact": "", "red_flags": []})])
    hint = rule_extract(TEXT)
    ext = await llm.extract(TEXT, hint)
    assert ext.budget_max == 45000 and ext.deadline_days == 7 and ext.title == hint.title


@pytest.mark.asyncio
async def test_score_applies_hard_rules_over_model():
    out = {"score": 95, "verdict": "match", "why": "Отлично", "fit": [{"ok": True, "text": "Навыки"}], "red_flags": []}
    llm, c = llm_with([resp(out)])
    p = Profile(min_budget=100000)
    s = await llm.score(view(), p, Learned())
    assert c.messages.calls[0]["model"] == "claude-sonnet-5-5"
    assert s.verdict == "skip" and s.score <= 40  # бюджет ниже минимума важнее мнения модели
    assert s.scored_by == "claude"


@pytest.mark.asyncio
async def test_score_includes_profile_and_examples_in_system():
    from leadscout.models import Example
    out = {"score": 82, "verdict": "match", "why": "Подходит", "fit": [{"ok": True, "text": "Навыки совпали"}], "red_flags": []}
    llm, c = llm_with([resp(out)])
    learned = Learned(examples=[Example("Парсер", "x", ["Парсинг"], "6 000 ₽", False, "Низкий бюджет")])
    s = await llm.score(view(), Profile(), learned)
    sys_text = c.messages.calls[0]["system"][0]["text"]
    assert "Telegram-боты" in sys_text and "Парсер" in sys_text and "Низкий бюджет" in sys_text
    assert s.score == 82 and s.verdict == "match"


@pytest.mark.asyncio
async def test_refusal_and_truncation_are_unavailable():
    llm, _ = llm_with([resp({}, stop="refusal")])
    with pytest.raises(LLMUnavailable):
        await llm.score(view(), Profile(), Learned())
    llm, _ = llm_with([resp({}, stop="max_tokens")])
    with pytest.raises(LLMUnavailable):
        await llm.score(view(), Profile(), Learned())
    llm, _ = llm_with([resp(None, text="не json")])
    with pytest.raises(LLMUnavailable):
        await llm.score(view(), Profile(), Learned())


def api_error(cls, status):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


@pytest.mark.asyncio
async def test_errors_become_unavailable_with_human_message():
    llm, _ = llm_with([api_error(anthropic.AuthenticationError, 401)])
    with pytest.raises(LLMUnavailable):
        await llm.draft(view(), Profile(), "warm", 0, 45000)
    assert llm.last_error == "Ключ Claude не подошёл"
    llm, _ = llm_with([api_error(anthropic.RateLimitError, 429)])
    with pytest.raises(LLMUnavailable):
        await llm.draft(view(), Profile(), "warm", 0, 45000)
    assert "лимит" in llm.last_error


@pytest.mark.asyncio
async def test_fallback_uses_rules_on_failure():
    primary, _ = llm_with([api_error(anthropic.AuthenticationError, 401)] * 4)
    fb = FallbackLLM(primary, RuleLLM())
    text = await fb.draft(view(), Profile(), "warm", 0, 45000)
    assert "45" in text and fb.failures == 1
    s = await fb.score(view(), Profile(), Learned())
    assert s.scored_by == "rules"
    ext = await fb.extract(TEXT, rule_extract(TEXT))
    assert ext.is_order
    assert fb.last_error == "Ключ Claude не подошёл"


@pytest.mark.asyncio
async def test_draft_returns_clean_text():
    llm, c = llm_with([resp(None, text="  Здравствуйте!   Беру ваш заказ.\n\n\nПришлю примеры работ сегодня.  ")])
    t = await llm.draft(view(), Profile(), "short", 1, 45000)
    assert t == "Здравствуйте! Беру ваш заказ.\n\nПришлю примеры работ сегодня."
    sent = json.loads(c.messages.calls[0]["messages"][0]["content"])
    assert sent["тон"] == "short" and sent["вариант"] == 2 and sent["условия"]["цена_рублей"] == 45000


@pytest.mark.asyncio
async def test_parse_profile():
    out = {"skills": ["Telegram-боты", "n8n"], "day_rate": 7000, "min_budget": 25000, "bio": "Делаю ботов.",
           "pay_types": ["fixed"], "cases": [{"title": "Бот салона", "note": "+30% записей", "url": ""}]}
    llm, _ = llm_with([resp(out)])
    patch = await llm.parse_profile("Я делаю ботов", Profile())
    assert patch["day_rate"] == 7000 and patch["cases"][0]["title"] == "Бот салона"
    Profile(**{**Profile().model_dump(), **patch})
