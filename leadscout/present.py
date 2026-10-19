"""Превращает строки базы в то, что показывает приложение."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import store
from .db import Database
from .extract import Flag
from .models import OrderView
from .profile import Profile
from .scoring import order_keys
from .taxonomy import TAXONOMY, label
from .util import fmt_age, fmt_money, minutes_ago

CUR_SYM = {"RUB": "₽", "USD": "$", "EUR": "€"}
PERIOD_TXT = {"fixed": "", "hour": " в час", "day": " в день", "month": " в месяц"}


def _num(n: float) -> str:
    return f"{int(round(n)):,}".replace(",", " ")


def _short(n: float) -> str:
    return f"{int(round(n / 1000))}" if n >= 1000 and n % 1000 == 0 else _num(n)


def budget_label(row: dict) -> str:
    lo, hi = row.get("budget_min"), row.get("budget_max")
    cur = row.get("currency") or "RUB"
    period = row.get("period") or "fixed"
    if lo is None and hi is None:
        return "бюджет не указан"
    sym = CUR_SYM.get(cur, "₽")
    if lo is not None and hi is not None and lo != hi:
        if cur == "RUB" and lo >= 1000 and hi >= 1000 and lo % 1000 == 0 and hi % 1000 == 0:
            txt = f"{_short(lo)}–{_short(hi)} тыс. ₽"
        else:
            txt = f"{_num(lo)}–{_num(hi)} {sym}" if cur == "RUB" else f"{sym}{_num(lo)}–{_num(hi)}"
    elif lo is not None and hi is None:
        txt = f"от {_num(lo)} {sym}" if cur == "RUB" else f"от {sym}{_num(lo)}"
    else:
        v = hi if hi is not None else lo
        txt = f"{_num(v)} {sym}" if cur == "RUB" else f"{sym}{_num(v)}"
        if cur != "RUB" and row.get("budget_rub_max"):
            txt += f" ≈ {fmt_money(row['budget_rub_max'])}"
        if period == "fixed" and cur == "RUB":
            txt += " фикс"
    return txt + PERIOD_TXT.get(period, "")


def _plural(n: int, one: str, few: str, many: str) -> str:
    m = abs(n) % 100
    if 11 <= m <= 14:
        return many
    m %= 10
    return one if m == 1 else few if 2 <= m <= 4 else many


def deadline_label(days: int | None) -> str:
    if not days:
        return "срок не указан"
    if days == 7:
        return "срок неделя"
    if days % 30 == 0 and days >= 30:
        n = days // 30
        return f"срок {n} {_plural(n, 'месяц', 'месяца', 'месяцев')}"
    if days % 7 == 0 and days >= 14:
        n = days // 7
        return f"срок {n} {_plural(n, 'неделя', 'недели', 'недель')}"
    return f"срок {days} {_plural(days, 'день', 'дня', 'дней')}"


def rate_per_day(row: dict) -> float | None:
    b = row.get("budget_rub_max") if row.get("budget_rub_max") is not None else row.get("budget_rub_min")
    if b is None or (row.get("period") or "fixed") != "fixed" or not row.get("deadline_days"):
        return None
    return b / max(1, row["deadline_days"])


def rate_label(rate: float | None) -> str | None:
    if rate is None:
        return None
    return f"≈ {rate / 1000:.1f} тыс. в день".replace(".", ",")


def skills_view(row: dict, profile: Profile) -> list[dict]:
    pk = profile.keys()
    out = []
    seen = set()
    for s in row.get("skills") or []:
        key = s if s in TAXONOMY else s.lower()
        if key in seen:
            continue
        seen.add(key)
        lab = label(s) if s in TAXONOMY else (s[:1].upper() + s[1:])
        out.append({"id": key, "label": lab, "hit": key in pk})
    return out[:7]


def flags_view(row: dict) -> list[dict]:
    return [f for f in (row.get("red_flags") or []) if isinstance(f, dict) and f.get("text")]


def order_card(row: dict, profile: Profile, now: datetime | None = None) -> dict:
    age = minutes_ago(row["posted_at"], now)
    flags = flags_view(row)
    rate = rate_per_day(row)
    bad = [f for f in flags if f["level"] == "high"] or flags
    return {
        "id": row["id"],
        "title": row["title"],
        "summary": row.get("summary") or "",
        "skills": skills_view(row, profile),
        "score": row["score"],
        "verdict": row["verdict"],
        "hot": row["verdict"] == "match" and row["score"] >= 90,
        "why": row.get("why") or "",
        "flag": bad[0]["text"] if bad else None,
        "budget": {
            "min": row.get("budget_min"), "max": row.get("budget_max"), "currency": row.get("currency"),
            "period": row.get("period"), "rub_min": row.get("budget_rub_min"), "rub_max": row.get("budget_rub_max"),
            "known": row.get("budget_min") is not None or row.get("budget_max") is not None,
            "label": budget_label(row),
        },
        "deadline_days": row.get("deadline_days"),
        "deadline_label": deadline_label(row.get("deadline_days")),
        "rate_day": round(rate) if rate else None,
        "rate_label": rate_label(rate),
        "age_min": age,
        "age_label": fmt_age(age),
        "fresh": age is not None and age < 30,
        "posted_at": row["posted_at"],
        "source": row.get("chat_title") or "",
        "dups": row.get("dup_count") or 0,
        "bids": row.get("bids") or 0,
        "state": row["state"],
        "feedback": row.get("feedback"),
        "reason": row.get("feedback_reason"),
        "stage": row.get("stage"),
        "offered": bool(row.get("offered_at")),
        "price_offered": row.get("price_offered"),
        "demo": bool(row.get("chat_demo")),
    }


def client_block(db: Database, row: dict) -> dict:
    """Что известно о заказчике. Только то, что видно из Telegram и нашей базы."""
    stats = store.author_stats(db, row.get("author_id"), row.get("author_username"))
    flags = row.get("author_flags") or {}
    username = row.get("author_username")
    signals: list[dict] = []
    signals.append({"label": "Публикаций за месяц", "value": str(stats["posts"]), "bad": stats["posts"] >= 6})
    signals.append({"label": "Пишет в чатах", "value": str(stats["chats"]), "bad": stats["chats"] >= 4})
    if username:
        signals.append({"label": "Профиль", "value": "@" + username, "bad": False})
    elif row.get("author_id"):
        signals.append({"label": "Профиль", "value": "без username", "bad": False})
    if flags.get("scam"):
        signals.append({"label": "Telegram", "value": "пометка «мошенник»", "bad": True})
    elif flags.get("fake"):
        signals.append({"label": "Telegram", "value": "пометка «подделка»", "bad": True})
    elif flags.get("premium"):
        signals.append({"label": "Telegram", "value": "Premium", "bad": False})
    return {
        "name": row.get("author_name"),
        "username": username,
        "url": f"https://t.me/{username}" if username else None,
        "signals": signals[:4],
    }


def contact_block(row: dict) -> dict:
    ctype = row.get("contact_type") or "none"
    value = row.get("contact_value") or ""
    url = row.get("contact_url") or ""
    texts = {"telegram": "Написать в Telegram", "author": "Написать автору", "email": "Написать на почту", "phone": "Позвонить"}
    return {"type": ctype, "value": value, "url": url, "label": texts.get(ctype, "")}


def order_detail(db: Database, row: dict, profile: Profile, draft: dict | None, now: datetime | None = None) -> dict:
    card = order_card(row, profile, now)
    srcs = store.order_sources(db, row["id"])
    msg_link = (srcs[0]["link"] if srcs else None) or store.chat_link(row, row.get("tg_msg_id"))
    from .scoring import matched_keys  # noqa: PLC0415

    ov = OrderView(
        title=row["title"], summary="", text="", skills=row.get("skills") or [], budget_min=None, budget_max=None,
        currency="RUB", period="fixed", budget_rub_min=None, budget_rub_max=None, deadline_days=None, age_minutes=None,
    )
    card.update({
        "fit": row.get("fit") or [],
        "flags": flags_view(row),
        "rec": {"min": row.get("rec_min"), "max": row.get("rec_max"), "note": row.get("rec_note") or ""},
        "text": row.get("text") or "",
        "sources": [{"chat": s["chat_title"], "link": s["link"], "posted_at": s["posted_at"]} for s in srcs],
        "message_link": msg_link,
        "client": client_block(db, row),
        "contact": contact_block(row),
        "draft": draft,
        "matched": len(matched_keys(ov, profile)),
        "scored_by": row.get("scored_by"),
    })
    return card


def keys_of(row: dict) -> set[str]:
    ov = OrderView(
        title=row["title"], summary="", text="", skills=row.get("skills") or [], budget_min=None, budget_max=None,
        currency="RUB", period="fixed", budget_rub_min=None, budget_rub_max=None, deadline_days=None, age_minutes=None,
    )
    return order_keys(ov)


def flag_objs(row: dict) -> list[Flag]:
    return [Flag(f["code"], f["text"], f["level"]) for f in (row.get("red_flags") or []) if f.get("code")]


def to_json_safe(x: Any) -> Any:
    return x
