"""Сердце приложения: собирает части вместе, запускает проверки по расписанию, управляет подключениями."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from . import ai_control, learning, store
from .bot import BotApi, BotError, BotService
from .config import Settings
from .db import Database
from .events import EventBus
from .llm.base import LLM, LLMUnavailable
from .llm.claude import ClaudeLLM, FallbackLLM
from .llm.openai_compat import PROVIDERS, OpenAICompatLLM
from .llm.rules import RuleLLM
from .models import RawMessage
from .notify import Notifier
from .pipeline import Pipeline
from .profile import load_profile
from .secrets_store import Secrets
from .sources.demo import DemoSource
from .sources.telegram import LoginError, TelegramLogin, TelegramProblem, TelegramReader, revoke_session
from .util import iso, now_utc, parse_iso

log = logging.getLogger("leadscout.runtime")

CONCURRENCY = 4
START_TIMEOUT = 25


class Runtime:
    def __init__(self, settings: Settings, db: Database | None = None):
        self.settings = settings
        settings.ensure_dirs()
        self.db = db or Database(settings.db_path)
        self.secrets = Secrets(self.db, settings)
        self.bus = EventBus()
        self.llm: LLM = RuleLLM()
        self.pipeline = Pipeline(self.db, self.llm, settings)
        self.source: DemoSource | TelegramReader | None = None
        self.bot: BotService | None = None
        self.bot_error: str | None = None
        self._bot_task: asyncio.Task | None = None
        self._loop_task: asyncio.Task | None = None
        self.notifier = Notifier(self.db, settings, lambda: self.bot)
        self._scan_lock = asyncio.Lock()
        self.scanning = False
        self.last_scan: dict | None = None
        self._last_scan_mono = 0.0
        self._login: TelegramLogin | None = None
        self._login_started = 0.0
        self._last_maint = 0.0
        self._reconnect_at = 0.0
        self._sem = asyncio.Semaphore(CONCURRENCY)

    # ------------------------------------------------------------ запуск ----
    async def start(self) -> None:
        self.build_llm()
        if self.db.get_setting("demo_enabled") is None:
            self.db.set_setting("demo_enabled", bool(self.settings.demo or not self._tg_creds()))
        await self.build_source()
        await self.build_bot()
        if self.settings.run_worker:
            self._loop_task = asyncio.create_task(self._loop(), name="scheduler")

    async def stop(self) -> None:
        for t in (self._loop_task, self._bot_task):
            if t:
                t.cancel()
        for t in (self._loop_task, self._bot_task):
            if t:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        if self.source:
            await self.source.stop()
        if self.bot:
            await self.bot.api.close()
        if self._login:
            await self._login.close()

    # --------------------------------------------------------------- ИИ ----
    def build_llm(self) -> None:
        key = self.secrets.get("anthropic_key")
        cfg = self.db.get_setting("ai_config")
        if key:
            self.llm = FallbackLLM(ClaudeLLM(key, self.settings.model_fast, self.settings.model_smart, db=self.db))
        elif cfg and cfg.get("base_url") and cfg.get("model"):
            self.llm = FallbackLLM(OpenAICompatLLM(
                cfg["base_url"], self.secrets.get("ai_key") or "", cfg["model"], db=self.db,
                rpm=int(cfg.get("rpm", 20)), rpd=int(cfg.get("rpd", 1000)), provider=cfg.get("provider", "custom")))
        else:
            self.llm = RuleLLM()
        self.pipeline.set_llm(self.llm)

    def llm_status(self) -> dict:
        primary = getattr(self.llm, "primary", None)
        mode = "rules" if self.llm.name == "rules" else ("claude" if self.llm.name == "claude" else "ai")
        err = getattr(self.llm, "last_error", None)
        usage = self.db.get_setting("usage:" + now_utc().strftime("%Y-%m"))
        out = {"mode": mode, "error": err, "usage": usage, "source": "none", "provider": None,
               "control": ai_control.state(self.db) if mode != "rules" else None}
        if mode == "claude":
            out["source"] = self.secrets.source("anthropic_key")
        elif mode == "ai":
            cfg = self.db.get_setting("ai_config") or {}
            out.update(source="app" if not self.secrets.source("ai_key") == "env" else "env", provider=cfg.get("provider"),
                       model=cfg.get("model"), used_today=primary.used_today(), limit_today=primary.rpd)
        return out

    async def connect_ai(self, provider: str, key: str, model: str = "", base_url: str = "") -> None:
        """Бесплатный или свой ИИ: проверяем одним коротким запросом и только потом сохраняем."""
        preset = PROVIDERS.get(provider)
        if preset is None:
            raise LLMUnavailable("Неизвестный сервис ИИ")
        base_url = (base_url or preset["base_url"]).strip()
        model = (model or preset["model"]).strip()
        key = key.strip()
        if not base_url.startswith(("http://", "https://")) or not model:
            raise LLMUnavailable("Укажите адрес сервиса (http:// или https://) и название модели")
        if preset["needs_key"] and len(key) < 8:
            raise LLMUnavailable("Вставьте ключ целиком. Его выдаёт сам сервис")
        probe = OpenAICompatLLM(base_url, key, model, rpm=preset["rpm"], rpd=preset["rpd"], provider=provider)
        try:
            await probe.check()
        finally:
            await probe.aclose()
        self.db.set_setting("ai_config", {"provider": provider, "base_url": base_url, "model": model,
                                          "rpm": preset["rpm"], "rpd": preset["rpd"]})
        if key:
            self.secrets.set("ai_key", key)
        else:
            self.secrets.delete("ai_key")
        self.build_llm()

    async def ai_models(self, provider: str, key: str, base_url: str = "") -> list[str]:
        preset = PROVIDERS.get(provider)
        if preset is None:
            raise LLMUnavailable("Неизвестный сервис ИИ")
        probe = OpenAICompatLLM((base_url or preset["base_url"]).strip(), key.strip(), preset["model"], provider=provider)
        try:
            return await probe.list_models()
        finally:
            await probe.aclose()

    def set_ai_control(self, patch: dict) -> dict:
        ai_control.save(self.db, patch)
        self.pipeline.reset_ai_state()
        self.bus.publish({"type": "status"})
        return self.llm_status()

    def disconnect_ai(self) -> None:
        self.db.del_setting("ai_config")
        self.secrets.delete("ai_key")
        self.build_llm()

    async def connect_claude(self, key: str) -> None:
        key = key.strip()
        if not key.startswith("sk-ant-") or len(key) < 30:
            raise LLMUnavailable("Это не похоже на ключ Claude. Он начинается с sk-ant-")
        probe = ClaudeLLM(key, self.settings.model_fast, self.settings.model_smart)
        await probe.check()
        self.secrets.set("anthropic_key", key)
        self.build_llm()

    def disconnect_claude(self) -> None:
        self.secrets.delete("anthropic_key")
        self.build_llm()

    # --------------------------------------------------------- Telegram ----
    def _tg_creds(self) -> tuple[int, str, str] | None:
        api_id, api_hash, session = (self.secrets.get(k) for k in ("tg_api_id", "tg_api_hash", "tg_session"))
        if api_id and api_hash and session:
            try:
                return int(api_id), api_hash, session
            except ValueError:
                return None
        return None

    def demo_enabled(self) -> bool:
        return bool(self.db.get_setting("demo_enabled", False))

    async def build_source(self) -> None:
        if self.source is not None:
            await self.source.stop()
            self.source = None
        creds = self._tg_creds()
        if creds:
            self.purge_demo()
            reader = TelegramReader(self.db, *creds)
            reader.listener = self.ingest_one
            try:
                await asyncio.wait_for(reader.start(), START_TIMEOUT)  # без связи с Telegram приложение всё равно открывается
            except TimeoutError:
                reader.state, reader.error = "error", "Telegram не отвечает. Проверьте интернет на сервере"
            self.source = reader
        elif self.demo_enabled():
            demo = DemoSource(self.db)
            await demo.start()
            self.source = demo

    def purge_demo(self) -> None:
        if self.db.scalar("SELECT 1 FROM chats WHERE is_demo=1"):
            self.db.run("DELETE FROM chats WHERE is_demo=1")
            self.db.del_setting("demo_tick")
        self.db.set_setting("demo_enabled", False)

    async def set_demo(self, on: bool) -> None:
        if on and self._tg_creds():
            raise TelegramProblem("Демо нельзя включить, пока подключён настоящий Telegram")
        if not on:
            self.purge_demo()
        else:
            self.db.set_setting("demo_enabled", True)
        await self.build_source()

    def tg_status(self) -> dict:
        has_id = self.secrets.has("tg_api_id") and self.secrets.has("tg_api_hash")
        base = {"has_api": has_id, "source": self.secrets.source("tg_session"), "logging_in": self._login is not None}
        if isinstance(self.source, TelegramReader):
            return {**base, **self.source.status()}
        return {**base, "state": "demo" if isinstance(self.source, DemoSource) else "off", "error": None, "me": None}

    async def tg_save_api(self, api_id: str, api_hash: str) -> None:
        try:
            n = int(api_id)
        except ValueError:
            raise LoginError("api_id это число, например 1234567") from None
        api_hash = api_hash.strip()
        if n <= 0 or len(api_hash) != 32:
            raise LoginError("Проверьте api_id и api_hash: хеш состоит из 32 символов")
        self.secrets.set("tg_api_id", str(n))
        self.secrets.set("tg_api_hash", api_hash)

    async def tg_login_start(self, phone: str) -> None:
        api_id, api_hash = self.secrets.get("tg_api_id"), self.secrets.get("tg_api_hash")
        if not api_id or not api_hash:
            raise LoginError("Сначала введите api_id и api_hash")
        if self._login:
            await self._login.close()
        self._login = TelegramLogin(int(api_id), api_hash)
        self._login_started = time.monotonic()
        try:
            await self._login.send_code(phone)
        except LoginError:
            self._login = None
            raise

    async def tg_login_code(self, code: str) -> str:
        login = self._active_login()
        res = await login.sign_in_code(code)
        if res == "ok":
            await self._finish_login(login)
        return res

    async def tg_login_password(self, password: str) -> None:
        login = self._active_login()
        await login.sign_in_password(password)
        await self._finish_login(login)

    def _active_login(self) -> TelegramLogin:
        if self._login is None or time.monotonic() - self._login_started > 900:
            self._login = None
            raise LoginError("Вход устарел. Начните заново")
        return self._login

    async def _finish_login(self, login: TelegramLogin) -> None:
        self.secrets.set("tg_session", login.session_string())
        await login.close()
        self._login = None
        await self.build_source()
        self.bus.publish({"type": "status"})

    async def tg_logout(self) -> None:
        creds = self._tg_creds()
        if creds and self.secrets.source("tg_session") == "app":
            try:
                await revoke_session(*creds)
            except Exception:
                log.debug("revoke failed", exc_info=True)
        if self.secrets.source("tg_session") == "app":
            self.secrets.delete("tg_session")
        await self.build_source()

    # ------------------------------------------------------------- бот ----
    async def build_bot(self) -> None:
        if self._bot_task:
            self._bot_task.cancel()
            try:
                await self._bot_task
            except (asyncio.CancelledError, Exception):
                pass
            self._bot_task = None
        if self.bot:
            await self.bot.api.close()
            self.bot = None
        self.bot_error = None
        token = self.secrets.get("bot_token")
        if not token:
            return
        service = BotService(BotApi(token), self.db, self.settings, self.pipeline, owner_id=int(self.secrets.get("owner_id") or 0) or None,
                             scan=self.scan, add_chat=self.add_chat)
        try:
            await service.setup()
        except BotError as e:
            self.bot_error = str(e)
            await service.api.close()
            return
        self.bot = service
        if self.settings.run_worker:
            self._bot_task = asyncio.create_task(service.run(), name="bot")

    async def connect_bot(self, token: str) -> dict:
        token = token.strip()
        if ":" not in token or len(token) < 30:
            raise BotError("Это не похоже на токен бота. Его выдаёт @BotFather, он выглядит как 123456:ABC-DEF…")
        probe = BotApi(token)
        try:
            await probe.call("getMe")
        finally:
            await probe.close()
        self.secrets.set("bot_token", token)
        await self.build_bot()
        return self.bot_status()

    async def disconnect_bot(self) -> None:
        self.secrets.delete("bot_token")
        self.db.del_setting("owner_id")
        self.db.del_setting("bot_pair")
        await self.build_bot()

    def bot_status(self) -> dict:
        if self.bot:
            return {"state": "ready", "username": self.bot.username, "paired": self.bot.paired, "pair_link": self.bot.pair_link(),
                    "error": None, "source": self.secrets.source("bot_token")}
        if self.bot_error:
            return {"state": "error", "error": self.bot_error, "username": None, "paired": False, "pair_link": None,
                    "source": self.secrets.source("bot_token")}
        return {"state": "off", "error": None, "username": None, "paired": False, "pair_link": None, "source": "none"}

    # ---------------------------------------------------------- чаты ----
    async def add_chat(self, ref: str) -> int:
        if self.source is None or not hasattr(self.source, "add_by_link"):
            raise TelegramProblem("Сначала подключите Telegram")
        chat_id = await self.source.add_by_link(ref)  # type: ignore[union-attr]
        self.bus.publish({"type": "chats"})
        return chat_id

    def chats_changed(self) -> None:
        if isinstance(self.source, TelegramReader):
            self.source.refresh_map()

    # ------------------------------------------------------ проверка ----
    async def ingest_one(self, raw: RawMessage) -> None:
        """Живое сообщение из Telegram."""
        ids = await self._ingest([raw], move_cursor=False)  # курсор двигает только полная проверка, иначе пропустим промежуток
        if ids:
            self.bus.publish({"type": "orders", "ids": ids})
            await self.notifier.after_scan(ids)

    async def _ingest(self, raws: list[RawMessage], move_cursor: bool = True) -> list[int]:
        new_ids: list[int] = []
        failed: set[tuple[int, int]] = set()

        async def one(raw: RawMessage) -> None:
            async with self._sem:
                try:
                    oid = await self.pipeline.process(raw)
                except Exception:
                    log.exception("message failed")
                    failed.add((raw.chat_id, raw.tg_msg_id))  # останется в базе необработанным, scan повторит
                    return
            if oid:
                new_ids.append(oid)

        plain = [r for r in raws if not r.reply_to]
        replies = [r for r in raws if r.reply_to]
        await asyncio.gather(*(one(r) for r in plain))
        for r in replies:  # ответы после объявлений: им нужно найти родителя
            await one(r)
        if move_cursor:
            for r in raws:
                if (r.chat_id, r.tg_msg_id) in failed:
                    continue
                store.bump_chat_cursor(self.db, r.chat_id, r.tg_msg_id)
        return new_ids

    async def scan(self, manual: bool = False) -> dict:
        if self._scan_lock.locked():
            return {"skipped": True}
        async with self._scan_lock:
            self.scanning = True
            self.bus.publish({"type": "scan", "running": True})
            run_id = self.db.run("INSERT INTO scan_runs(started_at, source) VALUES(?,?)", (iso(), getattr(self.source, "name", "none")))
            result: dict = {"messages": 0, "orders": 0, "new_ids": [], "error": None}
            try:
                src = self.source
                if src is None:
                    result["error"] = "Нет источника: подключите Telegram"
                else:
                    if manual and isinstance(src, DemoSource):
                        src.request_tick()
                    retried = await self.pipeline.retry_stuck()
                    raws = await src.fetch()
                    result["messages"] = len(raws)
                    ids = await self._ingest(raws)
                    if retried:
                        log.info("повторно обработано сообщений: %s", retried)
                    result["new_ids"], result["orders"] = ids, len(ids)
                    if isinstance(src, TelegramReader) and src.state != "ready":
                        result["error"] = src.error
                    await self.notifier.after_scan(ids)
            except Exception as e:
                log.exception("scan failed")
                result["error"] = f"{type(e).__name__}"
            finally:
                self.scanning = False
                self._last_scan_mono = time.monotonic()
                self.db.run("UPDATE scan_runs SET finished_at=?, messages=?, orders=?, error=? WHERE id=?",
                            (iso(), result["messages"], result["orders"], result["error"], run_id))
                self.last_scan = {"at": iso(), **{k: v for k, v in result.items() if k != "new_ids"}}
                self.db.set_setting("last_scan", self.last_scan)
            self.bus.publish({"type": "scan", "running": False, "orders": result["orders"], "ids": result["new_ids"]})
            if result["new_ids"]:
                self.bus.publish({"type": "orders", "ids": result["new_ids"]})
            return result

    def scan_status(self) -> dict:
        last = self.last_scan or self.db.get_setting("last_scan")
        nxt = None
        if self.settings.run_worker and self.source is not None:
            elapsed = time.monotonic() - self._last_scan_mono if self._last_scan_mono else 0
            nxt = max(0, int(self.settings.scan_minutes * 60 - elapsed))
        return {"running": self.scanning, "last": last, "next_in": nxt, "every_min": self.settings.scan_minutes}

    # ---------------------------------------------------- расписание ----
    async def _loop(self) -> None:
        await asyncio.sleep(2)
        while True:
            try:
                due = (time.monotonic() - self._last_scan_mono) >= self.settings.scan_minutes * 60
                await self._reconnect_telegram()
                if self.source is not None and due:
                    await self.scan()
                now = datetime.now(UTC)
                if self.notifier.digest_due(now):
                    await self.notifier.send_digest(now)
                if self.notifier.weekly_due(now):
                    await self.notifier.send_weekly(now)
                await self.notifier.flush_queue(now)
                await self.pipeline.upgrade_with_ai()
                await self._maintenance()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler step failed")
            await asyncio.sleep(20)

    async def _reconnect_telegram(self) -> None:
        """Telegram не ответил при запуске (нет сети, перезагрузка сервера): пробуем снова раз в несколько минут."""
        src = self.source
        if not isinstance(src, TelegramReader) or src.state not in {"error", "connecting"}:
            self._reconnect_at = 0.0
            return
        now = time.monotonic()
        if not self._reconnect_at:
            self._reconnect_at = now + 180
            return
        if now >= self._reconnect_at:
            self._reconnect_at = 0.0
            log.info("пробую подключиться к Telegram снова")
            await self.build_source()
            self.bus.publish({"type": "status"})

    async def _maintenance(self) -> None:
        if time.monotonic() - self._last_maint < 6 * 3600 and self._last_maint:
            return
        self._last_maint = time.monotonic()
        store.purge_old(self.db, 60)
        learning.refresh_suggestions(self.db, load_profile(self.db))

    def minutes_since_scan(self) -> int | None:
        last = parse_iso((self.last_scan or {}).get("at"))
        return int((now_utc() - last).total_seconds() // 60) if last else None

    def next_demo_available(self) -> bool:
        return isinstance(self.source, DemoSource)

    def since(self, minutes: int) -> str:
        return iso(now_utc() - timedelta(minutes=minutes))
