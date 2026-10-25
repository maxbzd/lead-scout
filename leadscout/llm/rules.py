"""Режим без нейросети: понятные правила. Работает офлайн, бесплатно, и служит запасным вариантом."""

from __future__ import annotations

import re

from .. import extract as ex
from ..models import Extraction, Learned, OrderView, Score
from ..profile import CAPACITY_MIN_BUDGET, Profile
from ..scoring import (
    below_minimum,
    draft_focus,
    finalize,
    hourly_to_day,
    matched_keys,
    order_keys,
    skill_phrase,
)
from ..taxonomy import label, labels, skill_ids
from ..util import clip, fmt_money, squash


def rule_extract(text: str, author_username: str | None = None, author_flags: dict | None = None) -> Extraction:
    budget = ex.parse_budget(text)
    contact = ex.parse_contact(text, author_username)
    flags = ex.scam_flags(text, author_flags)
    skills = skill_ids(text)
    return Extraction(
        is_order=ex.looks_like_order(text),
        title=ex.make_title(text),
        summary=clip(text, 220),
        skills=skills,
        budget_min=budget.min,
        budget_max=budget.max,
        currency=budget.currency,
        period=budget.period,
        deadline_days=ex.parse_deadline_days(text),
        contact=contact,
        flags=flags,
    )


class RuleLLM:
    name = "rules"

    async def extract(self, text: str, hint: Extraction) -> Extraction:
        return hint

    async def classify(self, texts, profile, yes, no):
        return [None] * len(texts)  # без ИИ решают правила отбора

    async def score(self, order: OrderView, profile: Profile, learned: Learned) -> Score:
        keys = order_keys(order)
        matched = matched_keys(order, profile)
        fit: list[dict] = []
        s = 28.0

        if keys:
            coverage = len(matched) / max(1, min(len(keys), 4))
            s += 42 * min(1.0, coverage)
            if coverage >= 0.75 and len(matched) >= 2:
                s += 4  # заказ почти целиком про ваши навыки
            if matched:
                fit.append({"ok": True, "text": f"Навыки совпали: {skill_phrase(profile, order, 4)}"})
            else:
                fit.append({"ok": False, "text": "Навыков из вашего списка в заказе не видно"})
        else:
            s += 6
            fit.append({"ok": False, "text": "Не удалось понять, какие навыки нужны"})

        b = order.budget_rub
        if b is None:
            s -= 3
            fit.append({"ok": False, "text": "Бюджет не указан, придётся уточнять"})
        elif below_minimum(order, profile):
            s -= 20
        else:
            s += 10
            if order.period == "fixed":
                fit.append({"ok": True, "text": f"Бюджет {fmt_money(b)} не ниже вашего минимума {fmt_money(profile.min_budget)}"})
            elif order.period == "hour":
                fit.append({"ok": True, "text": f"Ставка {fmt_money(b)} в час: за день ≈ {fmt_money(hourly_to_day(b))}"})

        rate = order.rate_per_day
        if rate is not None and profile.day_rate:
            if rate >= profile.day_rate:
                s += 6
                fit.append({"ok": True, "text": f"≈ {fmt_money(rate)} в день, не ниже вашей ставки"})
            elif rate >= profile.day_rate * 0.7:
                s += 3
            else:
                s -= 6
                fit.append({"ok": False, "text": f"≈ {fmt_money(rate)} в день, ниже вашей ставки {fmt_money(profile.day_rate)}"})

        if order.period == "hour" and "hourly" not in profile.pay_types:
            s -= 8
            fit.append({"ok": False, "text": "Почасовая оплата, а вы берёте фикс за проект"})
        if order.period == "month" and "retainer" not in profile.pay_types:
            s -= 6

        age = order.age_minutes
        if age is not None:
            if age < 30:
                s += 4
                fit.append({"ok": True, "text": f"Опубликовано {age} мин назад"})
            elif age < 120:
                s += 3
        if order.bids >= 6:
            s -= 4
            fit.append({"ok": False, "text": f"Откликов уже {order.bids}, конкуренция высокая"})

        if order.deadline_days and b is not None:
            s += 2

        bonus = sum(learned.skill_weights.get(k, 0.0) for k in keys)
        s += max(-8.0, min(8.0, bonus))

        s = int(max(0, min(97, round(s))))
        verdict = "match" if s >= 70 else "maybe" if s >= 45 else "skip"
        why = _why(order, profile, matched)
        raw = Score(score=s, verdict=verdict, why=why, fit=fit, flags=order.flags, scored_by="rules")
        return finalize(raw, order, profile, order.flags)

    async def draft(self, order: OrderView, profile: Profile, tone: str, variant: int, price: int | None) -> str:
        return build_draft(order, profile, tone, variant, price)

    async def parse_profile(self, text: str, current: Profile) -> dict:
        ids = skill_ids(text)
        patch: dict = {}
        if ids:
            patch["skills"] = labels(ids)
        m = re.search(r"(?:ставк\w*|беру|день)\D{0,12}(\d[\d\s]{2,8})\s*(?:₽|руб|р\b|в день|/день)", text, re.IGNORECASE)
        if m:
            patch["day_rate"] = int(re.sub(r"\D", "", m.group(1)))
        m2 = re.search(r"(?:от|минимум|не\s+меньше)\s*(\d[\d\s]{3,8})\s*(?:₽|руб|р\b)", text, re.IGNORECASE)
        if m2:
            patch["min_budget"] = int(re.sub(r"\D", "", m2.group(1)))
        sentences = re.split(r"(?<=[.!?])\s+", squash(text))
        if sentences and sentences[0]:
            patch["bio"] = clip(" ".join(sentences[:2]), 300)
        return patch


def _why(order: OrderView, profile: Profile, matched: list[str]) -> str:
    parts: list[str] = []
    if matched:
        parts.append("Совпало: " + ", ".join(labels(matched)[:3]) + ".")
    b = order.budget_rub
    if b is not None and not below_minimum(order, profile):
        if order.period == "fixed" and b >= profile.min_budget * 1.5:
            parts.append("Бюджет заметно выше вашего минимума.")
        elif order.period == "fixed":
            parts.append("Бюджет подходит.")
    elif b is None:
        parts.append("Бюджет не указан.")
    if order.age_minutes is not None and order.age_minutes < 30:
        parts.append("Заказ свежий.")
    if not parts:
        parts.append("Совпадений с вашими навыками мало.")
    return clip(" ".join(parts), 150)


# ------------------------------------------------------------- черновики ----


def _case_line(order: OrderView, profile: Profile) -> str:
    if not profile.cases:
        return ""
    keys = order_keys(order)
    best = None
    for c in profile.cases:
        if keys & set(skill_ids(c.title + " " + c.note)):
            best = c
            break
    best = best or profile.cases[0]
    line = f"Похожая работа: {best.title}"
    if best.note:
        line += f" ({best.note})"
    if best.url:
        line += f", {best.url}"
    return line + "."


def _terms(order: OrderView, price: int | None) -> str:
    """«за 7 дней, 46 000 ₽» для вставки в предложение."""
    parts = []
    if order.deadline_days:
        n = order.deadline_days
        parts.append(f"{n} {_plural(n, 'день', 'дня', 'дней')}")
    if price:
        parts.append(fmt_money(price))
    return ", ".join(parts)


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def build_draft(order: OrderView, profile: Profile, tone: str, variant: int, price: int | None) -> str:
    focus = draft_focus(profile, order, 3) or "задачами такого типа"
    terms = _terms(order, price)
    case = _case_line(order, profile)
    bio = profile.bio.strip()
    title = clip(order.title, 70)
    v = variant % 2

    if tone == "short":
        if v == 0:
            text = f"Здравствуйте! Беру ваш заказ. Работаю с {focus}."
            if terms:
                text += f" Условия: {terms}."
            text += " Пришлю примеры, если нужно."
        else:
            text = f"Добрый день! Заказ «{title}» мне подходит."
            if terms:
                text += f" Готов взяться: {terms}."
            text += " Скину похожие работы."
        return text

    if tone == "formal":
        if v == 0:
            text = f"Добрый день. Направляю предложение по заказу «{title}». Опыт: {focus}."
            if terms:
                text += f" Условия: {terms}."
            if case:
                text += " " + case
            text += " Готов обсудить детали и приступить в согласованный срок."
        else:
            text = f"Здравствуйте. Прошу рассмотреть мою кандидатуру по заказу «{title}»."
            text += f" Профильные направления: {focus}."
            if terms:
                text += f" Предлагаю: {terms}."
            if case:
                text += " " + case
        return text

    # warm (по умолчанию)
    if v == 0:
        text = f"Здравствуйте! Прочитал про ваш заказ и понял, что это моя тема: работаю с {focus}."
        if bio:
            text += " " + bio
        if terms:
            text += f" Могу взяться на таких условиях: {terms}"
            text += ". Первую версию покажу, не дожидаясь конца срока." if order.deadline_days and order.deadline_days >= 4 else "."
        if case:
            text += " " + case
        text += " Если удобно, обсудим детали."
    else:
        text = f"Добрый день! Ваша задача мне очень понятна: это то, чем я занимаюсь ({focus})."
        if terms:
            text += f" Предлагаю {terms}."
        if case:
            text += " " + case
        text += " Расскажу, как вижу решение, и отвечу на вопросы."
    return text


def capacity_to_min(capacity: str) -> int:
    return CAPACITY_MIN_BUDGET.get(capacity, 20000)


__all__ = ["RuleLLM", "rule_extract", "build_draft", "label", "capacity_to_min"]
