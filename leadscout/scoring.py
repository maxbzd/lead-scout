"""Общие правила оценки. Их применяют и ИИ-режим, и простой: так итог всегда объясним и предсказуем."""

from __future__ import annotations

from .extract import Flag, stop_word_hit
from .models import OrderView, Score
from .profile import Profile
from .taxonomy import AI_CORE, DRAFT_PHRASE, join_phrase, labels, order_ids, skill_ids
from .util import fmt_money


def order_keys(order: OrderView) -> set[str]:
    keys: set[str] = set()
    for s in order.skills:
        ids = skill_ids(s)
        keys.update(ids if ids else [s.strip().lower()])
    return keys


def matched_keys(order: OrderView, profile: Profile) -> list[str]:
    pk = profile.keys()
    return [k for k in order_ids(order_keys(order)) if k in pk]


def hourly_to_day(rate: float) -> float:
    return rate * 8


def below_minimum(order: OrderView, profile: Profile) -> bool:
    b = order.budget_rub
    if b is None or profile.min_budget <= 0:
        return False
    if order.period == "hour":
        return hourly_to_day(b) < profile.day_rate * 0.6
    if order.period == "day":
        return b < profile.day_rate * 0.6
    return b < profile.min_budget


def recommend_price(order: OrderView, profile: Profile, won_history: list[float] | None = None) -> tuple[int | None, int | None, str]:
    """Вилка цены и короткое пояснение. Только из реальных чисел: бюджет заказчика, ваша ставка, ваши прошлые заказы."""
    b_lo, b_hi = order.budget_rub_min, order.budget_rub_max
    days = order.deadline_days
    if order.period != "fixed" and order.budget_rub is not None:
        return None, None, "Оплата не за проект, а по ставке. Проверьте её против вашей."
    if b_hi is not None or b_lo is not None:
        base = ((b_lo or b_hi) + (b_hi or b_lo)) / 2
        lo, hi = base * 0.92, base * 1.06
        notes = [f"Заказчик указал {fmt_money(base)}."]
        if order.bids >= 6:
            lo, hi = base * 0.88, base
            notes.append(f"Откликов уже {order.bids}: держите цену ближе к бюджету.")
        elif order.bids == 0 and (order.age_minutes or 0) < 120:
            hi = base * 1.12
            notes.append("Откликов пока нет: можно просить верхнюю часть вилки.")
        if days is not None and days <= 3:
            hi *= 1.1
            notes.append("Срок короткий: можно добавить за срочность.")
        if won_history:
            med = sorted(won_history)[len(won_history) // 2]
            notes.append(f"Похожие вы брали примерно за {fmt_money(med)}.")
        return _round(lo), _round(hi), " ".join(notes)
    if profile.day_rate > 0:
        d = days or 5
        base = profile.day_rate * d
        note = f"Бюджет не указан. По вашей ставке {fmt_money(profile.day_rate)} в день на {d} дн. это {fmt_money(base)}."
        if won_history:
            med = sorted(won_history)[len(won_history) // 2]
            note += f" Похожие вы брали примерно за {fmt_money(med)}."
        return _round(base * 0.9), _round(base * 1.15), note
    return None, None, "Бюджет не указан. Уточните его у заказчика."


def _round(x: float) -> int:
    step = 1000 if x >= 10000 else 500
    return int(round(x / step) * step)


def finalize(score: Score, order: OrderView, profile: Profile, flags: list[Flag]) -> Score:
    """Жёсткие правила поверх оценки: мошенничество, стоп-слова и бюджет ниже вашего минимума."""
    flag_map = {f.code: f for f in flags}
    for f in score.flags:
        flag_map.setdefault(f.code, f)
    all_flags = list(flag_map.values())
    s = int(max(0, min(99, score.score)))
    fit = [dict(x) for x in score.fit]
    why = score.why

    hit = stop_word_hit(f"{order.title} {order.text}", profile.stop_words)
    high = [f for f in all_flags if f.level == "high"]
    verdict = score.verdict
    doubt = any(f.code == "doubt" for f in all_flags)
    has_skills = bool(set(order_ids(order_keys(order))) & (profile.keys() | AI_CORE))  # про ИИ или про ваши навыки
    recall = profile.recall

    if high:
        s = min(s, 35)
        verdict = "scam"
        why = high[0].text + ". Рекомендую пропустить."
        fit = [{"ok": False, "text": f.text} for f in all_flags] + [x for x in fit if x.get("ok")]
    elif hit:
        s, verdict = _demote(s, recall)
        why = (f"Показываю на всякий случай: в заказе стоп-слово «{hit}»." if recall else f"В заказе есть стоп-слово «{hit}».")
        fit.insert(0, {"ok": False, "text": f"Стоп-слово из вашего списка: «{hit}»"})
    elif below_minimum(order, profile):
        s, verdict = _demote(s, recall)
        b = order.budget_rub
        if order.period == "fixed":
            why = f"Бюджет {fmt_money(b)} ниже вашего минимума {fmt_money(profile.min_budget)}."
            fit.insert(0, {"ok": False, "text": f"Бюджет {fmt_money(b)} ниже минимума {fmt_money(profile.min_budget)}"})
        else:
            why = "Ставка ниже вашей."
            fit.insert(0, {"ok": False, "text": "Ставка ниже вашей"})
        if recall:
            why = "Показываю на всякий случай. " + why
    else:
        medium = [f for f in all_flags if f.level == "medium"]
        if medium:
            s = min(s, 62)
            fit = [{"ok": False, "text": f.text} for f in medium] + fit
            if verdict == "match":
                verdict = "maybe"
        if verdict == "match" and s < 70:
            verdict = "maybe"
        if verdict == "maybe" and s >= 70:
            verdict = "match"
        if verdict in {"match", "maybe"} and s < 45:
            if recall and (has_skills or doubt):
                s, verdict = 45, "maybe"  # лучше лишний раз показать, чем пропустить
            else:
                verdict = "skip"
        elif verdict == "skip" and recall and (has_skills or doubt) and s >= 30:
            s, verdict = 45, "maybe"
        if doubt and verdict in {"match", "maybe"}:
            s, verdict = min(max(s, 45), 69), "maybe"
            why = "Не уверен, что это заказ, но похоже. " + why

    return Score(score=s, verdict=verdict, why=why, fit=fit[:7], flags=all_flags, scored_by=score.scored_by)


def _demote(s: int, recall: bool) -> tuple[int, str]:
    """Заказ нарушает ваше правило (стоп-слово, бюджет). В режиме «ничего не пропускать» он остаётся в ленте как «под вопросом»."""
    return (min(max(s, 45), 55), "maybe") if recall else (min(s, 30), "skip")


def skill_phrase(profile: Profile, order: OrderView, limit: int = 3) -> str:
    """Названия совпавших навыков для показа: «Telegram, Бот, LLM»."""
    return ", ".join(labels(matched_keys(order, profile))[:limit])


def draft_focus(profile: Profile, order: OrderView, limit: int = 3) -> str:
    """То же, но для живой речи: «Telegram, ботами и LLM»."""
    ids = matched_keys(order, profile)[:limit]
    return join_phrase([DRAFT_PHRASE.get(i, i) for i in ids])
