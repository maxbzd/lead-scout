"""Работа с данными: чаты, сообщения, заказы, оценки. Только SQL и простые преобразования."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .db import Database, dumps
from .extract import content_hash
from .models import RawMessage
from .util import iso, now_utc, parse_iso

# ------------------------------------------------------------------ чаты ----


def parse_chat_ref(ref: str) -> dict:
    """Из ссылки или @имени получаем username или invite-хеш."""
    r = ref.strip()
    m = re.search(r"(?:https?://)?t\.me/\+([\w-]+)", r) or re.search(r"(?:https?://)?t\.me/joinchat/([\w-]+)", r)
    if m:
        return {"kind": "invite", "hash": m.group(1), "link": f"https://t.me/+{m.group(1)}"}
    m = re.search(r"(?:https?://)?t\.me/([A-Za-z][\w]{3,31})(?:/\d+)?/?$", r)
    if m:
        u = m.group(1)
        return {"kind": "public", "username": u, "link": f"https://t.me/{u}"}
    m = re.fullmatch(r"@?([A-Za-z][\w]{3,31})", r)
    if m:
        u = m.group(1)
        return {"kind": "public", "username": u, "link": f"https://t.me/{u}"}
    return {"kind": "bad"}


def add_chat(db: Database, title: str, *, link: str | None = None, username: str | None = None,
             tg_id: int | None = None, members: int | None = None, is_demo: bool = False,
             enabled: bool = True) -> int:
    if tg_id is not None:
        row = db.one("SELECT id FROM chats WHERE tg_id=?", (tg_id,))
        if row:
            db.run("UPDATE chats SET enabled=1 WHERE id=?", (row["id"],))
            return row["id"]
    if username:
        row = db.one("SELECT id FROM chats WHERE lower(username)=lower(?)", (username,))
        if row:
            db.run("UPDATE chats SET enabled=1 WHERE id=?", (row["id"],))
            return row["id"]
    return db.run(
        "INSERT INTO chats(tg_id,username,link,title,members,enabled,is_demo,added_at) VALUES(?,?,?,?,?,?,?,?)",
        (tg_id, username, link, title, members, int(enabled), int(is_demo), iso()),
    )


def list_chats(db: Database) -> list[dict]:
    since = iso(now_utc() - timedelta(days=30))
    rows = db.all("SELECT * FROM chats ORDER BY enabled DESC, added_at")
    total = db.scalar(
        "SELECT COUNT(*) FROM orders o JOIN messages m ON m.id=o.message_id WHERE o.verdict IN ('match','maybe') AND o.posted_at>=?",
        (since,), 0,
    )
    for c in rows:
        n = db.scalar(
            "SELECT COUNT(*) FROM orders o JOIN messages m ON m.id=o.message_id "
            "WHERE m.chat_id=? AND o.verdict IN ('match','maybe') AND o.posted_at>=?",
            (c["id"], since), 0,
        )
        c["found"] = n
        c["share"] = round(100 * n / total) if total else 0
    return rows


def chat_link(chat: dict | None, tg_msg_id: int | None) -> str | None:
    if not chat or not tg_msg_id:
        return None
    u = chat.get("username") or chat.get("chat_username")
    if u:
        return f"https://t.me/{u}/{tg_msg_id}"
    tg = chat.get("tg_id") or chat.get("chat_tg")
    if tg:
        s = str(abs(int(tg)))
        if s.startswith("100") and len(s) > 10:
            s = s[3:]
        return f"https://t.me/c/{s}/{tg_msg_id}"
    return None


# ------------------------------------------------------------- сообщения ----


def insert_message(db: Database, raw: RawMessage) -> tuple[int, bool]:
    existing = db.one("SELECT id FROM messages WHERE chat_id=? AND tg_msg_id=?", (raw.chat_id, raw.tg_msg_id))
    if existing:
        return existing["id"], False
    mid = db.run(
        "INSERT INTO messages(chat_id,tg_msg_id,text,author_id,author_username,author_name,author_flags,reply_to,"
        "replies_count,posted_at,fetched_at,content_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (raw.chat_id, raw.tg_msg_id, raw.text, raw.author_id, raw.author_username, raw.author_name,
         dumps(raw.author_flags or {}), raw.reply_to, raw.replies_count, iso(raw.posted_at), iso(), content_hash(raw.text)),
    )
    return mid, True


def is_unprocessed(db: Database, message_id: int) -> bool:
    row = db.one("SELECT status FROM messages WHERE id=?", (message_id,))
    return bool(row) and row["status"] == "new"


def unprocessed_messages(db: Database, limit: int = 40) -> list[RawMessage]:
    """Записанные, но не обработанные сообщения старше двух минут (свежие ещё могут быть в работе)."""
    rows = db.all("SELECT * FROM messages WHERE status='new' AND fetched_at<? ORDER BY id LIMIT ?",
                  (iso(now_utc() - timedelta(minutes=2)), limit))
    return [raw_from_row(r) for r in rows]


def raw_from_row(r: dict) -> RawMessage:
    flags = r.get("author_flags")
    return RawMessage(
        chat_id=r["chat_id"], tg_msg_id=r["tg_msg_id"], text=r["text"], posted_at=parse_iso(r["posted_at"]) or now_utc(),
        author_id=r["author_id"], author_username=r["author_username"], author_name=r["author_name"],
        author_flags=flags if isinstance(flags, dict) else {}, reply_to=r["reply_to"], replies_count=r["replies_count"] or 0,
    )


DISCARDED_DAYS = 7
DISCARDED_WHERE = "m.status='noise' AND m.posted_at>=? AND LENGTH(m.text)>=20"


def discarded_since() -> str:
    return iso(now_utc() - timedelta(days=DISCARDED_DAYS))


def count_discarded(db: Database) -> int:
    return db.scalar(f"SELECT COUNT(*) FROM messages m WHERE {DISCARDED_WHERE}", (discarded_since(),), 0)


def list_discarded(db: Database, q: str = "", limit: int = 100) -> list[dict]:
    """Сообщения, которые бот не счёл заказом. Показываем их, чтобы ничего не пропало молча."""
    args: list = [discarded_since()]
    where = DISCARDED_WHERE
    if q:
        where += " AND ulower(m.text) LIKE ? ESCAPE '\\'"
        args.append("%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    return db.all(
        "SELECT m.id, m.text, m.note, m.posted_at, m.author_username, m.author_name, m.tg_msg_id, c.title AS chat_title, "
        "c.username AS chat_username, c.tg_id AS chat_tg, c.is_demo AS chat_demo FROM messages m JOIN chats c ON c.id=m.chat_id "
        f"WHERE {where} ORDER BY m.posted_at DESC LIMIT ?", (*args, limit),
    )


def set_message_status(db: Database, message_id: int, status: str, note: str | None = None) -> None:
    db.run("UPDATE messages SET status=?, note=? WHERE id=?", (status, note, message_id))


KIND_LABELS = {"other": "не заказ", "offer": "предложение услуг", "vacancy": "вакансия в штат", "ad": "реклама", "chat": "обсуждение", "rules": "правила чата"}


def why_discarded(note: str | None) -> str:
    """Короткое объяснение для списка «Отброшено»: кто и почему решил, что это не заказ."""
    who, _, kind = (note or "").partition(":")
    label = KIND_LABELS.get(kind)
    if who == "ai" and label:
        return f"ИИ: {label}"
    if who == "rule" and label:
        return f"по правилам: {label}"
    return "не похоже на заказ"


def delete_orders(db: Database, ids: list[int]) -> list[dict]:
    """Удаляем заказы. Сообщение остаётся с пометкой «удалено»: при следующей проверке оно не вернётся в ленту."""
    gone: list[dict] = []
    for i in range(0, len(ids), 400):
        part = [int(x) for x in ids[i: i + 400]]
        marks = ",".join("?" * len(part))
        rows = db.all(f"SELECT o.id, o.message_id, m.text FROM orders o JOIN messages m ON m.id=o.message_id WHERE o.id IN ({marks})", part)
        if not rows:
            continue
        mids = [r["message_id"] for r in rows]
        mm = ",".join("?" * len(mids))
        db.run(f"UPDATE messages SET status='deleted', note='user:deleted' WHERE id IN ({mm})", mids)
        db.run(f"DELETE FROM orders WHERE id IN ({','.join('?' * len(rows))})", [r["id"] for r in rows])
        gone += rows
    return gone


def delete_messages(db: Database, ids: list[int]) -> int:
    """Убираем отброшенные сообщения из списка «Отброшено» (сами записи остаются, чтобы не вернуться при проверке)."""
    n = 0
    for i in range(0, len(ids), 400):
        part = [int(x) for x in ids[i: i + 400]]
        n += db.run(f"UPDATE messages SET status='deleted', note='user:deleted' WHERE status='noise' AND id IN ({','.join('?' * len(part))})", part)
    return n


def bump_chat_cursor(db: Database, chat_id: int, tg_msg_id: int) -> None:
    db.run("UPDATE chats SET last_msg_id=MAX(last_msg_id,?), last_scan_at=?, last_error=NULL WHERE id=?",
           (tg_msg_id, iso(), chat_id))


# ---------------------------------------------------------------- заказы ----

ORDER_SELECT = """
SELECT o.*, m.text AS text, m.author_id AS author_id, m.author_username AS author_username,
       m.author_name AS author_name, m.author_flags AS author_flags, m.tg_msg_id AS tg_msg_id,
       m.chat_id AS chat_id, c.title AS chat_title, c.username AS chat_username, c.tg_id AS chat_tg,
       c.is_demo AS chat_demo
FROM orders o
JOIN messages m ON m.id = o.message_id
JOIN chats c ON c.id = m.chat_id
"""


def get_order(db: Database, order_id: int) -> dict | None:
    return db.one(ORDER_SELECT + " WHERE o.id=?", (order_id,))


def order_by_message(db: Database, message_id: int) -> dict | None:
    return db.one(ORDER_SELECT + " WHERE o.message_id=?", (message_id,))


def recent_orders(db: Database, days: int = 14) -> list[dict]:
    since = iso(now_utc() - timedelta(days=days))
    return db.all(
        "SELECT o.id, o.title, o.dup_count, o.budget_max, o.currency, m.text, m.author_id, m.chat_id FROM orders o JOIN messages m ON m.id=o.message_id "
        "WHERE o.created_at>=? ORDER BY o.id DESC LIMIT 800",
        (since,),
    )


def add_source(db: Database, order_id: int, chat_id: int, message_id: int, link: str | None, posted_at: str) -> None:
    db.run(
        "INSERT OR IGNORE INTO order_sources(order_id,chat_id,message_id,link,posted_at) VALUES(?,?,?,?,?)",
        (order_id, chat_id, message_id, link, posted_at),
    )


def order_sources(db: Database, order_id: int) -> list[dict]:
    return db.all(
        "SELECT s.*, c.title AS chat_title FROM order_sources s JOIN chats c ON c.id=s.chat_id WHERE s.order_id=? ORDER BY s.posted_at",
        (order_id,),
    )


def author_stats(db: Database, author_id: int | None, author_username: str | None) -> dict:
    """Что мы знаем об авторе по своей же базе. Ничего чужого мы не придумываем."""
    if not author_id and not author_username:
        return {"posts": 0, "chats": 0}
    since = iso(now_utc() - timedelta(days=30))
    if author_id:
        cond, arg = "m.author_id=?", author_id
    else:
        cond, arg = "lower(m.author_username)=lower(?)", author_username
    posts = db.scalar(
        f"SELECT COUNT(*) FROM orders o JOIN messages m ON m.id=o.message_id WHERE {cond} AND o.created_at>=?",
        (arg, since), 0,
    )
    chats = db.scalar(
        f"SELECT COUNT(DISTINCT m.chat_id) FROM messages m WHERE {cond} AND m.posted_at>=?",
        (arg, since), 0,
    )
    return {"posts": posts, "chats": chats}


def won_amounts(db: Database, skill_keys: set[str], limit: int = 12) -> list[float]:
    """Суммы взятых вами заказов с похожими навыками. Нужны для честной подсказки цены."""
    rows = db.all(
        "SELECT skills, COALESCE(won_amount, price_offered) AS amount FROM orders WHERE stage='work' "
        "AND COALESCE(won_amount, price_offered) IS NOT NULL ORDER BY stage_at DESC LIMIT ?",
        (limit * 3,),
    )
    out = []
    for r in rows:
        sk = set(x.lower() for x in (r["skills"] or []))
        if sk & {k.lower() for k in skill_keys}:
            out.append(float(r["amount"]))
    return out[:limit]


# --------------------------------------------------------------- черновики ----


def save_draft(db: Database, order_id: int, tone: str, variant: int, price: float | None, text: str) -> None:
    db.run(
        "INSERT INTO drafts(order_id,tone,variant,price,text,created_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(order_id,tone,variant) DO UPDATE SET text=excluded.text, price=excluded.price, created_at=excluded.created_at",
        (order_id, tone, variant, price, text, iso()),
    )


def get_draft(db: Database, order_id: int, tone: str, variant: int = 0) -> dict | None:
    return db.one("SELECT * FROM drafts WHERE order_id=? AND tone=? AND variant=?", (order_id, tone, variant))


# ------------------------------------------------------- оценки и этапы ----

FEEDBACK_REASONS = {
    "profile": "Не мой профиль",
    "budget": "Низкий бюджет",
    "terms": "Не те сроки",
    "scam": "Подозрительно",
    "notorder": "Это не заказ",
    "other": "Просто не нравится",
}


def set_feedback(db: Database, order_id: int, kind: str | None, reason: str | None = None) -> None:
    now = iso()
    row = db.one("SELECT score FROM orders WHERE id=?", (order_id,))
    if kind in {"up", "down"}:
        db.run("UPDATE orders SET feedback=?, feedback_reason=?, feedback_at=? WHERE id=?", (kind, reason, now, order_id))
        if kind == "up":  # вы считаете, что бот отсеял зря: возвращаем в ленту
            db.run("UPDATE orders SET verdict='maybe', score=MAX(score,50) WHERE id=? AND verdict IN ('skip','scam')", (order_id,))
        db.run("DELETE FROM feedback_events WHERE order_id=?", (order_id,))  # у заказа одна актуальная оценка
        db.run("INSERT INTO feedback_events(order_id,kind,reason,score_at,created_at) VALUES(?,?,?,?,?)",
               (order_id, kind, reason, row["score"] if row else None, now))
    else:
        db.run("UPDATE orders SET feedback=NULL, feedback_reason=NULL, feedback_at=NULL WHERE id=?", (order_id,))
        db.run("DELETE FROM feedback_events WHERE order_id=?", (order_id,))


def set_state(db: Database, order_id: int, state: str) -> None:
    db.run("UPDATE orders SET state=? WHERE id=?", (state, order_id))


def mark_offered(db: Database, order_id: int, price: float | None) -> None:
    now = iso()
    db.run(
        "UPDATE orders SET offered_at=COALESCE(offered_at,?), price_offered=COALESCE(?,price_offered), "
        "stage=COALESCE(stage,'wait'), stage_at=COALESCE(stage_at,?) WHERE id=?",
        (now, price, now, order_id),
    )


def unmark_offered(db: Database, order_id: int) -> None:
    db.run("UPDATE orders SET offered_at=NULL, price_offered=NULL, stage=NULL, stage_at=NULL, won_amount=NULL WHERE id=?", (order_id,))


STAGES = {"wait", "ans", "work", "no"}


def set_stage(db: Database, order_id: int, stage: str, amount: float | None = None) -> None:
    now = iso()
    db.run("UPDATE orders SET stage=?, stage_at=?, won_amount=? WHERE id=?",
           (stage, now, amount if stage == "work" else None, order_id))
    db.run("UPDATE orders SET offered_at=COALESCE(offered_at,?) WHERE id=?", (now, order_id))


def bump_bids(db: Database, order_id: int, n: int = 1) -> None:
    db.run("UPDATE orders SET bids=bids+? WHERE id=?", (n, order_id))


def set_bids(db: Database, order_id: int, n: int) -> None:
    db.run("UPDATE orders SET bids=MAX(bids,?) WHERE id=?", (n, order_id))


# ------------------------------------------------------------ служебное ----


def purge_old(db: Database, days: int = 60) -> int:
    """Старый мусор убираем: заказы, с которыми вы ничего не делали, и их сообщения."""
    cutoff = iso(now_utc() - timedelta(days=days))
    n = db.scalar(
        "SELECT COUNT(*) FROM orders o WHERE o.posted_at<? AND o.feedback IS NULL AND o.stage IS NULL "
        "AND o.state NOT IN ('saved') AND o.offered_at IS NULL", (cutoff,), 0)
    db.run(  # одним запросом: построчное удаление держало бы работу приложения секундами
        "DELETE FROM messages WHERE id IN (SELECT o.message_id FROM orders o WHERE o.posted_at<? AND o.feedback IS NULL "
        "AND o.stage IS NULL AND o.state NOT IN ('saved') AND o.offered_at IS NULL)", (cutoff,))
    # болтовня, повторы и ответы нужны только неделю («Отброшено» показывает последние 7 дней), иначе база пухнет от оживлённых чатов
    db.run(
        "DELETE FROM messages WHERE posted_at<? AND status IN ('noise','reply','new','dup','deleted') "
        "AND id NOT IN (SELECT message_id FROM orders)",
        (iso(now_utc() - timedelta(days=DISCARDED_DAYS + 1)),),
    )
    return n


def now_minus(minutes: int) -> datetime:
    return now_utc() - timedelta(minutes=minutes)


def add_insight(db: Database, kind: str, text: str, payload: Any = None) -> int:
    return db.run("INSERT INTO insights(kind,text,payload,created_at) VALUES(?,?,?,?)", (kind, text, dumps(payload), iso()))
