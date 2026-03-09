"""
Diagnóstico fase 2: Entender el flujo completo del token.
Interceptar grecaptcha.enterprise.execute para ver qué devuelve y cómo llega a rcBuscar.
También ver si el widget ID correcto es el que se usa.
"""
import asyncio
import json
from playwright.async_api import async_playwright

PORTAL_URL = (
    "https://srienlinea.sri.gob.ec/tuportal-internet"
    "/accederAplicacion.jspa?redireccion=57&idGrupo=55"
)

async def main():
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

    # Load cookies
    import os
    cookie_file = "sessions/1207481803001.json"
    if os.path.exists(cookie_file):
        with open(cookie_file) as f:
            data = json.load(f)
        cookies = data.get("cookies", []) if isinstance(data, dict) else data
        if cookies:
            await ctx.add_cookies(cookies)

    page = await ctx.new_page()
    page.set_default_timeout(30000)

    await page.goto(PORTAL_URL, timeout=30000)
    await asyncio.sleep(5)

    try:
        await page.wait_for_selector('[id="frmPrincipal:ano"]', timeout=20000)
    except Exception:
        print(f"[!] No form. URL: {page.url[:80]}")
        await browser.close(); await pw.stop(); return

    await page.select_option('[id="frmPrincipal:ano"]', label="2025")
    await asyncio.sleep(2)
    await page.select_option('[id="frmPrincipal:mes"]', label="Enero")
    await asyncio.sleep(2)

    print("[+] Formulario listo. Interceptando flujo...")

    # Interceptar COMPLETAMENTE el flujo para ver paso a paso
    result = await page.evaluate("""
    () => {
        return new Promise((resolve) => {
            const log = [];
            const origExecute = grecaptcha.enterprise.execute;
            const origRcBuscar = window.rcBuscar;
            const origPFab = PrimeFaces.ab;

            // 1. Interceptar grecaptcha.enterprise.execute
            grecaptcha.enterprise.execute = function() {
                log.push({
                    step: 'execute_called',
                    args: JSON.stringify(Array.from(arguments)).substring(0, 200),
                    argCount: arguments.length,
                });
                const result = origExecute.apply(this, arguments);
                // execute returns a Promise with the token
                if (result && result.then) {
                    result.then(token => {
                        log.push({
                            step: 'execute_resolved',
                            tokenLen: token ? token.length : 0,
                            tokenSnippet: token ? token.substring(0, 40) : 'null',
                        });
                    }).catch(err => {
                        log.push({ step: 'execute_rejected', error: err.message });
                    });
                } else {
                    log.push({ step: 'execute_returned_non_promise', type: typeof result });
                }
                return result;
            };

            // 2. Interceptar rcBuscar para ver qué params recibe
            window.rcBuscar = function() {
                log.push({
                    step: 'rcBuscar_called',
                    argCount: arguments.length,
                    args: JSON.stringify(Array.from(arguments)).substring(0, 300),
                });
                // Check textareas state
                const tas = document.querySelectorAll('[name="g-recaptcha-response"]');
                tas.forEach((t, i) => {
                    log.push({
                        step: 'textarea_at_rcBuscar',
                        index: i,
                        id: t.id,
                        form: (t.closest('form') || {}).id || 'none',
                        valueLen: t.value.length,
                        snippet: t.value.substring(0, 40),
                    });
                });
                origRcBuscar.apply(this, arguments);
                window.rcBuscar = origRcBuscar;
            };

            // 3. Interceptar PrimeFaces.ab para ver los params enviados
            PrimeFaces.ab = function(cfg) {
                log.push({
                    step: 'PrimeFaces_ab_called',
                    source: cfg.source,
                    formId: cfg.formId,
                    hasParams: !!cfg.params,
                    params: cfg.params ? JSON.stringify(cfg.params).substring(0, 300) : 'none',
                });
                origPFab.apply(this, arguments);
                PrimeFaces.ab = origPFab;
            };

            // Timeout
            const timeout = setTimeout(() => {
                grecaptcha.enterprise.execute = origExecute;
                window.rcBuscar = origRcBuscar;
                PrimeFaces.ab = origPFab;
                resolve({ error: 'timeout', log: log });
            }, 45000);

            // 4. Escuchar el evento AJAX de PrimeFaces
            if (typeof $ !== 'undefined') {
                $(document).on('pfAjaxComplete', function(e, xhr, settings) {
                    clearTimeout(timeout);
                    grecaptcha.enterprise.execute = origExecute;
                    window.rcBuscar = origRcBuscar;
                    PrimeFaces.ab = origPFab;
                    const msgs = document.getElementById('formMessages:messages');
                    const panel = document.getElementById('frmPrincipal:panelListaComprobantes');
                    log.push({
                        step: 'ajax_complete',
                        messages: msgs ? msgs.innerText.trim() : '',
                        panelLen: panel ? panel.innerHTML.length : 0,
                    });
                    resolve({ log: log });
                });
            }

            // Also resolve after rcBuscar + delay
            const origRcBuscar2 = window.rcBuscar;
            window.rcBuscar = function() {
                origRcBuscar2.apply(this, arguments);
                setTimeout(() => {
                    clearTimeout(timeout);
                    const msgs = document.getElementById('formMessages:messages');
                    const panel = document.getElementById('frmPrincipal:panelListaComprobantes');
                    log.push({
                        step: 'after_rcBuscar_delay',
                        messages: msgs ? msgs.innerText.trim() : '',
                        panelLen: panel ? panel.innerHTML.length : 0,
                    });
                    resolve({ log: log });
                }, 12000);
            };

            // Trigger!
            console.log('[DIAG2] Triggering executeRecaptcha...');
            executeRecaptcha('consulta_cel_recibidos');
        });
    }
    """)

    print("\n" + "=" * 60)
    print("FLUJO COMPLETO INTERCEPTADO")
    print("=" * 60)
    for entry in result.get("log", []):
        step = entry.pop("step", "?")
        print(f"\n  [{step}]")
        for k, v in entry.items():
            print(f"    {k}: {v}")

    await page.screenshot(path="screenshots/diagnose2_final.png")
    await browser.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
