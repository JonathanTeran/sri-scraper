"""
Test de ciclo completo: login -> captcha -> consulta -> descarga XML
Usa el motor Playwright (SRIScraperEngine) que funciona en Docker.
"""
import asyncio

from config.settings import Settings
from scrapers.engine import SRIScraperEngine
from utils.manual_credentials import (
    get_manual_test_credentials,
    get_manual_test_period,
)


async def main():
    creds = get_manual_test_credentials()
    anio, mes = get_manual_test_period()
    settings = Settings()
    settings.playwright_headless = False  # Xvfb lo maneja

    engine = SRIScraperEngine(
        tenant_ruc=creds.ruc,
        tenant_usuario=creds.usuario,
        tenant_password=creds.password,
        periodo_anio=anio,
        periodo_mes=mes,
        tipo_comprobante="Factura",
        settings=settings,
    )

    print("Iniciando ciclo completo con Playwright engine...")
    result = await engine.ejecutar()

    print("\n--- Resultados ---")
    print(f"Total encontrados: {result.total_encontrados}")
    print(f"Nuevos descargados: {result.total_nuevos}")
    print(f"Errores: {result.total_errores}")
    print(f"Duración: {result.duracion_seg:.1f}s")
    print(f"Página final: {result.pagina_final}")
    if result.xmls_descargados:
        print(f"XMLs: {len(result.xmls_descargados)}")
        for x in result.xmls_descargados[:3]:
            print(f"  - {x.get('clave_acceso', 'N/A')[:20]}...")


if __name__ == "__main__":
    asyncio.run(main())
