"""Путь сообщения: прочитали, поняли, отсеяли дубли, оценили, сохранили."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from . import extract as ex
from . import learning, store
from .config import Settings
from .db import Database, dumps
from .llm.base import LLM
from .llm.rules import RuleLLM, rule_extract
from .models import Extraction, OrderView, RawMessage, Score
from .profile import Profile, load_profile
from .scoring import finalize, recommend_price
from .taxonomy import profile_keys
from .util import iso, minutes_ago, now_utc

log = logging.getLogger("leadscout.pipeline")

DUP_THRESHOLD = 0.72
SAME_AUTHOR_THRESHOLD = 0.45
SAME_CHAT_THRESHOLD = 0.85  # в одном чате похожий текст чаще всего другая задача, а не повтор
TONE_BY_NAME = {"short": "short", "warm": "warm", "formal": "formal"}


class Pipeline:
    def __init__(self, db: Database, llm: LLM, settings: Settings):
        self.db = db
        self.llm = llm
        self.settings = settings
        self._lock = asyncio.Lock()
        self._rules = RuleLLM()
        self._inflight: set[tuple[int, int]] = set()

    def set_llm(self, llm: LLM) -> None:
        self.llm = llm

    # ----------------------------------------------------------- вход ----
    async def process(self, raw: RawMessage) -> int | None:
        """Возвращает номер нового заказа или None."""
        text = (raw.text or "").strip()
        if not text:
            return None
        key = (raw.chat_id, raw.tg_msg_id)
        if key in self._inflight:
            return None  # то же сообщение уже обрабатывается (пришло и вживую, и при проверке)
        mid, inserted = store.insert_message(self.db, raw)
        if not inserted and not store.is_unprocessed(self.db, mid):
            return None
        self._inflight.add(key)
        try:
            return await self._process(raw, text, mid)
        finally:
            self._inflight.discard(key)

    async def retry_stuck(self, limit: int = 40) -> int:
        """Сообщения, которые записались, но не успели обработаться (сбой или остановка): пробуем ещё раз."""
        n = 0
        for raw in store.unprocessed_messages(self.db, limit):
            try:
                if await self.process(raw):
                    n += 1
            except Exception:
                log.exception("retry failed")
        return n

    async def promote(self, message_id: int) -> int | None:
        """«Это заказ»: берём отброшенное сообщение и обрабатываем его без отбора."""
        row = self.db.one("SELECT * FROM messages WHERE id=?", (message_id,))
        if not row or self.db.one("SELECT 1 FROM orders WHERE message_id=?", (message_id,)):
            return None
        store.set_message_status(self.db, message_id, "new")
        raw = store.raw_from_row(row)
        raw.reply_to = None
        return await self._process(raw, (raw.text or "").strip(), message_id, force=True)

    async def _process(self, raw: RawMessage, text: str, mid: int, force: bool = False) -> int | None:
        chat = self.db.one("SELECT * FROM chats WHERE id=?", (raw.chat_id,)) or {}
        if raw.reply_to and self._handle_reply(raw, mid):
            return None
        strict = self._is_candidate(text)
        lenient = force or (not strict and self._recall() and ex.maybe_order(text))
        if not strict and not lenient:
            store.set_message_status(self.db, mid, "noise")
            return None
        if self._attach_duplicate(raw, mid, text, chat):
            return None

        hint = rule_extract(text, raw.author_username, raw.author_flags)
        try:
            ext = await self.llm.extract(text, hint)
        except Exception:  # любая неожиданность: берём то, что поняли правилами
            log.exception("extract failed, using rules")
            ext = hint
        if not ext.is_order:
            # правила сочли это заказом, а нейросеть нет (или заказ без явных слов): в режиме «ничего не пропускать» показываем с пометкой
            if force or (self._recall() and (hint.is_order or (self.llm.name != "claude" and lenient))):
                ext.is_order = True
                ext.flags = [*ext.flags, ex.Flag("doubt", "Не уверен, что это заказ: похоже по формулировке", "low")]
            else:
                store.set_message_status(self.db, mid, "noise")
                return None

        profile = load_profile(self.db)
        view = self._view(ext, text, raw, chat)
        score = await self._score(view, profile, ext)

        async with self._lock:
            # пока думали, мог прийти такой же заказ из другого чата
            if self._attach_duplicate(raw, mid, text, chat):
                return None
            order_id = self._save(mid, raw, ext, view, score, profile, chat)
        await self._pre_draft(order_id, view, profile, score)
        return order_id

    # ------------------------------------------------------- проверки ----
    def _recall(self) -> bool:
        return load_profile(self.db).recall

    def _is_candidate(self, text: str) -> bool:
        short = 20 if self._recall() else 40
        if len(ex.squash(text)) < short:
            return False
        if self.llm.name == "claude":
            pos, seeker, ads = ex.order_signals(text)
            return pos >= 1 and not (ads >= 2 and pos < 3) and not (seeker >= 2 and pos < 3)
        return ex.looks_like_order(text)

    def _handle_reply(self, raw: RawMessage, mid: int) -> bool:
        """Ответ на чужое объявление считаем откликом конкурента."""
        parent = self.db.one("SELECT id, author_id FROM messages WHERE chat_id=? AND tg_msg_id=?", (raw.chat_id, raw.reply_to))
        if not parent:
            return False
        order = store.order_by_message(self.db, parent["id"])
        if not order:
            return False
        store.set_message_status(self.db, mid, "reply")
        if raw.author_id and parent.get("author_id") and raw.author_id == parent["author_id"]:
            return True  # заказчик уточняет, это не отклик
        if len(ex.squash(raw.text)) >= 6:
            store.bump_bids(self.db, order["id"])
            self.refresh_rec(order["id"])
        return True

    def refresh_rec(self, order_id: int) -> None:
        """Откликов стало больше: подсказку по цене пересчитываем."""
        row = self.db.one(store.ORDER_SELECT + " WHERE o.id=?", (order_id,))
        if not row:
            return
        profile = load_profile(self.db)
        lo, hi, note = recommend_price(self.view_of(row), profile, store.won_amounts(self.db, profile_keys(profile.skills)))
        self.db.run("UPDATE orders SET rec_min=?, rec_max=?, rec_note=? WHERE id=?", (lo, hi, note, order_id))

    def _attach_duplicate(self, raw: RawMessage, mid: int, text: str, chat: dict) -> bool:
        h = ex.content_hash(text)
        for o in store.recent_orders(self.db):
            sim = ex.similarity(o["text"], text)
            same_author = bool(raw.author_id) and o.get("author_id") == raw.author_id
            # один и тот же человек публикует заказ в нескольких чатах, часто чуть переписав текст
            if o.get("chat_id") == raw.chat_id:
                is_dup = ex.content_hash(o["text"]) == h or sim >= SAME_CHAT_THRESHOLD
            else:
                is_dup = ex.content_hash(o["text"]) == h or sim >= DUP_THRESHOLD or (same_author and sim >= SAME_AUTHOR_THRESHOLD)
            if is_dup:
                store.add_source(self.db, o["id"], raw.chat_id, mid, store.chat_link(chat, raw.tg_msg_id), iso(raw.posted_at))
                self.db.run("UPDATE orders SET dup_count=dup_count+1 WHERE id=?", (o["id"],))
                store.set_message_status(self.db, mid, "dup")
                return True
        return False

    # ---------------------------------------------------------- оценка ----
    def _view(self, ext: Extraction, text: str, raw: RawMessage, chat: dict) -> OrderView:
        s = self.settings
        lo = ex.to_rub(ext.budget_min, ext.currency, s.usd_rub, s.eur_rub)
        hi = ex.to_rub(ext.budget_max, ext.currency, s.usd_rub, s.eur_rub)
        if lo is None and hi is not None:
            lo = hi
        return OrderView(
            title=ext.title, summary=ext.summary, text=text, skills=list(ext.skills),
            budget_min=ext.budget_min, budget_max=ext.budget_max, currency=ext.currency, period=ext.period,
            budget_rub_min=lo, budget_rub_max=hi, deadline_days=ext.deadline_days,
            age_minutes=minutes_ago(iso(raw.posted_at)), bids=raw.replies_count or 0, flags=list(ext.flags),
            source=chat.get("title", ""),
        )

    async def _score(self, view: OrderView, profile: Profile, ext: Extraction) -> Score:
        """Явный мусор отсеиваем правилами и не тратим на него нейросеть."""
        probe = finalize(Score(score=60, verdict="maybe", why="", scored_by="rules"), view, profile, view.flags)
        if probe.verdict in {"scam", "skip"}:
            return probe
        learned = learning.learned(self.db)
        try:
            return await self.llm.score(view, profile, learned)
        except Exception:  # ИИ упал неожиданно: не теряем заказ
            log.exception("score failed, using rules")
            return await self._rules.score(view, profile, learned)

    def _save(self, mid: int, raw: RawMessage, ext: Extraction, view: OrderView, score: Score,
              profile: Profile, chat: dict) -> int:
        won = store.won_amounts(self.db, profile_keys(profile.skills) | set(view.skills))
        rec_lo, rec_hi, note = recommend_price(view, profile, won)
        flags = [f.as_dict() for f in score.flags]
        order_id = self.db.run(
            "INSERT INTO orders(message_id,title,summary,budget_min,budget_max,currency,period,budget_rub_min,budget_rub_max,"
            "deadline_days,skills,contact_type,contact_value,contact_url,score,verdict,why,fit,red_flags,rec_min,rec_max,rec_note,"
            "bids,posted_at,created_at,scored_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, view.title, view.summary, view.budget_min, view.budget_max, view.currency, view.period,
             view.budget_rub_min, view.budget_rub_max, view.deadline_days, dumps(view.skills), ext.contact.type,
             ext.contact.value, ext.contact.url, score.score, score.verdict, score.why, dumps(score.fit), dumps(flags),
             rec_lo, rec_hi, note, view.bids, iso(raw.posted_at), iso(), score.scored_by),
        )
        store.add_source(self.db, order_id, raw.chat_id, mid, store.chat_link(chat, raw.tg_msg_id), iso(raw.posted_at))
        store.set_message_status(self.db, mid, "order")
        return order_id

    async def _pre_draft(self, order_id: int, view: OrderView, profile: Profile, score: Score) -> None:
        if score.verdict != "match":
            return
        try:
            await self.make_draft(order_id, profile.tone, 0, view=view, profile=profile)
        except Exception:
            log.warning("pre-draft failed for %s", order_id, exc_info=True)

    # ------------------------------------------------------- черновики ----
    def view_of(self, row: dict) -> OrderView:
        flags = [ex.Flag(f["code"], f["text"], f["level"]) for f in (row.get("red_flags") or []) if f.get("code")]
        return OrderView(
            title=row["title"], summary=row.get("summary") or "", text=row.get("text") or "", skills=row.get("skills") or [],
            budget_min=row["budget_min"], budget_max=row["budget_max"], currency=row["currency"] or "RUB",
            period=row["period"] or "fixed", budget_rub_min=row["budget_rub_min"], budget_rub_max=row["budget_rub_max"],
            deadline_days=row["deadline_days"], age_minutes=minutes_ago(row["posted_at"]), bids=row["bids"], flags=flags,
            source=row.get("chat_title", ""),
        )

    def default_price(self, row: dict) -> int | None:
        lo, hi = row.get("rec_min"), row.get("rec_max")
        if lo is None or hi is None:
            return None
        mid = (lo + hi) / 2
        step = 1000 if mid >= 10000 else 500
        return int(round(mid / step) * step)

    async def make_draft(self, order_id: int, tone: str, variant: int, *, view: OrderView | None = None,
                         profile: Profile | None = None, price: int | None = None, force: bool = False) -> dict:
        tone = TONE_BY_NAME.get(tone, "warm")
        row = store.get_order(self.db, order_id)
        if row is None:
            raise KeyError(order_id)
        if not force:
            cached = store.get_draft(self.db, order_id, tone, variant)
            if cached and (price is None or cached["price"] == price):
                return {"text": cached["text"], "price": cached["price"], "tone": tone, "variant": variant}
        profile = profile or load_profile(self.db)
        view = view or self.view_of(row)
        price = price if price is not None else self.default_price(row)
        text = await self.llm.draft(view, profile, tone, variant, price)
        store.save_draft(self.db, order_id, tone, variant, price, text)
        return {"text": text, "price": price, "tone": tone, "variant": variant}

    # --------------------------------------------------- пересчёт ----
    async def rescore_recent(self, hours: int = 72, limit: int = 60, use_llm: bool = False) -> int:
        """Применяет новые настройки профиля к недавним заказам без оценок от вас."""
        profile = load_profile(self.db)
        since = iso(now_utc() - timedelta(hours=hours))
        rows = self.db.all(
            store.ORDER_SELECT + " WHERE o.created_at>=? AND o.feedback IS NULL AND o.stage IS NULL AND o.state!='hidden' "
            "ORDER BY o.id DESC LIMIT ?", (since, limit),
        )
        learned = learning.learned(self.db)
        n = 0
        for row in rows:
            view = self.view_of(row)
            if use_llm or row["scored_by"] != "claude":
                engine: LLM = self.llm if use_llm else self._rules
                score = await engine.score(view, profile, learned)
            else:
                # оценка от ИИ остаётся; меняем её, только если заказ теперь нарушает жёсткие правила
                old = Score(score=row["score"], verdict=row["verdict"] if row["verdict"] in {"match", "maybe"} else "maybe",
                            why=row["why"] or "", fit=list(row["fit"] or []), flags=[], scored_by="claude")
                score = finalize(old, view, profile, view.flags)
                if score.verdict not in {"skip", "scam"} or row["verdict"] in {"skip", "scam"}:
                    continue
            lo, hi, note = recommend_price(view, profile, store.won_amounts(self.db, profile_keys(profile.skills)))
            self.db.run(
                "UPDATE orders SET score=?, verdict=?, why=?, fit=?, red_flags=?, rec_min=?, rec_max=?, rec_note=?, scored_by=? WHERE id=?",
                (score.score, score.verdict, score.why, dumps(score.fit), dumps([f.as_dict() for f in score.flags]),
                 lo, hi, note, score.scored_by, row["id"]),
            )
            n += 1
        return n
