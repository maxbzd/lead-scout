"""Установленная программа: своё окно, один экземпляр, кнопка «Закрыть программу»."""

import http.server
import json
import threading

from fastapi.testclient import TestClient

from leadscout import desktop
from leadscout.config import Settings
from leadscout.web.app import create_app

from .conftest import H


def local_client(tmp_path):
    s = Settings(data_dir=tmp_path, local=True, demo=True, run_worker=False, host="127.0.0.1")
    return TestClient(create_app(s), base_url="http://localhost:8000")


def test_quit_button_stops_the_program(tmp_path):
    with local_client(tmp_path) as c:
        stopped = []
        c.app.state.quit = lambda: stopped.append(True)
        assert c.post("/api/quit", headers=H).json() == {"ok": True}
        assert stopped == [True]


def test_quit_does_not_exist_on_a_server(client):
    assert client.post("/api/login", json={"password": "secret-pw"}, headers=H).status_code == 200
    assert client.post("/api/quit", headers=H).status_code == 404


def test_quit_needs_the_csrf_header(tmp_path):
    with local_client(tmp_path) as c:
        c.app.state.quit = lambda: None
        assert c.post("/api/quit").status_code in (400, 401, 403)


def test_alive_recognises_a_running_scout():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"authed": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert desktop.alive(srv.server_port)
    finally:
        srv.shutdown()
    assert not desktop.alive(srv.server_port)  # порт закрыт: значит, программа не работает
    assert not desktop.alive(1)


def test_window_falls_back_to_the_browser_when_no_chrome_or_edge(monkeypatch):
    opened = []
    monkeypatch.setattr(desktop, "find_browser", lambda: None)
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: opened.append(url))
    desktop.open_window("http://localhost:8000")
    assert opened == ["http://localhost:8000"]


def test_window_opens_as_an_app_window_when_chrome_is_there(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "find_browser", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    desktop.open_window("http://localhost:8000")
    assert calls and calls[0][1] == "--app=http://localhost:8000"


def test_foreign_site_cannot_post_to_the_local_app(tmp_path):
    with local_client(tmp_path) as c:
        assert c.post("/api/scan").status_code == 403
        assert c.put("/api/notifications", json={}).status_code == 403
        assert c.post("/api/connect/cloud", json={"url": "https://evil.example.com", "token": "e" * 32}).status_code == 403
        assert c.get("/api/orders").status_code == 200
