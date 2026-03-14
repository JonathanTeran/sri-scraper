"""
CAPTCHA token pre-validation before submission.

Analyzes token quality indicators to decide whether to submit
a token or request a new one, saving wasted portal attempts.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import structlog

log = structlog.get_logger()

# reCAPTCHA token patterns
_MIN_TOKEN_LENGTH = 100
_TYPICAL_V2_TOKEN_LENGTH = (400, 800)
_TYPICAL_V3_TOKEN_LENGTH = (800, 2000)
_TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9_-]+$')


@dataclass
class TokenValidationResult:
    """Result of token pre-validation."""

    valid: bool
    confidence: float     # 0.0 - 1.0
    reason: str = ""
    token_type: str = ""  # "v2", "v3", "enterprise_v3", "unknown"

    @property
    def should_submit(self) -> bool:
        """Whether the token is worth submitting to the portal."""
        return self.valid and self.confidence >= 0.3


def validate_token(
    token: str | None,
    *,
    variant: str = "",
    provider: str = "",
    solve_duration_sec: float = 0.0,
) -> TokenValidationResult:
    """Pre-validate a CAPTCHA token before submitting to the portal.

    Checks:
    1. Token is not empty/None
    2. Token meets minimum length
    3. Token contains valid characters
    4. Token length matches expected range for variant
    5. Solve duration is within normal bounds
    """
    if not token:
        return TokenValidationResult(
            valid=False, confidence=0.0, reason="empty_token",
        )

    # Basic format check
    if len(token) < _MIN_TOKEN_LENGTH:
        return TokenValidationResult(
            valid=False, confidence=0.0,
            reason=f"too_short_{len(token)}",
        )

    if not _TOKEN_PATTERN.match(token):
        # Some tokens can have dots — be lenient
        clean = token.replace(".", "")
        if not _TOKEN_PATTERN.match(clean):
            return TokenValidationResult(
                valid=False, confidence=0.1,
                reason="invalid_characters",
            )

    # Determine token type from length and variant
    token_len = len(token)
    confidence = 0.7  # base confidence for valid-looking token

    if "v3" in variant or "v3" in variant.lower():
        token_type = "v3"
        min_len, max_len = _TYPICAL_V3_TOKEN_LENGTH
        if min_len <= token_len <= max_len:
            confidence += 0.15
        elif token_len < min_len:
            confidence -= 0.2
    elif "v2" in variant:
        token_type = "v2"
        min_len, max_len = _TYPICAL_V2_TOKEN_LENGTH
        if min_len <= token_len <= max_len:
            confidence += 0.15
        elif token_len > max_len * 2:
            confidence -= 0.1
    else:
        token_type = "unknown"

    # Solve duration analysis
    if solve_duration_sec > 0:
        if solve_duration_sec < 2.0:
            # Suspiciously fast — might be a cached/stale token
            confidence -= 0.15
            log.debug("token_solve_muy_rapido", duration_sec=solve_duration_sec)
        elif solve_duration_sec > 120:
            # Very slow — token might be expired by submission time
            confidence -= 0.2
            log.debug("token_solve_muy_lento", duration_sec=solve_duration_sec)
        elif 5 <= solve_duration_sec <= 30:
            # Normal range
            confidence += 0.1

    # Provider-specific adjustments
    if provider == "capsolver":
        confidence += 0.05  # Generally reliable
    elif provider == "2captcha":
        confidence += 0.03

    confidence = max(0.0, min(1.0, confidence))

    result = TokenValidationResult(
        valid=True,
        confidence=round(confidence, 3),
        token_type=token_type,
    )

    log.debug(
        "token_validado",
        valid=result.valid,
        confidence=result.confidence,
        token_type=result.token_type,
        token_len=token_len,
        variant=variant,
        provider=provider,
        solve_sec=round(solve_duration_sec, 1),
    )
    return result


def estimate_token_freshness(
    token: str,
    obtained_at: float,
) -> float:
    """Estimate how fresh a token is (0.0 = expired, 1.0 = fresh).

    reCAPTCHA tokens expire after approximately 120 seconds.
    """
    age_sec = time.time() - obtained_at
    if age_sec < 0:
        return 0.0
    # Linear decay: 100% at 0s, 50% at 60s, 0% at 120s
    freshness = max(0.0, 1.0 - age_sec / 120.0)
    return round(freshness, 3)
