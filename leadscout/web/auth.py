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
    def __init__(self, password: str, signing_key: bytes):
        self.password = password
        self.key = signing_key
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
            return int(exp) > time.time()
        except (ValueError, TypeError):
            return False


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def require_auth(request: Request) -> None:
    auth: Auth = request.app.state.auth
    if not auth.valid(request.cookies.get(COOKIE)):
        raise HTTPException(status_code=401, detail="Нужно войти")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("x-requested-with") != "leadscout":
        raise HTTPException(status_code=403, detail="Запрос не из приложения")
