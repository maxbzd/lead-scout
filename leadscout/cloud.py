"""Связь с облачным сайтом (Cloudflare): компьютер присылает результаты и забирает ваши действия с сайта.

Главным остаётся ваш компьютер: база, чтение чатов, ИИ. Облако хранит копию для просмотра и очередь действий:
оценки, закладки, отклики, настройки. Пока компьютер выключен, они ждут в очереди и выполняются при следующем запуске.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import httpx

from . import present, store
from .llm.rules import build_draft
from .profile import load_profile
from .util import fmt_money, iso, now_utc

if TYPE_CHECKING:
    from .runtime import Runtime

log = logging.getLogger("leadscout.cloud")

WINDOW_DAYS = 14
MAX_ORDERS = 2500
CHUNK = 25  # у бесплатного тарифа Cloudflare ограничено число обращений к базе за один запрос
KV_EVERY = 60  # статистику и настройки обновляем не чаще раза в минуту, если ничего не случилось
TONES = ("short", "warm", "formal")

# Что сайт вправе поручить компьютеру. Всё остальное (подключения, ключи, чаты) только на самом компьютере.
ALLOWED = [
    ("POST", re.compile(r"^/orders/\d+/(feedback|save|offer|stage|restore)$")),
    ("POST", re.compile(r"^/discarded/\d+/promote$")),
    ("POST", re.compile(r"^/(orders|discarded)/delete$")),
    ("POST", re.compile(r"^/scan$")),
    ("POST", re.compile(r"^/suggestions/\d+$")),
    ("PUT", re.compile(r"^/(profile|notifications|ai/control|settings/scan)$")),
]
ORDER_PATH = re.compile(r"^/orders/(\d+)/")


class CloudError(Exception):
    """Понятная ошибка связи с облаком."""


def allowed(method: str, path: str) -> bool:
    return any(method == m and rx.match(path) for m, rx in ALLOWED)


def _digest(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        raise CloudError("Укажите адрес сайта")
    if not url.startswith("https://") and not re.match(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$", url):
        raise CloudError("Адрес должен начинаться с https://")
    return url


class CloudSync:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.db = rt.db
        self.db.run("CREATE TABLE IF NOT EXISTS sync_state(kind TEXT NOT NULL, key TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(kind, key))")
        self._lock = asyncio.Lock()
        self.transport: httpx.AsyncBaseTransport | None = None  # подмена сети в тестах
        self._client: httpx.AsyncClient | None = None
        self._internal: httpx.AsyncClient | None = None
        self._kv_at = 0.0
        self._events = rt.bus.open_queue()  # любое событие программы значит, что на сайт пора отправить новое
        self.last_ok: str | None = None
        self.last_error: str | None = None
        self.applied = 0

    # ----------------------------------------------------------- настройки ----
    @property
    def url(self) -> str:
        return (self.rt.settings.cloud_url or (self.db.get_setting("cloud_config") or {}).get("url") or "").rstrip("/")

    @property
    def token(self) -> str:
        return self.rt.secrets.get("cloud_token") or ""

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token)

    def status(self) -> dict:
        return {"connected": self.enabled, "url": self.url or None, "last_ok": self.last_ok, "error": self.last_error,
                "source": "env" if self.rt.settings.cloud_url else ("app" if self.enabled else "none")}

    async def connect(self, url: str, token: str) -> None:
        url = normalize_url(url)
        token = token.strip()
        if len(token) < 16:
            raise CloudError("Вставьте ключ синхронизации целиком (его показал мастер установки облака)")
        async with httpx.AsyncClient(timeout=20, transport=self.transport) as c:
            try:
                r = await c.get(f"{url}/sync/ping", headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as e:
                raise CloudError("Сайт не отвечает. Проверьте адрес и интернет") from e
        if r.status_code == 401:
            raise CloudError("Ключ синхронизации не подошёл")
        if r.status_code >= 400:
            raise CloudError(f"Сайт ответил ошибкой {r.status_code}. Проверьте адрес")
        self.db.set_setting("cloud_config", {"url": url})
        self.rt.secrets.set("cloud_token", token)
        self.db.run("DELETE FROM sync_state")  # на новом сайте пусто: отправим всё заново
        self.last_error = None
        await self.close()

    def disconnect(self) -> None:
        self.db.del_setting("cloud_config")
        self.rt.secrets.delete("cloud_token")
        self.db.run("DELETE FROM sync_state")
        self.last_ok = self.last_error = None

    async def shutdown(self) -> None:
        self.rt.bus.close_queue(self._events)
        await self.close()

    async def close(self) -> None:
        for c in (self._client, self._internal):
            if c is not None:
                await c.aclose()
        self._client = self._internal = None

    # -------------------------------------------------------------- запросы ----
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60, transport=self.transport, headers={"Authorization": f"Bearer {self.token}"})
        return self._client

    async def _cloud(self, method: str, path: str, body: Any = None) -> Any:
        try:
            r = await self._http().request(method, f"{self.url}/sync{path}", json=body)
        except httpx.HTTPError as e:
            raise CloudError("Нет связи с сайтом") from e
        if r.status_code == 401:
            raise CloudError("Ключ синхронизации не подошёл")
        if r.status_code >= 400:
            raise CloudError(f"Сайт ответил ошибкой {r.status_code}")
        return r.json()

    def _me(self) -> httpx.AsyncClient:
        """Клиент к собственному API программы: действия с сайта выполняются теми же функциями, что и в приложении."""
        if self._internal is None:
            app = self.rt.app
            headers = {"X-Requested-With": "leadscout", "X-Scout-Internal": "1"}
            if not self.rt.settings.local:
                headers["Cookie"] = f"ls_session={app.state.auth.issue()}"
            self._internal = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost",
                                               headers=headers, timeout=900)
        return self._internal

    async def _own(self, path: str) -> Any:
        r = await self._me().get(f"/api{path}")
        r.raise_for_status()
        return r.json()

    # --------------------------------------------------------------- цикл ----
    async def cycle(self, force_kv: bool = False) -> None:
        if not self.enabled or self._lock.locked():
            return
        async with self._lock:
            try:
                changed = await self.pull()
                # заказы сверяются по отпечаткам каждый раз (это дёшево, уходит только изменившееся),
                # а статистика и настройки обновляются сразу после событий программы и иначе раз в минуту
                await self.push(force_kv=force_kv or changed or self._drain())
                self.last_ok = iso()
                self.last_error = None
            except CloudError as e:
                self.last_error = str(e)
                log.warning("cloud sync: %s", e)
            except Exception:
                self.last_error = "Сбой синхронизации с сайтом"
                log.exception("cloud sync failed")

    def _drain(self) -> bool:
        seen = False
        while not self._events.empty():
            self._events.get_nowait()
            seen = True
        return seen

    async def pull(self) -> bool:
        data = await self._cloud("GET", "/commands")
        items = data.get("items") or []
        if not items:
            return False
        done: list[int] = []
        errors: dict[str, str] = {}
        touched: set[int] = set()
        for cmd in items:
            method, path = str(cmd.get("method", "")), str(cmd.get("path", ""))
            if not allowed(method, path):
                errors[str(cmd["id"])] = "Это действие недоступно с сайта"
                continue
            m = ORDER_PATH.match(path)
            if m:
                touched.add(int(m.group(1)))
            try:
                r = await self._me().request(method, f"/api{path}", json=cmd.get("body") or {})
                if r.status_code < 400:
                    done.append(cmd["id"])
                else:
                    detail = ""
                    try:
                        detail = r.json().get("detail", "")
                    except ValueError:
                        pass
                    errors[str(cmd["id"])] = str(detail)[:200] or f"ошибка {r.status_code}"
            except Exception as e:  # одно действие не должно останавливать остальные
                log.exception("cloud command failed: %s %s", method, path)
                errors[str(cmd["id"])] = type(e).__name__
        await self._cloud("POST", "/ack", {"done": done, "errors": errors})
        for oid in touched:  # на сайте ответ был предположением: пусть итог компьютера перезапишет его
            self.db.run("DELETE FROM sync_state WHERE kind='order' AND key=?", (str(oid),))
        self.applied += len(done)
        return True

    # ----------------------------------------------------------- отправка ----
    async def push(self, force_kv: bool = False) -> None:
        await self._push_orders()
        await self._push_discarded()
        if force_kv or time.monotonic() - self._kv_at > KV_EVERY:
            await self._push_kv()
            self._kv_at = time.monotonic()

    def _state(self, kind: str) -> dict[str, str]:
        return {r["key"]: r["hash"] for r in self.db.all("SELECT key, hash FROM sync_state WHERE kind=?", (kind,))}

    def _remember(self, kind: str, pairs: dict[str, str]) -> None:
        for key, h in pairs.items():
            self.db.run("INSERT INTO sync_state(kind,key,hash) VALUES(?,?,?) ON CONFLICT(kind,key) DO UPDATE SET hash=excluded.hash", (kind, key, h))

    def _forget(self, kind: str, keys: list[str]) -> None:
        for key in keys:
            self.db.run("DELETE FROM sync_state WHERE kind=? AND key=?", (kind, key))

    async def _push_orders(self) -> None:
        profile = load_profile(self.db)
        since = iso(now_utc() - timedelta(days=WINDOW_DAYS))
        rows = self.db.all(store.ORDER_SELECT + " WHERE o.created_at>=? ORDER BY o.id DESC LIMIT ?", (since, MAX_ORDERS))
        stamps = {r["order_id"]: r["t"] for r in self.db.all("SELECT order_id, MAX(created_at) AS t FROM drafts GROUP BY order_id")}
        seed = _digest(profile.model_dump())
        known = self._state("order")
        changed: list[tuple[dict, str]] = []
        for row in rows:
            fp = _digest([seed, row, stamps.get(row["id"])])
            if known.get(str(row["id"])) != fp:
                changed.append((row, fp))
        gone = [k for k in known if int(k) not in {r["id"] for r in rows}]
        for i in range(0, len(changed), CHUNK):
            part = changed[i: i + CHUNK]
            payload = [self._pack(row, profile) for row, _ in part]
            await self._cloud("POST", "/push", {"orders": payload})
            self._remember("order", {str(row["id"]): fp for row, fp in part})
            await asyncio.sleep(0)  # не занимаем программу надолго
        for i in range(0, len(gone), 100):
            await self._cloud("POST", "/push", {"remove": [int(k) for k in gone[i: i + 100]]})
            self._forget("order", gone[i: i + 100])

    def _pack(self, row: dict, profile: Any) -> dict:
        db = self.db
        card = present.order_card(row, profile)
        draft = store.get_draft(db, row["id"], profile.tone, 0)
        card["draft"] = {"text": draft["text"], "price": draft["price"]} if draft else None
        card["contact_url"] = row.get("contact_url") or ""
        detail = present.order_detail(db, row, profile, {"text": draft["text"], "price": draft["price"], "tone": draft["tone"], "variant": 0} if draft else None)
        detail["contact_url"] = card["contact_url"]
        detail["cloud_drafts"] = self._drafts(row, profile)
        budget = row.get("budget_rub_max") or row.get("budget_rub_min") or 0
        search = " ".join([row["title"], row.get("summary") or "", (row.get("text") or "")[:600], row.get("chat_title") or ""])
        return {
            "id": row["id"], "verdict": card["verdict"], "score": card["score"], "hot": card["hot"], "state": card["state"],
            "feedback": card["feedback"], "offered": card["offered"], "stage": card["stage"], "posted_at": row["posted_at"],
            "budget": float(budget), "fixed": (row.get("period") or "fixed") == "fixed", "search": search.lower(),
            "card": card, "detail": detail,
        }

    def _drafts(self, row: dict, profile: Any) -> dict:
        """Готовые черновики: сайт не может звать ИИ, поэтому тексты (по два варианта на тон) готовятся здесь."""
        if row["verdict"] == "scam":
            return {}
        view = self.rt.pipeline.view_of(row)
        base_price = self.rt.pipeline.default_price(row)
        out = {}
        for tone in TONES:
            saved = store.get_draft(self.db, row["id"], tone, 0)
            price = saved["price"] if saved and saved["price"] else base_price
            first = saved["text"] if saved else build_draft(view, profile, tone, 0, price)
            out[tone] = {"price": price, "label": fmt_money(price) if price else None,
                         "texts": [first, build_draft(view, profile, tone, 1, price)]}
        return out

    async def _push_discarded(self) -> None:
        data = await self._own("/orders?filter=discarded&limit=100")
        items = data["items"]
        known = self._state("discarded")
        fresh = {str(i["id"]): _digest(i) for i in items}
        changed = [i for i in items if known.get(str(i["id"])) != fresh[str(i["id"])]]
        gone = [k for k in known if k not in fresh]
        if changed or gone:
            body = {"discarded": [{"id": i["id"], "posted_at": i.get("posted_at") or iso(), "search": i["text"], "item": i} for i in changed],
                    "discarded_remove": [int(k) for k in gone]}
            await self._cloud("POST", "/push", body)
            self._remember("discarded", {str(i["id"]): fresh[str(i["id"])] for i in changed})
            self._forget("discarded", gone)

    async def _push_kv(self) -> None:
        status = await self._own("/status")
        status.pop("cloud", None)
        orders_meta = await self._own("/orders?limit=1")
        kv = {
            "status": status,
            "stats": await self._own("/stats"),
            "profile": await self._own("/profile"),
            "notif": await self._own("/notifications"),
            "chats": await self._own("/chats"),
            "feed_meta": {"total_read": orders_meta.get("total_read", 0)},
        }
        known = self._state("kv")
        # время и счётчики в статусе меняются постоянно: сравниваем без них, чтобы не слать лишнее
        fresh = {k: _digest(self._calm(k, v)) for k, v in kv.items()}
        changed = {k: v for k, v in kv.items() if known.get(k) != fresh[k]}
        if changed:
            await self._cloud("POST", "/push", {"kv": changed})
            self._remember("kv", {k: fresh[k] for k in changed})

    @staticmethod
    def _calm(key: str, value: Any) -> Any:
        if key != "status":
            return value
        v = json.loads(json.dumps(value, default=str))
        v.get("scan", {}).pop("next_in", None)
        return v
