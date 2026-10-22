"""Чтение чатов из вашего аккаунта Telegram (только чтение) и вход в аккаунт через приложение."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from telethon import TelegramClient, errors, events, functions, types, utils
from telethon.sessions import StringSession

from .. import store
from ..db import Database
from ..models import RawMessage
from ..util import iso, parse_iso

log = logging.getLogger("leadscout.telegram")

Listener = Callable[[RawMessage], Awaitable[None]]

FIRST_SCAN_LIMIT = 500
FIRST_SCAN_HOURS = 72
CATCHUP_LIMIT = 1000

AUTH_LOST = (
    errors.AuthKeyUnregisteredError,
    errors.SessionRevokedError,
    errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
    errors.AuthKeyDuplicatedError,
)


class TelegramProblem(Exception):
    """Ошибка, понятная человеку: её текст можно показывать в приложении."""


def _client(session: str, api_id: int, api_hash: str) -> TelegramClient:
    return TelegramClient(
        StringSession(session or ""), api_id, api_hash,
        device_model="Lead Scout", app_version="1.0", lang_code="ru", system_lang_code="ru",
        flood_sleep_threshold=20,
    )


def sender_info(sender: Any) -> dict:
    """Что берём из профиля отправителя. Каналы и боты не считаем людьми-заказчиками."""
    if isinstance(sender, types.User):
        return {
            "id": sender.id,
            "username": sender.username if not sender.bot else None,
            "name": " ".join(x for x in [sender.first_name, sender.last_name] if x) or None,
            "flags": {
                "scam": bool(getattr(sender, "scam", False)),
                "fake": bool(getattr(sender, "fake", False)),
                "premium": bool(getattr(sender, "premium", False)),
                "bot": bool(sender.bot),
            },
        }
    if sender is not None:
        return {"id": getattr(sender, "id", None), "username": None, "name": utils.get_display_name(sender), "flags": {}}
    return {"id": None, "username": None, "name": None, "flags": {}}


def _hidden_links(msg: Any) -> list[str]:
    """Ссылки, спрятанные за словами («Писать сюда»): иначе контакт заказчика теряется."""
    out: list[str] = []
    for ent in getattr(msg, "entities", None) or []:
        url = getattr(ent, "url", None)
        if isinstance(ent, types.MessageEntityTextUrl) and isinstance(url, str) and ("t.me/" in url or url.startswith("mailto:")):
            out.append(url)
    return out[:3]


def message_to_raw(msg: Any, chat_row: dict, sender: Any) -> RawMessage | None:
    text = getattr(msg, "message", None) or ""
    if not text.strip():
        return None
    extra = [u for u in _hidden_links(msg) if u not in text]
    if extra:
        text = text + "\n" + " ".join(extra)
    info = sender_info(sender)
    posted = msg.date
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    reply_to = None
    rt = getattr(msg, "reply_to", None)
    if rt is not None and getattr(rt, "reply_to_msg_id", None):
        reply_to = rt.reply_to_msg_id
    replies = getattr(getattr(msg, "replies", None), "replies", 0) or 0
    return RawMessage(
        chat_id=chat_row["id"], tg_msg_id=msg.id, text=text, posted_at=posted.astimezone(UTC),
        author_id=info["id"] or getattr(msg, "sender_id", None), author_username=info["username"],
        author_name=info["name"], author_flags=info["flags"], reply_to=reply_to, replies_count=replies,
        link=store.chat_link(chat_row, msg.id),
    )


def describe_error(e: Exception) -> str:
    if isinstance(e, errors.FloodWaitError):
        return f"Telegram просит подождать {e.seconds} сек."
    if isinstance(e, (errors.ChannelPrivateError, errors.ChatForbiddenError, errors.ChannelBannedError, errors.UserBannedInChannelError)):
        return "Нет доступа к чату: закрытый, или вас из него убрали"
    if isinstance(e, (errors.UsernameNotOccupiedError, errors.UsernameInvalidError)):
        return "Такого чата или канала нет"
    if isinstance(e, (errors.InviteHashExpiredError, errors.InviteHashInvalidError)):
        return "Ссылка-приглашение недействительна"
    if isinstance(e, AUTH_LOST):
        return "Telegram разлогинил аккаунт. Войдите заново"
    return f"Ошибка Telegram: {type(e).__name__}"


class TelegramReader:
    name = "telegram"

    def __init__(self, db: Database, api_id: int, api_hash: str, session: str):
        self.db = db
        self.api_id, self.api_hash, self.session = api_id, api_hash, session
        self.client: TelegramClient | None = None
        self.listener: Listener | None = None
        self.state = "off"  # off | connecting | ready | unauthorized | error
        self.error: str | None = None
        self.me: dict | None = None
        self._entities: dict[int, Any] = {}
        self._by_tg: dict[int, int] = {}
        self._dialogs_loaded = False
        self._handler = None
        self.seen: dict[int, int] = {}  # чат -> самый новый просмотренный id, включая сообщения без текста

    # ------------------------------------------------------------ жизнь ----
    async def start(self) -> None:
        self.state = "connecting"
        try:
            self.client = _client(self.session, self.api_id, self.api_hash)
            await self.client.connect()
            if not await self.client.is_user_authorized():
                self.state, self.error = "unauthorized", "Войдите в Telegram заново"
                return
            me = await self.client.get_me()
            self.me = {"name": utils.get_display_name(me), "username": me.username, "id": me.id}
            self._handler = self._on_new
            self.client.add_event_handler(self._handler, events.NewMessage(incoming=True))
            self.refresh_map()
            self.state, self.error = "ready", None
        except AUTH_LOST as e:
            self.state, self.error = "unauthorized", describe_error(e)
        except Exception as e:
            log.exception("telegram start failed")
            self.state, self.error = "error", describe_error(e)

    async def stop(self) -> None:
        if self.client is not None:
            try:
                if self._handler:
                    self.client.remove_event_handler(self._handler)
                await self.client.disconnect()
            except Exception:
                log.debug("disconnect failed", exc_info=True)
        self.client = None
        self.state = "off"

    @property
    def ready(self) -> bool:
        return self.state == "ready" and self.client is not None

    def status(self) -> dict:
        return {"state": self.state, "error": self.error, "me": self.me}

    def refresh_map(self) -> None:
        self._by_tg = {c["tg_id"]: c["id"] for c in self.db.all("SELECT id, tg_id FROM chats WHERE enabled=1 AND tg_id IS NOT NULL")}

    # ------------------------------------------------------------ живые ----
    async def _on_new(self, event: Any) -> None:
        try:
            chat_pk = self._by_tg.get(event.chat_id)
            if chat_pk is None or self.listener is None:
                return
            row = self.db.one("SELECT * FROM chats WHERE id=?", (chat_pk,))
            if not row:
                return
            sender = await event.message.get_sender()
            raw = message_to_raw(event.message, row, sender)
            if raw:
                await self.listener(raw)
        except Exception:
            log.exception("live message failed")

    # ------------------------------------------------------- получение ----
    async def _entity(self, row: dict) -> Any:
        assert self.client is not None
        tg = row.get("tg_id")
        if tg in self._entities:
            return self._entities[tg]
        ent = None
        if tg is not None:
            try:
                ent = await self.client.get_entity(tg)
            except ValueError:
                if not self._dialogs_loaded:
                    await self.client.get_dialogs()  # заполняет кэш сущностей
                    self._dialogs_loaded = True
                    try:
                        ent = await self.client.get_entity(tg)
                    except ValueError:
                        ent = None
        if ent is None and row.get("username"):
            ent = await self.client.get_entity(row["username"])
        if ent is None:
            raise TelegramProblem("Не нашёл чат в вашем аккаунте. Откройте его в Telegram и нажмите «Обновить».")
        if tg is not None:
            self._entities[tg] = ent
        return ent

    async def fetch(self) -> list[RawMessage]:
        """Догоняет пропущенное во всех включённых чатах."""
        if not self.ready:
            return []
        assert self.client is not None
        if not self.client.is_connected():
            try:
                await asyncio.wait_for(self.client.connect(), 20)  # после обрыва сети Telethon сам не оживает
            except Exception:
                self.state, self.error = "error", "Нет связи с Telegram. Пробую подключиться снова"
                return []
        out: list[RawMessage] = []
        self.seen = {}
        for row in self.db.all("SELECT * FROM chats WHERE enabled=1 AND is_demo=0"):
            try:
                out.extend(await self._fetch_chat(row))
            except errors.FloodWaitError as e:
                self._chat_error(row["id"], describe_error(e))
                if e.seconds <= 30:
                    await asyncio.sleep(e.seconds + 1)
            except AUTH_LOST as e:
                self.state, self.error = "unauthorized", describe_error(e)
                return out
            except TelegramProblem as e:
                self._chat_error(row["id"], str(e))
            except Exception as e:
                log.warning("chat %s failed: %r", row["title"], e)
                self._chat_error(row["id"], describe_error(e))
            await asyncio.sleep(1.0)  # не торопим Telegram
        return out

    async def _fetch_chat(self, row: dict) -> list[RawMessage]:
        assert self.client is not None
        ent = await self._entity(row)
        first = row["last_msg_id"] == 0
        out: list[RawMessage] = []
        senders: dict[int, Any] = {}
        newest = 0
        last = parse_iso(row.get("last_scan_at"))
        stale = (datetime.now(UTC) - last) if last else None
        if first:
            horizon = datetime.now(UTC) - timedelta(hours=FIRST_SCAN_HOURS)
            it = self.client.iter_messages(ent, limit=FIRST_SCAN_LIMIT)
        elif stale is not None and stale > timedelta(hours=6):
            # давно не читали (сервер был выключен): идём от недавнего времени, а не от старого курсора, чтобы свежее не ждало часами
            start = max(last - timedelta(minutes=10), datetime.now(UTC) - timedelta(hours=FIRST_SCAN_HOURS))
            horizon = None
            it = self.client.iter_messages(ent, offset_date=start, limit=CATCHUP_LIMIT, reverse=True)
        else:
            horizon = None
            it = self.client.iter_messages(ent, min_id=row["last_msg_id"], limit=CATCHUP_LIMIT, reverse=True)
        async for msg in it:
            if horizon and msg.date < horizon:
                break
            newest = max(newest, getattr(msg, "id", 0) or 0)
            if getattr(msg, "action", None) is not None or getattr(msg, "out", False):
                continue  # служебные и ваши собственные сообщения не заказы
            sid = getattr(msg, "sender_id", None)
            sender = senders.get(sid)
            if sender is None and sid is not None:
                try:
                    sender = await msg.get_sender()
                except Exception:
                    sender = None
                senders[sid] = sender
            raw = message_to_raw(msg, row, sender)
            if raw:
                out.append(raw)
        if first:
            out.reverse()  # сначала старые
        if newest:
            self.seen[row["id"]] = newest
        self.db.run("UPDATE chats SET last_scan_at=?, last_error=NULL WHERE id=?", (iso(), row["id"]))
        if not out and first and row["last_msg_id"] == 0:
            # пустой чат: запоминаем, что первый просмотр был
            latest = await self.client.get_messages(ent, limit=1)
            if latest:
                store.bump_chat_cursor(self.db, row["id"], latest[0].id)
        return out

    def commit_cursors(self) -> None:
        """Сообщения обработаны: двигаем курсоры, в том числе через картинки и служебные записи без текста."""
        for chat_id, msg_id in self.seen.items():
            store.bump_chat_cursor(self.db, chat_id, msg_id)
        self.seen = {}

    def _chat_error(self, chat_id: int, text: str) -> None:
        self.db.run("UPDATE chats SET last_error=?, last_scan_at=? WHERE id=?", (text, iso(), chat_id))

    # --------------------------------------------------------- чаты ----
    async def list_dialogs(self) -> list[dict]:
        """Группы и каналы, в которых вы уже состоите: выбирайте из списка вместо ссылок."""
        if not self.ready:
            raise TelegramProblem("Сначала подключите Telegram")
        assert self.client is not None
        have = {c["tg_id"] for c in self.db.all("SELECT tg_id FROM chats WHERE tg_id IS NOT NULL")}
        out = []
        async for d in self.client.iter_dialogs(limit=400):
            ent = d.entity
            if isinstance(ent, types.User) or not (d.is_group or d.is_channel):
                continue
            self._entities[d.id] = ent
            out.append({
                "tg_id": d.id,
                "title": d.name,
                "username": getattr(ent, "username", None),
                "kind": "channel" if getattr(ent, "broadcast", False) else "group",
                "added": d.id in have,
            })
        self._dialogs_loaded = True
        return out

    async def add_dialog(self, tg_id: int) -> int:
        if not self.ready:
            raise TelegramProblem("Сначала подключите Telegram")
        assert self.client is not None
        ent = self._entities.get(tg_id)
        if ent is None:
            try:
                ent = await self.client.get_entity(tg_id)
            except ValueError as e:
                raise TelegramProblem("Не нашёл этот чат в вашем аккаунте") from e
        return await self._register(ent, link=None)

    async def add_by_link(self, ref: str) -> int:
        """Добавляет чат по ссылке или @имени, при необходимости вступает в него."""
        if not self.ready:
            raise TelegramProblem("Сначала подключите Telegram")
        assert self.client is not None
        parsed = store.parse_chat_ref(ref)
        try:
            if parsed["kind"] == "bad":
                raise TelegramProblem("Не похоже на ссылку на чат. Пример: https://t.me/имя_чата")
            if parsed["kind"] == "public":
                ent = await self.client.get_entity(parsed["username"])
                if isinstance(ent, types.User):
                    raise TelegramProblem("Это личный аккаунт, а не чат или канал")
                try:
                    await self.client(functions.channels.JoinChannelRequest(ent))
                except errors.UserAlreadyParticipantError:
                    pass
                except errors.RPCError:
                    log.debug("join skipped", exc_info=True)  # читать можно и без вступления в публичный канал
                return await self._register(ent, link=parsed["link"])
            h = parsed["hash"]
            info = await self.client(functions.messages.CheckChatInviteRequest(h))
            if isinstance(info, types.ChatInviteAlready):
                ent = info.chat
            else:
                try:
                    upd = await self.client(functions.messages.ImportChatInviteRequest(h))
                    ent = upd.chats[0]
                except errors.UserAlreadyParticipantError:
                    raise TelegramProblem("Вы уже в этом чате: выберите его в списке «Мои чаты»") from None
            return await self._register(ent, link=parsed["link"])
        except TelegramProblem:
            raise
        except errors.RPCError as e:
            raise TelegramProblem(describe_error(e)) from e
        except ValueError as e:
            raise TelegramProblem("Не нашёл такой чат") from e

    async def _register(self, ent: Any, link: str | None) -> int:
        assert self.client is not None
        tg_id = utils.get_peer_id(ent)
        members = getattr(ent, "participants_count", None)
        if members is None and isinstance(ent, types.Channel):
            try:
                full = await self.client(functions.channels.GetFullChannelRequest(ent))
                members = full.full_chat.participants_count
            except Exception:
                members = None
        chat_id = store.add_chat(
            self.db, utils.get_display_name(ent) or "Чат", link=link, username=getattr(ent, "username", None),
            tg_id=tg_id, members=members,
        )
        self._entities[tg_id] = ent
        self.refresh_map()
        return chat_id


# ------------------------------------------------------------------ вход ----


class LoginError(Exception):
    """Понятная ошибка входа."""


class TelegramLogin:
    """Вход в аккаунт по номеру, коду и (если включён) паролю. Всё происходит на вашем сервере."""

    def __init__(self, api_id: int, api_hash: str):
        self.api_id, self.api_hash = api_id, api_hash
        self.client: TelegramClient | None = None
        self.phone = ""
        self.code_hash = ""
        self.created = datetime.now(UTC)

    async def send_code(self, phone: str) -> None:
        phone = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
        if not phone.startswith("+"):
            phone = "+" + phone
        self.phone = phone
        self.client = _client("", self.api_id, self.api_hash)
        try:
            await self.client.connect()
            sent = await self.client.send_code_request(phone)
            self.code_hash = sent.phone_code_hash
        except Exception as e:
            await self.close()  # не оставляем открытое соединение при любой ошибке
            raise self._login_error(e) from e

    @staticmethod
    def _login_error(e: Exception) -> LoginError:
        if isinstance(e, errors.PhoneNumberInvalidError):
            return LoginError("Номер не подошёл. Введите его с кодом страны, например +79991234567")
        if isinstance(e, errors.ApiIdInvalidError):
            return LoginError("api_id или api_hash неверные. Проверьте их на my.telegram.org")
        if isinstance(e, errors.PhoneNumberBannedError):
            return LoginError("Этот номер заблокирован в Telegram")
        if isinstance(e, errors.FloodWaitError):
            return LoginError(f"Слишком много попыток. Подождите {e.seconds // 60 + 1} мин.")
        if isinstance(e, errors.PhoneNumberFloodError):
            return LoginError("Слишком много запросов кода. Попробуйте позже")
        return LoginError(f"Не получилось отправить код: {type(e).__name__}")

    async def sign_in_code(self, code: str) -> str:
        """Возвращает 'ok' или 'password', если включена двухфакторная защита."""
        assert self.client is not None
        digits = "".join(ch for ch in code if ch.isdigit())
        if not digits:
            raise LoginError("Введите код из Telegram цифрами")
        try:
            await self.client.sign_in(self.phone, digits, phone_code_hash=self.code_hash)
            return "ok"
        except errors.SessionPasswordNeededError:
            return "password"
        except errors.PhoneCodeInvalidError as e:
            raise LoginError("Код неверный. Проверьте его в Telegram") from e
        except errors.PhoneCodeExpiredError as e:
            raise LoginError("Код устарел. Запросите новый") from e
        except errors.FloodWaitError as e:
            raise LoginError(f"Слишком много попыток. Подождите {e.seconds // 60 + 1} мин.") from e

    async def sign_in_password(self, password: str) -> None:
        assert self.client is not None
        try:
            await self.client.sign_in(password=password)
        except errors.PasswordHashInvalidError as e:
            raise LoginError("Пароль двухфакторной защиты неверный") from e
        except errors.FloodWaitError as e:
            raise LoginError(f"Слишком много попыток. Подождите {e.seconds // 60 + 1} мин.") from e

    def session_string(self) -> str:
        assert self.client is not None
        return self.client.session.save()

    async def close(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None


async def revoke_session(api_id: int, api_hash: str, session: str) -> None:
    """Выход: Telegram удалит эту сессию из списка устройств."""
    client = _client(session, api_id, api_hash)
    try:
        await client.connect()
        if await client.is_user_authorized():
            await client.log_out()
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
