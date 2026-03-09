"""Runner async compartido para tareas Celery sync."""

import asyncio
from typing import Any, Coroutine, TypeVar

from db.session import dispose_cached_engine

try:
    from celery.signals import worker_process_init, worker_process_shutdown
except ModuleNotFoundError:  # pragma: no cover - fallback para tests unitarios
    class _SignalStub:
        def connect(self, func=None, **_: Any):
            if func is None:
                return lambda callback: callback
            return func

    worker_process_init = _SignalStub()
    worker_process_shutdown = _SignalStub()

T = TypeVar("T")

_worker_loop: asyncio.AbstractEventLoop | None = None


def _get_worker_loop() -> asyncio.AbstractEventLoop:
    global _worker_loop

    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_worker_loop)
    return _worker_loop


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Ejecuta corrutinas reutilizando un loop por proceso worker."""
    loop = _get_worker_loop()
    return loop.run_until_complete(coro)


def close_worker_loop() -> None:
    """Libera recursos async cacheados al cerrar el proceso worker."""
    global _worker_loop

    loop = _worker_loop
    if loop is None or loop.is_closed():
        _worker_loop = None
        return

    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(dispose_cached_engine())
    finally:
        loop.close()
        _worker_loop = None
        asyncio.set_event_loop(None)


@worker_process_init.connect
def _init_worker_loop(**_: Any) -> None:
    _get_worker_loop()


@worker_process_shutdown.connect
def _shutdown_worker_loop(**_: Any) -> None:
    close_worker_loop()
