"""Мелкие общие помощники: время, деньги, тексты."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    return (dt or now_utc()).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def minutes_ago(value: str | None, now: datetime | None = None) -> int | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    return max(0, int(((now or now_utc()) - dt).total_seconds() // 60))


def get_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def local_now(tz_name: str, now: datetime | None = None) -> datetime:
    return (now or now_utc()).astimezone(get_tz(tz_name))


def start_of_local_day(tz_name: str, now: datetime | None = None) -> datetime:
    ln = local_now(tz_name, now)
    return ln.replace(hour=0, minute=0, second=0, microsecond=0)


def in_quiet_hours(tz_name: str, start: str, end: str, now: datetime | None = None) -> bool:
    """Тихие часы вида 23:00 - 08:00 (могут переходить через полночь)."""
    try:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except ValueError:
        return False
    cur = local_now(tz_name, now)
    mins = cur.hour * 60 + cur.minute
    a, b = sh * 60 + sm, eh * 60 + em
    if a == b:
        return False
    return (a <= mins < b) if a < b else (mins >= a or mins < b)


def fmt_money(n: float | int | None) -> str:
    if n is None:
        return ""
    return f"{int(round(n)):,}".replace(",", " ") + " ₽"


def fmt_age(minutes: int | None) -> str:
    if minutes is None:
        return ""
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч" + (f" {rest} мин" if rest and hours < 6 else "")
    days = hours // 24
    return f"{days} дн"


_WS = re.compile(r"\s+")


def squash(text: str) -> str:
    return _WS.sub(" ", text).strip()


def clip(text: str, limit: int) -> str:
    text = squash(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def week_start(dt: datetime) -> datetime:
    d = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=d.weekday())
