"""Общий интерфейс «мозга»: разбор, оценка, черновик, импорт профиля."""

from __future__ import annotations

from typing import Protocol

from ..models import Extraction, Learned, OrderView, Score
from ..profile import Profile

TONES = ("short", "warm", "formal")


class LLM(Protocol):
    name: str

    async def extract(self, text: str, hint: Extraction) -> Extraction: ...

    async def score(self, order: OrderView, profile: Profile, learned: Learned) -> Score: ...

    async def draft(self, order: OrderView, profile: Profile, tone: str, variant: int, price: int | None) -> str: ...

    async def parse_profile(self, text: str, current: Profile) -> dict: ...


class LLMUnavailable(Exception):
    """ИИ не ответил: вызывающий код переходит на простые правила."""
