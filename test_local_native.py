"""
Test LOCAL (sin Docker) para verificar si reCAPTCHA nativo pasa
en un browser real con Chrome de macOS.

Si este test PASA → el problema es Docker/Xvfb.
Si este test FALLA → el problema es otro (quizás el sitekey o el flujo).
"""
import asyncio
import json

import nodriver as uc
from utils.manual_credentials import (
    get_manual_test_credentials,
    get_manual_test_period,
)

PORTAL_URL = (
    "https://srienlinea.sri.gob.ec/comprobantes-electronicos-internet/"
    "pages/consultas/recibidos/comprobantesRecibidos.jsf"
)


async def main():
    creds = get_manual_test_credentials()
    anio, mes = get_manual_test_period()
    print("[+] Arrancando Chrome real (macOS)...")
    browser = await uc.start(headless=False)
    page = await browser.get(PORTAL_URL)
    await asyncio.sleep(8)

    # Login si necesario
    if "auth/realms" in page.url:
        print("[+] Haciendo login...")
        try:
            el = await page.select("input#usuario")
            await el.click()
            await asyncio.sleep(0.3)
            await el.send_keys(creds.usuario)
        except Exception as e:
            print(f"  usuario error: {e}")

        await asyncio.sleep(0.5)
        try:
            el = await page.select("input#password")
            await el.click()
            await asyncio.sleep(0.3)
            await el.send_keys(creds.password)
        except Exception as e:
            print(f"  password error: {e}")

        await asyncio.sleep(1)
        try:
            btn = await page.select("input#kc-login")
            await btn.click()
        except Exception as e:
            print(f"  submit error: {e}")

        # Esperar el redirect de Keycloak
        await asyncio.sleep(25)

        # Capturar la tab actual (puede haber cambiado)
        page = browser.main_tab
        url = page.url
        print(f"  Post-login URL: {url[:80]}")

        # Si aún en auth, las credenciales pueden estar mal o hay delay
        if "auth/realms" in url:
            body = await page.evaluate("document.body.innerText")
            print(f"  Body: {body[:200]}")
            # Intentar login de nuevo
            if "Clave" in body or "usuario" in body:
                print("  [!] Form login aún visible, reintentando...")
                try:
                    el = await page.select("input#usuario")
                    await el.clear_input()
                    await el.send_keys(creds.usuario)
                    await asyncio.sleep(0.5)
                    el = await page.select("input#password")
                    await el.clear_input()
                    await el.send_keys(creds.password)
                    await asyncio.sleep(0.5)
                    btn = await page.select("input#kc-login")
                    await btn.click()
                    await asyncio.sleep(25)
                    page = browser.main_tab
                    url = page.url
                    print(f"  Post-login2 URL: {url[:80]}")
                except Exception as e:
                    print(f"  Login retry error: {e}")

        # Navegar en la MISMA tab a comprobantes
        await page.evaluate(f"window.location.href = '{PORTAL_URL}'")
        await asyncio.sleep(20)

    url = page.url
    print(f"[+] URL: {url[:80]}")

    # Si en perfil o auth, intentar de nuevo
    if "perfil" in url or "sri-en-linea" in url or "auth/realms" in url:
        print("[+] Redirigido, navegando a comprobantes...")
        await page.evaluate(f"window.location.href = '{PORTAL_URL}'")
        await asyncio.sleep(20)

    # Verificar formulario
    try:
        body = await page.evaluate("document.body.innerHTML")
    except Exception:
        page = browser.main_tab
        await asyncio.sleep(2)
        body = await page.evaluate("document.body.innerHTML")

    if "frmPrincipal" not in body:
        print("[!] No se encontró el formulario")
        browser.stop()
        return

    print("[+] Formulario encontrado. Seleccionando período...")

    # Seleccionar período
    await page.evaluate("""
    (year) => {
        const a = document.getElementById('frmPrincipal:anio')
                  || document.getElementById('frmPrincipal:ano');
        if (a) {
            for (let o of a.options)
                if (o.text === String(year)) { a.value = o.value; break; }
            a.dispatchEvent(new Event('change', {bubbles: true}));
        }
    }
    """, anio)
    await asyncio.sleep(2)

    month_label = [
        None, "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    ][mes]
    await page.evaluate("""
    (monthLabel) => {
        const m = document.getElementById('frmPrincipal:mes');
        if (m) {
            for (let o of m.options)
                if (o.text === monthLabel) { m.value = o.value; break; }
            m.dispatchEvent(new Event('change', {bubbles: true}));
        }
    }
    """, month_label)
    await asyncio.sleep(2)

    # Seleccionar día = Todos
    await page.evaluate("""
    (() => {
        const d = document.getElementById('frmPrincipal:dia');
        if (d) { d.value = '0'; d.dispatchEvent(new Event('change', {bubbles: true})); }
    })()
    """)
    await asyncio.sleep(1)

    # Diagnóstico reCAPTCHA
    diag = await page.evaluate("""
    (() => ({
        hasEnterprise: typeof grecaptcha !== 'undefined' && !!grecaptcha.enterprise,
        executeExists: typeof executeRecaptcha === 'function',
        rcBuscarExists: typeof rcBuscar === 'function',
        textareaCount: document.querySelectorAll('[name="g-recaptcha-response"]').length,
    }))()
    """)
    print(f"[+] reCAPTCHA diag: {json.dumps(diag)}")

    # Ejecutar reCAPTCHA NATIVO — Chrome real en macOS
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
        print("\n*** CAPTCHA ACEPTADO EN CHROME REAL! ***")
        print("*** El problema es Docker/Xvfb ***")
    elif "captcha" in result.get("messages", "").lower():
        print("\n*** CAPTCHA RECHAZADO INCLUSO EN CHROME REAL ***")
        print("*** El problema NO es el entorno sino el flujo ***")
    else:
        print(f"\n*** Resultado: {result} ***")
    print(f"{'='*60}")

    await asyncio.sleep(3)
    browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
