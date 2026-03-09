"""
Factory para seleccionar el proveedor de CAPTCHA según configuración.

CAPTCHA_PROVIDER=2captcha  → usa 2Captcha (default)
CAPTCHA_PROVIDER=capsolver → usa CapSolver
"""

import structlog

log = structlog.get_logger()


def crear_resolver(provider: str, api_key: str):
    """
    Retorna una instancia del resolver según el proveedor.
    Ambos exponen la misma interfaz: resolver(), reportar_token_malo(), last_token.
    """
    provider = provider.lower().strip()

    if provider == "capsolver":
        from captcha.capsolver import CapSolverResolver
        log.info("captcha_provider_seleccionado", provider="capsolver")
        return CapSolverResolver(api_key)

    # Default: 2captcha
    from captcha.resolver import CaptchaResolver
    log.info("captcha_provider_seleccionado", provider="2captcha")
    return CaptchaResolver(api_key)


def crear_resolvers(
    provider: str,
    twocaptcha_api_key: str,
    capsolver_api_key: str,
):
    """Retorna resolvers disponibles en orden de preferencia.

    El proveedor configurado va primero, seguido del alterno si también
    tiene API key configurada.
    """
    preferred = provider.lower().strip()
    ordered: list[dict] = []
    seen: set[str] = set()

    def _append(name: str, api_key: str) -> None:
        if name in seen or not api_key:
            return
        resolver = crear_resolver(name, api_key)
        ordered.append({"provider": name, "resolver": resolver})
        seen.add(name)

    if preferred == "capsolver":
        _append("capsolver", capsolver_api_key)
        _append("2captcha", twocaptcha_api_key)
    else:
        _append("2captcha", twocaptcha_api_key)
        _append("capsolver", capsolver_api_key)

    if not ordered:
        fallback_key = (
            capsolver_api_key if preferred == "capsolver"
            else twocaptcha_api_key
        )
        ordered.append({
            "provider": preferred if preferred == "capsolver" else "2captcha",
            "resolver": crear_resolver(preferred, fallback_key),
        })

    return ordered
