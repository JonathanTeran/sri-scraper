class SRIBaseError(Exception):
    """Excepción base para errores del SRI."""
    pass


class SRILoginError(SRIBaseError):
    """Error de login. NO reintentar."""
    pass


class SRISessionExpiredError(SRIBaseError):
    """Sesión expirada. Re-login y continuar."""
    pass


class SRICaptchaError(SRIBaseError):
    """Error de CAPTCHA. Esperar 5min y reintentar."""
    pass


class ProviderError(SRICaptchaError):
    """Error irrecuperable de proveedor. Cambiar al fallback."""
    pass


class SRIMaintenanceError(SRIBaseError):
    """SRI en mantenimiento. Esperar 30min."""
    pass


class SRITimeoutError(SRIBaseError):
    """Timeout. Reintentar con backoff."""
    pass


class SRIDownloadError(SRIBaseError):
    """Error de descarga. Reintentar 3 veces."""
    pass


class SRIParserError(SRIBaseError):
    """Error de parsing. Guardar XML, continuar."""
    pass


class XMLInvalidError(SRIParserError):
    """XML no es XML real."""
    pass
