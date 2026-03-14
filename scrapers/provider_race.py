"""
Concurrent CAPTCHA provider racing.

Requests tokens from multiple providers simultaneously and uses
the first valid response, cancelling the slower one to save credits.
"""

from __future__ import annotations

import asyncio
import time

import structlog

log = structlog.get_logger()


async def race_providers(
    resolvers: list[dict],
    *,
    site_key: str,
    page_url: str,
    enterprise: bool = False,
    action: str | None = None,
    score: float | None = None,
    invisible: bool = False,
    timeout_sec: float = 60.0,
) -> dict | None:
    """Race multiple CAPTCHA providers concurrently.

    Returns the first successful result as:
    {
        "token": str,
        "provider": str,
        "resolver": resolver_instance,
        "duration_sec": float,
    }
    Or None if all providers fail/timeout.

    Remaining tasks are cancelled after the first success.
    """
    if not resolvers:
        return None

    if len(resolvers) == 1:
        # No point racing with a single provider
        return await _solve_single(
            resolvers[0], site_key=site_key, page_url=page_url,
            enterprise=enterprise, action=action, score=score,
            invisible=invisible,
        )

    log.info(
        "provider_race_iniciando",
        providers=[r["provider"] for r in resolvers],
        enterprise=enterprise,
        action=action,
    )

    tasks: dict[asyncio.Task, dict] = {}
    for resolver_info in resolvers:
        task = asyncio.create_task(
            _solve_single(
                resolver_info, site_key=site_key, page_url=page_url,
                enterprise=enterprise, action=action, score=score,
                invisible=invisible,
            ),
            name=f"race_{resolver_info['provider']}",
        )
        tasks[task] = resolver_info

    winner = None
    start = time.monotonic()

    try:
        done, pending = await asyncio.wait(
            tasks.keys(),
            timeout=timeout_sec,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Check completed tasks for a valid result
        for task in done:
            try:
                result = task.result()
                if result and result.get("token"):
                    winner = result
                    break
            except Exception as exc:
                provider = tasks[task]["provider"]
                log.warning("provider_race_error", provider=provider, error=str(exc))

        # If first batch didn't yield results, wait for remaining
        if not winner and pending:
            done2, still_pending = await asyncio.wait(
                pending,
                timeout=max(timeout_sec - (time.monotonic() - start), 5),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done2:
                try:
                    result = task.result()
                    if result and result.get("token"):
                        winner = result
                        break
                except Exception:
                    pass
            pending = still_pending

    finally:
        # Cancel all remaining tasks
        all_pending = [t for t in tasks if not t.done()]
        for task in all_pending:
            task.cancel()
        if all_pending:
            await asyncio.gather(*all_pending, return_exceptions=True)

    elapsed = time.monotonic() - start

    if winner:
        log.info(
            "provider_race_ganador",
            winner=winner["provider"],
            duration_sec=round(elapsed, 2),
            token_len=len(winner["token"]),
        )
    else:
        log.warning("provider_race_sin_ganador", duration_sec=round(elapsed, 2))

    return winner


async def _solve_single(
    resolver_info: dict,
    *,
    site_key: str,
    page_url: str,
    enterprise: bool,
    action: str | None,
    score: float | None,
    invisible: bool,
) -> dict | None:
    """Solve CAPTCHA with a single provider and return result dict."""
    provider = resolver_info["provider"]
    resolver = resolver_info["resolver"]
    start = time.monotonic()

    try:
        token = await resolver.resolver_token_recaptcha(
            site_key=site_key,
            page_url=page_url,
            enterprise=enterprise,
            action=action,
            score=score,
            invisible=invisible,
        )
        duration = time.monotonic() - start

        if token:
            return {
                "token": token,
                "provider": provider,
                "resolver": resolver,
                "duration_sec": round(duration, 2),
            }
        return None
    except Exception as exc:
        duration = time.monotonic() - start
        log.debug(
            "provider_race_intento_fallido",
            provider=provider,
            error=str(exc),
            duration_sec=round(duration, 2),
        )
        raise
