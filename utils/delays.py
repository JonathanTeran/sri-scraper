import asyncio
import json
import math
import random
from typing import Any

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


async def simular_actividad_humana(
    page: Any,
    *,
    focus_selector: str = "#frmPrincipal",
) -> None:
    """Simula actividad ligera antes del CAPTCHA para mejorar el score."""
    selector_literal = json.dumps(focus_selector)
    viewport = await page.evaluate(f"""
    (() => {{
        const selector = {selector_literal};
        return {{
        width: Math.max(window.innerWidth || 0, 1280),
        height: Math.max(window.innerHeight || 0, 720),
        scrollHeight: Math.max(
            document.body ? document.body.scrollHeight : 0,
            document.documentElement ? document.documentElement.scrollHeight : 0
        ),
        focusSelectorFound: !!document.querySelector(selector),
        }};
    }})()
    """)

    rng = random.Random()
    width = max(int(viewport.get("width", 1280)), 640)
    height = max(int(viewport.get("height", 720)), 480)
    scroll_height = max(int(viewport.get("scrollHeight", height)), height)
    points = [
        (
            rng.randint(width // 7, width // 3),
            rng.randint(height // 5, height // 2),
        ),
        (
            rng.randint(width // 3, (width * 2) // 3),
            rng.randint(height // 4, (height * 3) // 4),
        ),
        (
            rng.randint((width * 2) // 3, max((width * 5) // 6, 1)),
            rng.randint(height // 5, (height * 4) // 5),
        ),
    ]

    mouse = getattr(page, "mouse", None)
    if mouse and hasattr(mouse, "move"):
        current_x = rng.randint(30, width // 4)
        current_y = rng.randint(30, height // 3)
        await mouse.move(current_x, current_y, steps=4)

        for target_x, target_y in points:
            control_x = (current_x + target_x) / 2 + rng.randint(-90, 90)
            control_y = (current_y + target_y) / 2 + rng.randint(-60, 60)
            steps = rng.randint(16, 24)
            for step in range(1, steps + 1):
                t = step / steps
                curve_x = (
                    ((1 - t) ** 2) * current_x
                    + 2 * (1 - t) * t * control_x
                    + (t**2) * target_x
                )
                curve_y = (
                    ((1 - t) ** 2) * current_y
                    + 2 * (1 - t) * t * control_y
                    + (t**2) * target_y
                )
                curve_y += math.sin(t * math.pi) * rng.uniform(-4, 4)
                await mouse.move(curve_x, curve_y)
                await asyncio.sleep(rng.uniform(0.01, 0.035))
            current_x, current_y = target_x, target_y

        if hasattr(mouse, "wheel"):
            await mouse.wheel(
                0,
                rng.randint(80, min(max(scroll_height // 8, 120), 240)),
            )
    else:
        synthetic_points = [
            {"x": x, "y": y, "delay": rng.randint(40, 120)}
            for x, y in points
        ]
        await page.evaluate(
            f"""
            (() => {{
                const selector = {selector_literal};
                const points = {json.dumps(synthetic_points)};
                const scrollBy = {rng.randint(80, min(max(scroll_height // 8, 120), 220))};
                const target = document.querySelector(selector) || document.body;
                let totalDelay = 0;
                for (const point of points) {{
                    totalDelay += point.delay;
                    window.setTimeout(() => {{
                        const evt = new MouseEvent('mousemove', {{
                            bubbles: true,
                            clientX: point.x,
                            clientY: point.y,
                        }});
                        document.dispatchEvent(evt);
                        target.dispatchEvent(new MouseEvent('mouseover', {{
                            bubbles: true,
                            clientX: point.x,
                            clientY: point.y,
                        }}));
                    }}, totalDelay);
                }}
                window.setTimeout(() => window.scrollBy(0, scrollBy), totalDelay + 60);
            }})()
            """,
        )

    try:
        await page.evaluate(
            f"""
            (() => {{
                const selector = {selector_literal};
                const target = document.querySelector(selector) || document.body;
                target.dispatchEvent(new MouseEvent('mouseenter', {{ bubbles: true }}));
                target.dispatchEvent(new MouseEvent('mouseover', {{ bubbles: true }}));
            }})()
            """,
        )
    except Exception:
        pass

    await asyncio.sleep(rng.uniform(1.1, 2.4))
