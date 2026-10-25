"""Все запросы приложения к серверу."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .. import learning, present, stats, store
from ..bot import BotError
from ..cloud import CloudError
from ..llm.base import LLMUnavailable
from ..llm.openai_compat import PROVIDERS
from ..profile import (
    CAPACITY_MIN_BUDGET,
    Notifications,
    Profile,
    load_notifications,
    load_profile,
    save_notifications,
    save_profile,
)
from ..runtime import Runtime
from ..secrets_store import mask
from ..sources.telegram import LoginError, TelegramProblem
from ..updater import UpdateError, Updater
from ..util import fmt_age, minutes_ago
from .auth import COOKIE, MAX_AGE, Auth, client_ip, require_auth

public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


def rt_of(request: Request) -> Runtime:
    return request.app.state.rt


class LoginIn(BaseModel):
    password: str = Field(max_length=200)


@public.get("/health")
def health() -> dict:
    return {"ok": True}


@public.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    auth: Auth = request.app.state.auth
    ip = client_ip(request)
    wait = auth.blocked(ip)
    if wait:
        return JSONResponse({"detail": f"Слишком много попыток. Подождите {wait // 60 + 1} мин."}, status_code=429,
                            headers={"Retry-After": str(wait)})
    if not auth.check_password(body.password):
        auth.fail(ip)
        return JSONResponse({"detail": "Пароль не подошёл"}, status_code=401)
    auth.ok(ip)
    secure = request.app.state.settings.cookie_secure
    if secure is None:
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    r = JSONResponse({"ok": True})
    r.set_cookie(COOKIE, auth.issue(), max_age=MAX_AGE, httponly=True, samesite="lax", secure=bool(secure), path="/")
    return r


@public.post("/logout")
def logout(request: Request):
    request.app.state.auth.revoke(request.cookies.get(COOKIE))
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE, path="/")
    return r


@public.get("/session")
def session(request: Request) -> dict:
    auth: Auth = request.app.state.auth
    return {"authed": request.app.state.settings.local or auth.valid(request.cookies.get(COOKIE))}


# ----------------------------------------------------------------- заказы ----

FILTERS = {
    "all": "o.verdict IN ('match','maybe') AND o.state!='hidden'",
    "hot": "o.verdict='match' AND o.score>=90 AND o.state!='hidden'",
    "saved": "o.state='saved'",
    "rated": "o.feedback IS NOT NULL",
    "offered": "o.offered_at IS NOT NULL",
    "rejected": "o.verdict IN ('skip','scam') AND o.state!='hidden'",
}
ORDER_BY = {
    "score": "o.score DESC, o.posted_at DESC",
    "new": "o.posted_at DESC",
    "budget": "COALESCE(o.budget_rub_max, o.budget_rub_min, 0) DESC, o.score DESC",
}


@router.get("/orders")
def list_orders(request: Request, filter: str = "all", sort: str = "score", q: str = "", limit: int = 80) -> dict:
    rt = rt_of(request)
    db = rt.db
    profile = load_profile(db)
    if filter == "discarded":
        return _discarded_feed(rt, q, limit)
    where = FILTERS.get(filter, FILTERS["all"])
    args: list = []
    if q.strip():
        where += (" AND (ulower(o.title) LIKE ? ESCAPE '\\' OR ulower(o.summary) LIKE ? ESCAPE '\\' "
                  "OR ulower(m.text) LIKE ? ESCAPE '\\' OR ulower(c.title) LIKE ? ESCAPE '\\')")
        needle = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{needle}%"
        args += [like] * 4
    limit = max(1, min(200, limit))
    rows = db.all(store.ORDER_SELECT + f" WHERE {where} ORDER BY {ORDER_BY.get(sort, ORDER_BY['score'])} LIMIT ?", (*args, limit))
    counts, hot_sum = _counts(rt)
    cards = [present.order_card(r, profile) for r in rows]
    if cards:
        marks = ",".join("?" * len(cards))
        drafts = {d["order_id"]: d for d in rt.db.all(
            f"SELECT order_id, text, price FROM drafts WHERE tone=? AND variant=0 AND order_id IN ({marks})",
            (profile.tone, *[c["id"] for c in cards]))}
        for c, r in zip(cards, rows, strict=True):
            d = drafts.get(c["id"])
            c["draft"] = {"text": d["text"], "price": d["price"]} if d else None
            c["contact_url"] = r.get("contact_url") or ""
    return {
        "items": cards,
        "counts": counts,
        "hot_sum": hot_sum,
        "total_read": db.scalar("SELECT COUNT(*) FROM messages WHERE posted_at>=?", (rt.since(7 * 24 * 60),), 0),
    }


def _counts(rt: Runtime) -> tuple[dict, int]:
    db = rt.db
    counts = {
        key: db.scalar("SELECT COUNT(*) FROM orders o JOIN messages m ON m.id=o.message_id JOIN chats c ON c.id=m.chat_id WHERE " + cond, default=0)
        for key, cond in FILTERS.items()
    }
    counts["discarded"] = store.count_discarded(db)
    hot_sum = db.scalar(
        "SELECT SUM(COALESCE(o.budget_rub_max,o.budget_rub_min)) FROM orders o WHERE " + FILTERS["hot"] + " AND o.period='fixed'", default=0) or 0
    return counts, hot_sum


def _discarded_feed(rt: Runtime, q: str, limit: int) -> dict:
    """Всё, что бот не счёл заказом: смотрите и возвращайте в ленту кнопкой «Это заказ»."""
    rows = store.list_discarded(rt.db, q.strip(), max(1, min(200, limit)))
    items = []
    for r in rows:
        age = fmt_age(minutes_ago(r["posted_at"]))
        items.append({
            "id": r["id"], "kind": "message", "text": r["text"][:900], "chat": r["chat_title"], "age_label": age,
            "author": ("@" + r["author_username"]) if r["author_username"] else (r["author_name"] or ""),
            "link": store.chat_link({"username": r["chat_username"], "tg_id": r["chat_tg"]}, r["tg_msg_id"]),
            "demo": bool(r["chat_demo"]),
        })
    counts, hot_sum = _counts(rt)
    return {"items": items, "counts": counts, "hot_sum": hot_sum, "kind": "messages",
            "total_read": rt.db.scalar("SELECT COUNT(*) FROM messages WHERE posted_at>=?", (rt.since(7 * 24 * 60),), 0)}


@router.post("/discarded/{message_id}/promote")
async def promote_discarded(message_id: int, request: Request) -> dict:
    rt = rt_of(request)
    oid = await rt.pipeline.promote(message_id)
    if oid is None:
        raise HTTPException(409, "Это сообщение уже есть в ленте или повторяет другой заказ")
    rt.bus.publish({"type": "orders", "ids": [oid]})
    return {"order": _card(rt, oid)}


def _row(rt: Runtime, order_id: int) -> dict:
    if not 0 < order_id < 2**62:
        raise HTTPException(404, "Заказ не найден")
    row = store.get_order(rt.db, order_id)
    if row is None:
        raise HTTPException(404, "Заказ не найден")
    return row


def _card(rt: Runtime, order_id: int) -> dict:
    return present.order_card(_row(rt, order_id), load_profile(rt.db))


@router.get("/orders/{order_id}")
def order_detail(order_id: int, request: Request) -> dict:
    rt = rt_of(request)
    row = _row(rt, order_id)
    profile = load_profile(rt.db)
    cached = store.get_draft(rt.db, order_id, profile.tone, 0)
    draft = {"text": cached["text"], "price": cached["price"], "tone": cached["tone"], "variant": 0} if cached else None
    return present.order_detail(rt.db, row, profile, draft)


class DraftIn(BaseModel):
    tone: Literal["short", "warm", "formal"] = "warm"
    variant: int = Field(default=0, ge=0, le=20)
    price: int | None = Field(default=None, ge=0, le=100_000_000)
    force: bool = False


@router.post("/orders/{order_id}/draft")
async def make_draft(order_id: int, body: DraftIn, request: Request) -> dict:
    rt = rt_of(request)
    _row(rt, order_id)
    return await rt.pipeline.make_draft(order_id, body.tone, body.variant, price=body.price, force=body.force or body.price is not None)


class FeedbackIn(BaseModel):
    kind: Literal["up", "down", "clear"]
    reason: Literal["profile", "budget", "terms", "scam", "notorder", "other"] | None = None


@router.post("/orders/{order_id}/feedback")
def feedback(order_id: int, body: FeedbackIn, request: Request) -> dict:
    rt = rt_of(request)
    row = _row(rt, order_id)
    if body.kind == "up":
        rt.pipeline.teach(row.get("text") or "", True)
        store.set_feedback(rt.db, order_id, "up")
        if row["state"] == "hidden":
            store.set_state(rt.db, order_id, "new")
    elif body.kind == "down":
        if body.reason == "notorder":
            rt.pipeline.teach(row.get("text") or "", False)  # ИИ ошибся: запоминаем как пример «не заказ»
        store.set_feedback(rt.db, order_id, "down", body.reason or "other")
        store.set_state(rt.db, order_id, "hidden")
    else:
        store.set_feedback(rt.db, order_id, None)
        if row["state"] == "hidden":
            store.set_state(rt.db, order_id, "new")
    learning.refresh_suggestions(rt.db, load_profile(rt.db))
    return {"order": _card(rt, order_id), "learned": rt.db.scalar("SELECT COUNT(*) FROM feedback_events", default=0)}


class SaveIn(BaseModel):
    saved: bool


@router.post("/orders/{order_id}/save")
def save_order(order_id: int, body: SaveIn, request: Request) -> dict:
    rt = rt_of(request)
    _row(rt, order_id)
    store.set_state(rt.db, order_id, "saved" if body.saved else "new")
    return {"order": _card(rt, order_id)}


class OfferIn(BaseModel):
    done: bool = True
    price: int | None = Field(default=None, ge=0, le=100_000_000)


@router.post("/orders/{order_id}/offer")
def offer(order_id: int, body: OfferIn, request: Request) -> dict:
    rt = rt_of(request)
    _row(rt, order_id)
    if body.done:
        store.mark_offered(rt.db, order_id, body.price)
    else:
        store.unmark_offered(rt.db, order_id)
    return {"order": _card(rt, order_id)}


class StageIn(BaseModel):
    stage: Literal["wait", "ans", "work", "no"]
    amount: int | None = Field(default=None, ge=0, le=100_000_000)


@router.post("/orders/{order_id}/stage")
def stage(order_id: int, body: StageIn, request: Request) -> dict:
    rt = rt_of(request)
    row = _row(rt, order_id)
    amount = body.amount if body.amount is not None else (row.get("price_offered") or row.get("budget_rub_max"))
    store.set_stage(rt.db, order_id, body.stage, amount)
    return {"order": _card(rt, order_id)}


@router.post("/orders/{order_id}/restore")
def restore(order_id: int, request: Request) -> dict:
    rt = rt_of(request)
    _row(rt, order_id)
    store.set_feedback(rt.db, order_id, None)
    store.set_state(rt.db, order_id, "new")
    return {"order": _card(rt, order_id)}


# ----------------------------------------------------------- аналитика и статус ----


@router.get("/stats")
def get_stats(request: Request) -> dict:
    rt = rt_of(request)
    data = stats.build(rt.db, load_profile(rt.db).tz)
    data["suggestions"] = [{"id": s["id"], "kind": s["kind"], "text": s["text"]} for s in learning.open_suggestions(rt.db)]
    return data


@router.get("/status")
def status(request: Request) -> dict:
    rt = rt_of(request)
    profile = load_profile(rt.db)
    chats = store.list_chats(rt.db)
    tg, bot, llm = rt.tg_status(), rt.bot_status(), rt.llm_status()
    ready = [
        {"id": "chats", "ok": any(c["enabled"] for c in chats), "label": "Чаты выбраны", "count": sum(1 for c in chats if c["enabled"])},
        {"id": "skills", "ok": bool(profile.skills), "label": "Навыки и бюджет заданы"},
        {"id": "cases", "ok": bool(profile.cases), "label": f"Портфолио: {len(profile.cases)}" if profile.cases else "Портфолио для откликов"},
        {"id": "telegram", "ok": tg["state"] == "ready", "label": "Telegram подключён"},
        {"id": "bot", "ok": bool(bot["paired"]), "label": "Бот в Telegram подключён"},
        {"id": "ai", "ok": llm["mode"] != "rules" and not llm["error"], "label": "ИИ-режим включён"},
    ]
    return {
        "demo": rt.demo_enabled() and tg["state"] != "ready",
        "scan": rt.scan_status(),
        "telegram": tg,
        "bot": bot,
        "llm": llm,
        "ready": ready,
        "tz": profile.tz,
        "public_url": rt.settings.public_url,
        "local": rt.settings.local,
        "cloud": rt.cloud.status(),
        "update": rt.updater.status() if rt.settings.local else None,
    }


@router.post("/scan")
async def scan(request: Request) -> dict:
    rt = rt_of(request)
    res = await rt.scan(manual=True)
    return {"skipped": bool(res.get("skipped")), "orders": res.get("orders", 0), "messages": res.get("messages", 0),
            "error": res.get("error"), "ids": res.get("new_ids", [])}


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    rt = rt_of(request)

    async def gen():
        yield "retry: 4000\n\n"
        q = rt.bus.open_queue()
        rt.sse_clients += 1
        try:
            quiet = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=5)  # часто проверяем, что окно ещё открыто
                    quiet = 0
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    quiet += 1
                    if quiet % 4 == 0:
                        yield ": ping\n\n"
        finally:
            rt.sse_clients -= 1
            rt.bus.close_queue(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ профиль ----


@router.get("/profile")
def get_profile(request: Request) -> dict:
    rt = rt_of(request)
    return {"profile": load_profile(rt.db).model_dump(), "presets": CAPACITY_MIN_BUDGET}


@router.put("/profile")
async def put_profile(body: Profile, request: Request) -> dict:
    rt = rt_of(request)
    saved = save_profile(rt.db, body)
    rt.spawn(rt.pipeline.rescore_recent(use_llm=False))
    rt.bus.publish({"type": "orders", "ids": []})
    return {"profile": saved.model_dump()}


class ImportIn(BaseModel):
    text: str = Field(min_length=20, max_length=8000)


@router.post("/profile/import")
async def import_profile(body: ImportIn, request: Request) -> dict:
    rt = rt_of(request)
    patch = await rt.llm.parse_profile(body.text, load_profile(rt.db))
    if not patch:
        raise HTTPException(422, "Не нашёл в тексте ничего полезного. Опишите, что вы делаете и сколько берёте")
    return {"patch": patch, "smart": rt.llm.name != "rules"}


@router.post("/profile/rescore")
async def rescore(request: Request) -> dict:
    rt = rt_of(request)
    n = await rt.pipeline.rescore_recent(use_llm=rt.pipeline.ai_on(), limit=40)  # при паузе ИИ пересчёт идёт правилами
    return {"rescored": n}


@router.get("/notifications")
def get_notifications(request: Request) -> dict:
    return load_notifications(rt_of(request).db).model_dump()


@router.put("/notifications")
def put_notifications(body: Notifications, request: Request) -> dict:
    return save_notifications(rt_of(request).db, body).model_dump()


# --------------------------------------------------------------------- чаты ----


@router.get("/chats")
def get_chats(request: Request) -> dict:
    rt = rt_of(request)
    return {"items": store.list_chats(rt.db), "telegram": rt.tg_status()}


class ChatAddIn(BaseModel):
    link: str = Field(min_length=3, max_length=300)


@router.post("/chats")
async def add_chat(body: ChatAddIn, request: Request) -> dict:
    rt = rt_of(request)
    chat_id = await rt.add_chat(body.link)
    rt.spawn(rt.scan(False))
    return {"id": chat_id, "items": store.list_chats(rt.db)}


class DialogIn(BaseModel):
    tg_id: int


@router.get("/telegram/dialogs")
async def dialogs(request: Request) -> dict:
    rt = rt_of(request)
    src = rt.source
    if src is None or not hasattr(src, "list_dialogs"):
        raise TelegramProblem("Сначала подключите Telegram")
    return {"items": await src.list_dialogs()}


@router.post("/chats/from-dialog")
async def add_dialog(body: DialogIn, request: Request) -> dict:
    rt = rt_of(request)
    src = rt.source
    if src is None or not hasattr(src, "add_dialog"):
        raise TelegramProblem("Сначала подключите Telegram")
    chat_id = await src.add_dialog(body.tg_id)
    rt.spawn(rt.scan(False))
    return {"id": chat_id, "items": store.list_chats(rt.db)}


class ChatPatch(BaseModel):
    enabled: bool


@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: int, body: ChatPatch, request: Request) -> dict:
    rt = rt_of(request)
    rt.db.run("UPDATE chats SET enabled=? WHERE id=?", (int(body.enabled), chat_id))
    rt.chats_changed()
    return {"items": store.list_chats(rt.db)}


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: int, request: Request) -> dict:
    rt = rt_of(request)
    rt.db.run("DELETE FROM chats WHERE id=?", (chat_id,))
    rt.chats_changed()
    return {"items": store.list_chats(rt.db)}


# ---------------------------------------------------------- предложения бота ----


class SuggestIn(BaseModel):
    accept: bool


@router.post("/suggestions/{sid}")
async def resolve(sid: int, body: SuggestIn, request: Request) -> dict:
    rt = rt_of(request)
    profile = load_profile(rt.db)
    action = learning.resolve_suggestion(rt.db, sid, body.accept, profile)
    if action is None:
        raise HTTPException(404, "Предложение уже закрыто")
    if "min_budget" in action:
        profile.min_budget = action["min_budget"]
        save_profile(rt.db, profile)
        rt.spawn(rt.pipeline.rescore_recent(use_llm=False))
    if "chat_id" in action:
        rt.chats_changed()
    return {"ok": True, "action": action}


# ------------------------------------------------------------- подключения ----


class KeyIn(BaseModel):
    key: str = Field(min_length=10, max_length=300)


@router.post("/connect/claude")
async def connect_claude(body: KeyIn, request: Request) -> dict:
    rt = rt_of(request)
    await rt.connect_claude(body.key)
    return {"llm": rt.llm_status()}


@router.delete("/connect/claude")
def disconnect_claude(request: Request) -> dict:
    rt = rt_of(request)
    rt.disconnect_claude()
    return {"llm": rt.llm_status()}


class ScanIn(BaseModel):
    minutes: int = Field(ge=0, le=1440)  # 0 = только по кнопке

    @field_validator("minutes")
    @classmethod
    def _min(cls, v: int) -> int:
        if 0 < v < 5:
            raise ValueError("не чаще раза в 5 минут")
        return v


@router.put("/settings/scan")
def set_scan(body: ScanIn, request: Request) -> dict:
    rt = rt_of(request)
    rt.set_scan_minutes(body.minutes)
    return {"scan": rt.scan_status()}


class AiControlIn(BaseModel):
    paused: bool | None = None
    auto: bool | None = None
    limit: int | None = Field(default=None, ge=5, le=500)


@router.put("/ai/control")
def ai_control_put(body: AiControlIn, request: Request) -> dict:
    rt = rt_of(request)
    return {"llm": rt.set_ai_control(body.model_dump(exclude_none=True))}


class AiIn(BaseModel):
    provider: str = Field(max_length=20)
    key: str = Field(default="", max_length=300)
    model: str = Field(default="", max_length=120)
    base_url: str = Field(default="", max_length=300)


@router.get("/connect/ai/providers")
def ai_providers() -> dict:
    return {"items": [{"id": k, **{f: v[f] for f in ("label", "base_url", "model", "needs_key", "key_url", "note", "rpd")}}
                      for k, v in PROVIDERS.items()]}


@router.post("/connect/ai/models")
async def ai_models(body: AiIn, request: Request) -> dict:
    rt = rt_of(request)
    return {"items": await rt.ai_models(body.provider, body.key, body.base_url)}


@router.post("/connect/ai")
async def connect_ai(body: AiIn, request: Request) -> dict:
    rt = rt_of(request)
    await rt.connect_ai(body.provider, body.key, body.model, body.base_url)
    return {"llm": rt.llm_status()}


@router.delete("/connect/ai")
def disconnect_ai(request: Request) -> dict:
    rt = rt_of(request)
    rt.disconnect_ai()
    return {"llm": rt.llm_status()}


class TgApiIn(BaseModel):
    api_id: str = Field(max_length=20)
    api_hash: str = Field(max_length=64)


@router.post("/connect/telegram/api")
async def tg_api(body: TgApiIn, request: Request) -> dict:
    rt = rt_of(request)
    await rt.tg_save_api(body.api_id, body.api_hash)
    return {"telegram": rt.tg_status(), "api_id": mask(rt.secrets.get("tg_api_id"), 2)}


class PhoneIn(BaseModel):
    phone: str = Field(min_length=5, max_length=30)


@router.post("/connect/telegram/phone")
async def tg_phone(body: PhoneIn, request: Request) -> dict:
    rt = rt_of(request)
    await rt.tg_login_start(body.phone)
    return {"step": "code"}


class CodeIn(BaseModel):
    code: str = Field(min_length=3, max_length=12)


@router.post("/connect/telegram/code")
async def tg_code(body: CodeIn, request: Request) -> dict:
    rt = rt_of(request)
    res = await rt.tg_login_code(body.code)
    if res == "ok":
        return {"step": "done", "telegram": rt.tg_status()}
    return {"step": "password"}


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.post("/connect/telegram/password")
async def tg_password(body: PasswordIn, request: Request) -> dict:
    rt = rt_of(request)
    await rt.tg_login_password(body.password)
    return {"step": "done", "telegram": rt.tg_status()}


@router.delete("/connect/telegram")
async def tg_logout(request: Request) -> dict:
    rt = rt_of(request)
    await rt.tg_logout()
    return {"telegram": rt.tg_status()}


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


@router.post("/connect/bot")
async def connect_bot(body: TokenIn, request: Request) -> dict:
    rt = rt_of(request)
    return {"bot": await rt.connect_bot(body.token)}


@router.delete("/connect/bot")
async def disconnect_bot(request: Request) -> dict:
    rt = rt_of(request)
    await rt.disconnect_bot()
    return {"bot": rt.bot_status()}


def _local(request: Request) -> Runtime:
    rt = rt_of(request)
    if not rt.settings.local:
        raise HTTPException(404, "Не найдено")
    return rt


@router.post("/update/check")
async def update_check(request: Request) -> dict:
    rt = _local(request)
    return await rt.updater.check(time.monotonic())


@router.post("/update/install")
async def update_install(request: Request) -> dict:
    """Скачать новую версию, запустить установщик и закрыть программу: установщик откроет обновлённую."""
    rt = _local(request)
    stop = getattr(request.app.state, "quit", None)
    if rt.updater.status()["can_install"] is not True or stop is None:
        raise UpdateError("Обновить кнопкой можно только в установленной программе для Windows. Скачайте свежий установщик вручную")
    installer = await rt.updater.download()
    Updater.launch(installer)
    rt.spawn(_quit_soon(stop))
    return {"ok": True}


async def _quit_soon(stop) -> None:
    await asyncio.sleep(1.5)  # даём ответу дойти до окна
    stop()


@router.post("/quit")
def quit_app(request: Request) -> dict:
    """Закрыть программу на этом компьютере (кнопка в профиле). На сервере её нет."""
    rt = rt_of(request)
    stop = getattr(request.app.state, "quit", None)
    if not rt.settings.local or stop is None:
        raise HTTPException(404, "Не найдено")
    stop()
    return {"ok": True}


class CloudIn(BaseModel):
    url: str = Field(min_length=8, max_length=300)
    token: str = Field(min_length=16, max_length=200)


@router.post("/connect/cloud")
async def connect_cloud(body: CloudIn, request: Request) -> dict:
    rt = rt_of(request)
    await rt.cloud.connect(body.url, body.token)
    rt.bus.publish({"type": "status"})
    return {"cloud": rt.cloud.status()}


@router.delete("/connect/cloud")
def disconnect_cloud(request: Request) -> dict:
    rt = rt_of(request)
    rt.cloud.disconnect()
    return {"cloud": rt.cloud.status()}


class DemoIn(BaseModel):
    on: bool


@router.post("/demo")
async def demo(body: DemoIn, request: Request) -> dict:
    rt = rt_of(request)
    await rt.set_demo(body.on)
    if body.on:
        rt.spawn(rt.scan(False))
    return {"demo": rt.demo_enabled()}


HUMAN_ERRORS = (LoginError, TelegramProblem, BotError, LLMUnavailable, CloudError, UpdateError)
