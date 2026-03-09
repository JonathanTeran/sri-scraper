"""
Interceptar el POST real que PrimeFaces envía al SRI para ver
exactamente qué datos incluye, especialmente el token de reCAPTCHA.
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
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.6261.94 Safari/537.36"
        ),
        viewport={"width": 1366, "height": 768},
        locale="es-EC",
        timezone_id="America/Guayaquil",
    )
    await ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """)

    import os
    cookie_file = "sessions/1207481803001.json"
    if os.path.exists(cookie_file):
        with open(cookie_file) as f:
            data = json.load(f)
        cookies = data.get("cookies", []) if isinstance(data, dict) else data
        if cookies:
            await ctx.add_cookies(cookies)

    page = await ctx.new_page()

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

    print("[+] Interceptando POST de PrimeFaces...")

    # Interceptar el request AJAX de PrimeFaces
    captured_post = {}

    async def intercept_request(route):
        request = route.request
        url = request.url
        if request.method == "POST" and "comprobantesRecibidos" in url:
            post_body = request.post_data or ""
            captured_post["url"] = url
            captured_post["method"] = request.method
            captured_post["body"] = post_body

            # Parse form data
            params = {}
            if post_body:
                for pair in post_body.split("&"):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        from urllib.parse import unquote
                        k = unquote(k)
                        v = unquote(v)
                        params[k] = v[:100] + ("..." if len(v) > 100 else "")

            captured_post["params"] = params
            print(f"\n  [INTERCEPTED POST] {url[:100]}")
            print(f"  Content-Type: {request.headers.get('content-type', 'N/A')}")

            # Look for recaptcha token in params
            for k, v in params.items():
                if "recaptcha" in k.lower() or "captcha" in k.lower():
                    print(f"  *** CAPTCHA PARAM: {k} = {v}")
                elif "javax.faces" in k.lower():
                    print(f"  JSF: {k} = {v[:50]}")
                elif "frmPrincipal" in k:
                    print(f"  FORM: {k} = {v}")

        await route.continue_()

    await page.route("**/*", intercept_request)

    # Ejecutar reCAPTCHA nativo y dejar que PrimeFaces envíe el POST
    result = await page.evaluate("""
    () => {
        return new Promise((resolve) => {
            const origRcBuscar = window.rcBuscar;
            const timeout = setTimeout(() => {
                window.rcBuscar = origRcBuscar;
                resolve({ error: 'timeout' });
            }, 45000);

            window.rcBuscar = function() {
                // Capture form data BEFORE submit
                const form = document.getElementById('frmPrincipal');
                const formData = form ? new FormData(form) : null;
                const formEntries = {};
                if (formData) {
                    for (const [key, value] of formData.entries()) {
                        if (key.includes('recaptcha') || key.includes('ViewState')) {
                            formEntries[key] = typeof value === 'string'
                                ? value.substring(0, 60) + '...' : 'non-string';
                        }
                    }
                }
                console.log('[DIAG] Form entries with captcha:', JSON.stringify(formEntries));

                origRcBuscar.apply(this, arguments);
                window.rcBuscar = origRcBuscar;

                setTimeout(() => {
                    clearTimeout(timeout);
                    const msgs = document.getElementById('formMessages:messages');
                    const panel = document.getElementById('frmPrincipal:panelListaComprobantes');
                    resolve({
                        messages: msgs ? msgs.innerText.trim() : '',
                        panelLen: panel ? panel.innerHTML.length : 0,
                        formEntries: formEntries,
                    });
                }, 12000);
            };

            executeRecaptcha('consulta_cel_recibidos');
        });
    }
    """)

    print(f"\n{'='*60}")
    print("RESULTADO:")
    print(json.dumps(result, indent=2))

    if captured_post.get("params"):
        print(f"\nPOST params capturados:")
        for k, v in captured_post["params"].items():
            print(f"  {k}: {v}")

    await page.unroute("**/*")
    await page.screenshot(path="screenshots/intercept_post.png")
    await browser.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
