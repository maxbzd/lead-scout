"""Простые структуры данных, которыми обмениваются части системы."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .extract import Contact, Flag


@dataclass
class RawMessage:
    chat_id: int  # номер чата в нашей базе
    tg_msg_id: int
    text: str
    posted_at: datetime
    author_id: int | None = None
    author_username: str | None = None
    author_name: str | None = None
    author_flags: dict = field(default_factory=dict)
    reply_to: int | None = None
    replies_count: int = 0
    link: str | None = None


@dataclass
class Extraction:
    """Что нашли в тексте сообщения."""

    is_order: bool
    title: str
    summary: str = ""
    skills: list[str] = field(default_factory=list)  # идентификаторы из taxonomy или свои слова
    budget_min: float | None = None
    budget_max: float | None = None
    currency: str = "RUB"
    period: str = "fixed"
    deadline_days: int | None = None
    contact: Contact = field(default_factory=Contact)
    flags: list[Flag] = field(default_factory=list)


@dataclass
class OrderView:
    """Заказ в том виде, в каком его видит оценщик и составитель откликов."""

    title: str
    summary: str
    text: str
    skills: list[str]
    budget_min: float | None
    budget_max: float | None
    currency: str
    period: str
    budget_rub_min: float | None
    budget_rub_max: float | None
    deadline_days: int | None
    age_minutes: int | None
    bids: int = 0
    flags: list[Flag] = field(default_factory=list)
    source: str = ""

    @property
    def budget_rub(self) -> float | None:
        """Одно число для сравнений: верх диапазона, если он есть."""
        return self.budget_rub_max if self.budget_rub_max is not None else self.budget_rub_min

    @property
    def rate_per_day(self) -> float | None:
        b = self.budget_rub
        if b is None or self.period != "fixed" or not self.deadline_days:
            return None
        return b / max(1, self.deadline_days)


@dataclass
class Score:
    score: int
    verdict: str  # match | maybe | skip | scam
    why: str
    fit: list[dict] = field(default_factory=list)  # [{ok: bool, text: str}]
    flags: list[Flag] = field(default_factory=list)
    scored_by: str = "rules"


@dataclass
class Example:
    title: str
    summary: str
    skills: list[str]
    budget: str
    liked: bool
    reason: str = ""


@dataclass
class Learned:
    examples: list[Example] = field(default_factory=list)
    skill_weights: dict[str, float] = field(default_factory=dict)
    chat_weights: dict[int, float] = field(default_factory=dict)
