"""Удобства установленной программы: автозапуск с Windows, фоновый режим, резервная копия данных."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from leadscout import autostart, desktop
from leadscout.config import Settings
from leadscout.web.app import create_app

from .conftest import H


def local(tmp_path):
    s = Settings(data_dir=tmp_path, local=True, demo=True, run_worker=False, host="127.0.0.1")
    return TestClient(create_app(s), base_url="http://localhost:8000")


class FakeReg:
    HKEY_CURRENT_USER = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def OpenKey(self, *a):  # noqa: N802
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def QueryValueEx(self, k, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError
        return self.values[name], 1

    def SetValueEx(self, k, name, _r, _t, value):  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, k, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError
        del self.values[name]


@pytest.fixture()
def winreg(monkeypatch):
    reg = FakeReg()
    monkeypatch.setattr(autostart, "supported", lambda: True)
    monkeypatch.setattr(autostart, "_winreg", lambda: reg)
    return reg


def test_autostart_writes_and_removes_the_registry_value(winreg):
    assert not autostart.enabled()
    autostart.set_enabled(True)
    assert winreg.values["Scout"].endswith("--background") and autostart.enabled()
    autostart.set_enabled(False)
    autostart.set_enabled(False)  # повторно: не ошибка
    assert not autostart.enabled()


def test_autostart_api_in_the_installed_app(tmp_path, winreg):
    with local(tmp_path) as c:
        assert c.get("/api/status").json()["autostart"] == {"supported": True, "enabled": False}
        assert c.put("/api/autostart", json={"on": True}, headers=H).json() == {"supported": True, "enabled": True}
        assert c.put("/api/autostart", json={"on": False}, headers=H).json()["enabled"] is False
        assert c.put("/api/autostart", json={"on": True}).status_code == 403


def test_autostart_is_not_offered_when_launched_from_files(tmp_path):
    with local(tmp_path) as c:
        assert c.get("/api/status").json()["autostart"]["supported"] is False
        assert c.put("/api/autostart", json={"on": True}, headers=H).status_code == 400


def test_no_autostart_on_the_server(client):
    client.post("/api/login", json={"password": "secret-pw"}, headers=H)
    assert client.get("/api/status").json()["autostart"] is None
    assert client.put("/api/autostart", json={"on": True}, headers=H).status_code == 404


def test_background_start_does_not_open_a_window(tmp_path, monkeypatch):
    opened, ran = [], {}
    monkeypatch.setenv("LS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(desktop, "open_window", lambda url: opened.append(url))

    def fake_run(settings, opener=None, on_ready=None, announce=True):
        ran["opener"] = opener
        opener("http://localhost:1")
        return 0

    monkeypatch.setattr("leadscout.__main__.run_local", fake_run)
    monkeypatch.setattr(desktop.sys, "frozen", False, raising=False)
    assert desktop.main(["--background"]) == 0
    assert opened == []
    assert desktop.main([]) == 0
    assert opened == ["http://localhost:1"]


def test_backup_is_a_working_database_without_secrets(tmp_path):
    with local(tmp_path) as c:
        c.post("/api/scan", headers=H)
        rt = c.app.state.rt
        rt.secrets.set("cloud_token", "super-secret-token-value-123456")
        r = c.get("/api/backup")
        assert r.status_code == 200 and "scout-backup-" in r.headers["content-disposition"]
        out = tmp_path / "copy.db"
        out.write_bytes(r.content)
    con = sqlite3.connect(out)
    assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] > 0
    assert con.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'secret:%'").fetchone()[0] == 0
    assert b"super-secret-token" not in out.read_bytes()


def test_backup_not_on_a_server(client):
    client.post("/api/login", json={"password": "secret-pw"}, headers=H)
    assert client.get("/api/backup").status_code == 404
