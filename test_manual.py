"""
Test manual: Abre Chrome, el usuario navega y hace login manualmente.
Cuando esté en la página de comprobantes, presionar Enter en la terminal.
Luego el script ejecuta el reCAPTCHA nativo y muestra el resultado.
"""
import asyncio
import json
import nodriver as uc

PORTAL_URL = (
    "https://srienlinea.sri.gob.ec/comprobantes-electronicos-internet/"
    "pages/consultas/recibidos/comprobantesRecibidos.jsf"
)


async def main():
    print("=" * 60)
    print("TEST MANUAL DE reCAPTCHA")
    print("=" * 60)
    print("\n1. Se abrirá Chrome")
    print("2. Navega al SRI, haz login, ve a comprobantes recibidos")
    print("3. Selecciona año y mes en el formulario")
    print("4. Vuelve a la terminal y presiona ENTER")
    print("=" * 60)

    browser = await uc.start(headless=False)
    page = await browser.get(PORTAL_URL)

    input("\n>>> Presiona ENTER cuando estés en la página de comprobantes con año/mes seleccionados...")

    # Obtener la tab activa
    page = browser.main_tab
    url = page.url
    print(f"\n[+] URL actual: {url[:80]}")

    # Diagnóstico
    diag = await page.evaluate("""
    (() => ({
        hasForm: !!document.getElementById('frmPrincipal:ano')
                 || !!document.getElementById('frmPrincipal:anio'),
        hasEnterprise: typeof grecaptcha !== 'undefined' && !!grecaptcha.enterprise,
        executeExists: typeof executeRecaptcha === 'function',
        rcBuscarExists: typeof rcBuscar === 'function',
    }))()
    """)
    print(f"[+] Diagnóstico: {json.dumps(diag)}")

    if not diag.get("executeExists"):
        print("[!] executeRecaptcha no existe en la página. ¿Estás en la página correcta?")
        browser.stop()
        return

    # Ejecutar reCAPTCHA nativo
    print("\n[+] Ejecutando reCAPTCHA NATIVO en Chrome real...")
    result = await page.evaluate("""
    (() => new Promise((resolve) => {
        const origRcBuscar = window.rcBuscar;
        const timeout = setTimeout(() => {
            window.rcBuscar = origRcBuscar;
            const ta = document.querySelector('[name="g-recaptcha-response"]');
            resolve({
                error: 'timeout_45s',
                tokenLen: ta ? ta.value.length : -1,
            });
        }, 45000);

        window.rcBuscar = function() {
            const ta = document.querySelector('[name="g-recaptcha-response"]');
            origRcBuscar.apply(this, arguments);
            window.rcBuscar = origRcBuscar;
            setTimeout(() => {
                clearTimeout(timeout);
                const msgs = document.getElementById('formMessages:messages');
                const panel = document.getElementById('frmPrincipal:panelListaComprobantes');
                resolve({
                    messages: msgs ? msgs.innerText.trim() : '',
                    panelLen: panel ? panel.innerHTML.length : 0,
                    tokenLen: ta ? ta.value.length : -1,
                });
            }, 12000);
        };

        if (typeof executeRecaptcha === 'function') {
            executeRecaptcha('consulta_cel_recibidos');
        } else {
            clearTimeout(timeout);
            window.rcBuscar = origRcBuscar;
            resolve({ error: 'no_executeRecaptcha' });
        }
    }))()
    """)

    print(f"\n{'='*60}")
    print(f"RESULTADO: {json.dumps(result, indent=2)}")
    if result.get("panelLen", 0) > 50:
        print("\n*** CAPTCHA ACEPTADO! ***")
    elif "captcha" in result.get("messages", "").lower():
        print("\n*** CAPTCHA RECHAZADO ***")
    else:
        print(f"\n*** Otro resultado ***")
    print(f"{'='*60}")

    input("\n>>> Presiona ENTER para cerrar el browser...")
    browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
