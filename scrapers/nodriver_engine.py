"""
Motor de scraping para el portal SRI en Línea de Ecuador usando nodriver.

Una instancia por ejecución (tenant + período + tipo).
Orquesta: login → navegar → consultar → iterar páginas → descargar XMLs.
"""

import enum
import re
import asyncio
import json
from dataclasses import dataclass, field

import structlog
import nodriver as uc

from captcha.factory import crear_resolvers
from config.settings import Settings
from scrapers.exceptions import (
    SRICaptchaError,
    SRILoginError,
    SRIMaintenanceError,
    SRISessionExpiredError,
    SRITimeoutError,
)
from scrapers.portal import RECAPTCHA_ACTION, load_js_asset
from utils.delays import simular_actividad_humana
from utils.time import utc_now

log = structlog.get_logger()

# ── URLs del portal SRI ────────────────────────────────────────────────────
URLS = {
    "login": (
        "https://srienlinea.sri.gob.ec/auth/realms/Internet"
        "/protocol/openid-connect/auth"
        "?client_id=app-sri-claves-angular"
        "&redirect_uri=https%3A%2F%2Fsrienlinea.sri.gob.ec"
        "%2Fsri-en-linea%2F%2Fcontribuyente%2Fperfil"
        "&response_mode=fragment&response_type=code&scope=openid"
    ),
    "portal": (
        "https://srienlinea.sri.gob.ec/tuportal-internet"
        "/accederAplicacion.jspa?redireccion=57&idGrupo=55"
    ),
    "comprobantes": (
        "https://srienlinea.sri.gob.ec/comprobantes-electronicos-internet/"
        "pages/consultas/recibidos/comprobantesRecibidos.jsf"
    )
}

MESES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

class EstadoPortal(enum.Enum):
    NORMAL = "normal"
    MANTENIMIENTO = "mantenimiento"
    SESION_EXPIRADA = "sesion_expirada"
    ERROR_SISTEMA = "error_sistema"

@dataclass
class EjecucionResult:
    total_encontrados: int = 0
    total_nuevos: int = 0
    total_errores: int = 0
    xmls_descargados: list[dict] = field(default_factory=list)
    duracion_seg: float = 0.0
    pagina_final: int = 1

class SRINodriverEngine:
    """
    Motor alternativo indetectable usando `nodriver`.
    """

    def __init__(
        self,
        tenant_ruc: str,
        tenant_usuario: str,
        tenant_password: str,
        periodo_anio: int,
        periodo_mes: int,
        tipo_comprobante: str,
        settings: Settings,
        pagina_inicio: int = 1,
    ):
        self._ruc = tenant_ruc
        self._usuario = tenant_usuario
        self._password = tenant_password
        self._anio = periodo_anio
        self._mes = periodo_mes
        self._tipo = tipo_comprobante
        self._settings = settings
        self._pagina_inicio = pagina_inicio

        self._browser = None
        self._page = None
        self._captcha_resolvers = crear_resolvers(
            settings.captcha_provider,
            settings.twocaptcha_api_key,
            settings.capsolver_api_key,
        )
        self._captcha_resolver = self._captcha_resolvers[0]["resolver"]

        self._comprobantes_html: list[dict] = []

        self._log = log.bind(
            tenant_ruc=tenant_ruc,
            periodo=f"{periodo_anio}-{periodo_mes:02d}",
            tipo=tipo_comprobante,
            engine="nodriver"
        )

    async def ejecutar(self) -> EjecucionResult:
        inicio = utc_now()
        result = EjecucionResult()

        try:
            await self._inicializar_browser()
            await self._login()
            await self._navegar_comprobantes()
            total = await self._seleccionar_periodo_y_consultar()

            result.total_encontrados = total
            if total == 0:
                self._log.info("sin_comprobantes")
                return result

            if self._comprobantes_html:
                self._log.info("comprobantes_encontrados", count=len(self._comprobantes_html))
                # For Phase 1 we just get the claves and HTML downloading will be implemented in the next step
                
        except Exception as e:
            self._log.error("error_inesperado", error=str(e))
            raise
        finally:
            duracion = (utc_now() - inicio).total_seconds()
            result.duracion_seg = duracion
            self._log.info("ejecucion_finalizada", duracion_seg=duracion)
            await self._cerrar_browser()

        return result

    async def _inicializar_browser(self):
        self._log.info("arrancando_nodriver")
        
        # In Docker, headless must be True unless xvfb is set up. 
        # But nodriver works best fully headed, so we require xvfb running.
        headless = self._settings.playwright_headless
        
        # Buscar el binario de Chromium instalado por Playwright (en Docker)
        chrome_path = None
        import glob
        candidates = glob.glob(
            "/root/.cache/ms-playwright/*/chrome-linux/chrome"
        )
        if candidates:
            chrome_path = candidates[0]

        kwargs = dict(
            headless=headless,
            browser_args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1366,768",
            ],
        )
        if chrome_path:
            kwargs["browser_executable_path"] = chrome_path
            self._log.info("usando_chromium_playwright", path=chrome_path)

        self._browser = await uc.start(**kwargs)
        self._page = self._browser.main_tab

    async def _cerrar_browser(self):
        if self._browser:
            self._browser.stop()

    def _captcha_assisted_available(self) -> bool:
        return (
            self._settings.captcha_assisted_enabled()
            and not self._settings.playwright_headless
        )

    def _build_captcha_attempt_plan(self, max_intentos: int) -> list[dict]:
        attempts: list[dict] = []
        assist_mode = self._settings.captcha_assisted_mode.lower().strip()
        if assist_mode == "only" and self._captcha_assisted_available():
            return [{"mode": "assisted"}]

        native_attempts = min(2, max_intentos)
        for _ in range(native_attempts):
            attempts.append({"mode": "native"})

        provider_slots = max_intentos - native_attempts
        if provider_slots > 0:
            provider_variants = [
                {"variant": "enterprise_v3", "score": 0.9},
                {"variant": "enterprise_v2", "score": None},
            ]
            provider_attempts: list[dict] = []
            for resolver_info in self._captcha_resolvers:
                for variant in provider_variants:
                    provider_attempts.append({
                        "mode": "provider",
                        "provider": resolver_info["provider"],
                        "resolver": resolver_info["resolver"],
                        "variant": variant["variant"],
                        "score": variant["score"],
                    })
            attempts.extend(provider_attempts[:provider_slots])

        if assist_mode == "fallback" and self._captcha_assisted_available():
            attempts.append({"mode": "assisted"})
        return attempts

    async def _evaluate_asset(
        self,
        asset_name: str,
        payload: dict | None = None,
        *,
        await_promise: bool = False,
    ):
        page = self._page
        assert page is not None

        asset = load_js_asset(asset_name).strip()
        if payload is None:
            expression = f"({asset})()"
        else:
            payload_literal = json.dumps(payload)
            expression = f"({asset})({payload_literal})"
        return await page.evaluate(
            expression,
            await_promise=await_promise,
        )

    async def _extraer_site_key_recaptcha(self) -> str | None:
        result = await self._evaluate_asset("extract_site_key.js")
        return result or None

    async def _obtener_site_key_consulta(self) -> str:
        site_key = await self._extraer_site_key_recaptcha()
        if site_key:
            return site_key
        fallback = self._settings.recaptcha_sitekey_fallback.strip()
        if fallback:
            self._log.warning("captcha_sitekey_fallback_en_uso")
            return fallback
        raise SRICaptchaError(
            "No se pudo detectar la sitekey reCAPTCHA del portal"
        )

    async def _resolver_token_con_proveedor(
        self,
        resolver,
        provider: str,
        site_key: str,
        score: float | None,
    ) -> str | None:
        page = self._page
        assert page is not None

        self._log.info(
            "captcha_provider_intento",
            provider=provider,
            site_key=site_key[:10],
            variant="enterprise_v3" if score is not None else "enterprise_v2",
        )
        token = await resolver.resolver_token_recaptcha(
            site_key=site_key,
            page_url=page.url.split("?")[0].split("#")[0],
            enterprise=True,
            action=RECAPTCHA_ACTION,
            score=score,
        )
        if token:
            self._log.info(
                "captcha_provider_token_obtenido",
                provider=provider,
                token_len=len(token),
            )
        return token

    async def _ejecutar_consulta_controlada(
        self,
        *,
        token: str | None,
        source: str,
    ) -> dict:
        payload = {
            "token": token,
            "source": source,
            "action": RECAPTCHA_ACTION,
        }
        return await self._evaluate_asset(
            "controlled_query.js",
            payload,
            await_promise=True,
        )

    async def _resetear_recaptcha(self) -> None:
        await self._evaluate_asset("reset_recaptcha.js")

    async def _ejecutar_recaptcha_nativo(self) -> dict:
        self._log.info("recaptcha_nativo_iniciando")
        page = self._page
        assert page is not None

        try:
            try:
                await simular_actividad_humana(page)
            except Exception as exc:
                self._log.warning(
                    "actividad_humana_pre_captcha_error",
                    error=str(exc),
                )
            result = await self._ejecutar_consulta_controlada(
                token=None,
                source="native",
            )
            self._log.info("recaptcha_nativo_resultado", result=result)
            return result
        except Exception as exc:
            self._log.error("recaptcha_nativo_error", error=str(exc))
            return {
                "messages": "",
                "panelLen": 0,
                "panelHtml": "",
                "error": str(exc),
            }

    async def _ejecutar_consulta_asistida(self) -> dict:
        page = self._page
        assert page is not None

        timeout_sec = self._settings.captcha_assisted_timeout_sec
        self._log.warning(
            "captcha_modo_asistido",
            timeout_sec=timeout_sec,
            url=page.url,
        )
        await page.evaluate(
            f"""
            (() => {{
                const existing = document.getElementById('codex-captcha-assist');
                if (existing) existing.remove();
                const banner = document.createElement('div');
                banner.id = 'codex-captcha-assist';
                banner.innerText =
                    'Modo asistido activo. Complete el captcha y presione Consultar. '
                    + 'La espera termina en {timeout_sec} segundos.';
                Object.assign(banner.style, {{
                    position: 'fixed',
                    top: '12px',
                    right: '12px',
                    zIndex: '99999',
                    background: '#111827',
                    color: '#ffffff',
                    padding: '12px 14px',
                    borderRadius: '10px',
                    fontSize: '14px',
                    boxShadow: '0 10px 25px rgba(0,0,0,0.25)',
                    maxWidth: '360px',
                }});
                document.body.appendChild(banner);
            }})()
            """
        )

        auto_submitted = False
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            result = await page.evaluate(
                """
                (() => {
                    const msgs = document.getElementById('formMessages:messages');
                    const panel = document.getElementById(
                        'frmPrincipal:panelListaComprobantes'
                    );
                    const tas = document.querySelectorAll(
                        '[name="g-recaptcha-response"]'
                    );
                    return {
                        source: 'assisted',
                        messages: msgs ? msgs.innerText.trim() : '',
                        panelLen: panel ? panel.innerHTML.length : 0,
                        panelHtml: panel ? panel.innerHTML : '',
                        textareas: Array.from(tas).map((t, i) => ({
                            index: i,
                            len: t.value.length,
                            form: (t.closest('form') || {}).id || 'none',
                        })),
                    };
                })()
                """
            )
            if result.get("panelLen", 0) > 50:
                return result

            token_present = any(
                item.get("len", 0) > 100
                for item in result.get("textareas", [])
            )
            if token_present and not auto_submitted:
                auto_submitted = True
                await page.evaluate(
                    """
                    (() => {
                        if (typeof window.rcBuscar === 'function') {
                            window.rcBuscar();
                            return;
                        }
                        const btn = document.querySelector(
                            '[id="frmPrincipal:btnBuscar"]'
                        );
                        if (btn) btn.click();
                    })()
                    """
                )

            await asyncio.sleep(2)

        return {
            "source": "assisted",
            "messages": "",
            "panelLen": 0,
            "panelHtml": "",
            "error": f"assist_timeout_{timeout_sec}s",
        }

    async def _login(self):
        self._log.info("iniciando_login")
        await self._page.get(URLS["login"])
        await asyncio.sleep(8)
        
        body = await self._page.evaluate("document.body.innerText")
        
        if "Clave" in body or "usuario" in await self._page.evaluate("document.body.innerHTML"):
            self._log.info("formulario_login_detectado")
            
            try:
                el = await self._page.select("input#usuario")
                await el.click()
                await asyncio.sleep(0.3)
                await el.send_keys(self._usuario)
            except Exception as e:
                self._log.warning("error_input_usuario", error=str(e))

            await asyncio.sleep(0.5)

            try:
                el = await self._page.select("input#password")
                await el.click()
                await asyncio.sleep(0.3)
                await el.send_keys(self._password)
            except Exception as e:
                self._log.warning("error_input_password", error=str(e))

            await asyncio.sleep(1)

            try:
                btn = await self._page.select("input#kc-login")
                await btn.click()
            except Exception as e:
                self._log.warning("error_submit_login", error=str(e))

            # Esperar a que se procese el login y el Keycloak redirija
            await asyncio.sleep(15)
            
        url_actual = self._page.url
        self._log.info("estado_post_login", url=url_actual)
        
        if "auth/realms" in url_actual:
            raise SRILoginError("No se pudo completar el login (SSO falló).")

    async def _navegar_comprobantes(self):
        self._log.info("navegando_a_comprobantes")
        await self._page.get(URLS["comprobantes"])
        await asyncio.sleep(15)
        
        url_actual = self._page.url
        # If redirected to profile, try forcing navigation again
        if "perfil" in url_actual or "sri-en-linea" in url_actual:
            self._log.info("forzando_navegacion_comprobantes_nuevamente")
            await self._page.get(URLS["comprobantes"])
            await asyncio.sleep(15)
            
        body = await self._page.evaluate("document.body.innerHTML")
        if "Comprobantes electrónicos" not in body and "frmPrincipal" not in body:
            raise SRITimeoutError("No se pudo cargar el módulo de comprobantes recibidos.")

    async def _seleccionar_periodo_y_consultar(self) -> int:
        self._log.info("seleccionando_filtros")
        page = self._page
        assert page is not None
        
        # Seleccionar Año
        await page.evaluate(f'''
        () => {{
            const a = document.getElementById('frmPrincipal:anio');
            if (a) {{
                for (let o of a.options)
                    if (o.text==='{self._anio}') {{ a.value=o.value; break; }}
                a.dispatchEvent(new Event('change', {{bubbles:true}}));
            }}
        }}
        ''')
        await asyncio.sleep(2)
        
        # Seleccionar Mes
        mes_str = MESES[self._mes - 1]
        await page.evaluate(f'''
        () => {{
            const m = document.getElementById('frmPrincipal:mes');
            if (m) {{
                for (let o of m.options)
                    if (o.text==='{mes_str}') {{ m.value=o.value; break; }}
                m.dispatchEvent(new Event('change', {{bubbles:true}}));
            }}
        }}
        ''')
        await asyncio.sleep(2)

        max_intentos = 5
        attempt_plan = self._build_captcha_attempt_plan(max_intentos)
        captcha_exitoso = False
        panel_html = ""
        msgs = ""

        for intento, attempt in enumerate(attempt_plan, start=1):
            self._log.info(
                "consulta_intento",
                intento=intento,
                modo=attempt["mode"],
                provider=attempt.get("provider"),
                variant=attempt.get("variant"),
            )

            if attempt["mode"] == "native":
                query_result = await self._ejecutar_recaptcha_nativo()
            elif attempt["mode"] == "assisted":
                query_result = await self._ejecutar_consulta_asistida()
            else:
                try:
                    await simular_actividad_humana(page)
                except Exception as exc:
                    self._log.warning(
                        "actividad_humana_pre_provider_error",
                        error=str(exc),
                    )
                site_key = await self._obtener_site_key_consulta()
                token = await self._resolver_token_con_proveedor(
                    attempt["resolver"],
                    attempt["provider"],
                    site_key,
                    attempt.get("score"),
                )
                if not token:
                    self._log.warning(
                        "captcha_provider_sin_token",
                        intento=intento,
                        provider=attempt["provider"],
                        variant=attempt.get("variant"),
                    )
                    await self._resetear_recaptcha()
                    continue
                query_result = await self._ejecutar_consulta_controlada(
                    token=token,
                    source=attempt["provider"],
                )

            self._log.info("resultado_rcbuscar", qr=query_result)

            msgs = query_result.get("messages", "").lower()
            panel_len = query_result.get("panelLen", 0)
            error = query_result.get("error")

            if panel_len > 50:
                panel_html = query_result.get("panelHtml", "")
                captcha_exitoso = True
                break
            if "captcha" in msgs:
                self._log.warning(
                    "captcha_rechazado",
                    intento=intento,
                    provider=attempt.get("provider"),
                    variant=attempt.get("variant"),
                )
                if attempt["mode"] == "provider":
                    await attempt["resolver"].reportar_token_malo()
                await self._resetear_recaptcha()
                await asyncio.sleep(2)
                continue
            if error:
                self._log.warning(
                    "consulta_controlada_error",
                    intento=intento,
                    provider=attempt.get("provider"),
                    variant=attempt.get("variant"),
                    error=error,
                )
                await self._resetear_recaptcha()
                await asyncio.sleep(2)
                continue

            self._log.info("sin_datos", intento=intento)
            await asyncio.sleep(2)

        if not captcha_exitoso:
            if "no se encontraron" in msgs:
                return 0
            body_text = await page.evaluate("document.body.innerText") or ""
            if "no se encontraron" in body_text.lower():
                return 0
            raise SRICaptchaError("No se pudo consultar comprobantes")
            
        # Very simple parse for now
        claves = list(set(re.findall(r'\b(\d{49})\b', panel_html)))
        self._comprobantes_html = [{"clave_acceso": c} for c in claves]
        return len(claves)
