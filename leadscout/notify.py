"""Когда и что отправлять в Telegram: мгновенные сообщения, утренняя сводка, итоги недели."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from . import store
from .bot import BotService
from .config import Settings
from .db import Database
from .profile import load_notifications, load_profile
from .util import in_quiet_hours, iso, local_now, minutes_ago, now_utc, parse_iso

log = logging.getLogger("leadscout.notify")

FRESH_MINUTES = 240


class Notifier:
    def __init__(self, db: Database, settings: Settings, get_bot: Callable[[], BotService | None]):
        self.db = db
        self.settings = settings
        self.get_bot = get_bot

    def _bot(self) -> BotService | None:
        bot = self.get_bot()
        return bot if bot and bot.paired else None

    # ------------------------------------------------ после проверки ----
    async def after_scan(self, order_ids: list[int], now: datetime | None = None) -> int:
        """Мгновенные уведомления о самых подходящих свежих заказах. Возвращает, сколько отправили."""
        bot = self._bot()
        n = load_notifications(self.db)
        if bot is None or n.paused or not n.instant:
            return 0
        tz = load_profile(self.db).tz
        sent = 0
        for oid in order_ids:
            row = store.get_order(self.db, oid)
            if not row or row["verdict"] not in {"match", "maybe"} or row["score"] < n.instant_threshold:
                continue
            if row["state"] != "new" or row["feedback"] or row["alert_state"] != "none":
                continue
            age = minutes_ago(row["posted_at"], now)
            if age is None or age > FRESH_MINUTES:
                continue
            big = (row["budget_rub_max"] or row["budget_rub_min"] or 0) >= n.quiet_override_budget
            if n.quiet and in_quiet_hours(tz, n.quiet_start, n.quiet_end, now) and not big:
                self.db.run("UPDATE orders SET alert_state='queued' WHERE id=?", (oid,))
                continue
            try:
                await bot.send_card(row, "Новый заказ" if row["verdict"] == "match" else "Возможно, подойдёт")
            except Exception:
                log.warning("send failed for %s", oid, exc_info=True)
                continue
            self.db.run("UPDATE orders SET alert_state='sent', alerted_at=? WHERE id=?", (iso(), oid))
            sent += 1
        return sent

    # -------------------------------------------------------- сводка ----
    def _digest_rows(self, since_iso: str, limit: int = 15) -> list[dict]:
        return self.db.all(
            store.ORDER_SELECT + " WHERE o.verdict IN ('match','maybe') AND o.state='new' AND o.feedback IS NULL AND o.offered_at IS NULL "
            "AND o.alert_state NOT IN ('sent','digested') AND o.created_at>=? ORDER BY (o.verdict='match') DESC, o.score DESC, o.posted_at DESC LIMIT ?",
            (since_iso, limit),
        )

    async def send_digest(self, now: datetime | None = None) -> int:
        bot = self._bot()
        n = load_notifications(self.db)
        if bot is None or n.paused or not n.digest:
            return 0
        state = self.db.get_setting("notify_state", {})
        since = parse_iso(state.get("last_digest")) or ((now or now_utc()) - timedelta(hours=24))
        rows = self._digest_rows(iso(since))
        if not rows:
            state["last_digest"] = iso(now)
            self.db.set_setting("notify_state", state)
            return 0
        title = "Доброе утро! Вот что нашлось" if (local_now(load_profile(self.db).tz, now).hour < 12) else "Свежие заказы"
        rejected = self.db.scalar("SELECT COUNT(*) FROM orders WHERE verdict IN ('skip','scam') AND state!='hidden' AND created_at>=?", (iso(since),), 0)
        discarded = store.count_discarded(self.db)
        note = ""
        if rejected or discarded:
            note = f"Отсеяно: {rejected}, не похоже на заказ: {discarded}. Загляните в «Отсеянные» и «Отброшено», если боитесь что-то пропустить."
        await bot.send_digest(rows, f"{title}: {len(rows)}", note)
        state["last_digest"] = iso(now)  # отмечаем только после отправки: при сбое сводка уйдёт в следующий раз
        self.db.set_setting("notify_state", state)
        for r in rows:
            self.db.run("UPDATE orders SET alert_state='digested' WHERE id=? AND alert_state!='sent'", (r["id"],))
        return len(rows)

    def digest_due(self, now: datetime | None = None) -> bool:
        n = load_notifications(self.db)
        if not n.digest or n.paused:
            return False
        tz = load_profile(self.db).tz
        cur = local_now(tz, now)
        hh, mm = (int(x) for x in n.digest_time.split(":"))
        target = cur.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cur < target:
            return False
        last = parse_iso(self.db.get_setting("notify_state", {}).get("last_digest"))
        return last is None or local_now(tz, last).date() < cur.date()

    async def flush_queue(self, now: datetime | None = None) -> int:
        """Если сводка отключена, заказы, накопленные в тихие часы, отправляем поштучно после их окончания."""
        bot = self._bot()
        n = load_notifications(self.db)
        if bot is None or n.paused or n.digest:
            return 0
        tz = load_profile(self.db).tz
        if n.quiet and in_quiet_hours(tz, n.quiet_start, n.quiet_end, now):
            return 0
        rows = self.db.all(store.ORDER_SELECT + " WHERE o.alert_state='queued' AND o.state='new' AND o.feedback IS NULL ORDER BY o.score DESC LIMIT 5")
        for r in rows:
            await bot.send_card(r, "Заказ, пока вы отдыхали")
            self.db.run("UPDATE orders SET alert_state='sent', alerted_at=? WHERE id=?", (iso(), r["id"]))
        return len(rows)

    # ------------------------------------------------------ итоги недели ----
    def weekly_due(self, now: datetime | None = None) -> bool:
        n = load_notifications(self.db)
        if not n.weekly or n.paused:
            return False
        tz = load_profile(self.db).tz
        cur = local_now(tz, now)
        if cur.weekday() != 6 or cur.hour < 19:
            return False
        last = parse_iso(self.db.get_setting("notify_state", {}).get("last_weekly"))
        return last is None or (cur - local_now(tz, last)) > timedelta(days=5)

    async def send_weekly(self, now: datetime | None = None) -> None:
        bot = self._bot()
        if bot is None:
            return
        state = self.db.get_setting("notify_state", {})
        state["last_weekly"] = iso(now)
        self.db.set_setting("notify_state", state)
        await bot.cmd_stats()
