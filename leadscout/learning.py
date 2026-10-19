"""Самообучение: ваши оценки превращаются в примеры для ИИ, веса навыков и предложения по настройкам."""

from __future__ import annotations

import statistics
from datetime import timedelta

from . import store
from .db import Database
from .models import Example, Learned
from .profile import Profile
from .scoring import order_keys
from .taxonomy import labels
from .util import fmt_money, iso, now_utc, parse_iso, week_start

UP_WEIGHT = 1.5
DOWN_WEIGHT = -2.0


def _view_keys(row: dict) -> set[str]:
    from .models import OrderView

    ov = OrderView(
        title=row["title"], summary=row.get("summary") or "", text="", skills=row.get("skills") or [],
        budget_min=row["budget_min"], budget_max=row["budget_max"], currency=row["currency"] or "RUB",
        period=row["period"] or "fixed", budget_rub_min=row["budget_rub_min"], budget_rub_max=row["budget_rub_max"],
        deadline_days=row["deadline_days"], age_minutes=None,
    )
    return order_keys(ov)


def budget_text(row: dict) -> str:
    lo, hi = row.get("budget_rub_min"), row.get("budget_rub_max")
    if lo is None and hi is None:
        return "бюджет не указан"
    return fmt_money(hi if hi is not None else lo)


def learned(db: Database, limit: int = 14) -> Learned:
    rows = db.all(store.ORDER_SELECT + " WHERE o.feedback IS NOT NULL ORDER BY o.feedback_at DESC LIMIT 200")
    out = Learned()
    weights: dict[str, float] = {}
    chat_w: dict[int, float] = {}
    for i, r in enumerate(rows):
        up = r["feedback"] == "up"
        w = UP_WEIGHT if up else DOWN_WEIGHT
        decay = 1.0 if i < 40 else 0.5
        # «дёшево», «не те сроки», «подозрительно» не значит «навык не мой»: такие 👎 навыки не штрафуют
        if up or (r.get("feedback_reason") or "other") in {"profile", "other"}:
            for k in _view_keys(r):
                weights[k] = max(-6.0, min(6.0, weights.get(k, 0.0) + w * decay))
        chat_w[r["chat_id"]] = chat_w.get(r["chat_id"], 0.0) + (1 if up else -1)
        if i < limit:
            reason = store.FEEDBACK_REASONS.get(r.get("feedback_reason") or "", "")
            out.examples.append(Example(
                title=r["title"], summary=(r.get("summary") or "")[:160], skills=labels(sorted(_view_keys(r)))[:5],
                budget=budget_text(r), liked=up, reason=reason,
            ))
    out.skill_weights = weights
    out.chat_weights = chat_w
    return out


# ------------------------------------------------------------ точность ----


def accuracy(db: Database, weeks: int = 6, now=None) -> dict:
    """Доля 👍 среди оценённых заказов, которые бот считал подходящими (от 70). Считается по неделям."""
    now = now or now_utc()
    start = week_start(now) - timedelta(weeks=weeks - 1)
    rows = db.all(
        "SELECT kind, score_at, created_at FROM feedback_events WHERE created_at>=? AND score_at IS NOT NULL",
        (iso(start),),
    )
    buckets: dict[int, list[int]] = {i: [0, 0] for i in range(weeks)}
    for r in rows:
        dt = parse_iso(r["created_at"])
        if dt is None or (r["score_at"] or 0) < 70:
            continue
        idx = int((week_start(dt) - start).days // 7)
        if 0 <= idx < weeks:
            buckets[idx][0 if r["kind"] == "up" else 1] += 1
    series = []
    for i in range(weeks):
        up, down = buckets[i]
        n = up + down
        series.append({"week": i + 1, "value": round(100 * up / n) if n >= 3 else None, "n": n})
    total_up = sum(b[0] for b in buckets.values())
    total_n = sum(b[0] + b[1] for b in buckets.values())
    return {
        "series": series,
        "current": round(100 * total_up / total_n) if total_n >= 5 else None,
        "n": total_n,
    }


# -------------------------------------------------- предложения и журнал ----


def refresh_suggestions(db: Database, profile: Profile) -> int:
    """Ищет закономерности в ваших оценках и предлагает поправки. Ничего не меняет без вашего согласия."""
    created = 0
    since = iso(now_utc() - timedelta(days=30))

    def has_open(kind: str, key: str) -> bool:
        rows = db.all("SELECT payload FROM insights WHERE kind=? AND status IN ('open','dismissed')", (kind,))
        return any((r["payload"] or {}).get("key") == key for r in rows)

    # 1. «Низкий бюджет» несколько раз подряд: поднять минимум
    low = db.all(
        "SELECT budget_rub_max AS b FROM orders WHERE feedback='down' AND feedback_reason='budget' AND feedback_at>=? "
        "AND budget_rub_max IS NOT NULL AND period='fixed'", (since,),
    )
    if len(low) >= 3:
        top = sorted(r["b"] for r in low)[int(len(low) * 0.75) - 1]
        new_min = int(-(-(top + 1) // 5000) * 5000)  # строго выше этих заказов, иначе порог их не отсечёт
        if new_min >= profile.min_budget + 2500:
            key = f"min:{new_min}"
            if not has_open("raise_min", key):
                store.add_insight(
                    db, "raise_min",
                    f"Вы {len(low)} {'раза' if len(low) % 10 in (2, 3, 4) and not 11 <= len(low) % 100 <= 14 else 'раз'} отметили заказы как «Низкий бюджет». Поднять минимум до {fmt_money(new_min)}?",
                    {"key": key, "min_budget": new_min},
                )
                created += 1

    # 2. Чат, который только огорчает
    rows = db.all(
        "SELECT m.chat_id AS cid, c.title AS title, "
        "SUM(CASE WHEN o.feedback='down' THEN 1 ELSE 0 END) AS d, SUM(CASE WHEN o.feedback='up' THEN 1 ELSE 0 END) AS u "
        "FROM orders o JOIN messages m ON m.id=o.message_id JOIN chats c ON c.id=m.chat_id "
        "WHERE o.feedback_at>=? AND c.enabled=1 GROUP BY m.chat_id", (since,),
    )
    for r in rows:
        if r["d"] >= 5 and r["u"] == 0:
            key = f"mute:{r['cid']}"
            if not has_open("mute_chat", key):
                store.add_insight(
                    db, "mute_chat",
                    f"Из чата «{r['title']}» вам не подошло {r['d']} заказов, и ни одного нужного. Отключить его?",
                    {"key": key, "chat_id": r["cid"]},
                )
                created += 1
    return created


def open_suggestions(db: Database) -> list[dict]:
    return db.all("SELECT * FROM insights WHERE status='open' AND kind IN ('raise_min','mute_chat') ORDER BY id DESC LIMIT 5")


def resolve_suggestion(db: Database, sid: int, accept: bool, profile: Profile) -> dict | None:
    row = db.one("SELECT * FROM insights WHERE id=? AND status='open'", (sid,))
    if not row:
        return None
    payload = row["payload"] or {}
    action = None
    if accept:
        if row["kind"] == "raise_min":
            action = {"min_budget": int(payload["min_budget"])}
        elif row["kind"] == "mute_chat":
            db.run("UPDATE chats SET enabled=0 WHERE id=?", (payload["chat_id"],))
            action = {"chat_id": payload["chat_id"], "enabled": False}
    db.run("UPDATE insights SET status=? WHERE id=?", ("accepted" if accept else "dismissed", sid))
    return action or {}


def log_lines(db: Database) -> list[dict]:
    """Короткий рассказ о том, чему бот научился. Только факты из ваших оценок."""
    out: list[dict] = []
    since = iso(now_utc() - timedelta(days=7))
    reasons = db.all(
        "SELECT feedback_reason AS r, COUNT(*) AS n FROM orders WHERE feedback='down' AND feedback_at>=? AND feedback_reason IS NOT NULL "
        "GROUP BY feedback_reason ORDER BY n DESC", (since,),
    )
    for r in reasons[:2]:
        name = store.FEEDBACK_REASONS.get(r["r"], r["r"])
        out.append({"when": "неделя", "text": f"Вы отметили {r['n']} {_plural(r['n'])} «{name.lower()}». Учитываю при подборе."})
    ups = db.all(store.ORDER_SELECT + " WHERE o.feedback='up' AND o.feedback_at>=? ORDER BY o.feedback_at DESC LIMIT 40", (since,))
    if ups:
        counts: dict[str, int] = {}
        for r in ups:
            for k in _view_keys(r):
                counts[k] = counts.get(k, 0) + 1
        top = [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:2]]
        if top:
            out.append({"when": "неделя", "text": f"Вам подходят заказы про {', '.join(labels(top))}. Такие показываю выше."})
    caught = db.scalar("SELECT COUNT(*) FROM orders WHERE verdict='scam' AND created_at>=?", (since,), 0)
    if caught:
        out.append({"when": "неделя", "text": f"Отсеяла {caught} {_plural(caught)} с признаками обмана."})
    skipped = db.scalar("SELECT COUNT(*) FROM orders WHERE verdict='skip' AND created_at>=?", (since,), 0)
    if skipped:
        out.append({"when": "неделя", "text": f"Пропустила {skipped} {_plural(skipped)}: не по вашему профилю или ниже минимума."})
    if not out:
        out.append({"when": "сейчас", "text": "Пока мало оценок. Ставьте 👍 и 👎 под заказами, и подбор станет точнее."})
    return out[:4]


def _plural(n: int) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return "заказов"
    n %= 10
    return "заказ" if n == 1 else "заказа" if 2 <= n <= 4 else "заказов"


def median_budget(values: list[float]) -> float | None:
    return statistics.median(values) if values else None
