"""
Resolución de CAPTCHAs usando CapSolver como proveedor.

Soporta:
- reCAPTCHA v3 Enterprise ProxyLess (score alto)
- reCAPTCHA v2 Enterprise ProxyLess
- reCAPTCHA v2 ProxyLess
- hCaptcha ProxyLess
- CAPTCHA de imagen
"""

import asyncio
import base64
import aiohttp
import structlog

from playwright.async_api import Page

log = structlog.get_logger()

CAPSOLVER_API = "https://api.capsolver.com"


class CapSolverResolver:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._max_intentos = 3
        self.last_token: str | None = None
        self._last_task_id: str | None = None

    async def _request(self, endpoint: str, payload: dict) -> dict:
        url = f"{CAPSOLVER_API}/{endpoint}"
        payload["clientKey"] = self._api_key
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if data.get("errorId", 0) != 0:
                    raise RuntimeError(
                        f"CapSolver error: {data.get('errorCode')} - "
                        f"{data.get('errorDescription')}"
                    )
                return data

    async def _create_and_wait(self, task: dict, timeout: int = 120) -> dict:
        create_resp = await self._request("createTask", {"task": task})
        task_id = create_resp["taskId"]
        self._last_task_id = task_id
        log.info("capsolver_task_creada", task_id=task_id, tipo=task.get("type"))

        for _ in range(timeout // 3):
            await asyncio.sleep(3)
            result = await self._request("getTaskResult", {"taskId": task_id})
            status = result.get("status")
            if status == "ready":
                return result.get("solution", {})
            if status == "failed":
                raise RuntimeError(f"CapSolver failed: {result.get('errorCode')}")

        raise TimeoutError(f"CapSolver task {task_id} timeout ({timeout}s)")

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
        if not self._api_key:
            return None
        try:
            if enterprise and action and score:
                task = {
                    "type": "ReCaptchaV3EnterpriseTaskProxyLess",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                    "pageAction": action,
                    "minScore": score,
                }
            elif enterprise:
                task = {
                    "type": "ReCaptchaV2EnterpriseTaskProxyLess",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                    "isInvisible": invisible,
                }
                if action:
                    task["pageAction"] = action
            else:
                task = {
                    "type": "ReCaptchaV2TaskProxyLess",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                }
                if invisible:
                    task["isInvisible"] = True
            log.info("capsolver_token_solicitando", task_type=task["type"], action=action)
            solution = await self._create_and_wait(task)
            token = solution.get("gRecaptchaResponse", "")
            if token:
                self.last_token = token
                log.info("capsolver_token_obtenido", token_len=len(token))
            return token or None
        except Exception as e:
            log.error("capsolver_token_error", error=str(e))
            return None

    async def reportar_token_malo(self) -> None:
        if self._api_key and self._last_task_id:
            try:
                await self._request(
                    "feedbackTask",
                    {"taskId": self._last_task_id, "result": {"invalid": True}},
                )
                log.info("capsolver_token_reportado", task_id=self._last_task_id)
            except Exception as e:
                log.warning("capsolver_report_error", error=str(e))

    async def resolver(
        self,
        page: Page,
        enterprise: bool = False,
        site_key_override: str | None = None,
        action: str | None = None,
        score: float | None = None,
    ) -> bool:
        for intento in range(1, self._max_intentos + 1):
            log.info(
                "capsolver_intento",
                intento=intento,
                max=self._max_intentos,
                enterprise=enterprise,
                action=action,
            )

            recaptcha = await page.query_selector(
                "iframe[src*='recaptcha'], div.g-recaptcha"
            )
            if recaptcha or site_key_override:
                site_key = site_key_override or (
                    await self._extraer_site_key_recaptcha(page)
                )
                if site_key:
                    ok = await self._resolver_recaptcha(
                        page, site_key, enterprise=enterprise,
                        action=action, score=score,
                    )
                    if ok:
                        return True

            hcaptcha = await page.query_selector("iframe[src*='hcaptcha']")
            if hcaptcha:
                site_key = await self._extraer_site_key_hcaptcha(page)
                if site_key:
                    ok = await self._resolver_hcaptcha(page, site_key)
                    if ok:
                        return True

            img_captcha = await page.query_selector(
                "img[src*='captcha'], img[alt*='captcha' i]"
            )
            if img_captcha:
                ok = await self._resolver_imagen(page, img_captcha)
                if ok:
                    return True

            if intento < self._max_intentos:
                log.warning("capsolver_reintento", espera_seg=10)
                await asyncio.sleep(10)

        log.error("capsolver_fallo_total", intentos=self._max_intentos)
        return False

    async def _extraer_site_key_recaptcha(self, page: Page) -> str | None:
        return await page.evaluate("""
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

    async def _extraer_site_key_hcaptcha(self, page: Page) -> str | None:
        return await page.evaluate("""
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

    async def _resolver_recaptcha(
        self, page: Page, site_key: str,
        enterprise: bool = False, action: str | None = None,
        score: float | None = None,
    ) -> bool:
        if not self._api_key:
            log.error("capsolver_sin_api_key")
            return False

        try:
            page_url = page.url

            if enterprise and action and score:
                task = {
                    "type": "ReCaptchaV3EnterpriseTaskProxyLess",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                    "pageAction": action,
                    "minScore": score,
                }
            elif enterprise:
                task = {
                    "type": "ReCaptchaV2EnterpriseTaskProxyLess",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                }
            else:
                task = {
                    "type": "ReCaptchaV2TaskProxyLess",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                }

            log.info(
                "capsolver_recaptcha_solicitando",
                site_key=site_key[:10],
                task_type=task["type"],
                action=action,
                score=score,
            )

            solution = await self._create_and_wait(task)
            token = solution.get("gRecaptchaResponse", "")

            if not token:
                log.error("capsolver_sin_token_en_respuesta")
                return False

            await page.evaluate(f"""
                () => {{
                    const token = '{token}';
                    document.querySelectorAll(
                        '[name="g-recaptcha-response"]'
                    ).forEach(el => {{ el.value = token; }});

                    if (typeof grecaptcha !== 'undefined'
                        && grecaptcha.enterprise) {{
                        grecaptcha.enterprise.execute = function() {{
                            return Promise.resolve(token);
                        }};
                        grecaptcha.enterprise.reset = function() {{}};
                    }}
                    window.executeRecaptcha = function(accion) {{}};
                    console.log('reCAPTCHA token set via CapSolver');
                }}
            """)

            self.last_token = token
            log.info(
                "capsolver_recaptcha_resuelto",
                token_len=len(token),
                action=action,
                task_type=task["type"],
            )
            return True

        except Exception as e:
            log.error("capsolver_recaptcha_error", error=str(e))
            return False

    async def _resolver_hcaptcha(self, page: Page, site_key: str) -> bool:
        if not self._api_key:
            return False
        try:
            task = {
                "type": "HCaptchaTaskProxyLess",
                "websiteURL": page.url,
                "websiteKey": site_key,
            }
            solution = await self._create_and_wait(task)
            token = solution.get("gRecaptchaResponse", "")
            await page.evaluate(f"""
                () => {{
                    const ta = document.querySelector(
                        'textarea[name="h-captcha-response"]');
                    if (ta) ta.value = '{token}';
                }}
            """)
            log.info("capsolver_hcaptcha_resuelto")
            return True
        except Exception as e:
            log.error("capsolver_hcaptcha_error", error=str(e))
            return False

    async def _resolver_imagen(self, page: Page, img_element) -> bool:
        if not self._api_key:
            return False
        try:
            screenshot = await img_element.screenshot()
            img_b64 = base64.b64encode(screenshot).decode()
            task = {"type": "ImageToTextTask", "body": img_b64}
            solution = await self._create_and_wait(task)
            text = solution.get("text", "")
            captcha_input = await page.query_selector(
                "input[name*='captcha' i], "
                "input[id*='captcha' i], "
                "input[placeholder*='captcha' i]"
            )
            if captcha_input:
                await captcha_input.fill(text)
                log.info("capsolver_imagen_resuelto")
                return True
            log.warning("capsolver_imagen_sin_input")
            return False
        except Exception as e:
            log.error("capsolver_imagen_error", error=str(e))
            return False
