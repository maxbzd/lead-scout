"""Пауза ИИ: ручная и автоматическая (когда в ленте накопилось много неразобранных заказов).

Пауза останавливает только фоновую работу ИИ: разбор и оценку новых заказов. Бот продолжает читать чаты и
оценивать правилами, а черновики по вашему нажатию пишутся как обычно.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from .db import Database
from .util import iso, now_utc

DEFAULT: dict[str, Any] = {"paused": False, "auto": True, "limit": 40}
LIMITS = (20, 40, 80, 150)

BACKLOG_SQL = (
    "SELECT COUNT(*) FROM orders WHERE verdict IN ('match','maybe') AND state='new' AND feedback IS NULL AND offered_at IS NULL "
    "AND created_at>=?"
)  # считаем только последнюю неделю: давнее забытое не должно держать ИИ на паузе вечно


def load(db: Database) -> dict[str, Any]:
    cur = db.get_setting("ai_control") or {}
    out = {**DEFAULT, **{k: cur[k] for k in DEFAULT if k in cur}}
    out["limit"] = max(5, min(500, int(out["limit"])))
    return out


def save(db: Database, patch: dict[str, Any]) -> dict[str, Any]:
    cur = load(db)
    for k in DEFAULT:
        if patch.get(k) is not None:
            cur[k] = patch[k]
    cur["paused"], cur["auto"] = bool(cur["paused"]), bool(cur["auto"])
    cur["limit"] = max(5, min(500, int(cur["limit"])))
    db.set_setting("ai_control", cur)
    return cur


def backlog(db: Database) -> int:
    return int(db.scalar(BACKLOG_SQL, (iso(now_utc() - timedelta(days=7)),), default=0) or 0)


def state(db: Database) -> dict[str, Any]:
    c = load(db)
    n = backlog(db)
    reason = "manual" if c["paused"] else "backlog" if (c["auto"] and n >= c["limit"]) else None
    return {**c, "backlog": n, "active": reason is None, "reason": reason}
