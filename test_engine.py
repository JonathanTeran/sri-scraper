import asyncio

from config.settings import Settings
from scrapers.nodriver_engine import SRINodriverEngine
from utils.manual_credentials import (
    get_manual_test_credentials,
    get_manual_test_period,
)

async def main():
    creds = get_manual_test_credentials()
    anio, mes = get_manual_test_period()
    settings = Settings()
    # Desactivar headless para ver qué está pasando si queremos
    settings.playwright_headless = True

    engine = SRINodriverEngine(
        tenant_ruc=creds.ruc,
        tenant_usuario=creds.usuario,
        tenant_password=creds.password,
        periodo_anio=anio,
        periodo_mes=mes,
        tipo_comprobante="Factura",
        settings=settings
    )

    print("Iniciando ejecución...")
    resultados = await engine.ejecutar()
    
    print("\n--- Resultados ---")
    print(f"Total encontrados: {resultados.total_encontrados}")
    print(f"Nuevos descargados: {resultados.total_nuevos}")
    print(f"Errores: {resultados.total_errores}")

if __name__ == "__main__":
    asyncio.run(main())
