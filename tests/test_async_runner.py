import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tasks.async_runner import close_worker_loop, run_async


class LoopBoundResource:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None

    async def use(self) -> int:
        current_loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = current_loop
        elif self._loop is not current_loop:
            raise RuntimeError("attached to a different loop")

        await asyncio.sleep(0)
        return id(current_loop)


async def _current_loop() -> asyncio.AbstractEventLoop:
    await asyncio.sleep(0)
    return asyncio.get_running_loop()


def test_run_async_reuses_the_same_loop_across_calls() -> None:
    resource = LoopBoundResource()

    try:
        first_loop_id = run_async(resource.use())
        second_loop_id = run_async(resource.use())
    finally:
        close_worker_loop()

    assert first_loop_id == second_loop_id


def test_close_worker_loop_creates_a_fresh_loop_next_time() -> None:
    try:
        first_loop = run_async(_current_loop())
        close_worker_loop()
        second_loop = run_async(_current_loop())
    finally:
        close_worker_loop()

    assert first_loop.is_closed()
    assert first_loop is not second_loop
