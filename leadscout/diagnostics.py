"""Выгрузка работы ИИ: что он видит, что решает и где ошибается. Файл можно показать разработчику для настройки точности."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING

from . import classify
from .llm.claude import EXTRACT_SYSTEM, SCORE_SYSTEM
from .profile import load_profile
from .updater import VERSION
from .util import clip, iso, now_utc

if TYPE_CHECKING:
    from .runtime import Runtime

_HANDLE = re.compile(r"(?<![\w.])@[A-Za-z][\w]{3,31}")
_URL = re.compile(r"(?:https?://|t\.me/|www\.)\S+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\s\-().]*){10,13}(?!\d)")


def redact(text: str | None) -> str:
    """Прячем то, что идентифицирует людей: ники, ссылки, почту, телефоны. Смысл сообщения остаётся."""
    t = text or ""
    t = _EMAIL.sub("[почта]", t)
    t = _URL.sub("[ссылка]", t)
    t = _HANDLE.sub("@ник", t)
    return _PHONE.sub("[телефон]", t)


def _bucket(note: str | None, status: str) -> str:
    if note:
        return note.split("<")[0]
    return "удалено" if status == "deleted" else "без пометки"


def build(rt: Runtime, hide: bool = True, limit: int = 250, days: int = 7) -> dict:
    db = rt.db
    profile = load_profile(db)
    cut = (lambda s: redact(s)) if hide else (lambda s: s or "")
    since = iso(now_utc() - timedelta(days=days))
    rows = db.all(
        "SELECT m.id, m.text, m.posted_at, m.status, m.note, c.title AS chat, c.is_demo AS demo, "
        "o.id AS order_id, o.title, o.why, o.score, o.verdict, (o.verdict='match' AND o.score>=90) AS hot, o.state, o.feedback, o.feedback_reason, o.offered_at, o.scored_by "
        "FROM messages m JOIN chats c ON c.id=m.chat_id LEFT JOIN orders o ON o.message_id=m.id "
        "WHERE m.posted_at>=? AND m.status IN ('order','noise','deleted') ORDER BY m.posted_at DESC LIMIT ?",
        (since, max(10, min(2000, limit))))
    items = []
    summary: dict[str, int] = {}
    for r in rows:
        decision = _bucket(r["note"], r["status"])
        summary[decision] = summary.get(decision, 0) + 1
        order = None
        if r["order_id"]:
            order = {"id": r["order_id"], "title": cut(r["title"]), "why": cut(r["why"]), "score": r["score"], "verdict": r["verdict"], "hot": bool(r["hot"]),
                     "state": r["state"], "scored_by": r["scored_by"], "feedback": r["feedback"], "feedback_reason": r["feedback_reason"],
                     "offered": bool(r["offered_at"])}
        items.append({"id": r["id"], "chat": r["chat"], "demo": bool(r["demo"]), "at": r["posted_at"], "decision": r["note"], "result": r["status"],
                      "text": clip(cut(r["text"]), 520), "order": order})

    corrections = {
        "бот_пропустил_а_вы_вернули": [
            {"text": clip(cut(r["text"]), 520), "было": (r["note"] or "").split("<", 1)[-1]}
            for r in db.all("SELECT text, note FROM messages WHERE note LIKE 'user:promote<%' ORDER BY id DESC LIMIT 60")],
        "бот_показал_а_вы_сказали_не_заказ": [
            {"text": clip(cut(r["text"]), 520), "решение": r["note"]}
            for r in db.all("SELECT m.text, m.note FROM orders o JOIN messages m ON m.id=o.message_id "
                            "WHERE o.feedback='down' AND o.feedback_reason='notorder' ORDER BY o.id DESC LIMIT 60")],
        "не_подошло_по_другой_причине": [
            {"text": clip(cut(r["text"]), 400), "причина": r["feedback_reason"], "оценка": r["score"]}
            for r in db.all("SELECT m.text, o.feedback_reason, o.score FROM orders o JOIN messages m ON m.id=o.message_id "
                            "WHERE o.feedback='down' AND COALESCE(o.feedback_reason,'')!='notorder' ORDER BY o.id DESC LIMIT 40")],
        "вам_подошло": [
            {"text": clip(cut(r["text"]), 400), "оценка": r["score"]}
            for r in db.all("SELECT m.text, o.score FROM orders o JOIN messages m ON m.id=o.message_id WHERE o.feedback='up' ORDER BY o.id DESC LIMIT 40")],
    }
    yes, no = classify.examples(db)
    stored = db.get_setting(classify.EXAMPLES_KEY, []) or []
    sample_system, _ = classify.build_prompt(["<сюда подставляются прочитанные сообщения по номерам>"], profile, [cut(t) for t in yes], [cut(t) for t in no])
    llm = rt.llm_status()
    prof = profile.model_dump()
    if hide:
        prof["cases"] = [{"title": c["title"], "note": c["note"]} for c in prof.get("cases", [])]
    return {
        "программа": VERSION,
        "создано": iso(),
        "контакты_скрыты": hide,
        "окно_дней": days,
        "ИИ": {"режим": llm.get("mode"), "сервис": llm.get("provider"), "модель": llm.get("model"), "ошибка": llm.get("error")},
        "как_это_работает": [
            "1. Жёсткие правила сразу отбрасывают объявления исполнителей (#помогу, «я делаю…») и правила чата: rule:*",
            "2. ИИ читает новые сообщения пачками по 15 и для каждого отвечает kind: order/offer/vacancy/ad/chat/rules и unsure: ai:*",
            "3. Если ИИ недоступен, на паузе или кончился лимит, решают слова-правила: gate:order, gate:maybe, gate:no",
            "4. Дальше заказ получает оценку 0-100 (правилами и затем ИИ по вашему профилю и примерам прошлых оценок)",
            "5. Ваши действия «Это заказ», 👍, 👎 с причиной «Это не заказ» становятся примерами в подсказке ИИ (user:promote, user:deleted)",
        ],
        "системная_подсказка_чтения": sample_system,
        "системная_подсказка_оценки": SCORE_SYSTEM,
        "системная_подсказка_разбора": EXTRACT_SYSTEM,
        "профиль": prof,
        "примеры_которые_видит_ИИ": {"заказы": [cut(t) for t in yes], "не_заказы": [cut(t) for t in no], "всего_запомнено": len(stored)},
        "итоги_решений": dict(sorted(summary.items(), key=lambda kv: -kv[1])),
        "ваши_исправления": corrections,
        "сообщения": items,
    }
