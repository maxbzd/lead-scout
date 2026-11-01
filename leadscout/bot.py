"""Telegram-бот: уведомления о заказах, кнопки «подходит / не то», черновики откликов.

Написан на прямых запросах к Bot API (httpx): так меньше зависимостей и проще проверять.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import secrets as pysecrets
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from . import store
from .config import Settings
from .db import Database
from .pipeline import Pipeline
from .present import order_card
from .profile import load_notifications, load_profile, save_notifications
from .util import clip, fmt_money

log = logging.getLogger("leadscout.bot")

API = "https://api.telegram.org"


_TG_URL = re.compile(r"https://t\.me/[A-Za-z][A-Za-z0-9_]{3,63}")


class BotError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class BotApi:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self.token = token
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0))

    async def call(self, method: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(2):
            try:
                r = await self.client.post(f"{API}/bot{self.token}/{method}", json=params)
            except httpx.HTTPError as e:
                raise BotError(f"Нет связи с Telegram: {type(e).__name__}") from e
            try:
                data = r.json()
            except ValueError as e:
                raise BotError(f"Telegram ответил не так, как ждали ({r.status_code})", r.status_code) from e
            if r.status_code == 429 and attempt == 0 and method != "getUpdates":
                wait = float((data.get("parameters") or {}).get("retry_after", 2))
                await asyncio.sleep(min(30.0, max(0.5, wait)))  # Telegram просит подождать: ждём и пробуем ещё раз
                continue
            break
        if not data.get("ok"):
            desc = data.get("description", "ошибка")
            if r.status_code == 401:
                desc = "Токен бота не подошёл"
            raise BotError(desc, r.status_code)
        return data["result"]

    async def close(self) -> None:
        await self.client.aclose()


def esc(s: Any) -> str:
    return html.escape(str(s), quote=False)


def kb(*rows: list[dict]) -> dict:
    return {"inline_keyboard": [list(r) for r in rows if r]}


def btn(text: str, data: str | None = None, url: str | None = None) -> dict:
    b: dict[str, Any] = {"text": text}
    if url:
        b["url"] = url
    else:
        b["callback_data"] = data
    return b


class BotService:
    """Логика бота. Отправка уходит в BotApi, а решения принимаются здесь."""

    def __init__(self, api: BotApi, db: Database, settings: Settings, pipeline: Pipeline,
                 owner_id: int | None, scan: Callable[[bool], Awaitable[dict]] | None = None,
                 add_chat: Callable[[str], Awaitable[int]] | None = None):
        self.api = api
        self.db = db
        self.settings = settings
        self.pipeline = pipeline
        self.owner_id = owner_id or self.db.get_setting("owner_id") or None
        self.scan = scan
        self._tasks: set[asyncio.Task] = set()
        self.add_chat = add_chat
        self.username: str | None = None
        self._offset = 0
        self._stop = False

    # ------------------------------------------------------ настройка ----
    async def setup(self) -> dict:
        me = await self.api.call("getMe")
        self.username = me.get("username")
        self.db.set_setting("bot_username", self.username)
        try:
            await self.api.call("deleteWebhook", drop_pending_updates=False)
            await self.api.call("setMyCommands", commands=[
                {"command": "digest", "description": "Лучшие заказы за ночь"},
                {"command": "stats", "description": "Итоги недели"},
                {"command": "chats", "description": "Какие чаты читаю"},
                {"command": "add", "description": "Добавить чат по ссылке"},
                {"command": "pause", "description": "Отключить уведомления"},
                {"command": "resume", "description": "Включить уведомления"},
                {"command": "app", "description": "Открыть приложение"},
            ])
        except BotError:
            log.debug("setMyCommands failed", exc_info=True)
        if not self.owner_id and not self.db.get_setting("bot_pair"):
            self.db.set_setting("bot_pair", pysecrets.token_urlsafe(6))
        return me

    def pair_link(self) -> str | None:
        code = self.db.get_setting("bot_pair")
        if self.owner_id or not code or not self.username:
            return None
        return f"https://t.me/{self.username}?start={code}"

    @property
    def paired(self) -> bool:
        return bool(self.owner_id)

    def app_url(self, order_id: int | None = None) -> str | None:
        # с облачным сайтом кнопки в Telegram ведут на него: он открывается на телефоне, когда компьютер выключен
        cloud = self.settings.cloud_url or (self.db.get_setting("cloud_config") or {}).get("url") or ""
        base = (cloud or self.settings.public_url).rstrip("/")
        if not base.startswith("https://"):
            return None
        return f"{base}/#/o/{order_id}" if order_id else base

    # --------------------------------------------------- форматирование ----
    def card_text(self, row: dict, header: str | None = None) -> str:
        profile = load_profile(self.db)
        c = order_card(row, profile)
        icon = "🔥" if c["hot"] else "⚠️" if c["verdict"] == "scam" else "•"
        bits = [f"<b>{c['score']}%</b>", esc(c["budget"]["label"]), esc(c["deadline_label"])]
        if c["rate_label"]:
            bits.append(esc(c["rate_label"]))
        lines = [f"{icon} <b>{esc(c['title'])}</b>", " · ".join(bits)]
        if c["why"]:
            lines.append(esc(c["why"]))
        if c["flag"]:
            lines.append(f"⚠️ {esc(c['flag'])}")
        meta = []
        if c["age_label"]:
            meta.append(esc(c["age_label"]) + " назад")
        meta.append(f"откликов: {c['bids']}")
        if c["source"]:
            meta.append(esc(c["source"]))
        if c["dups"]:
            meta.append(f"ещё в {c['dups']} чат." if c["dups"] > 1 else "ещё в 1 чате")
        lines.append("<i>" + " · ".join(meta) + "</i>")
        text = "\n".join(lines)
        return (f"{esc(header)}\n\n" + text) if header else text

    def card_keyboard(self, row: dict) -> dict:
        oid = row["id"]
        rows: list[list[dict]] = []
        if row["verdict"] != "scam":
            rows.append([btn("✉️ Предложить", f"offer:{oid}"), btn("👍", f"fb:up:{oid}"), btn("👎", f"fb:dn:{oid}")])
        else:
            rows.append([btn("Пропустить", f"fb:dn:{oid}"), btn("Это не обман", f"fb:up:{oid}")])
        tail = []
        url = self.app_url(oid)
        if url:
            tail.append(btn("Открыть в приложении", url=url))
        if _TG_URL.fullmatch(row.get("contact_url") or ""):  # Telegram отвергает кнопку с любой другой ссылкой, и пропадает вся карточка
            tail.append(btn("Написать", url=row["contact_url"]))
        if tail:
            rows.append(tail)
        return kb(*rows)

    # ------------------------------------------------------- отправка ----
    async def send_card(self, row: dict, header: str | None = None) -> None:
        if not self.owner_id:
            return
        text = self.card_text(row, header)[:4000]
        try:
            await self.api.call("sendMessage", chat_id=self.owner_id, text=text, parse_mode="HTML",
                                reply_markup=self.card_keyboard(row), link_preview_options={"is_disabled": True})
        except BotError as e:
            if e.code != 400:
                raise
            # чаще всего виноваты кнопки со ссылками: отправляем карточку с простыми кнопками
            oid = row["id"]
            plain = kb([btn("✉️ Предложить", f"offer:{oid}"), btn("👍", f"fb:up:{oid}"), btn("👎", f"fb:dn:{oid}")])
            await self.api.call("sendMessage", chat_id=self.owner_id, text=text, parse_mode="HTML", reply_markup=plain,
                                link_preview_options={"is_disabled": True})

    async def _ack(self, cb_id: str, text: str | None = None) -> None:
        """Подтверждение нажатия. Устаревшее нажатие (Telegram отвечает 400) не должно отменять само действие."""
        try:
            await self.api.call("answerCallbackQuery", callback_query_id=cb_id, text=text)
        except BotError:
            log.debug("callback ack failed", exc_info=True)

    async def send_text(self, text: str, markup: dict | None = None) -> None:
        if not self.owner_id:
            return
        await self.api.call("sendMessage", chat_id=self.owner_id, text=text[:4000], parse_mode="HTML", reply_markup=markup,
                            link_preview_options={"is_disabled": True})

    async def send_digest(self, rows: list[dict], title: str, note: str = "") -> None:
        if not rows or not self.owner_id:
            return
        lines = [f"<b>{esc(title)}</b>", ""]
        buttons: list[list[dict]] = []
        for i, r in enumerate(rows, 1):
            c = order_card(r, load_profile(self.db))
            lines.append(f"{i}. <b>{esc(clip(c['title'], 60))}</b>\n    {c['score']}% · {esc(c['budget']['label'])} · {esc(c['deadline_label'])}")
            buttons.append([btn(f"{i}. {clip(c['title'], 34)}", f"show:{r['id']}")])
        if note:
            lines += ["", esc(note)]
        url = self.app_url()
        if url:
            buttons.append([btn("Открыть приложение", url=url)])
        await self.send_text("\n".join(lines), kb(*buttons))

    # ---------------------------------------------------------- обновления ----
    async def run(self) -> None:
        """Бесконечный опрос Telegram. Останавливается отменой задачи."""
        backoff = 1.0
        while not self._stop:
            try:
                updates = await self.api.call("getUpdates", offset=self._offset, timeout=25,
                                              allowed_updates=["message", "callback_query"])
                backoff = 1.0
                for u in updates:
                    self._offset = max(self._offset, u["update_id"] + 1)
                    try:
                        await self.handle_update(u)
                    except Exception:
                        log.exception("update failed")
            except asyncio.CancelledError:
                raise
            except BotError as e:
                if e.code == 401:
                    log.error("bot token rejected")
                    return
                if e.code == 409:  # где-то ещё запущен опрос с тем же токеном
                    log.warning("bot conflict: another instance is polling")
                await asyncio.sleep(min(30.0, backoff))
                backoff *= 2
            except Exception:
                log.exception("polling failed")
                await asyncio.sleep(min(30.0, backoff))
                backoff *= 2

    async def handle_update(self, u: dict) -> None:
        if "callback_query" in u:
            await self.on_callback(u["callback_query"])
        elif "message" in u and u["message"].get("text") is not None:
            await self.on_message(u["message"])

    # ---------------------------------------------------------- сообщения ----
    def _is_owner(self, user_id: int | None) -> bool:
        return bool(self.owner_id and user_id == self.owner_id)

    async def on_message(self, m: dict) -> None:
        user_id = (m.get("from") or {}).get("id")
        chat_id = m["chat"]["id"]
        text = (m.get("text") or "").strip()
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()

        if cmd == "/start":
            code = self.db.get_setting("bot_pair")
            if not self.owner_id and code and arg == code:
                self.owner_id = user_id
                self.db.set_setting("owner_id", user_id)
                self.db.del_setting("bot_pair")
                await self.api.call("sendMessage", chat_id=chat_id, parse_mode="HTML", text=(
                    "<b>Готово, бот подключён.</b>\n\nСюда придут свежие заказы, которые подходят вам, и утренняя сводка. "
                    "Под каждым заказом есть кнопки: 👍 подходит, 👎 не то, ✉️ предложить.\n\n/digest лучшее за ночь\n/stats итоги недели\n/pause тишина"))
                return
            if self._is_owner(user_id):
                await self.api.call("sendMessage", chat_id=chat_id, parse_mode="HTML",
                                    text="Я на месте. Свежие заказы пришлю сам, а сводку можно запросить командой /digest.")
                return
            await self.api.call("sendMessage", chat_id=chat_id, text=(
                "Это личный бот. Чтобы подключить его, откройте приложение Scout, раздел «Профиль», и нажмите кнопку подключения бота."))
            return

        if not self._is_owner(user_id):
            await self.api.call("sendMessage", chat_id=chat_id, text="Это личный бот, ответить не могу.")
            return

        if cmd == "/digest":
            await self.cmd_digest()
        elif cmd == "/stats":
            await self.cmd_stats()
        elif cmd == "/chats":
            await self.cmd_chats()
        elif cmd == "/add":
            await self.cmd_add(arg)
        elif cmd == "/pause":
            n = load_notifications(self.db)
            n.paused = True
            save_notifications(self.db, n)
            await self.send_text("Уведомления выключены. Включить: /resume. Заказы я всё равно собираю.")
        elif cmd == "/resume":
            n = load_notifications(self.db)
            n.paused = False
            save_notifications(self.db, n)
            await self.send_text("Уведомления включены.")
        elif cmd == "/app":
            url = self.app_url()
            await self.send_text("Приложение: " + esc(url) if url else "Адрес сайта не задан: подключите облачный сайт в Профиле (или задайте LS_PUBLIC_URL).")
        elif cmd in {"/help", "/?"}:
            await self.send_text("/digest · /stats · /chats · /add ссылка · /pause · /resume · /app")
        elif text.startswith("https://t.me/") or text.startswith("t.me/"):
            await self.cmd_add(text)
        else:
            await self.send_text("Не понял. Команды: /digest, /stats, /chats, /add, /pause, /resume, /app")

    async def cmd_digest(self) -> None:
        rows = self.db.all(
            store.ORDER_SELECT + " WHERE o.verdict IN ('match','maybe') AND o.state='new' AND o.feedback IS NULL AND o.offered_at IS NULL "
            "AND c.is_demo=0 ORDER BY (o.verdict='match') DESC, o.score DESC, o.posted_at DESC LIMIT 10")
        if not rows:
            await self.send_text("Свежих подходящих заказов сейчас нет. Как только появятся, напишу.")
            return
        await self.send_digest(rows, "Лучшие заказы сейчас")

    async def cmd_stats(self) -> None:
        from . import stats  # noqa: PLC0415

        s = stats.build(self.db, load_profile(self.db).tz)
        t = {x["id"]: x for x in s["tiles"]}
        acc = s["accuracy"]["current"]
        lines = [
            "<b>Итоги недели</b>",
            f"Отобрано: {t['selected']['value']} ({esc(t['selected']['note'])})",
            f"Предложений: {t['offers']['value']}",
            f"Взято в работу: {t['work']['value']} ({esc(t['work']['note'])})",
        ]
        if acc is not None:
            lines.append(f"Точность подбора: {acc}%")
        await self.send_text("\n".join(lines))

    async def cmd_chats(self) -> None:
        chats = store.list_chats(self.db)
        if not chats:
            await self.send_text("Чатов пока нет. Добавьте ссылкой: /add https://t.me/имя_чата")
            return
        lines = ["<b>Читаю чаты</b>"]
        for c in chats[:40]:
            mark = "✅" if c["enabled"] else "⏸"
            err = f" — {esc(clip(c['last_error'], 80))}" if c.get("last_error") else ""
            lines.append(f"{mark} {esc(c['title'])}{err}")
        if len(chats) > 40:
            lines.append(f"…и ещё {len(chats) - 40}. Полный список в приложении.")
        await self.send_text("\n".join(lines))

    async def cmd_add(self, ref: str) -> None:
        if not ref:
            await self.send_text("Пришлите ссылку: /add https://t.me/имя_чата")
            return
        if self.add_chat is None:
            await self.send_text("Добавление чатов сейчас недоступно: Telegram-аккаунт не подключён.")
            return
        try:
            await self.add_chat(ref)
        except Exception as e:  # текст ошибки понятен человеку
            await self.send_text("Не получилось: " + esc(str(e)))
            return
        await self.send_text("Чат добавлен. Начну читать при ближайшей проверке.")
        if self.scan:
            task = asyncio.create_task(self.scan(True))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------ кнопки ----
    async def on_callback(self, cb: dict) -> None:
        user_id = (cb.get("from") or {}).get("id")
        cb_id = cb["id"]
        if not self._is_owner(user_id):
            await self._ack(cb_id, "Это личный бот")
            return
        data = cb.get("data") or ""
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")
        parts = data.split(":")
        kind = parts[0]
        try:
            oid = int(parts[-1])
        except ValueError:
            await self._ack(cb_id)
            return
        row = store.get_order(self.db, oid)
        if row is None:
            await self._ack(cb_id, "Этого заказа уже нет")
            return

        if kind == "fb":
            if parts[1] == "up":
                self.pipeline.teach(row.get("text") or "", True)
                store.set_feedback(self.db, oid, "up")
                if row["state"] == "hidden":
                    store.set_state(self.db, oid, "new")
                await self._ack(cb_id, "Запомнил: похожих станет больше")
                await self._mark(chat_id, message_id, row, "👍")
            else:
                rows = [[btn(label, f"why:{code}:{oid}")] for code, label in store.FEEDBACK_REASONS.items()]
                await self._ack(cb_id)
                await self.api.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=kb(*rows))
        elif kind == "why":
            code = parts[1]
            if code == "notorder":
                self.pipeline.teach(row.get("text") or "", False)
            store.set_feedback(self.db, oid, "down", code)
            store.set_state(self.db, oid, "hidden")
            await self._ack(cb_id, "Скрыл. Учту при подборе")
            await self._mark(chat_id, message_id, row, "👎 " + store.FEEDBACK_REASONS.get(code, ""), drop_buttons=True)
        elif kind == "save":
            store.set_state(self.db, oid, "saved" if row["state"] != "saved" else "new")
            await self._ack(cb_id, "Отложено")
        elif kind == "show":
            await self._ack(cb_id)
            await self.send_card(row)
        elif kind == "offer":
            await self._ack(cb_id)
            await self.send_offer(row, 0)
        elif kind == "redo":
            variant = int(parts[1])
            await self._ack(cb_id)
            await self.send_offer(row, variant)
        elif kind == "sent":
            store.mark_offered(self.db, oid, self.pipeline.default_price(row))
            await self._ack(cb_id, "Отметил: предложение отправлено")
            await self._mark(chat_id, message_id, row, "✉️ отправлено", drop_buttons=True, keep_text=True)
        else:
            await self._ack(cb_id)

    async def _mark(self, chat_id: int, message_id: int, row: dict, tag: str, *, drop_buttons: bool = False,
                    keep_text: bool = False) -> None:
        """Помечаем карточку: добавляем в конец отметку и, если нужно, убираем кнопки."""
        if keep_text:
            markup = {"inline_keyboard": []}
            try:
                await self.api.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=markup)
            except BotError:
                pass
            return
        text = self.card_text(row) + f"\n\n<b>{esc(tag)}</b>"
        markup = {"inline_keyboard": []} if drop_buttons else self.card_keyboard(row)
        try:
            await self.api.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML",
                                reply_markup=markup, link_preview_options={"is_disabled": True})
        except BotError:
            log.debug("edit failed", exc_info=True)

    async def send_offer(self, row: dict, variant: int) -> None:
        profile = load_profile(self.db)
        draft = await self.pipeline.make_draft(row["id"], profile.tone, variant, profile=profile)
        price = draft.get("price")
        head = "<b>Черновик предложения</b>" + (f" · цена {esc(fmt_money(price))}" if price else "")
        text = f"{head}\n\n<code>{esc(draft['text'])}</code>\n\n<i>Нажмите на текст, чтобы скопировать.</i>"
        rows = [[btn("🔁 Другой вариант", f"redo:{variant + 1}:{row['id']}"), btn("✅ Отправил", f"sent:{row['id']}")]]
        if (row.get("contact_url") or "").startswith("https://"):
            rows.insert(0, [btn("Открыть чат", url=row["contact_url"])])
        await self.send_text(text, kb(*rows))

