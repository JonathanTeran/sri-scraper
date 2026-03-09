import os

from playwright.async_api import Page
from utils.time import utc_now


async def tomar_screenshot(
    page: Page,
    nombre: str,
    tenant_ruc: str,
    screenshot_path: str = "./screenshots",
) -> str:
    """Toma screenshot y lo guarda con timestamp. Retorna el path guardado."""
    os.makedirs(screenshot_path, exist_ok=True)
    ts = utc_now().strftime("%Y%m%d_%H%M%S")
    filename = f"{tenant_ruc}_{nombre}_{ts}.png"
    filepath = os.path.join(screenshot_path, filename)
    await page.screenshot(path=filepath, full_page=True)
    return filepath
