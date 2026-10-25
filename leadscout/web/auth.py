"""Вход по паролю: подписанная cookie, защита от подбора."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

COOKIE = "ls_session"
MAX_AGE = 60 * 60 * 24 * 30
FAIL_WINDOW = 600
FAIL_LIMIT = 8


class Auth:
    def __init__(self, password: str, signing_key: bytes, db=None):
        self.password = password
        self.key = signing_key
        self.db = db  # здесь хранится список отозванных входов (после «Выйти» cookie перестаёт работать)
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    # --- пароль и подбор -------------------------------------------------
    def check_password(self, given: str) -> bool:
        return hmac.compare_digest(
            hashlib.sha256(given.encode("utf-8")).digest(), hashlib.sha256(self.password.encode("utf-8")).digest()
        )

    def blocked(self, ip: str) -> int:
        """Сколько секунд ждать, если слишком много неудачных попыток."""
        q = self._fails[ip]
        now = time.monotonic()
        while q and now - q[0] > FAIL_WINDOW:
            q.popleft()
        if len(q) >= FAIL_LIMIT:
            return int(FAIL_WINDOW - (now - q[0])) + 1
        return 0

    def fail(self, ip: str) -> None:
        if len(self._fails) > 2000:  # не даём словарю расти бесконечно
            now = time.monotonic()
            for k in [k for k, q in self._fails.items() if not q or now - q[-1] > FAIL_WINDOW]:
                self._fails.pop(k, None)
        self._fails[ip].append(time.monotonic())

    def ok(self, ip: str) -> None:
        self._fails.pop(ip, None)

    # --- cookie ----------------------------------------------------------
    def _sign(self, payload: str) -> str:
        return hmac.new(self.key, payload.encode("ascii"), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        exp = int(time.time()) + MAX_AGE
        nonce = secrets.token_hex(8)
        payload = f"{exp}.{nonce}"
        return f"{payload}.{self._sign(payload)}"

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        try:
            exp, nonce, sig = token.split(".")
            payload = f"{exp}.{nonce}"
            if not hmac.compare_digest(sig, self._sign(payload)):
                return False
            return int(exp) > time.time() and nonce not in self._revoked()
        except (ValueError, TypeError):
            return False

    def _revoked(self) -> list[str]:
        return (self.db.get_setting("revoked_sessions", []) if self.db else []) or []

    def revoke(self, token: str | None) -> None:
        """«Выйти»: этот вход больше не действует, даже если cookie украли."""
        if not token or not self.db:
            return
        try:
            exp, nonce, _ = token.split(".")
        except ValueError:
            return
        self.db.set_setting("revoked_sessions", (self._revoked() + [nonce])[-300:])


def client_ip(request: Request) -> str:
    # заголовок X-Forwarded-For учитывает сам uvicorn, и только от доверенного прокси
    return request.client.host if request.client else "?"


def require_auth(request: Request) -> None:
    local = request.app.state.settings.local
    if not local:
        auth: Auth = request.app.state.auth
        if not auth.valid(request.cookies.get(COOKIE)):
            raise HTTPException(status_code=401, detail="Нужно войти")
    # без пароля на своём компьютере этот заголовок единственное, что не даёт чужому сайту в соседней вкладке отправлять запросы программе
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("x-requested-with") != "leadscout":
        raise HTTPException(status_code=403, detail="Запрос не из приложения")
