"""Профиль фрилансера и настройки уведомлений: хранятся в таблице settings."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from .db import Database
from .taxonomy import profile_keys

CAPACITY_MIN_BUDGET = {"free": 20000, "busy": 40000, "full": 80000}


class Case(BaseModel):
    title: str = Field(max_length=120)
    note: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=300)


class Profile(BaseModel):
    skills: list[str] = Field(default_factory=lambda: ["Telegram-боты", "LLM и промпты", "n8n и автоматизация", "RAG"])
    pay_types: list[str] = Field(default_factory=lambda: ["fixed", "hourly"])
    min_budget: int = Field(default=20000, ge=0, le=10_000_000)
    day_rate: int = Field(default=6000, ge=0, le=1_000_000)
    stop_words: list[str] = Field(default_factory=lambda: ["бесплатно", "залог", "тестовое без оплаты", "mlm"])
    bio: str = Field(default="", max_length=600)
    cases: list[Case] = Field(default_factory=list)
    tone: str = "warm"
    capacity: str = "free"
    tz: str = "Europe/Moscow"
    unpaid_check: bool = True
    recall: bool = True  # «ничего не пропускать»: сомнительное показываем, а не прячем

    @field_validator("skills", "stop_words")
    @classmethod
    def _clean_list(cls, v: list[str]) -> list[str]:
        seen: list[str] = []
        for item in v:
            item = (item or "").strip()
            if item and item.lower() not in {s.lower() for s in seen} and len(item) <= 60:
                seen.append(item)
        return seen[:40]

    @field_validator("tone")
    @classmethod
    def _tone(cls, v: str) -> str:
        return v if v in {"short", "warm", "formal"} else "warm"

    @field_validator("capacity")
    @classmethod
    def _cap(cls, v: str) -> str:
        return v if v in CAPACITY_MIN_BUDGET else "free"

    @field_validator("pay_types")
    @classmethod
    def _pay(cls, v: list[str]) -> list[str]:
        return [x for x in v if x in {"fixed", "hourly", "retainer"}]

    def keys(self) -> set[str]:
        return profile_keys(self.skills)


class Notifications(BaseModel):
    instant: bool = True
    instant_threshold: int = Field(default=45, ge=30, le=100)
    quiet: bool = True
    quiet_start: str = "23:00"
    quiet_end: str = "08:00"
    quiet_override_budget: int = 100000
    digest: bool = True
    digest_time: str = "08:00"
    weekly: bool = False
    paused: bool = False

    @field_validator("quiet_start", "quiet_end", "digest_time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        try:
            h, m = (int(x) for x in v.split(":"))
            if not (0 <= h < 24 and 0 <= m < 60):
                raise ValueError
        except ValueError:
            raise ValueError("время должно быть в формате ЧЧ:ММ") from None
        return f"{h:02d}:{m:02d}"


def load_profile(db: Database) -> Profile:
    data = db.get_setting("profile") or {}
    try:
        return Profile(**data)
    except Exception:
        return Profile()


def save_profile(db: Database, profile: Profile) -> Profile:
    db.set_setting("profile", profile.model_dump())
    return profile


def update_profile(db: Database, patch: dict[str, Any]) -> Profile:
    cur = load_profile(db).model_dump()
    cur.update({k: v for k, v in patch.items() if k in Profile.model_fields})
    return save_profile(db, Profile(**cur))


def load_notifications(db: Database) -> Notifications:
    data = db.get_setting("notifications") or {}
    try:
        return Notifications(**data)
    except Exception:
        return Notifications()


def save_notifications(db: Database, n: Notifications) -> Notifications:
    db.set_setting("notifications", n.model_dump())
    return n
