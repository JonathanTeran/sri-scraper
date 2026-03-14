"""
Advanced human behavior simulation for reCAPTCHA Enterprise v3.

Goes beyond basic mouse movements to simulate realistic browsing
patterns that reCAPTCHA Enterprise scores as human (0.7+).

Includes:
- Bézier curve mouse movement with variable speed
- Natural scroll patterns (reading behavior)
- Typing with realistic cadence (key press/release timing)
- Idle pauses that mimic reading or thinking
- Form interaction patterns
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass

import structlog

log = structlog.get_logger()


@dataclass
class BehaviorProfile:
    """Configurable behavior parameters to vary between sessions."""

    mouse_speed: float = 1.0       # multiplier for movement speed
    typing_speed: float = 1.0      # multiplier for typing speed (lower = slower)
    scroll_tendency: float = 0.5   # how much the user scrolls (0-1)
    pause_tendency: float = 0.5    # how often the user pauses (0-1)
    jitter: float = 0.3            # randomness in movements

    @classmethod
    def random(cls, seed: str | None = None) -> BehaviorProfile:
        rng = random.Random(seed)
        return cls(
            mouse_speed=rng.uniform(0.6, 1.4),
            typing_speed=rng.uniform(0.7, 1.3),
            scroll_tendency=rng.uniform(0.3, 0.8),
            pause_tendency=rng.uniform(0.2, 0.6),
            jitter=rng.uniform(0.15, 0.45),
        )


def _bezier_points(
    start: tuple[float, float],
    end: tuple[float, float],
    steps: int,
    rng: random.Random,
) -> list[tuple[float, float]]:
    """Generate points along a cubic Bézier curve with random control points."""
    sx, sy = start
    ex, ey = end
    mid_x = (sx + ex) / 2
    mid_y = (sy + ey) / 2
    spread_x = abs(ex - sx) * 0.4
    spread_y = abs(ey - sy) * 0.4

    c1x = mid_x + rng.uniform(-spread_x, spread_x)
    c1y = mid_y + rng.uniform(-spread_y, spread_y)
    c2x = mid_x + rng.uniform(-spread_x, spread_x)
    c2y = mid_y + rng.uniform(-spread_y, spread_y)

    points = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        x = u**3 * sx + 3 * u**2 * t * c1x + 3 * u * t**2 * c2x + t**3 * ex
        y = u**3 * sy + 3 * u**2 * t * c1y + 3 * u * t**2 * c2y + t**3 * ey
        # Add micro-jitter for realism
        x += rng.uniform(-1.5, 1.5)
        y += rng.uniform(-1.5, 1.5)
        points.append((x, y))
    return points


async def simulate_mouse_movement(
    page,
    *,
    profile: BehaviorProfile | None = None,
    target_selector: str | None = None,
    use_playwright_api: bool = False,
) -> None:
    """Simulate natural mouse movement across the page.

    Works with both nodriver pages (via JS events) and Playwright pages
    (via mouse.move API).
    """
    profile = profile or BehaviorProfile()
    rng = random.Random()

    viewport = await page.evaluate("""
        (() => ({
            width: window.innerWidth || 1280,
            height: window.innerHeight || 720,
        }))()
    """)
    w = viewport.get("width", 1280)
    h = viewport.get("height", 720)

    # Start position
    current_x = rng.randint(int(w * 0.1), int(w * 0.3))
    current_y = rng.randint(int(h * 0.1), int(h * 0.3))

    # Generate 3-5 waypoints
    num_waypoints = rng.randint(3, 5)
    waypoints = []
    for _ in range(num_waypoints):
        waypoints.append((
            rng.randint(int(w * 0.05), int(w * 0.95)),
            rng.randint(int(h * 0.05), int(h * 0.95)),
        ))

    # If targeting a specific element, make the last waypoint near it
    if target_selector:
        try:
            target_bounds = await page.evaluate(f"""
                (() => {{
                    const el = document.querySelector({repr(target_selector)});
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return {{
                        x: r.x + r.width / 2 + Math.random() * 10 - 5,
                        y: r.y + r.height / 2 + Math.random() * 10 - 5,
                    }};
                }})()
            """)
            if target_bounds:
                waypoints[-1] = (target_bounds["x"], target_bounds["y"])
        except Exception:
            pass

    mouse = getattr(page, "mouse", None)
    has_mouse_api = use_playwright_api and mouse and hasattr(mouse, "move")

    for target_x, target_y in waypoints:
        steps = rng.randint(15, 30)
        speed_factor = profile.mouse_speed

        points = _bezier_points(
            (current_x, current_y),
            (target_x, target_y),
            steps,
            rng,
        )

        for px, py in points:
            if has_mouse_api:
                await mouse.move(px, py)
            else:
                await page.evaluate(f"""
                    document.dispatchEvent(new MouseEvent('mousemove', {{
                        bubbles: true,
                        clientX: {px},
                        clientY: {py},
                    }}));
                """)
            # Variable speed: slower at start/end, faster in middle
            base_delay = 0.015 / speed_factor
            delay = base_delay + rng.uniform(0, 0.01 * profile.jitter)
            await asyncio.sleep(delay)

        current_x, current_y = target_x, target_y

        # Occasional micro-pause between waypoints (thinking)
        if rng.random() < profile.pause_tendency:
            await asyncio.sleep(rng.uniform(0.2, 0.8))


async def simulate_scroll_reading(
    page,
    *,
    profile: BehaviorProfile | None = None,
) -> None:
    """Simulate a user reading a page by scrolling gradually."""
    profile = profile or BehaviorProfile()
    rng = random.Random()

    scroll_info = await page.evaluate("""
        (() => ({
            scrollHeight: document.documentElement.scrollHeight,
            viewportHeight: window.innerHeight || 720,
            currentScroll: window.scrollY,
        }))()
    """)

    max_scroll = scroll_info["scrollHeight"] - scroll_info["viewportHeight"]
    if max_scroll <= 0:
        return

    # Scroll 20-50% of the page in chunks
    target_scroll = min(
        scroll_info["currentScroll"] + max_scroll * rng.uniform(0.2, 0.5),
        max_scroll,
    )
    current = scroll_info["currentScroll"]

    while current < target_scroll:
        # Each scroll chunk: 50-200px
        chunk = rng.uniform(50, 200) * profile.scroll_tendency
        current = min(current + chunk, target_scroll)

        await page.evaluate(f"""
            window.scrollTo({{
                top: {current},
                behavior: 'smooth',
            }})
        """)
        # Reading pause between scrolls
        await asyncio.sleep(rng.uniform(0.3, 1.2) / profile.mouse_speed)


async def simulate_typing(
    page,
    selector: str,
    text: str,
    *,
    profile: BehaviorProfile | None = None,
    use_playwright_api: bool = False,
) -> None:
    """Type text with realistic human cadence.

    Includes variable delays between keystrokes, occasional pauses,
    and key-press/key-release timing.
    """
    profile = profile or BehaviorProfile()
    rng = random.Random()

    keyboard = getattr(page, "keyboard", None)
    has_keyboard_api = use_playwright_api and keyboard and hasattr(keyboard, "type")

    # Click the field first
    try:
        if use_playwright_api:
            await page.click(selector)
        else:
            await page.evaluate(f"""
                (() => {{
                    const el = document.querySelector({repr(selector)});
                    if (el) {{ el.focus(); el.click(); }}
                }})()
            """)
    except Exception:
        pass

    await asyncio.sleep(rng.uniform(0.2, 0.5))

    for i, char in enumerate(text):
        if has_keyboard_api:
            await keyboard.type(char)
        else:
            await page.evaluate(f"""
                (() => {{
                    const el = document.activeElement;
                    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) {{
                        el.value += {repr(char)};
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }})()
            """)

        # Base typing delay (60-120 WPM equivalent)
        base_delay = rng.uniform(0.04, 0.12) / profile.typing_speed

        # Occasional longer pauses (thinking, reading what was typed)
        if rng.random() < 0.08:
            base_delay += rng.uniform(0.3, 0.8)

        # Slightly longer delay after spaces
        if char == " ":
            base_delay *= rng.uniform(1.1, 1.5)

        await asyncio.sleep(base_delay)


async def simulate_pre_captcha_activity(
    page,
    *,
    profile: BehaviorProfile | None = None,
    use_playwright_api: bool = False,
) -> None:
    """Full pre-CAPTCHA behavior simulation.

    Combines mouse movement, scrolling, and idle pauses to build
    up reCAPTCHA Enterprise trust score before the CAPTCHA challenge.
    """
    profile = profile or BehaviorProfile.random()
    rng = random.Random()

    log.debug("behavior_pre_captcha_iniciando", speed=profile.mouse_speed)

    try:
        # 1. Mouse movement across form area
        await simulate_mouse_movement(
            page,
            profile=profile,
            target_selector="#frmPrincipal",
            use_playwright_api=use_playwright_api,
        )

        # 2. Brief scroll (reading the form)
        if rng.random() < profile.scroll_tendency:
            await simulate_scroll_reading(page, profile=profile)

        # 3. Hover near form elements
        form_selectors = [
            "#frmPrincipal\\:ano",
            "#frmPrincipal\\:mes",
            "#frmPrincipal\\:cmbTipoComprobante",
            "#frmPrincipal\\:btnBuscar",
        ]
        hover_target = rng.choice(form_selectors)
        await simulate_mouse_movement(
            page,
            profile=profile,
            target_selector=hover_target,
            use_playwright_api=use_playwright_api,
        )

        # 4. Natural idle pause (reading/thinking)
        await asyncio.sleep(rng.uniform(0.8, 2.0) * profile.pause_tendency)

        log.debug("behavior_pre_captcha_completado")

    except Exception as exc:
        log.warning("behavior_pre_captcha_error", error=str(exc))
