"""
Gestión de sesiones/cookies para mantener login entre ejecuciones.

Guarda cookies en sessions/{tenant_ruc}.json para reutilizar
sesiones válidas sin re-login.
"""

import json
import os
import structlog
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except Exception:  # pragma: no cover - Windows fallback
    fcntl = None

from playwright.async_api import BrowserContext
from utils.time import utc_now

log = structlog.get_logger()

SESSIONS_DIR = "sessions"


class SessionManager:
    def __init__(self, tenant_ruc: str):
        self._tenant_ruc = tenant_ruc
        self._session_file = os.path.join(
            SESSIONS_DIR, f"{tenant_ruc}.json"
        )
        self._lock_file = os.path.join(
            SESSIONS_DIR, f"{tenant_ruc}.lock"
        )

    @contextmanager
    def _file_lock(self):
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        lock_handle = open(self._lock_file, "a+", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()

    async def cargar_cookies(self, context: BrowserContext) -> bool:
        """
        Carga cookies guardadas en el contexto del navegador.
        Retorna True si se cargaron cookies válidas.
        """
        if not os.path.exists(self._session_file):
            log.info(
                "session_no_encontrada", ruc=self._tenant_ruc
            )
            return False

        try:
            with self._file_lock():
                with open(self._session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

            cookies = data.get("cookies", [])
            if not cookies:
                return False

            # Verificar si las cookies han expirado
            now = utc_now().timestamp()
            cookies_validas = []
            for cookie in cookies:
                expires = cookie.get("expires", -1)
                if expires == -1 or expires > now:
                    cookies_validas.append(cookie)

            if not cookies_validas:
                log.info(
                    "session_expirada", ruc=self._tenant_ruc
                )
                return False

            await context.add_cookies(cookies_validas)
            log.info(
                "session_cargada",
                ruc=self._tenant_ruc,
                cookies=len(cookies_validas),
            )
            return True

        except (json.JSONDecodeError, KeyError) as e:
            log.warning(
                "session_corrupta",
                ruc=self._tenant_ruc,
                error=str(e),
            )
            return False

    async def guardar_cookies(self, context: BrowserContext) -> None:
        """Guarda las cookies actuales del contexto."""
        os.makedirs(SESSIONS_DIR, exist_ok=True)

        cookies = await context.cookies()
        data = {
            "ruc": self._tenant_ruc,
            "saved_at": utc_now().isoformat(),
            "cookies": cookies,
        }

        with self._file_lock():
            temp_path = Path(f"{self._session_file}.tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self._session_file)

        log.info(
            "session_guardada",
            ruc=self._tenant_ruc,
            cookies=len(cookies),
        )

    def limpiar_sesion(self) -> None:
        """Elimina el archivo de sesión."""
        with self._file_lock():
            if os.path.exists(self._session_file):
                os.remove(self._session_file)
                log.info(
                    "session_eliminada", ruc=self._tenant_ruc
                )
