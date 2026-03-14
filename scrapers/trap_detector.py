"""
Trap and honeypot detection for the SRI portal.

Detects when the portal changes its CAPTCHA configuration dynamically,
presents impossible challenges, or serves honeypot elements designed
to catch bots.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger()


async def detect_sitekey_change(
    page,
    known_sitekey: str | None,
    *,
    extract_asset_fn=None,
) -> dict:
    """Check if the portal's reCAPTCHA sitekey has changed.

    Returns:
    {
        "changed": bool,
        "current_sitekey": str | None,
        "previous_sitekey": str | None,
        "alert": str | None,
    }
    """
    current_sitekey = None

    if extract_asset_fn:
        try:
            current_sitekey = await extract_asset_fn()
        except Exception:
            pass

    if not current_sitekey:
        try:
            current_sitekey = await page.evaluate("""
                (() => {
                    const iframes = document.querySelectorAll('iframe[src*="recaptcha"]');
                    for (const iframe of iframes) {
                        const match = iframe.src.match(/[?&]k=([^&]+)/);
                        if (match) return match[1];
                    }
                    const divs = document.querySelectorAll('[data-sitekey]');
                    for (const div of divs) {
                        if (div.dataset.sitekey) return div.dataset.sitekey;
                    }
                    return null;
                })()
            """)
        except Exception:
            pass

    changed = (
        known_sitekey is not None
        and current_sitekey is not None
        and known_sitekey != current_sitekey
    )

    result = {
        "changed": changed,
        "current_sitekey": current_sitekey,
        "previous_sitekey": known_sitekey,
        "alert": None,
    }

    if changed:
        result["alert"] = (
            f"sitekey changed: {known_sitekey[:10]}... -> {current_sitekey[:10]}..."
        )
        log.warning(
            "trap_sitekey_cambio_detectado",
            previous=known_sitekey[:10] if known_sitekey else None,
            current=current_sitekey[:10] if current_sitekey else None,
        )

    return result


async def detect_honeypot_fields(page) -> list[dict]:
    """Detect hidden form fields that might be honeypots.

    Honeypots are invisible fields that bots fill in but humans don't.
    If we detect them, we should leave them empty.
    """
    try:
        honeypots = await page.evaluate("""
            (() => {
                const suspicious = [];
                const form = document.getElementById('frmPrincipal')
                    || document.querySelector('form');
                if (!form) return suspicious;

                const inputs = form.querySelectorAll(
                    'input[type="text"], input[type="email"], input:not([type])'
                );
                for (const input of inputs) {
                    const style = window.getComputedStyle(input);
                    const isHidden = (
                        style.display === 'none'
                        || style.visibility === 'hidden'
                        || style.opacity === '0'
                        || parseInt(style.height) === 0
                        || parseInt(style.width) === 0
                        || input.offsetParent === null
                    );
                    // Honeypot indicators: hidden but not disabled, no known ID
                    const knownIds = [
                        'frmPrincipal:ano', 'frmPrincipal:mes',
                        'frmPrincipal:dia', 'frmPrincipal:cmbTipoComprobante',
                        'frmPrincipal:btnBuscar', 'javax.faces.ViewState',
                    ];
                    const isKnown = knownIds.some(
                        id => input.id === id || input.name === id
                    );
                    if (isHidden && !input.disabled && !isKnown) {
                        suspicious.push({
                            id: input.id || '',
                            name: input.name || '',
                            type: input.type || 'text',
                            tabindex: input.tabIndex,
                            autocomplete: input.autocomplete || '',
                        });
                    }
                }
                return suspicious;
            })()
        """)
    except Exception:
        return []

    if honeypots:
        log.warning("trap_honeypot_detectado", count=len(honeypots), fields=honeypots)
    return honeypots


async def detect_captcha_anomalies(page) -> dict:
    """Detect unusual CAPTCHA configurations that might indicate traps.

    Checks for:
    - Multiple reCAPTCHA instances (unusual, might confuse bots)
    - Invisible CAPTCHA with visible checkbox (contradictory)
    - Missing or malformed site keys
    - Changed form structure
    """
    try:
        analysis = await page.evaluate("""
            (() => {
                const result = {
                    recaptchaIframeCount: 0,
                    recaptchaWidgetCount: 0,
                    hasInvisibleBadge: false,
                    hasVisibleCheckbox: false,
                    sitekeyCount: 0,
                    sitekeys: [],
                    formActionChanged: false,
                    suspiciousScripts: 0,
                    anomalies: [],
                };

                // Count reCAPTCHA iframes
                const iframes = document.querySelectorAll('iframe[src*="recaptcha"]');
                result.recaptchaIframeCount = iframes.length;

                // Count widgets
                const widgets = document.querySelectorAll('.g-recaptcha');
                result.recaptchaWidgetCount = widgets.length;

                // Check for invisible badge
                const badge = document.querySelector('.grecaptcha-badge');
                result.hasInvisibleBadge = !!badge;

                // Check for visible checkbox
                const checkboxFrame = document.querySelector(
                    'iframe[src*="recaptcha/api2/anchor"]'
                );
                result.hasVisibleCheckbox = !!checkboxFrame;

                // Collect all sitekeys
                const sitekeys = new Set();
                for (const iframe of iframes) {
                    const match = iframe.src.match(/[?&]k=([^&]+)/);
                    if (match) sitekeys.add(match[1]);
                }
                for (const div of document.querySelectorAll('[data-sitekey]')) {
                    if (div.dataset.sitekey) sitekeys.add(div.dataset.sitekey);
                }
                result.sitekeys = Array.from(sitekeys);
                result.sitekeyCount = sitekeys.size;

                // Anomaly detection
                if (result.recaptchaIframeCount > 3) {
                    result.anomalies.push('too_many_recaptcha_iframes');
                }
                if (result.hasInvisibleBadge && result.hasVisibleCheckbox) {
                    result.anomalies.push('mixed_visible_invisible_captcha');
                }
                if (result.sitekeyCount > 1) {
                    result.anomalies.push('multiple_sitekeys');
                }
                if (result.sitekeyCount === 0 && result.recaptchaIframeCount > 0) {
                    result.anomalies.push('iframe_without_sitekey');
                }

                // Check for suspicious inline scripts
                const scripts = document.querySelectorAll('script:not([src])');
                for (const script of scripts) {
                    const text = (script.textContent || '').toLowerCase();
                    if (
                        text.includes('trap') ||
                        text.includes('honeypot') ||
                        text.includes('bot-detect')
                    ) {
                        result.suspiciousScripts++;
                    }
                }
                if (result.suspiciousScripts > 0) {
                    result.anomalies.push('suspicious_scripts');
                }

                return result;
            })()
        """)
    except Exception as exc:
        log.warning("trap_analysis_error", error=str(exc))
        return {"anomalies": [], "error": str(exc)}

    if analysis.get("anomalies"):
        log.warning(
            "trap_anomalias_detectadas",
            anomalies=analysis["anomalies"],
            iframe_count=analysis.get("recaptchaIframeCount"),
            sitekey_count=analysis.get("sitekeyCount"),
        )

    return analysis


async def run_full_trap_check(
    page,
    known_sitekey: str | None = None,
    extract_asset_fn=None,
) -> dict:
    """Run all trap detection checks and return combined results."""
    sitekey_check = await detect_sitekey_change(
        page, known_sitekey, extract_asset_fn=extract_asset_fn,
    )
    honeypots = await detect_honeypot_fields(page)
    anomalies = await detect_captcha_anomalies(page)

    all_warnings = []
    if sitekey_check.get("changed"):
        all_warnings.append("sitekey_changed")
    if honeypots:
        all_warnings.append(f"honeypots_found_{len(honeypots)}")
    all_warnings.extend(anomalies.get("anomalies", []))

    result = {
        "safe": len(all_warnings) == 0,
        "warnings": all_warnings,
        "sitekey": sitekey_check,
        "honeypots": honeypots,
        "captcha_analysis": anomalies,
    }

    if all_warnings:
        log.warning("trap_check_warnings", warnings=all_warnings)
    else:
        log.debug("trap_check_limpio")

    return result
