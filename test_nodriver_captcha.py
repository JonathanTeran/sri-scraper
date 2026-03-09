"""
Test: Can nodriver pass SRI's reCAPTCHA Enterprise?
Strategy: Login → profile page → navigate to comprobantes again → test captcha
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
    print("Starting nodriver...")
    browser = await uc.start(headless=False)
    page = await browser.get(PORTAL_URL)
    await asyncio.sleep(8)

    print(f"URL: {page.url}")

    if "auth/realms" in page.url:
        print("Logging in via Keycloak...")

        try:
            el = await page.select("input#usuario")
            await el.click()
            await asyncio.sleep(0.3)
            await el.send_keys(creds.usuario)
        except Exception as e:
            print(f"  Username error: {e}")

        await asyncio.sleep(0.5)

        try:
            el = await page.select("input#password")
            await el.click()
            await asyncio.sleep(0.3)
            await el.send_keys(creds.password)
        except Exception as e:
            print(f"  Password error: {e}")

        await asyncio.sleep(1)

        try:
            btn = await page.select("input#kc-login")
            await btn.click()
            print("  Login submitted...")
        except Exception as e:
            print(f"  Submit error: {e}")

        # Wait for login redirect + profile load
        await asyncio.sleep(3)

        # Immediately go to comprobantes (interrupting profile SSO chain)
        print("  Going to comprobantes...")
        page = await browser.get(PORTAL_URL)
        await asyncio.sleep(15)
        print(f"  URL: {page.url}")

    # Check where we are
    try:
        body = await page.evaluate("document.body.innerText")
    except Exception:
        page = browser.main_tab
        await asyncio.sleep(2)
        body = await page.evaluate("document.body.innerText")

    url = page.url
    print(f"Current: {url[:80]}")

    # If on profile, navigate to comprobantes again
    if "perfil" in url or "sri-en-linea" in url:
        print("On profile page, navigating to comprobantes...")
        page = await browser.get(PORTAL_URL)
        await asyncio.sleep(15)
        url = page.url
        print(f"After 2nd nav: {url[:80]}")

        try:
            body = await page.evaluate("document.body.innerText")
        except Exception:
            page = browser.main_tab
            await asyncio.sleep(2)
            body = await page.evaluate("document.body.innerText")

    # If on auth page (SSO should auto-login now)
    if "auth/realms" in url:
        print("On auth page, waiting for SSO auto-login...")
        # Check if login form is shown or SSO is processing
        if "Clave" in body:
            print("Login form shown — SSO failed. Logging in again...")
            try:
                el = await page.select("input#usuario")
                await el.click()
                await asyncio.sleep(0.3)
                await el.send_keys(creds.usuario)
            except Exception:
                pass
            await asyncio.sleep(0.3)
            try:
                el = await page.select("input#password")
                await el.click()
                await asyncio.sleep(0.3)
                await el.send_keys(creds.password)
            except Exception:
                pass
            await asyncio.sleep(0.5)
            try:
                btn = await page.select("input#kc-login")
                await btn.click()
            except Exception:
                pass
            await asyncio.sleep(20)
            try:
                url = page.url
                body = await page.evaluate("document.body.innerText")
            except Exception:
                page = browser.main_tab
                await asyncio.sleep(2)
                url = page.url
                body = await page.evaluate("document.body.innerText")

            print(f"After login: {url[:80]}")

            # If on profile again, try navigating one more time
            if "perfil" in url or "sri-en-linea" in url:
                print("On profile again, 3rd attempt to comprobantes...")
                page = await browser.get(PORTAL_URL)
                await asyncio.sleep(20)
                url = page.url
                try:
                    body = await page.evaluate("document.body.innerText")
                except Exception:
                    page = browser.main_tab
                    await asyncio.sleep(2)
                    body = await page.evaluate("document.body.innerText")
                    url = page.url
                print(f"3rd attempt URL: {url[:80]}")

    print(f"\n=== Final State ===")
    print(f"URL: {url}")
    print(f"Body: {body[:200]}")

    if "Comprobantes" not in body:
        # Check via form elements
        try:
            has_form = await page.evaluate(
                "!!document.getElementById('frmPrincipal:anio')")
        except Exception:
            has_form = False

        if not has_form:
            print("\nFAILED: Not on comprobantes page")
            browser.stop()
            return

    print("\n*** On comprobantes page! ***")

    # Select period
    await page.evaluate("""
    (year) => {
        const a = document.getElementById('frmPrincipal:anio');
        if (a) {
            for (let o of a.options)
                if (o.text===String(year)) { a.value=o.value; break; }
            a.dispatchEvent(new Event('change', {bubbles:true}));
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
                if (o.text===monthLabel) { m.value=o.value; break; }
            m.dispatchEvent(new Event('change', {bubbles:true}));
        }
    }
    """, month_label)
    await asyncio.sleep(2)

    diag = await page.evaluate("""
    (() => ({
        hasEnterprise: typeof grecaptcha!=='undefined'
            && !!grecaptcha.enterprise,
        executeExists: typeof executeRecaptcha==='function',
        rcBuscarExists: typeof rcBuscar==='function',
    }))()
    """)
    print(f"reCAPTCHA: {diag}")

    print("Executing reCAPTCHA...")
    result = await page.evaluate("""
    (() => new Promise((resolve) => {
        const t = setTimeout(() => {
            const ta = document.querySelector(
                '[name="g-recaptcha-response"]');
            resolve({error:'timeout', tokenLen: ta?ta.value.length:-1});
        }, 30000);
        const orig = window.rcBuscar;
        window.rcBuscar = function() {
            const ta = document.querySelector(
                '[name="g-recaptcha-response"]');
            orig.apply(this, arguments);
            window.rcBuscar = orig;
            setTimeout(() => {
                clearTimeout(t);
                const m = document.getElementById(
                    'formMessages:messages');
                const p = document.getElementById(
                    'frmPrincipal:panelListaComprobantes');
                resolve({
                    messages: m?m.innerText.trim():'',
                    panelLen: p?p.innerHTML.length:0,
                    tokenLen: ta?ta.value.length:-1,
                });
            }, 10000);
        };
        if (typeof executeRecaptcha==='function') {
            executeRecaptcha('consulta_cel_recibidos');
        } else {
            clearTimeout(t);
            window.rcBuscar = orig;
            resolve({error:'no_executeRecaptcha'});
        }
    }))()
    """)

    print(f"\n{'='*50}")
    print(f"RESULT: {json.dumps(result, indent=2)}")
    if result.get("panelLen", 0) > 50:
        print("*** CAPTCHA PASSED! ***")
    elif "captcha" in result.get("messages", "").lower():
        print("*** CAPTCHA REJECTED ***")
    elif result.get("error"):
        print(f"*** ERROR: {result['error']} ***")
    print(f"{'='*50}")

    await asyncio.sleep(3)
    browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
