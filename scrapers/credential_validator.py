"""Validación ligera de credenciales SRI antes de persistir tenants."""

from __future__ import annotations

from dataclasses import dataclass

from config.settings import Settings
from scrapers.engine import SRIScraperEngine
from scrapers.exceptions import (
    SRICaptchaError,
    SRILoginError,
    SRIMaintenanceError,
    SRITimeoutError,
)
from utils.time import utc_now


@dataclass(frozen=True)
class CredentialValidationResult:
    ok: bool
    message: str


class _StatelessSessionManager:
    async def cargar_cookies(self, context) -> bool:  # pragma: no cover - trivial
        return False

    async def guardar_cookies(self, context) -> None:  # pragma: no cover - trivial
        return None

    def limpiar_sesion(self) -> None:  # pragma: no cover - trivial
        return None


async def validar_credenciales_sri(
    *,
    ruc: str,
    usuario: str,
    password: str,
    settings: Settings,
) -> CredentialValidationResult:
    periodo = utc_now()
    validation_settings = settings.model_copy(
        update={
            "playwright_headless": True,
            "browser_persistent_context": False,
            "captcha_assisted_mode": "off",
        }
    )
    engine = SRIScraperEngine(
        tenant_ruc=ruc,
        tenant_usuario=usuario,
        tenant_password=password,
        periodo_anio=periodo.year,
        periodo_mes=periodo.month,
        tipo_comprobante="Factura",
        settings=validation_settings,
    )
    engine._session_mgr = _StatelessSessionManager()

    try:
        await engine._inicializar_browser()
        await engine._login()
    except SRILoginError as exc:
        return CredentialValidationResult(
            ok=False,
            message=f"Credenciales inválidas: {exc}",
        )
    except SRICaptchaError as exc:
        return CredentialValidationResult(
            ok=False,
            message=f"No se pudo validar por captcha: {exc}",
        )
    except SRIMaintenanceError as exc:
        return CredentialValidationResult(
            ok=False,
            message=f"SRI en mantenimiento: {exc}",
        )
    except SRITimeoutError as exc:
        return CredentialValidationResult(
            ok=False,
            message=f"Timeout validando credenciales: {exc}",
        )
    except Exception as exc:
        return CredentialValidationResult(
            ok=False,
            message=f"Error validando credenciales: {exc}",
        )
    finally:
        await engine._cerrar_browser()

    return CredentialValidationResult(
        ok=True,
        message="Credenciales SRI válidas",
    )
