"""Tests para persistencia segura de sesiones."""

import asyncio
import json

from scrapers.session_manager import SessionManager


class _FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies
        self.added = None

    async def cookies(self):
        return self._cookies

    async def add_cookies(self, cookies):
        self.added = cookies


def test_session_manager_guarda_y_carga_con_write_atomico(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "scrapers.session_manager.SESSIONS_DIR",
        str(tmp_path),
    )
    cookies = [
        {"name": "JSESSIONID", "value": "abc", "expires": -1},
        {"name": "TOKEN", "value": "xyz", "expires": 9999999999},
    ]
    save_ctx = _FakeContext(cookies)
    load_ctx = _FakeContext([])
    manager = SessionManager("1207481803001")

    asyncio.run(manager.guardar_cookies(save_ctx))
    payload = json.loads((tmp_path / "1207481803001.json").read_text())

    assert payload["ruc"] == "1207481803001"
    assert len(payload["cookies"]) == 2
    assert not (tmp_path / "1207481803001.json.tmp").exists()

    loaded = asyncio.run(manager.cargar_cookies(load_ctx))

    assert loaded is True
    assert load_ctx.added == cookies
