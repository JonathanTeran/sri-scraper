"""
Resolución de CAPTCHAs usando 2captcha como proveedor principal.

Soporta:
- reCAPTCHA v2
- reCAPTCHA v3 Enterprise (score alto)
- hCaptcha
- CAPTCHA de imagen
"""

import asyncio
import structlog

try:
    from twocaptcha import TwoCaptcha
except Exception:  # pragma: no cover - optional in test/minimal envs
    TwoCaptcha = None

from playwright.async_api import Page

log = structlog.get_logger()


class CaptchaResolver:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._solver = TwoCaptcha(api_key) if api_key and TwoCaptcha else None
        self._max_intentos = 3
        self.last_token: str | None = None
        self._last_task_id: str | None = None

    async def resolver_token_recaptcha(
        self,
        site_key: str,
        page_url: str,
        enterprise: bool = False,
        action: str | None = None,
        score: float | None = None,
        invisible: bool = False,
    ) -> str | None:
        """Resuelve reCAPTCHA y devuelve solo el token (sin inyectar)."""
        if not self._solver:
            return None
        try:
            kwargs = dict(sitekey=site_key, url=page_url)
            if invisible:
                kwargs["invisible"] = 1
            if enterprise:
                kwargs["enterprise"] = 1
                if score is not None and action:
                    kwargs["version"] = "v3"
                    kwargs["action"] = action
                    kwargs["score"] = score
            result = await asyncio.to_thread(self._solver.recaptcha, **kwargs)
            token = result["code"]
            self._last_task_id = result.get("captchaId")
            self.last_token = token
            log.info("2captcha_token_obtenido", token_len=len(token))
            return token
        except Exception as e:
            log.error("2captcha_token_error", error=str(e))
            return None

    async def reportar_token_malo(self) -> None:
        """Report bad token to 2captcha for refund."""
        if self._solver and self._last_task_id:
            try:
                await asyncio.to_thread(
                    self._solver.report,
                    self._last_task_id,
                    False,
                )
                log.info(
                    "captcha_token_reportado",
                    task_id=self._last_task_id,
                )
            except Exception as e:
                log.warning(
                    "captcha_report_error", error=str(e)
                )

    async def resolver(
        self,
        page: Page,
        enterprise: bool = False,
        site_key_override: str | None = None,
        action: str | None = None,
        score: float | None = None,
    ) -> bool:
        """
        Detecta tipo de CAPTCHA y lo resuelve.
        Retorna True si resolvió exitosamente.
        """
        for intento in range(1, self._max_intentos + 1):
            log.info(
                "captcha_intento",
                intento=intento,
                max=self._max_intentos,
                enterprise=enterprise,
                action=action,
            )

            # Detectar reCAPTCHA (v2 o Enterprise)
            recaptcha = await page.query_selector(
                "iframe[src*='recaptcha'], div.g-recaptcha"
            )
            if recaptcha or site_key_override:
                site_key = site_key_override or (
                    await self._extraer_site_key_recaptcha(page)
                )
                if site_key:
                    ok = await self._resolver_recaptcha_v2(
                        page, site_key, enterprise=enterprise,
                        action=action, score=score,
                    )
                    if ok:
                        return True

            # Detectar hCaptcha
            hcaptcha = await page.query_selector(
                "iframe[src*='hcaptcha']"
            )
            if hcaptcha:
                site_key = await self._extraer_site_key_hcaptcha(page)
                if site_key:
                    ok = await self._resolver_hcaptcha(page, site_key)
                    if ok:
                        return True

            # Detectar CAPTCHA de imagen
            img_captcha = await page.query_selector(
                "img[src*='captcha'], img[alt*='captcha' i]"
            )
            if img_captcha:
                ok = await self._resolver_imagen(page, img_captcha)
                if ok:
                    return True

            if intento < self._max_intentos:
                log.warning("captcha_reintento", espera_seg=300)
                await asyncio.sleep(300)

        log.error("captcha_fallo_total", intentos=self._max_intentos)
        return False

    async def _extraer_site_key_recaptcha(self, page: Page) -> str | None:
        """Extrae el siteKey de reCAPTCHA v2."""
        site_key = await page.evaluate("""
            () => {
                const el = document.querySelector('.g-recaptcha');
                if (el) return el.getAttribute('data-sitekey');
                const iframe = document.querySelector('iframe[src*="recaptcha"]');
                if (iframe) {
                    const match = iframe.src.match(/[?&]k=([^&]+)/);
                    return match ? match[1] : null;
                }
                return null;
            }
        """)
        return site_key

    async def _extraer_site_key_hcaptcha(self, page: Page) -> str | None:
        """Extrae el siteKey de hCaptcha."""
        site_key = await page.evaluate("""
            () => {
                const el = document.querySelector('.h-captcha');
                if (el) return el.getAttribute('data-sitekey');
                const iframe = document.querySelector('iframe[src*="hcaptcha"]');
                if (iframe) {
                    const match = iframe.src.match(/sitekey=([^&]+)/);
                    return match ? match[1] : null;
                }
                return null;
            }
        """)
        return site_key

    async def _resolver_recaptcha_v2(
        self, page: Page, site_key: str,
        enterprise: bool = False, action: str | None = None,
        score: float | None = None,
    ) -> bool:
        """Resuelve reCAPTCHA v2 o Enterprise v3 via 2captcha API."""
        if not self._solver:
            log.error("captcha_sin_api_key")
            return False

        try:
            log.info(
                "captcha_recaptcha",
                site_key=site_key[:10],
                enterprise=enterprise,
                action=action,
                score=score,
            )
            kwargs = dict(sitekey=site_key, url=page.url)
            if enterprise:
                kwargs["enterprise"] = 1
                kwargs["invisible"] = 1
                if score is not None and action:
                    kwargs["version"] = "v3"
                    kwargs["action"] = action
                    kwargs["score"] = score

            result = await asyncio.to_thread(
                self._solver.recaptcha, **kwargs
            )
            token = result["code"]
            self._last_task_id = result.get("captchaId")

            await page.evaluate(f"""
                () => {{
                    const token = '{token}';

                    document.querySelectorAll(
                        '[name="g-recaptcha-response"]'
                    ).forEach(el => {{
                        el.value = token;
                    }});

                    if (typeof grecaptcha !== 'undefined'
                        && grecaptcha.enterprise) {{
                        grecaptcha.enterprise.execute =
                            function() {{
                            return Promise.resolve(token);
                        }};
                        grecaptcha.enterprise.reset = function() {{}};
                    }}

                    window.executeRecaptcha = function(accion) {{}};

                    console.log('reCAPTCHA token set, overrides installed');
                }}
            """)

            self.last_token = token
            log.info(
                "captcha_recaptcha_resuelto",
                token_len=len(token),
                action=action,
            )
            return True

        except Exception as e:
            log.error("captcha_recaptcha_error", error=str(e))
            return False

    async def _resolver_hcaptcha(
        self, page: Page, site_key: str
    ) -> bool:
        """Resuelve hCaptcha via 2captcha API."""
        if not self._solver:
            return False

        try:
            log.info("captcha_hcaptcha", site_key=site_key[:10])
            result = await asyncio.to_thread(
                self._solver.hcaptcha,
                sitekey=site_key,
                url=page.url,
            )
            token = result["code"]

            await page.evaluate(f"""
                () => {{
                    const textarea = document.querySelector(
                        'textarea[name="h-captcha-response"]'
                    );
                    if (textarea) textarea.value = '{token}';
                }}
            """)

            log.info("captcha_hcaptcha_resuelto")
            return True

        except Exception as e:
            log.error("captcha_hcaptcha_error", error=str(e))
            return False

    async def _resolver_imagen(
        self, page: Page, img_element
    ) -> bool:
        """Resuelve CAPTCHA de imagen via 2captcha."""
        if not self._solver:
            return False

        try:
            screenshot = await img_element.screenshot()
            import tempfile
            import os

            with tempfile.NamedTemporaryFile(
                suffix=".png", delete=False
            ) as f:
                f.write(screenshot)
                tmp_path = f.name

            try:
                result = await asyncio.to_thread(
                    self._solver.normal, tmp_path
                )
                solution = result["code"]
            finally:
                os.unlink(tmp_path)

            captcha_input = await page.query_selector(
                "input[name*='captcha' i], "
                "input[id*='captcha' i], "
                "input[placeholder*='captcha' i]"
            )
            if captcha_input:
                await captcha_input.fill(solution)
                log.info("captcha_imagen_resuelto")
                return True

            log.warning("captcha_imagen_sin_input")
            return False

        except Exception as e:
            log.error("captcha_imagen_error", error=str(e))
            return False
