"""Shared reCAPTCHA strategy for the SRI consulta flow."""

from __future__ import annotations

from scrapers.portal import RECAPTCHA_ACTION

PROVIDER_VARIANTS: tuple[dict, ...] = (
    {
        "variant": "enterprise_v3_high",
        "enterprise": True,
        "action": RECAPTCHA_ACTION,
        "score": 0.9,
        "invisible": True,
        "page_url_mode": "canonical",
    },
    {
        "variant": "enterprise_v3_low",
        "enterprise": True,
        "action": RECAPTCHA_ACTION,
        "score": 0.3,
        "invisible": True,
        "page_url_mode": "canonical",
    },
    {
        "variant": "enterprise_v2_action",
        "enterprise": True,
        "action": RECAPTCHA_ACTION,
        "score": None,
        "invisible": True,
        "page_url_mode": "canonical",
    },
    {
        "variant": "enterprise_v2_current_url",
        "enterprise": True,
        "action": RECAPTCHA_ACTION,
        "score": None,
        "invisible": True,
        "page_url_mode": "current",
    },
    {
        "variant": "v2_invisible_current_url",
        "enterprise": False,
        "action": None,
        "score": None,
        "invisible": True,
        "page_url_mode": "current",
    },
)


def build_captcha_attempt_plan(
    *,
    assist_mode: str,
    assisted_available: bool,
    captcha_resolvers: list[dict],
    max_attempts: int | None = None,
) -> list[dict]:
    attempts: list[dict] = []

    if assist_mode == "only" and assisted_available:
        return [{"mode": "assisted"}]

    native_attempts = 1 if captcha_resolvers else 2
    attempts.extend({"mode": "native"} for _ in range(native_attempts))

    def _provider_attempts(variants: tuple[dict, ...]) -> list[dict]:
        ordered: list[dict] = []
        for resolver_info in captcha_resolvers:
            for variant in variants:
                ordered.append(
                    {
                        "mode": "provider",
                        "provider": resolver_info["provider"],
                        "resolver": resolver_info["resolver"],
                        **variant,
                    }
                )
        return ordered

    if assist_mode == "fallback" and assisted_available:
        fast_variants = tuple(
            variant
            for variant in PROVIDER_VARIANTS
            if variant["variant"] in {
                "enterprise_v3_high",
                "enterprise_v2_action",
            }
        )
        tail_variants = tuple(
            variant
            for variant in PROVIDER_VARIANTS
            if variant["variant"] not in {
                "enterprise_v3_high",
                "enterprise_v2_action",
            }
        )
        attempts.extend(_provider_attempts(fast_variants))
        attempts.append({"mode": "assisted"})
        attempts.extend(_provider_attempts(tail_variants))
    else:
        attempts.extend(_provider_attempts(PROVIDER_VARIANTS))

    if assist_mode == "fallback" and assisted_available and not any(
        attempt["mode"] == "assisted" for attempt in attempts
    ):
        attempts.append({"mode": "assisted"})

    if max_attempts is not None:
        return attempts[:max_attempts]
    return attempts


def resolve_provider_page_url(page_url: str, page_url_mode: str) -> str:
    if page_url_mode == "current":
        return page_url.split("#")[0]
    return page_url.split("?")[0].split("#")[0]
