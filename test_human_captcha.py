"""
Test con simulación de comportamiento humano completo antes del reCAPTCHA.
Objetivo: subir el score de reCAPTCHA Enterprise generando señales humanas reales.
"""
import asyncio
import json
import random
from playwright.async_api import async_playwright

PORTAL_URL = (
    "https://srienlinea.sri.gob.ec/tuportal-internet"
    "/accederAplicacion.jspa?redireccion=57&idGrupo=55"
)


async def simular_humano(page):
    """Genera señales humanas: mouse, scroll, clicks, timing."""
    vw = 1366
    vh = 768

    # 1. Movimientos de mouse naturales (curva bezier-like)
    print("  [*] Simulando movimientos de mouse...")
    for _ in range(random.randint(5, 10)):
        x = random.randint(100, vw - 100)
        y = random.randint(100, vh - 100)
        # Mover en pasos para simular curva
        steps = random.randint(5, 15)
        await page.mouse.move(x, y, steps=steps)
        await asyncio.sleep(random.uniform(0.1, 0.4))

    # 2. Scrolls suaves
    print("  [*] Simulando scrolls...")
    for _ in range(random.randint(2, 4)):
        delta = random.choice([100, 200, -100, 150, -50])
        await page.mouse.wheel(0, delta)
        await asyncio.sleep(random.uniform(0.3, 0.8))

    # 3. Click en área neutral (no en botones)
    print("  [*] Clicks en áreas neutrales...")
    for _ in range(random.randint(1, 3)):
        x = random.randint(200, 800)
        y = random.randint(100, 400)
        await page.mouse.click(x, y)
        await asyncio.sleep(random.uniform(0.2, 0.5))

    # 4. Focus/blur en campos del formulario
    print("  [*] Interactuando con campos del formulario...")
    for sel in ['[id="frmPrincipal:ano"]', '[id="frmPrincipal:mes"]']:
        try:
            el = await page.query_selector(sel)
            if el:
                await el.click()
                await asyncio.sleep(random.uniform(0.3, 0.7))
                # Move mouse away
                await page.mouse.move(
                    random.randint(400, 800),
                    random.randint(200, 500),
                    steps=8
                )
                await asyncio.sleep(random.uniform(0.2, 0.5))
        except Exception:
            pass

    # 5. Esperar un tiempo "humano" pensando
    think_time = random.uniform(2, 4)
    print(f"  [*] Esperando {think_time:.1f}s (simulando lectura)...")
    await asyncio.sleep(think_time)


async def main():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=False,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--lang=es-EC",
        ],
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
        permissions=["geolocation"],
        geolocation={"latitude": -2.1894, "longitude": -79.8891},
    )

    # Stealth más completo
    await ctx.add_init_script("""
        // Hide webdriver
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

        // Chrome object
        window.chrome = { runtime: {}, app: { isInstalled: false } };

        // Plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                { name: 'Native Client', filename: 'internal-nacl-plugin' },
            ]
        });

        // Languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['es-EC', 'es', 'en-US', 'en']
        });

        // Hardware concurrency
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 4 });

        // Device memory
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

        // Max touch points (desktop = 0)
        Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });

        // WebGL vendor
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(param) {
            if (param === 37445) return 'Intel Inc.';
            if (param === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.call(this, param);
        };

        // Permissions
        const originalQuery = window.Notification && Notification.requestPermission;

        // Canvas fingerprint noise
        const toDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {
            if (type === 'image/png') {
                const ctx = this.getContext('2d');
                if (ctx) {
                    const imageData = ctx.getImageData(0, 0, this.width, this.height);
                    for (let i = 0; i < imageData.data.length; i += 4) {
                        imageData.data[i] += (Math.random() * 0.5) | 0;
                    }
                    ctx.putImageData(imageData, 0, 0);
                }
            }
            return toDataURL.apply(this, arguments);
        };
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
            print(f"[+] Cargadas {len(cookies)} cookies")

    page = await ctx.new_page()
    page.set_default_timeout(30000)

    # Navegar con delays naturales
    print("[+] Navegando al portal...")
    await page.goto(PORTAL_URL, timeout=30000)
    await asyncio.sleep(3)

    # Verificar si estamos en el formulario
    try:
        await page.wait_for_selector('[id="frmPrincipal:ano"]', timeout=20000)
    except Exception:
        print(f"[!] No form. URL: {page.url[:80]}")
        await browser.close(); await pw.stop(); return

    print("[+] Formulario cargado. Simulando interacción humana...")

    # FASE 1: Simular comportamiento humano ANTES de seleccionar filtros
    await simular_humano(page)

    # FASE 2: Seleccionar filtros de forma "humana"
    print("[+] Seleccionando período...")
    ano_el = await page.query_selector('[id="frmPrincipal:ano"]')
    if ano_el:
        await page.mouse.move(683, 300, steps=10)
        await asyncio.sleep(0.3)
        await ano_el.click()
        await asyncio.sleep(0.5)
        await ano_el.select_option(label="2025")
        await asyncio.sleep(random.uniform(1.5, 2.5))

    mes_el = await page.query_selector('[id="frmPrincipal:mes"]')
    if mes_el:
        await page.mouse.move(683, 350, steps=8)
        await asyncio.sleep(0.3)
        await mes_el.click()
        await asyncio.sleep(0.5)
        await mes_el.select_option(label="Enero")
        await asyncio.sleep(random.uniform(1.5, 2.5))

    # FASE 3: Más movimientos de mouse y espera antes del captcha
    print("[+] Más actividad humana antes del captcha...")
    await simular_humano(page)

    # FASE 4: Ejecutar reCAPTCHA nativo
    print("\n[+] Ejecutando reCAPTCHA nativo...")
    result = await page.evaluate("""
    () => {
        return new Promise((resolve) => {
            const origRcBuscar = window.rcBuscar;
            const timeout = setTimeout(() => {
                window.rcBuscar = origRcBuscar;
                resolve({ error: 'timeout_45s' });
            }, 45000);

            window.rcBuscar = function() {
                const ta = document.querySelector('#g-recaptcha-response');
                const tokenLen = ta ? ta.value.length : -1;
                origRcBuscar.apply(this, arguments);
                window.rcBuscar = origRcBuscar;
                setTimeout(() => {
                    clearTimeout(timeout);
                    const msgs = document.getElementById('formMessages:messages');
                    const panel = document.getElementById('frmPrincipal:panelListaComprobantes');
                    resolve({
                        messages: msgs ? msgs.innerText.trim() : '',
                        panelLen: panel ? panel.innerHTML.length : 0,
                        tokenLen: tokenLen,
                    });
                }, 12000);
            };

            executeRecaptcha('consulta_cel_recibidos');
        });
    }
    """)

    print(f"\n{'='*60}")
    print(f"RESULTADO: {json.dumps(result, indent=2)}")
    if result.get("panelLen", 0) > 50:
        print("*** CAPTCHA ACEPTADO! ***")
    elif "captcha" in result.get("messages", "").lower():
        print("*** CAPTCHA RECHAZADO ***")
    print(f"{'='*60}")

    # Si falló, intentar una vez más con más tiempo de "incubación"
    if result.get("panelLen", 0) < 50 and "captcha" in result.get("messages", "").lower():
        print("\n[+] Segundo intento - más incubación...")
        await asyncio.sleep(3)
        # Hacer más scroll y mouse
        for _ in range(8):
            await page.mouse.move(
                random.randint(100, 1200), random.randint(100, 600), steps=12
            )
            await asyncio.sleep(random.uniform(0.2, 0.6))
        await asyncio.sleep(random.uniform(3, 5))

        result2 = await page.evaluate("""
        () => {
            return new Promise((resolve) => {
                const origRcBuscar = window.rcBuscar;
                const timeout = setTimeout(() => {
                    window.rcBuscar = origRcBuscar;
                    resolve({ error: 'timeout_45s' });
                }, 45000);
                window.rcBuscar = function() {
                    origRcBuscar.apply(this, arguments);
                    window.rcBuscar = origRcBuscar;
                    setTimeout(() => {
                        clearTimeout(timeout);
                        const msgs = document.getElementById('formMessages:messages');
                        const panel = document.getElementById('frmPrincipal:panelListaComprobantes');
                        resolve({
                            messages: msgs ? msgs.innerText.trim() : '',
                            panelLen: panel ? panel.innerHTML.length : 0,
                        });
                    }, 12000);
                };
                executeRecaptcha('consulta_cel_recibidos');
            });
        }
        """)
        print(f"\nRESULTADO intento 2: {json.dumps(result2, indent=2)}")
        if result2.get("panelLen", 0) > 50:
            print("*** INTENTO 2: CAPTCHA ACEPTADO! ***")

    await page.screenshot(path="screenshots/human_captcha_final.png")

    # Guardar cookies actualizadas
    cookies = await ctx.cookies()
    import os
    os.makedirs("sessions", exist_ok=True)
    with open(cookie_file, "w") as f:
        json.dump({"ruc": "1207481803001", "cookies": cookies}, f)

    await browser.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
