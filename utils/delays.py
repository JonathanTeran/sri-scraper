import asyncio
import random

from playwright.async_api import Page


async def delay_humano(min_ms: int = 800, max_ms: int = 2500) -> None:
    """Pausa aleatoria entre acciones para simular comportamiento humano."""
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def escribir_como_humano(page: Page, selector: str, texto: str) -> None:
    """Escribe texto carácter por carácter con delays variables."""
    await page.click(selector)
    for char in texto:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(0.05, 0.18))


async def mover_mouse_natural(page: Page, x: int, y: int) -> None:
    """Mueve el mouse con offset aleatorio para simular movimiento natural."""
    await page.mouse.move(
        x + random.randint(-8, 8),
        y + random.randint(-8, 8),
    )
