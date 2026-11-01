"""Запуск вместе с Windows: программа стартует в фоне без окна, проверяет чаты по расписанию и принимает действия с телефона."""

from __future__ import annotations

import sys

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Scout"


def _winreg():
    import winreg  # только в Windows

    return winreg


def supported() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def command() -> str:
    return f'"{sys.executable}" --background'


def enabled() -> bool:
    if not supported():
        return False
    reg = _winreg()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, KEY) as k:
            value, _ = reg.QueryValueEx(k, NAME)
    except OSError:
        return False
    return str(value).strip().lower().startswith(f'"{sys.executable}"'.lower())


def set_enabled(on: bool) -> None:
    if not supported():
        raise OSError("Автозапуск доступен только в установленной программе для Windows")
    reg = _winreg()
    with reg.OpenKey(reg.HKEY_CURRENT_USER, KEY, 0, reg.KEY_SET_VALUE) as k:
        if on:
            reg.SetValueEx(k, NAME, 0, reg.REG_SZ, command())
        else:
            try:
                reg.DeleteValue(k, NAME)
            except FileNotFoundError:
                pass
