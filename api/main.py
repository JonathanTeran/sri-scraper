"""FastAPI app factory."""

from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.dependencies import get_settings_dep
from api.health import build_health_report
from api.routers import comprobantes, ejecuciones, exports, knowledge, tenants
from config.logging import setup_logging
from config.settings import Settings
from utils.runtime import ensure_runtime_directories

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings_dep()
    ensure_runtime_directories(settings)
    log.info("api_iniciada")
    yield
    log.info("api_detenida")


OPENAPI_TAGS = [
    {
        "name": "tenants",
        "description": (
            "Gestión de contribuyentes (tenants). Cada tenant representa una "
            "empresa o persona natural con credenciales del SRI. Las credenciales "
            "se almacenan encriptadas con Fernet."
        ),
    },
    {
        "name": "comprobantes",
        "description": (
            "Consulta de comprobantes electrónicos descargados del SRI. "
            "Soporta filtros por tipo, fecha y RUC emisor, paginación, "
            "detalle completo con líneas/retenciones/pagos, y descarga del XML original."
        ),
    },
    {
        "name": "ejecuciones",
        "description": (
            "Historial de ejecuciones de scraping. Cada ejecución registra "
            "el periodo consultado, cantidad de comprobantes encontrados/nuevos/errores, "
            "duración y estado final."
        ),
    },
    {
        "name": "exports",
        "description": (
            "Exportación de comprobantes a archivos Excel (.xlsx) con múltiples hojas: "
            "cabeceras, detalles y retenciones."
        ),
    },
    {
        "name": "knowledge",
        "description": (
            "Base de conocimiento persistente del SRI. Consulta estadísticas de motores, "
            "variantes CAPTCHA, proveedores, horarios óptimos y patrones de bloqueo "
            "acumulados a largo plazo."
        ),
    },
]


def create_app() -> FastAPI:
    app = FastAPI(
        title="SRI Scraper API",
        description=(
            "API REST para el sistema de descarga automática de comprobantes "
            "electrónicos del portal SRI en Línea (Ecuador).\n\n"
            "## Funcionalidades principales\n\n"
            "- **Tenants**: Administrar contribuyentes (multi-tenant)\n"
            "- **Scraping**: Lanzar descargas manuales o programadas de comprobantes\n"
            "- **Comprobantes**: Consultar, filtrar y descargar XMLs\n"
            "- **Ejecuciones**: Monitorear el historial de ejecuciones\n"
            "- **Exportación**: Generar reportes Excel\n\n"
            "## Notas\n\n"
            "- El SRI tiene mantenimiento nocturno entre **00:00–06:00** (Ecuador). "
            "Las ejecuciones automáticas se programan a las 06:30.\n"
            "- Mínimo 800ms de delay entre descargas para evitar bloqueos.\n"
            "- Credenciales almacenadas con encriptación Fernet."
        ),
        version="1.0.0",
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(
        tenants.router,
        prefix="/api/v1",
        tags=["tenants"],
    )
    app.include_router(
        comprobantes.router,
        prefix="/api/v1",
        tags=["comprobantes"],
    )
    app.include_router(
        ejecuciones.router,
        prefix="/api/v1",
        tags=["ejecuciones"],
    )
    app.include_router(
        exports.router,
        prefix="/api/v1",
        tags=["exports"],
    )
    app.include_router(
        knowledge.router,
        prefix="/api/v1",
        tags=["knowledge"],
    )

    @app.get("/health")
    async def health(
        settings: Settings = Depends(get_settings_dep),
    ):
        report = await build_health_report(settings)
        status_code = 200 if report["status"] == "ok" else 503
        return JSONResponse(status_code=status_code, content=report)

    @app.get("/ready")
    async def ready(
        settings: Settings = Depends(get_settings_dep),
    ):
        report = await build_health_report(settings)
        if report["status"] != "ok":
            return JSONResponse(status_code=503, content=report)
        return report

    return app


app = create_app()
