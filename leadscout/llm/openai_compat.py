"""ИИ по «стандартному» протоколу (OpenAI-совместимому): Gemini, Groq, OpenRouter, свой Ollama и другие.

У бесплатных тарифов есть лимиты, поэтому здесь работает один запрос на заказ (разбор и оценка вместе),
свой счётчик запросов в минуту и в день, а когда лимит кончился, управление берут простые правила.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any

import httpx

from ..db import Database
from ..extract import Flag
from ..models import Extraction, Learned, OrderView, Score
from ..profile import Profile
from ..scoring import finalize
from ..secrets_store import mask
from ..util import clip, local_now
from .base import LLMUnavailable
from .claude import SCORE_SYSTEM, ClaudeLLM

log = logging.getLogger("leadscout.llm")

# Готовые настройки. Названия моделей у провайдеров меняются: их можно поправить в приложении.
PROVIDERS: dict[str, dict[str, Any]] = {
    "gemini": {
        "label": "Google Gemini (бесплатно)", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3.5-flash-lite", "rpm": 12, "rpd": 900, "needs_key": True,
        "key_url": "https://aistudio.google.com/apikey",
        "note": "Бесплатный ключ берётся в Google AI Studio. Компьютер должен находиться в стране, где Gemini доступен (иначе нужен VPN).",
    },
    "groq": {
        "label": "Groq (бесплатно)", "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile", "rpm": 25, "rpd": 900, "needs_key": True,
        "key_url": "https://console.groq.com/keys",
        "note": "Быстрый, но у бесплатного тарифа маленький дневной лимит по токенам: хватит на небольшое число чатов.",
    },
    "openrouter": {
        "label": "OpenRouter (бесплатные модели)", "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct:free", "rpm": 15, "rpd": 45, "needs_key": True,
        "key_url": "https://openrouter.ai/keys",
        "note": "Бесплатные модели: 50 запросов в день, а после разового пополнения на $10 до 1000 в день.",
    },
    "ollama": {
        "label": "Своя модель на сервере (Ollama)", "base_url": "http://host.docker.internal:11434/v1",
        "model": "qwen2.5:7b", "rpm": 20, "rpd": 100000, "needs_key": False, "key_url": "",
        "note": "Полностью бесплатно и приватно, но нужен сервер с 8 ГБ памяти и больше, и работает медленнее.",
    },
    "custom": {
        "label": "Другой (адрес и модель вручную)", "base_url": "", "model": "", "rpm": 20, "rpd": 1000, "needs_key": True,
        "key_url": "", "note": "Любой сервис с адресом вида .../v1/chat/completions.",
    },
}

FREE_SCORE_SCHEMA_TEXT = (
    'Ответь ТОЛЬКО одним JSON-объектом без пояснений и без markdown, с полями: '
    '"is_order" (true, если это объявление о поиске исполнителя на задачу, иначе false), '
    '"score" (целое 0-100), "verdict" ("match", "maybe" или "skip"), "why" (одна фраза до 130 символов), '
    '"fit" (список из 3-5 объектов {"ok": true|false, "text": "короткая фраза"}), '
    '"red_flags" (список объектов {"text": "...", "level": "high"|"medium"}, часто пустой).\n'
    "Если сомневаешься, является ли сообщение заказом, ставь is_order=true: пропущенный заказ хуже лишнего. "
    "Но is_order=false всегда для объявлений исполнителей (тег #помогу, «я делаю…», «меня зовут…, я специалист», прайс, портфолио, «ищу заказы») "
    "и для правил чата: даже длинные и подробные объявления исполнителей это не заказ."
)


def parse_json_loose(text: str) -> dict:
    """Маленькие модели иногда оборачивают JSON в ```json ... ``` или добавляют пояснение: достаём объект."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE)
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if not m:
            raise LLMUnavailable("ответ не разобрался как JSON") from None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            raise LLMUnavailable("ответ не разобрался как JSON") from None
    if not isinstance(data, dict):
        raise LLMUnavailable("ответ не объект")
    return data


class OpenAICompatLLM(ClaudeLLM):
    name = "ai"
    scored_by = "ai"
    deferred = True  # оценка ИИ идёт в фоне, чтобы лимиты не тормозили проверку чатов

    def __init__(self, base_url: str, api_key: str | list[str], model: str, db: Database | None = None, rpm: int = 20,
                 rpd: int = 1000, client: httpx.AsyncClient | None = None, provider: str = "custom"):
        self.base_url = base_url.rstrip("/")
        keys = [api_key] if isinstance(api_key, str) else list(api_key)
        self.keys = [k.strip() for k in keys if k and k.strip()] or [""]  # несколько ключей складывают лимиты; пустой ключ только у своей модели
        self.api_key = self.keys[0]
        self.model_fast = self.model_smart = model
        self.model = model
        self.provider = provider
        self.db = db
        self.rpm = max(1, rpm)
        self.rpd_key = max(1, rpd)  # суточный лимит одного ключа
        self.rpd = self.rpd_key * len(self.keys)  # и всех вместе
        self.client = client or httpx.AsyncClient(timeout=60.0)
        self.last_error: str | None = None
        self.calls = 0
        self._gate = asyncio.Lock()
        self._last_call = 0.0
        self._blocked: dict[str, float] = {}  # ключ -> до какого момента ждём (ответ 429)
        self._bad: set[str] = set()  # ключи, которые сервис не принял
        self._counts: dict[str, int] = {}
        self._json_mode = True

    # --- лимиты ---------------------------------------------------------
    @staticmethod
    def _kid(key: str) -> str:
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]

    def _day(self) -> str:
        return local_now("UTC").strftime("%Y-%m-%d")

    def _day_key(self) -> str:
        return "ai_calls:" + self._day()

    def used_today(self) -> int:
        return int(self.db.get_setting(self._day_key(), 0)) if self.db else self.calls

    def key_used(self, key: str) -> int:
        if self.db:
            return int(self.db.get_setting(f"{self._day_key()}:{self._kid(key)}", 0))
        return self._counts.get(key, 0)

    def _count(self, key: str) -> None:
        self.calls += 1
        self._counts[key] = self._counts.get(key, 0) + 1
        if self.db:
            self.db.set_setting(self._day_key(), self.used_today() + 1)
            self.db.set_setting(f"{self._day_key()}:{self._kid(key)}", self.key_used(key) + 1)

    def _state(self, key: str, now: float) -> str:
        if key in self._bad:
            return "bad"
        if self.key_used(key) >= self.rpd_key:
            return "limit"
        if now < self._blocked.get(key, 0.0):
            return "blocked"
        return "ok"

    def key_states(self) -> list[dict]:
        now = time.monotonic()
        return [{"mask": mask(k) if k else "без ключа", "used": self.key_used(k), "limit": self.rpd_key, "state": self._state(k, now)} for k in self.keys]

    async def _acquire(self) -> str:
        """Выбираем ключ с наименьшим расходом за сегодня из тех, что сейчас работают, и держим паузу под лимит запросов в минуту."""
        now = time.monotonic()
        usable = [k for k in self.keys if self._state(k, now) == "ok"]
        if not usable:
            states = {self._state(k, now) for k in self.keys}
            if states == {"bad"}:
                self.last_error = "Ключ ИИ не подошёл или у него нет доступа к модели" if len(self.keys) == 1 else "Ни один ключ ИИ не подошёл"
            elif "blocked" in states:
                self.last_error = self.last_error or "Бесплатный лимит ИИ исчерпан: работаю по правилам и скоро попробую снова"
            else:
                self.last_error = "Дневной лимит бесплатного ИИ израсходован: до завтра работаю по правилам"
            raise LLMUnavailable(self.last_error)
        key = min(usable, key=self.key_used)
        async with self._gate:  # у каждого ключа свой лимит в минуту, поэтому с несколькими ключами можно чаще
            gap = 60.0 / (self.rpm * len(usable)) - (time.monotonic() - self._last_call)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last_call = time.monotonic()
        return key

    # --- вызов ----------------------------------------------------------
    async def _post(self, body: dict[str, Any], key: str) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            resp = await self.client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            if resp.status_code == 400 and "response_format" in body:
                self._json_mode = False  # провайдер не умеет JSON-режим: дальше без него
                body.pop("response_format")
                resp = await self.client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as e:
            self.last_error = "Нет связи с сервисом ИИ"
            raise LLMUnavailable(self.last_error) from e
        return resp

    async def _call(self, model: str, system: str, user: str, *, schema: dict | None = None,
                    effort: str = "low", max_tokens: int = 4000, wants_json: bool | None = None) -> str:
        wants_json = (schema is not None) if wants_json is None else wants_json
        body: dict[str, Any] = {
            "model": self.model, "temperature": 0.2, "max_tokens": min(max_tokens, 3000),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if wants_json and self._json_mode:
            body["response_format"] = {"type": "json_object"}
        resp = None
        for _ in range(len(self.keys)):  # ключ не принят или упёрся в лимит: пробуем следующий
            key = await self._acquire()
            resp = await self._post(body, key)
            self._count(key)
            if resp.status_code in (401, 403):
                self._bad.add(key)
                self.last_error = "Ключ ИИ не подошёл или у него нет доступа к модели" if len(self.keys) == 1 else "Один из ключей ИИ не подошёл"
                continue
            if resp.status_code == 429:
                self._blocked[key] = time.monotonic() + _retry_after(resp)
                self.last_error = "Бесплатный лимит ИИ исчерпан: работаю по правилам и скоро попробую снова"
                continue
            break
        assert resp is not None
        if resp.status_code in (401, 403, 429):
            raise LLMUnavailable(self.last_error or "ИИ недоступен")
        if resp.status_code == 404:
            self.last_error = f"Модель «{self.model}» не найдена у этого сервиса"
            raise LLMUnavailable(self.last_error)
        if resp.status_code >= 400:
            self.last_error = f"Сервис ИИ ответил ошибкой {resp.status_code}"
            raise LLMUnavailable(self.last_error)
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as e:
            self.last_error = "Сервис ИИ вернул непонятный ответ"
            raise LLMUnavailable(self.last_error) from e
        self.last_error = None
        self._meter_tokens(data)
        if not text.strip():
            raise LLMUnavailable("пустой ответ")
        return text

    def _meter_tokens(self, data: dict) -> None:
        if self.db is None:
            return
        try:
            u = data.get("usage") or {}
            key = "usage:" + local_now("UTC").strftime("%Y-%m")
            cur = self.db.get_setting(key, {"usd": 0.0, "calls": 0, "in": 0, "out": 0})
            cur["calls"] = cur.get("calls", 0) + 1
            cur["in"] = cur.get("in", 0) + int(u.get("prompt_tokens") or 0)
            cur["out"] = cur.get("out", 0) + int(u.get("completion_tokens") or 0)
            self.db.set_setting(key, cur)
        except Exception:
            log.debug("usage meter failed", exc_info=True)

    @staticmethod
    def _json(text: str) -> dict:
        return parse_json_loose(text)

    async def _classify_call(self, system: str, user: str) -> str:
        return await self._call(self.model, system, user, schema=None, wants_json=True, max_tokens=2000)

    # --- один запрос вместо двух ----------------------------------------
    async def extract(self, text: str, hint: Extraction) -> Extraction:
        return hint  # поля из текста берут правила, а «заказ или нет» решает оценка ниже: так запросов вдвое меньше

    async def score(self, order: OrderView, profile: Profile, learned: Learned) -> Score:
        system = SCORE_SYSTEM + "\n\n" + self._profile_block(profile) + self._examples_block(learned) + "\n\n" + FREE_SCORE_SCHEMA_TEXT
        payload = {
            "название": order.title, "текст": order.text[:2000], "навыки": order.skills,
            "бюджет_рублей": order.budget_rub, "период_оплаты": order.period, "срок_дней": order.deadline_days,
            "возраст_минут": order.age_minutes, "откликов_уже": order.bids,
        }
        data = self._json(await self._call(self.model, system, "Заказ:\n" + json.dumps(payload, ensure_ascii=False), schema=None, wants_json=True, max_tokens=900))
        flags = [Flag("llm_" + str(i), clip(str(f.get("text", "")), 120), f.get("level") if f.get("level") in {"high", "medium"} else "medium")
                 for i, f in enumerate(data.get("red_flags") or []) if isinstance(f, dict) and f.get("text")]
        fit = [{"ok": bool(x.get("ok")), "text": clip(str(x.get("text", "")), 140)}
               for x in (data.get("fit") or []) if isinstance(x, dict) and x.get("text")]
        score = _to_int(data.get("score"), 50)
        verdict = data.get("verdict") if data.get("verdict") in {"match", "maybe", "skip"} else "maybe"
        if data.get("is_order") is False:
            score, verdict = max(min(score, 40), 35), "skip"  # окончательно решит режим «ничего не пропускать»
            flags.append(Flag("doubt", "ИИ не уверен, что это заказ", "low"))
        raw = Score(score=score, verdict=verdict, why=clip(str(data.get("why") or ""), 150), fit=fit, flags=flags, scored_by=self.scored_by)
        return finalize(raw, order, profile, order.flags + flags)

    async def check(self) -> None:
        """Проверка ключа и модели одним коротким запросом."""
        text = await self._call(self.model, "Отвечай коротко.", "Ответь одним словом: ok", schema=None, wants_json=False, max_tokens=20)
        if not text.strip():
            raise LLMUnavailable("модель не ответила")

    async def list_models(self) -> list[str]:
        """Какие модели доступны по этому ключу: помогает выбрать актуальное имя, пока оно не устарело."""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            resp = await self.client.get(f"{self.base_url}/models", headers=headers)
        except httpx.HTTPError as e:
            raise LLMUnavailable("Нет связи с сервисом ИИ") from e
        if resp.status_code in (401, 403):
            raise LLMUnavailable("Ключ ИИ не подошёл")
        if resp.status_code >= 400:
            raise LLMUnavailable(f"Сервис ИИ ответил ошибкой {resp.status_code}")
        try:
            items = resp.json().get("data") or []
        except ValueError:
            return []
        ids = [str(i.get("id", "")).removeprefix("models/") for i in items if isinstance(i, dict)]
        return sorted({i for i in ids if i})[:80]

    async def aclose(self) -> None:
        await self.client.aclose()


def _retry_after(resp: httpx.Response) -> float:
    try:
        return min(3600.0, max(20.0, float(resp.headers.get("retry-after", ""))))
    except ValueError:
        return 120.0


def _to_int(v: Any, default: int) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


__all__ = ["PROVIDERS", "OpenAICompatLLM", "parse_json_loose"]
