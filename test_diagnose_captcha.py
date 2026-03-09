"""
Diagnóstico profundo del flujo reCAPTCHA del SRI.
Objetivo: entender EXACTAMENTE qué hace el JS del SRI y por qué rechaza tokens.

Pruebas:
1. Capturar el código fuente de executeRecaptcha y rcBuscar
2. Probar ejecución NATIVA (sin inyectar token externo)
3. Si nativa funciona → problema es token externo
4. Si nativa falla → problema es el entorno del browser
"""
import asyncio
import json

from playwright.async_api import async_playwright
from utils.manual_credentials import (
    get_manual_test_credentials,
    get_manual_test_period,
)

LOGIN_URL = (
    "https://srienlinea.sri.gob.ec/auth/realms/Internet"
    "/protocol/openid-connect/auth"
    "?client_id=app-sri-claves-angular"
    "&redirect_uri=https%3A%2F%2Fsrienlinea.sri.gob.ec"
    "%2Fsri-en-linea%2F%2Fcontribuyente%2Fperfil"
    "&response_mode=fragment&response_type=code&scope=openid"
)
PORTAL_URL = (
    "https://srienlinea.sri.gob.ec/tuportal-internet"
    "/accederAplicacion.jspa?redireccion=57&idGrupo=55"
)
COMPROBANTES_URL = (
    "https://srienlinea.sri.gob.ec/comprobantes-electronicos-internet/"
    "pages/consultas/recibidos/comprobantesRecibidos.jsf"
)


async def main():
    creds = get_manual_test_credentials()
    anio, mes = get_manual_test_period()
    pw = await async_playwright().start()

    browser = await pw.chromium.launch(
        headless=False,
        args=["--no-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled"],
    )
    ctx = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1366, "height": 768},
        locale="es-EC",
        timezone_id="America/Guayaquil",
    )
    await ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """)

    page = await ctx.new_page()

    # ── Paso 1: Login con cookies si existen ─────────────────────────────
    import os
    cookie_file = f"sessions/{creds.ruc}.json"
    if os.path.exists(cookie_file):
        with open(cookie_file) as f:
            cookies = json.load(f)
        if isinstance(cookies, dict):
            cookies = cookies.get("cookies", [])
        if isinstance(cookies, list) and cookies:
            await ctx.add_cookies(cookies)
            print(f"[+] Cargadas {len(cookies)} cookies")

    await page.goto(PORTAL_URL, timeout=30000)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(3)

    if "auth/realms" in page.url:
        print("[!] Sesión expirada, haciendo login...")
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)
        await page.fill("input#usuario", creds.usuario)
        await asyncio.sleep(0.5)
        await page.fill("input#password", creds.password)
        await asyncio.sleep(0.5)
        await page.click("input#kc-login")
        await page.wait_for_url("**/sri-en-linea/**", timeout=30000)
        print(f"[+] Login OK: {page.url[:60]}")
        # Guardar cookies
        cookies = await ctx.cookies()
        os.makedirs("sessions", exist_ok=True)
        with open(cookie_file, "w") as f:
            json.dump(cookies, f)

    # ── Paso 2: Navegar a comprobantes ──────────────────────────────────
    await page.goto(PORTAL_URL, timeout=30000)
    await asyncio.sleep(5)

    try:
        await page.wait_for_selector('[id="frmPrincipal:ano"]', timeout=20000)
    except Exception:
        print(f"[!] No se cargó el formulario. URL: {page.url[:80]}")
        await browser.close()
        await pw.stop()
        return

    print("[+] Formulario de comprobantes cargado")

    # Seleccionar periodo
    await page.select_option('[id="frmPrincipal:ano"]', label=str(anio))
    await asyncio.sleep(2)
    meses = {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
        5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
        9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
    }
    await page.select_option('[id="frmPrincipal:mes"]', label=meses[mes])
    await asyncio.sleep(2)

    # ── Paso 3: DIAGNÓSTICO PROFUNDO del JS del SRI ─────────────────────
    print("\n" + "=" * 60)
    print("DIAGNÓSTICO DEL JS DEL SRI")
    print("=" * 60)

    diag = await page.evaluate("""
    () => {
        const result = {};

        // 1. ¿Existe executeRecaptcha?
        result.executeRecaptchaExists = typeof executeRecaptcha === 'function';
        result.executeRecaptchaSource = typeof executeRecaptcha === 'function'
            ? executeRecaptcha.toString().substring(0, 500) : 'N/A';

        // 2. ¿Existe rcBuscar?
        result.rcBuscarExists = typeof rcBuscar === 'function';
        result.rcBuscarSource = typeof rcBuscar === 'function'
            ? rcBuscar.toString().substring(0, 500) : 'N/A';

        // 3. ¿Existe grecaptcha.enterprise?
        result.hasGrecaptcha = typeof grecaptcha !== 'undefined';
        result.hasEnterprise = typeof grecaptcha !== 'undefined'
            && !!grecaptcha.enterprise;
        result.enterpriseMethods = [];
        if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) {
            result.enterpriseMethods = Object.keys(grecaptcha.enterprise);
        }

        // 4. Textareas de recaptcha
        const tas = document.querySelectorAll('[name="g-recaptcha-response"]');
        result.textareaCount = tas.length;
        result.textareas = Array.from(tas).map((t, i) => ({
            index: i,
            form: (t.closest('form') || {}).id || 'none',
            id: t.id || 'none',
            valueLen: t.value.length,
            parentId: (t.parentElement || {}).id || 'none',
        }));

        // 5. Iframes de recaptcha
        const iframes = document.querySelectorAll('iframe[src*="recaptcha"]');
        result.iframeCount = iframes.length;
        result.iframes = Array.from(iframes).map((f, i) => {
            const kMatch = f.src.match(/[?&]k=([^&]+)/);
            return {
                index: i,
                sitekey: kMatch ? kMatch[1] : 'none',
                src: f.src.substring(0, 150),
            };
        });

        // 6. Scripts de recaptcha cargados
        const scripts = document.querySelectorAll('script[src*="recaptcha"]');
        result.recaptchaScripts = Array.from(scripts).map(s => s.src.substring(0, 150));

        // 7. ¿Hay un widget ID de recaptcha?
        result.grecaptchaGetResponse = 'N/A';
        if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) {
            try {
                result.grecaptchaGetResponse = grecaptcha.enterprise.getResponse
                    ? grecaptcha.enterprise.getResponse().substring(0, 50) : 'no getResponse';
            } catch(e) {
                result.grecaptchaGetResponse = 'error: ' + e.message;
            }
        }

        // 8. Variables globales relevantes
        result.hasOnloadCallback = typeof onloadCallback === 'function';
        result.hasVerifyCallback = typeof verifyCallback === 'function';

        // 9. Hidden inputs con tokens
        const hiddens = document.querySelectorAll('input[type="hidden"]');
        result.hiddenInputs = Array.from(hiddens)
            .filter(h => h.name && (
                h.name.includes('captcha') || h.name.includes('token')
                || h.name.includes('recaptcha')
            ))
            .map(h => ({ name: h.name, valueLen: h.value.length }));

        return result;
    }
    """)

    for key, val in diag.items():
        if isinstance(val, str) and len(val) > 200:
            print(f"\n  {key}:\n    {val}")
        else:
            print(f"  {key}: {json.dumps(val, indent=4) if isinstance(val, (dict, list)) else val}")

    # ── Paso 4: PRUEBA NATIVA ───────────────────────────────────────────
    print("\n" + "=" * 60)
    print("PRUEBA 1: reCAPTCHA NATIVO (sin token externo)")
    print("=" * 60)

    # Capturar requests de red para ver qué envía el form
    captcha_requests = []

    async def on_request(request):
        url = request.url
        if "comprobantesRecibidos" in url or "recaptcha" in url:
            captcha_requests.append({
                "url": url[:120],
                "method": request.method,
                "post_data": (request.post_data or "")[:500],
            })

    page.on("request", on_request)

    native_result = await page.evaluate("""
    () => {
        return new Promise((resolve) => {
            const origRcBuscar = window.rcBuscar;
            const timeout = setTimeout(() => {
                window.rcBuscar = origRcBuscar;
                const ta = document.querySelector('[name="g-recaptcha-response"]');
                resolve({
                    error: 'timeout_45s',
                    tokenLen: ta ? ta.value.length : -1,
                });
            }, 45000);

            // Interceptar rcBuscar para capturar el momento de submit
            window.rcBuscar = function() {
                const tas = document.querySelectorAll('[name="g-recaptcha-response"]');
                const taState = Array.from(tas).map((t, i) => ({
                    i: i, len: t.value.length,
                    form: (t.closest('form') || {}).id || 'none',
                    snippet: t.value.substring(0, 30),
                }));
                console.log('[DIAG] rcBuscar called, textareas:', JSON.stringify(taState));

                origRcBuscar.apply(this, arguments);
                window.rcBuscar = origRcBuscar;

                setTimeout(() => {
                    clearTimeout(timeout);
                    const msgs = document.getElementById('formMessages:messages');
                    const panel = document.getElementById('frmPrincipal:panelListaComprobantes');
                    resolve({
                        messages: msgs ? msgs.innerText.trim() : '',
                        panelLen: panel ? panel.innerHTML.length : 0,
                        textareasAtSubmit: taState,
                        native: true,
                    });
                }, 12000);
            };

            // Ejecutar el reCAPTCHA nativo del SRI
            if (typeof executeRecaptcha === 'function') {
                console.log('[DIAG] Calling native executeRecaptcha...');
                executeRecaptcha('consulta_cel_recibidos');
            } else {
                clearTimeout(timeout);
                window.rcBuscar = origRcBuscar;
                resolve({ error: 'no_executeRecaptcha' });
            }
        });
    }
    """)

    print(f"\n  Resultado nativo: {json.dumps(native_result, indent=2)}")
    print(f"\n  Requests capturados: {len(captcha_requests)}")
    for r in captcha_requests[-5:]:
        print(f"    {r['method']} {r['url']}")
        if r['post_data']:
            # Buscar el token en el post data
            pd = r['post_data']
            if 'g-recaptcha-response' in pd:
                idx = pd.index('g-recaptcha-response')
                print(f"    [TOKEN FOUND in POST at pos {idx}]")
                # Extract token value
                snippet = pd[idx:idx+100]
                print(f"    snippet: {snippet}")

    if native_result.get("panelLen", 0) > 50:
        print("\n*** NATIVO FUNCIONA! El problema son los tokens externos ***")
    elif "captcha" in native_result.get("messages", "").lower():
        print("\n*** NATIVO TAMBIÉN RECHAZADO - problema es el entorno del browser ***")
    else:
        print(f"\n*** Resultado inesperado ***")

    # ── Paso 5: Screenshot final ────────────────────────────────────────
    await page.screenshot(path="screenshots/diagnose_final.png")
    print("\n[+] Screenshot guardado en screenshots/diagnose_final.png")

    page.remove_listener("request", on_request)
    await browser.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
