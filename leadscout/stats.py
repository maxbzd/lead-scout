"""Цифры для вкладки «Аналитика». Всё считается из вашей базы: ничего не придумано."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from . import learning, store
from .db import Database
from .util import fmt_money, get_tz, iso, local_now, now_utc, parse_iso, start_of_local_day

WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _bucket_days(db: Database, sql: str, args: tuple, tz: str, days: int, now: datetime) -> list[int]:
    zone = get_tz(tz)
    today = start_of_local_day(tz, now)
    counts: Counter[int] = Counter()
    for r in db.all(sql, args):
        dt = parse_iso(r["ts"])
        if dt is None:
            continue
        d = (today.date() - dt.astimezone(zone).date()).days
        if 0 <= d < days:
            counts[d] += 1
    return [counts.get(days - 1 - i, 0) for i in range(days)]


def _bucket_sums(db: Database, sql: str, args: tuple, tz: str, days: int, now: datetime) -> list[int]:
    zone = get_tz(tz)
    today = start_of_local_day(tz, now)
    sums: Counter[int] = Counter()
    for r in db.all(sql, args):
        dt = parse_iso(r["ts"])
        if dt is None or r["v"] is None:
            continue
        d = (today.date() - dt.astimezone(zone).date()).days
        if 0 <= d < days:
            sums[d] += int(r["v"])
    return [sums.get(days - 1 - i, 0) for i in range(days)]


def build(db: Database, tz: str, now: datetime | None = None) -> dict:
    now = now or now_utc()
    since7 = iso(now - timedelta(days=7))
    since14 = iso(now - timedelta(days=14))
    since30 = iso(now - timedelta(days=30))
    good = "verdict IN ('match','maybe')"

    read_week = db.scalar("SELECT COUNT(*) FROM messages WHERE posted_at>=?", (since7,), 0)
    selected_week = db.scalar(f"SELECT COUNT(*) FROM orders WHERE {good} AND created_at>=?", (since7,), 0)
    saved = db.scalar("SELECT COUNT(*) FROM orders WHERE state='saved'", default=0)
    offers_week = db.scalar("SELECT COUNT(*) FROM orders WHERE offered_at>=?", (since7,), 0)
    answered = db.scalar("SELECT COUNT(*) FROM orders WHERE stage IN ('ans','work') AND offered_at>=?", (since7,), 0)
    work_n = db.scalar("SELECT COUNT(*) FROM orders WHERE stage='work'", default=0)
    work_sum = db.scalar("SELECT SUM(COALESCE(won_amount, price_offered)) FROM orders WHERE stage='work'", default=0) or 0
    feed_sum = db.scalar(
        f"SELECT SUM(COALESCE(budget_rub_max, budget_rub_min)) FROM orders WHERE {good} AND state!='hidden' "
        "AND offered_at IS NULL AND posted_at>=? AND period='fixed'", (since7,), 0,
    ) or 0

    spark_sel = _bucket_days(db, f"SELECT created_at AS ts FROM orders WHERE {good} AND created_at>=?", (since14,), tz, 12, now)
    spark_off = _bucket_days(db, "SELECT offered_at AS ts FROM orders WHERE offered_at>=?", (since14,), tz, 12, now)
    spark_work = _bucket_days(db, "SELECT stage_at AS ts FROM orders WHERE stage='work' AND stage_at>=?", (since14,), tz, 12, now)
    spark_sum = _bucket_sums(
        db, f"SELECT created_at AS ts, COALESCE(budget_rub_max, budget_rub_min) AS v FROM orders WHERE {good} AND created_at>=? "
        "AND period='fixed'", (since14,), tz, 12, now)

    tiles = [
        {"id": "feed", "label": "Бюджет на ленте", "value": fmt_money(feed_sum), "note": "в доступных заказах за неделю", "spark": spark_sum},
        {"id": "selected", "label": "Отобрано", "value": str(selected_week), "note": f"из {read_week} за неделю", "spark": spark_sel},
        {"id": "offers", "label": "Предложений", "value": str(offers_week), "note": "ваших за неделю", "spark": spark_off},
        {"id": "work", "label": "Взято в работу", "value": str(work_n), "note": f"на {fmt_money(work_sum)}" if work_n else "пока ни одного", "spark": spark_work},
    ]

    # отобрано по дням (последние 7)
    days = _bucket_days(db, f"SELECT created_at AS ts FROM orders WHERE {good} AND created_at>=?", (since14,), tz, 7, now)
    today = local_now(tz, now)
    labels = [WD[(today - timedelta(days=6 - i)).weekday()] for i in range(7)]

    # когда появляются заказы
    zone = get_tz(tz)
    hours = [0] * 24
    for r in db.all(f"SELECT posted_at AS ts FROM orders WHERE {good} AND posted_at>=?", (since30,)):
        dt = parse_iso(r["ts"])
        if dt:
            hours[dt.astimezone(zone).hour] += 1
    top_hours = sorted(range(24), key=lambda h: -hours[h])[:3] if sum(hours) >= 8 else []

    funnel = [
        {"label": "Прочитано", "value": read_week},
        {"label": "Отобрано", "value": selected_week},
        {"label": "Отложено", "value": saved},
        {"label": "Предложения", "value": offers_week},
        {"label": "Ответы", "value": answered},
        {"label": "В работу", "value": work_n},
    ]

    chats = [
        {"title": c["title"], "share": c["share"], "found": c["found"], "enabled": bool(c["enabled"]), "error": c["last_error"]}
        for c in store.list_chats(db)
    ]
    chats.sort(key=lambda c: -c["share"])

    acc = learning.accuracy(db, 6, now)
    usage = db.get_setting("usage:" + now.strftime("%Y-%m"), None)
    return {
        "tiles": tiles,
        "days": {"values": days, "labels": labels},
        "hours": {"values": hours, "top": top_hours, "total": sum(hours)},
        "funnel": funnel,
        "chats": chats,
        "accuracy": acc,
        "learned": db.scalar("SELECT COUNT(*) FROM feedback_events", default=0),
        "log": learning.log_lines(db),
        "usage": usage,
    }
