"""Режим «приложение на моём компьютере»: без пароля, только localhost, проверка по кнопке, самозакрытие."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from leadscout import store
from leadscout.__main__ import should_quit
from leadscout.config import Settings
from leadscout.web.app import create_app

from .conftest import H, make_raw


@pytest.fixture()
def local(tmp_path):
    s = Settings(data_dir=tmp_path, local=True, demo=True, run_worker=False, host="127.0.0.1")
    with TestClient(create_app(s), base_url="http://localhost:8000") as c:
        yield c


def test_no_password_needed_on_this_computer(local):
    assert local.get("/api/session").json() == {"authed": True}
    assert local.get("/api/orders").status_code == 200
    assert local.get("/api/status").json()["local"] is True


def test_foreign_host_header_is_rejected(local):
    r = local.get("/api/orders", headers={"Host": "evil.example.com"})
    assert r.status_code == 403
    assert local.get("/api/orders", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_default_is_manual_scan_and_nothing_runs_by_itself(local):
    rt = local.app.state.rt
    assert rt.scan_minutes == 0 and rt.manual
    st = local.get("/api/status").json()["scan"]
    assert st["every_min"] == 0 and st["next_in"] is None
    assert local.get("/api/orders").json()["counts"]["all"] == 0  # без нажатия ничего не читается
    r = local.post("/api/scan", headers=H).json()
    assert r["orders"] >= 5 and local.get("/api/orders").json()["counts"]["all"] >= 5


def test_interval_can_be_switched_between_manual_and_schedule(local):
    assert local.put("/api/settings/scan", json={"minutes": 60}, headers=H).json()["scan"]["every_min"] == 60
    assert local.put("/api/settings/scan", json={"minutes": 0}, headers=H).json()["scan"]["every_min"] == 0
    assert local.put("/api/settings/scan", json={"minutes": 3}, headers=H).status_code == 422


def test_normal_mode_still_needs_password(client):
    assert client.get("/api/orders").status_code == 401


def test_local_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("LS_LOCAL", "1")
    monkeypatch.delenv("LS_DATA_DIR", raising=False)
    monkeypatch.delenv("LS_HOST", raising=False)
    s = Settings.load(env_file=str(tmp_path / "none.env"))
    assert s.local and s.host == "127.0.0.1" and s.data_dir.name == ".leadscout"


@pytest.mark.parametrize(("now", "last", "clients", "busy", "expected"), [
    (1000, 900, 0, False, True),     # окно закрыто, тихо больше 90 секунд
    (1000, 990, 0, False, False),    # только что был запрос
    (1000, 100, 1, False, False),    # окно открыто (живое соединение)
    (1000, 100, 0, True, False),     # идёт проверка или уточнение ИИ
    (60, 0, 0, False, False),        # только запустились: даём время открыться окну
])
def test_should_quit(now, last, clients, busy, expected):
    assert should_quit(now, 0, last, clients, busy) is expected


async def test_ai_refines_right_after_a_manual_scan(tmp_path, monkeypatch):
    from leadscout.runtime import Runtime

    s = Settings(data_dir=tmp_path, local=True, demo=False, run_worker=False)
    rt = Runtime(s)
    await rt.start()
    calls = []

    async def fake_upgrade(limit=5, hours=72):
        calls.append(1)
        return 1 if len(calls) == 1 else 0

    monkeypatch.setattr(rt.pipeline, "upgrade_with_ai", fake_upgrade)
    monkeypatch.setattr(rt.pipeline, "ai_on", lambda: True)
    cid = store.add_chat(rt.db, "Чат", username="chat_local", tg_id=-1005)

    class Src:
        name = "x"

        async def fetch(self):
            return [make_raw(cid, "Нужен телеграм-бот с GPT для записи клиентов. Бюджет 45 000 ₽, срок 7 дней. @salon_loc", tg_msg_id=1)]

        async def stop(self):
            pass

    rt.source = Src()
    await rt.scan(manual=True)
    await asyncio.gather(*rt._tasks)
    assert len(calls) == 2  # уточнение шло до исчерпания, а не по таймеру
    await rt.stop()
