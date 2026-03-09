"""Abre Chrome, espera 90s para navegación manual, luego ejecuta reCAPTCHA."""
import asyncio
import json
import nodriver as uc

async def main():
    print("Abriendo Chrome...")
    browser = await uc.start(headless=False)
    page = await browser.get(
        "https://srienlinea.sri.gob.ec/comprobantes-electronicos-internet/"
        "pages/consultas/recibidos/comprobantesRecibidos.jsf"
    )
    print("Chrome abierto.")
    print("Tienes 90 segundos para:")
    print("  1. Hacer login en el SRI")
    print("  2. Navegar a comprobantes recibidos")
    print("  3. Seleccionar año y mes")
    print("\nEsperando 90 segundos...")

    for i in range(120, 0, -10):
        print(f"  {i}s restantes...")
        await asyncio.sleep(10)

    page = browser.main_tab
    url = page.url
    print(f"\nURL: {url[:120]}")

    # Verificar si estamos en la página correcta
    has_fn = await page.evaluate(
        "typeof executeRecaptcha === 'function'"
    )
    has_form = await page.evaluate(
        "!!document.getElementById('frmPrincipal:ano') || !!document.getElementById('frmPrincipal:anio')"
    )
    print(f"executeRecaptcha: {has_fn}, formulario: {has_form}")

    if not has_fn:
        print("ERROR: No estás en comprobantes recibidos.")
        print("Necesitas estar en: comprobantesRecibidos.jsf")
        browser.stop()
        return

    print("Ejecutando reCAPTCHA nativo...")
    result = await page.evaluate("""
    (() => new Promise((resolve) => {
        const orig = window.rcBuscar;
        const t = setTimeout(() => {
            window.rcBuscar = orig;
            resolve({error:'timeout'});
        }, 45000);
        window.rcBuscar = function() {
            orig.apply(this, arguments);
            window.rcBuscar = orig;
            setTimeout(() => {
                clearTimeout(t);
                const m = document.getElementById('formMessages:messages');
                const p = document.getElementById('frmPrincipal:panelListaComprobantes');
                resolve({
                    messages: m ? m.innerText.trim() : '',
                    panelLen: p ? p.innerHTML.length : 0,
                });
            }, 12000);
        };
        executeRecaptcha('consulta_cel_recibidos');
    }))()
    """)

    print(f"\nRESULTADO: {json.dumps(result, indent=2) if result else 'null'}")
    if not result:
        print("ERROR: evaluate retornó null")
        browser.stop()
        return
    if result.get("panelLen", 0) > 50:
        print("\n*** CAPTCHA ACEPTADO! ***")
    elif "captcha" in result.get("messages","").lower():
        print("\n*** CAPTCHA RECHAZADO ***")
    else:
        print(f"\n*** Otro resultado ***")

    await asyncio.sleep(5)
    browser.stop()

if __name__ == "__main__":
    asyncio.run(main())
