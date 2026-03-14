"""
Session warm-up: navigate public SRI pages before the CAPTCHA challenge.

reCAPTCHA Enterprise v3 monitors user activity over time.  By browsing
1-2 harmless pages before the query form, the session accumulates
"normal human" signals that raise the reCAPTCHA score from ~0.3 to ~0.7+.
"""

from __future__ import annotations

import asyncio
import json
import random

import structlog

log = structlog.get_logger()

# Public SRI pages that don't require authentication
_WARMUP_URLS = [
    "https://srienlinea.sri.gob.ec/sri-en-linea/inicio/NAT",
    "https://www.sri.gob.ec/",
    "https://srienlinea.sri.gob.ec/tuportal-internet/menusFavoritos.jspa",
]

# Pages to visit after login (within the portal)
_POST_LOGIN_WARMUP_URLS = [
    "https://srienlinea.sri.gob.ec/sri-en-linea//contribuyente/perfil",
]


async def warmup_session_nodriver(
    page,
    *,
    duration_sec: float = 5.0,
    post_login: bool = False,
) -> None:
    """Warm up a nodriver page with natural browsing activity.

    Navigates to 1-2 public pages, scrolls, moves mouse, and waits
    to build reCAPTCHA Enterprise trust signals.
    """
    rng = random.Random()
    urls = _POST_LOGIN_WARMUP_URLS if post_login else _WARMUP_URLS
    target_url = rng.choice(urls)

    log.info("warmup_iniciando", url=target_url, post_login=post_login)

    try:
        await page.get(target_url)
        await asyncio.sleep(rng.uniform(1.5, 3.0))

        # Simulate reading: scroll down slowly
        await page.evaluate("""
            (() => {
                const scrollStep = () => {
                    const maxScroll = Math.max(
                        document.body.scrollHeight - window.innerHeight, 200
                    );
                    const target = Math.min(
                        window.scrollY + Math.random() * 300 + 100,
                        maxScroll
                    );
                    window.scrollTo({
                        top: target,
                        behavior: 'smooth',
                    });
                };
                scrollStep();
                setTimeout(scrollStep, 800);
            })()
        """)
        await asyncio.sleep(rng.uniform(1.0, 2.0))

        # Simulate mouse activity
        await page.evaluate("""
            (() => {
                const dispatchMove = (x, y) => {
                    document.dispatchEvent(new MouseEvent('mousemove', {
                        bubbles: true, clientX: x, clientY: y,
                    }));
                };
                const w = window.innerWidth || 1280;
                const h = window.innerHeight || 720;
                for (let i = 0; i < 5; i++) {
                    setTimeout(() => {
                        dispatchMove(
                            Math.floor(Math.random() * w * 0.8 + w * 0.1),
                            Math.floor(Math.random() * h * 0.8 + h * 0.1),
                        );
                    }, i * 200);
                }
            })()
        """)
        await asyncio.sleep(rng.uniform(0.5, 1.5))

        log.info("warmup_completado", url=target_url)
    except Exception as exc:
        log.warning("warmup_error", url=target_url, error=str(exc))


async def warmup_session_playwright(
    page,
    *,
    duration_sec: float = 5.0,
    post_login: bool = False,
) -> None:
    """Warm up a Playwright page with natural browsing activity."""
    rng = random.Random()
    urls = _POST_LOGIN_WARMUP_URLS if post_login else _WARMUP_URLS
    target_url = rng.choice(urls)

    log.info("warmup_playwright_iniciando", url=target_url, post_login=post_login)

    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=15_000)
        await asyncio.sleep(rng.uniform(1.5, 3.0))

        # Scroll
        await page.evaluate("""
            window.scrollTo({
                top: Math.random() * 400 + 100,
                behavior: 'smooth',
            })
        """)
        await asyncio.sleep(rng.uniform(0.8, 1.5))

        # Mouse movements with Playwright mouse API
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        w = viewport["width"]
        h = viewport["height"]
        for _ in range(rng.randint(3, 6)):
            x = rng.randint(int(w * 0.1), int(w * 0.9))
            y = rng.randint(int(h * 0.1), int(h * 0.9))
            await page.mouse.move(x, y, steps=rng.randint(5, 12))
            await asyncio.sleep(rng.uniform(0.1, 0.3))

        await asyncio.sleep(rng.uniform(0.5, 1.5))
        log.info("warmup_playwright_completado", url=target_url)
    except Exception as exc:
        log.warning("warmup_playwright_error", url=target_url, error=str(exc))
