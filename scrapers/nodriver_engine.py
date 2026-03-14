"""
Motor de scraping para el portal SRI en Línea de Ecuador usando nodriver.

Una instancia por ejecución (tenant + período + tipo).
Orquesta: login → navegar → consultar → iterar páginas → descargar XMLs.
"""

import enum
import re
import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog
import nodriver as uc
from lxml import etree
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from captcha.factory import crear_resolvers
from config.settings import Settings
from scrapers.adaptive_strategy import AdaptiveStrategyTracker
from scrapers.behavior import simulate_pre_captcha_activity, BehaviorProfile
from scrapers.captcha_strategy import (
    build_captcha_attempt_plan,
    resolve_provider_page_url,
)
from scrapers.exceptions import (
    ProviderError,
    SRICaptchaError,
    SRIDownloadError,
    SRILoginError,
    SRIMaintenanceError,
    SRISessionExpiredError,
    SRITimeoutError,
)
from scrapers.fingerprint import (
    generate_fingerprint,
    build_stealth_script,
    build_nodriver_browser_args,
)
from scrapers.portal import RECAPTCHA_ACTION, load_js_asset
from scrapers.token_validator import validate_token, estimate_token_freshness
from scrapers.trap_detector import run_full_trap_check
from scrapers.warmup import warmup_session_nodriver
from utils.delays import delay_humano, simular_actividad_humana
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
    ),
}

SRI_SOAP_URL = (
    "https://cel.sri.gob.ec/comprobantes-electronicos-ws/"
    "AutorizacionComprobantesOffline"
)

SOAP_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soapenv:Envelope '
    'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:ec="http://ec.gob.sri.ws.autorizacion">'
    '<soapenv:Body>'
    '<ec:autorizacionComprobante>'
    '<claveAccesoComprobante>{clave}</claveAccesoComprobante>'
    '</ec:autorizacionComprobante>'
    '</soapenv:Body>'
    '</soapenv:Envelope>'
)

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
        on_page_processed: Callable[[int, list[dict]], Awaitable[None]] | None = None,
        should_skip_download: Callable[[str], Awaitable[bool]] | None = None,
        collect_results: bool = True,
    ):
        self._ruc = tenant_ruc
        self._usuario = tenant_usuario
        self._password = tenant_password
        self._anio = periodo_anio
        self._mes = periodo_mes
        self._tipo = tipo_comprobante
        self._settings = settings
        self._pagina_inicio = pagina_inicio
        self._on_page_processed = on_page_processed
        self._should_skip_download = should_skip_download
        self._collect_results = collect_results

        self._browser = None
        self._page = None
        self._captcha_resolvers = crear_resolvers(
            settings.captcha_provider,
            settings.twocaptcha_api_key,
            settings.capsolver_api_key,
        )
        self._captcha_resolver = self._captcha_resolvers[0]["resolver"]

        self._comprobantes_html: list[dict] = []
        self._ultima_pagina_procesada: int = 1
        self._adaptive_tracker: AdaptiveStrategyTracker | None = None

        # Generate unique fingerprint for this session
        fp_seed = f"{tenant_ruc}:{periodo_anio}:{periodo_mes}:{id(self)}"
        self._fingerprint = generate_fingerprint(fp_seed) if settings.fingerprint_rotation else None
        self._behavior_profile = BehaviorProfile.random(fp_seed) if settings.behavior_simulation else None
        self._known_sitekey: str | None = None  # for trap detection

        self._log = log.bind(
            tenant_ruc=tenant_ruc,
            periodo=f"{periodo_anio}-{periodo_mes:02d}",
            tipo=tipo_comprobante,
            engine="nodriver",
        )

    async def ejecutar(self) -> EjecucionResult:
        inicio = utc_now()
        result = EjecucionResult()

        try:
            await self._inicializar_browser()

            # Session warm-up: browse public pages to build reCAPTCHA trust
            if self._settings.session_warmup:
                await warmup_session_nodriver(self._page)

            await self._login()

            # Post-login warm-up
            if self._settings.session_warmup:
                await warmup_session_nodriver(self._page, post_login=True)

            await self._navegar_comprobantes()
            total = await self._seleccionar_periodo_y_consultar()

            result.total_encontrados = total
            if total == 0:
                self._log.info("sin_comprobantes")
                return result

            self._log.info(
                "comprobantes_encontrados",
                count=len(self._comprobantes_html),
            )

            # Download XMLs and paginate
            xmls = await self._procesar_todas_las_paginas()
            result.xmls_descargados = xmls
            result.total_nuevos = len(
                [x for x in xmls if x.get("xml_bytes")]
            )
            result.total_errores = len(
                [x for x in xmls if x.get("error")]
            )
            result.pagina_final = self._ultima_pagina_procesada

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

        # Use fingerprint for browser args if available
        if self._fingerprint:
            browser_args = build_nodriver_browser_args(self._fingerprint)
            self._log.info(
                "fingerprint_aplicado",
                fp_id=self._fingerprint.fingerprint_id,
                screen=f"{self._fingerprint.viewport_width}x{self._fingerprint.viewport_height}",
            )
        else:
            browser_args = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1366,768",
            ]

        kwargs = dict(
            headless=headless,
            browser_args=browser_args,
        )
        if chrome_path:
            kwargs["browser_executable_path"] = chrome_path
            self._log.info("usando_chromium_playwright", path=chrome_path)

        self._browser = await uc.start(**kwargs)
        self._page = self._browser.main_tab

        # Anti-detection: inject stealth patches using fingerprint-aware script
        try:
            if self._fingerprint:
                stealth_js = build_stealth_script(self._fingerprint)
            else:
                stealth_js = """
                (() => {
                    window.alert = function() { console.log('Interceptor: dismissed alert'); };
                    window.confirm = function() { return true; };
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [
                            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                            { name: 'Native Client', filename: 'internal-nacl-plugin' },
                        ],
                    });
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['es-EC', 'es', 'en-US', 'en'],
                    });
                    if (!window.chrome) {
                        window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
                    }
                    const origQuery = window.navigator.permissions?.query;
                    if (origQuery) {
                        window.navigator.permissions.query = (params) => (
                            params.name === 'notifications'
                                ? Promise.resolve({ state: Notification.permission })
                                : origQuery(params)
                        );
                    }
                    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
                    HTMLCanvasElement.prototype.toDataURL = function(type) {
                        const ctx = this.getContext('2d');
                        if (ctx) {
                            const style = ctx.fillStyle;
                            ctx.fillStyle = 'rgba(255,255,255,0.01)';
                            ctx.fillRect(0, 0, 1, 1);
                            ctx.fillStyle = style;
                        }
                        return origToDataURL.apply(this, arguments);
                    };
                    const getParam = WebGLRenderingContext.prototype.getParameter;
                    WebGLRenderingContext.prototype.getParameter = function(parameter) {
                        if (parameter === 37445) return 'Google Inc. (Intel)';
                        if (parameter === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)';
                        return getParam.apply(this, arguments);
                    };
                })()
                """
            await self._page.evaluate(stealth_js)
        except Exception as e:
            self._log.warning("error_inyectando_stealth", error=str(e))

    async def _limpiar_sesion(self):
        if self._browser and self._browser.connection:
            try:
                import nodriver.cdp.network as network
                await self._browser.connection.send(network.clear_browser_cookies())
                await self._browser.connection.send(network.clear_browser_cache())
                self._log.info("sesion_limpiada")
            except Exception as e:
                self._log.warning("error_limpiando_sesion", error=str(e))

    async def _cerrar_browser(self):
        if self._browser:
            await self._limpiar_sesion()
            try:
                self._browser.stop()
            except Exception as e:
                self._log.warning("error_cerrando_browser_graceful", error=str(e))
                
            # Hard kill orphaned chrome processes associated with this tab if any.
            import os
            try:
                browser_pid = getattr(self._browser, 'pid', None)
                if not browser_pid and hasattr(self._browser, 'process'):
                    browser_pid = self._browser.process.pid
                    
                if browser_pid:
                    try:
                        os.kill(browser_pid, 9)
                        self._log.info("browser_process_killed", pid=browser_pid)
                    except ProcessLookupError:
                        pass
            except Exception as e:
                self._log.debug("error_hard_kill", error=str(e))

    def _captcha_assisted_available(self) -> bool:
        return (
            self._settings.captcha_assisted_enabled()
            and not self._settings.playwright_headless
        )

    def _build_captcha_attempt_plan(self, max_intentos: int) -> list[dict]:
        assist_mode = self._settings.captcha_assisted_mode.lower().strip()
        return build_captcha_attempt_plan(
            assist_mode=assist_mode,
            assisted_available=self._captcha_assisted_available(),
            captcha_resolvers=self._captcha_resolvers,
            max_attempts=max_intentos,
        )

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
        attempt: dict,
    ) -> str | None:
        page = self._page
        assert page is not None

        page_url = resolve_provider_page_url(
            page.url,
            attempt.get("page_url_mode", "canonical"),
        )
        self._log.info(
            "captcha_provider_intento",
            provider=provider,
            site_key=site_key[:10],
            variant=attempt.get("variant"),
            enterprise=attempt.get("enterprise"),
            invisible=attempt.get("invisible"),
            action=attempt.get("action"),
            score=attempt.get("score"),
            page_url_mode=attempt.get("page_url_mode"),
        )
        try:
            token = await resolver.resolver_token_recaptcha(
                site_key=site_key,
                page_url=page_url,
                enterprise=attempt.get("enterprise", False),
                action=attempt.get("action"),
                score=attempt.get("score"),
                invisible=attempt.get("invisible", False),
            )
            if token:
                self._log.info(
                    "captcha_provider_token_obtenido",
                    provider=provider,
                    token_len=len(token),
                )
            return token
        except Exception as e:
            msg = str(e).lower()
            if "zero" in msg or "balance" in msg or "key" in msg:
                self._log.error("proveedor_balance_cero_o_invalido", provider=provider, error=str(e))
                raise ProviderError(str(e))
            self._log.warning("proveedor_error_temporal", provider=provider, error=str(e))
            return None

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
            "timeoutMs": max(self._settings.browser_timeout_ms, 45_000),
            "pollIntervalMs": 500,
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
                banner.innerHTML = `
                    <div style="font-weight:600;margin-bottom:8px;">
                        Modo asistido activo
                    </div>
                    <div style="line-height:1.4;">
                        Complete el captcha y luego use el boton de abajo si el
                        Consultar del portal no responde. La espera termina en
                        {timeout_sec} segundos.
                    </div>
                    <button
                        id="codex-captcha-assist-submit"
                        type="button"
                        style="margin-top:10px;padding:8px 12px;border:none;border-radius:8px;background:#2563eb;color:#fff;font-weight:600;cursor:pointer;"
                    >
                        Enviar consulta asistida
                    </button>
                    <div id="codex-captcha-assist-status" style="margin-top:8px;font-size:12px;opacity:0.9;"></div>
                `;
                document.body.appendChild(banner);

                const statusEl = document.getElementById('codex-captcha-assist-status');
                const setStatus = (text) => {{
                    if (statusEl) statusEl.textContent = text || '';
                }};
                const getTokenValue = () => {{
                    const tokenField = Array.from(
                        document.querySelectorAll('[name="g-recaptcha-response"]')
                    ).find((el) => (el.value || '').length > 100);
                    const liveToken = tokenField ? tokenField.value : '';
                    if (liveToken.length > 100) {{
                        window.__codexAssistedLastToken = liveToken;
                        return liveToken;
                    }}
                    return window.__codexAssistedLastToken || '';
                }};
                const assistedSubmit = () => {{
                    const token = getTokenValue();
                    if (token.length <= 100) {{
                        setStatus('Todavia no se detecta token de captcha.');
                        return false;
                    }}
                    window.__codexAssistedSubmitRequested = true;
                    setStatus('Solicitud enviada al backend. Espere respuesta...');
                    return true;
                }};

                window.__codexAssistedSubmitRequested = false;
                window.__codexAssistedLastToken = window.__codexAssistedLastToken || '';
                window.__codexAssistedSubmit = assistedSubmit;
                const helperBtn = document.getElementById('codex-captcha-assist-submit');
                if (helperBtn) {{
                    helperBtn.onclick = assistedSubmit;
                }}

                const interceptPortalSubmit = (event) => {{
                    if (assistedSubmit()) {{
                        if (event) {{
                            event.preventDefault();
                            event.stopPropagation();
                            if (typeof event.stopImmediatePropagation === 'function') {{
                                event.stopImmediatePropagation();
                            }}
                        }}
                        return false;
                    }}
                    return true;
                }};

                const searchBtn = document.getElementById('frmPrincipal:btnBuscar');
                if (searchBtn && !searchBtn.__codexAssistWrapped) {{
                    searchBtn.addEventListener('click', interceptPortalSubmit, true);
                    searchBtn.__codexAssistWrapped = true;
                }}

                if (typeof window.executeRecaptcha === 'function' && !window.__codexExecuteRecaptchaWrapped) {{
                    const originalExecuteRecaptcha = window.executeRecaptcha;
                    window.executeRecaptcha = function(...args) {{
                        if (assistedSubmit()) {{
                            return;
                        }}
                        return originalExecuteRecaptcha.apply(this, args);
                    }};
                    window.__codexExecuteRecaptchaWrapped = true;
                }}

                if (typeof window.onSubmit === 'function' && !window.__codexOnSubmitWrapped) {{
                    const originalOnSubmit = window.onSubmit;
                    window.onSubmit = function(...args) {{
                        if (assistedSubmit()) {{
                            return false;
                        }}
                        return originalOnSubmit.apply(this, args);
                    }};
                    window.__codexOnSubmitWrapped = true;
                }}
            }})()
            """
        )

        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            result = await page.evaluate(
                """
                (() => {
                    const msgs = document.getElementById('formMessages:messages');
                    const panel = document.getElementById(
                        'frmPrincipal:panelListaComprobantes'
                    );
                    const documentsPanel = document.getElementById(
                        'frmPrincipal:pnldocumentosrecibidos'
                    );
                    const tableContainer =
                        document.getElementById('frmPrincipal:tablaCompRecibidos')
                        || document.getElementById('frmPrincipal:pnldocumentosrecibidos');
                    const tableBody =
                        document.getElementById('frmPrincipal:tablaCompRecibidos_data')
                        || document.querySelector('[id="frmPrincipal:tablaCompRecibidos_data"]');
                    const table =
                        (tableBody && tableBody.closest('table'))
                        || (tableContainer && tableContainer.querySelector('table'))
                        || null;
                    const tas = document.querySelectorAll(
                        '[name="g-recaptcha-response"]'
                    );
                    const tokenField = Array.from(tas).find(
                        (el) => (el.value || '').length > 100
                    );
                    const liveToken = tokenField ? tokenField.value : '';
                    if (liveToken.length > 100) {{
                        window.__codexAssistedLastToken = liveToken;
                    }}
                    const cachedToken = window.__codexAssistedLastToken || '';
                    const rowCount = tableBody
                        ? Array.from(tableBody.querySelectorAll('tr')).filter(
                            (row) => (row.textContent || '').trim().length > 0
                        ).length
                        : (table
                            ? Array.from(table.querySelectorAll('tbody tr')).filter(
                                (row) => (row.textContent || '').trim().length > 0
                            ).length
                            : 0);
                    return {
                        source: 'assisted',
                        messages: msgs ? msgs.innerText.trim() : '',
                        panelLen: panel ? panel.innerHTML.length : 0,
                        panelHtml: panel ? panel.innerHTML : '',
                        documentsPanelLen: documentsPanel ? documentsPanel.innerHTML.length : 0,
                        documentsPanelHtml: documentsPanel ? documentsPanel.innerHTML : '',
                        tableContainerId: tableContainer ? (tableContainer.id || '') : '',
                        tableContainerHtmlLen: tableContainer ? tableContainer.innerHTML.length : 0,
                        tableContainerHtml: tableContainer ? tableContainer.innerHTML : '',
                        tableId: table ? (table.id || '') : '',
                        tableBodyId: tableBody ? (tableBody.id || '') : '',
                        tableRows: rowCount,
                        tableHtmlLen: table ? table.outerHTML.length : 0,
                        tableHtml: table ? table.outerHTML : '',
                        tokenReady: (liveToken.length > 100) || (cachedToken.length > 100),
                        cachedTokenLen: cachedToken.length,
                        submitRequested: Boolean(window.__codexAssistedSubmitRequested),
                        textareas: Array.from(tas).map((t, i) => ({
                            index: i,
                            len: t.value.length,
                            form: (t.closest('form') || {}).id || 'none',
                        })),
                    };
                })()
                """
            )
            if (
                result.get("panelLen", 0) > 50
                or result.get("documentsPanelLen", 0) > 100
                or result.get("tableContainerHtmlLen", 0) > 100
                or result.get("tableRows", 0) > 0
                or result.get("tableHtmlLen", 0) > 100
            ):
                return result

            if result.get("submitRequested"):
                token = await page.evaluate(
                    """
                    (() => {
                        const tokenField = Array.from(
                            document.querySelectorAll('[name="g-recaptcha-response"]')
                        ).find((el) => (el.value || '').length > 100);
                        const liveToken = tokenField ? tokenField.value : '';
                        if (liveToken.length > 100) {{
                            window.__codexAssistedLastToken = liveToken;
                        }}
                        window.__codexAssistedSubmitRequested = false;
                        return liveToken || window.__codexAssistedLastToken || '';
                    })()
                    """
                )
                if len(token) > 100:
                    self._log.info(
                        "captcha_asistido_submit_backend",
                        token_len=len(token),
                    )
                    return await self._ejecutar_consulta_controlada(
                        token=token,
                        source="assisted",
                    )

            await asyncio.sleep(2)

        return {
            "source": "assisted",
            "messages": "",
            "panelLen": 0,
            "panelHtml": "",
            "error": f"assist_timeout_{timeout_sec}s",
        }

    async def _wait_for_condition(
        self,
        js_expression: str,
        timeout_sec: float = 15.0,
        interval_sec: float = 0.5,
    ) -> bool:
        """Evaluate a JS expression periodically until it returns true or times out."""
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            try:
                result = await self._page.evaluate(js_expression)
                if result:
                    return True
            except Exception:
                pass
            await asyncio.sleep(interval_sec)
        return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((SRILoginError, SRITimeoutError, Exception)),
        reraise=True,
    )
    async def _login(self):
        self._log.info("iniciando_login")
        await self._page.get(URLS["login"])
        
        # Dynamic wait for login form
        listo = await self._wait_for_condition(
            "document.body.innerText.includes('Clave') || document.body.innerHTML.includes('usuario')",
            timeout_sec=15.0
        )
        
        if listo:
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

            # Dynamic wait for Keycloak to redirect instead of 15s sleep
            await self._wait_for_condition(
                "window.location.href.includes('sri-en-linea')",
                timeout_sec=20.0
            )
            
        url_actual = self._page.url
        self._log.info("estado_post_login", url=url_actual)
        
        if "auth/realms" in url_actual:
            raise SRILoginError("No se pudo completar el login (SSO falló).")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((SRITimeoutError, Exception)),
        reraise=True,
    )
    async def _navegar_comprobantes(self):
        self._log.info("navegando_a_comprobantes")
        await self._page.get(URLS["comprobantes"])
        
        nav_1 = await self._wait_for_condition(
            "document.body.innerHTML.includes('Comprobantes electrónicos') || document.body.innerHTML.includes('frmPrincipal')",
            timeout_sec=15.0
        )
        
        url_actual = self._page.url
        # If redirected to profile, try forcing navigation again
        if not nav_1 and ("perfil" in url_actual or "sri-en-linea" in url_actual):
            self._log.info("forzando_navegacion_comprobantes_nuevamente")
            await self._page.get(URLS["comprobantes"])
            await self._wait_for_condition(
                "document.body.innerHTML.includes('Comprobantes electrónicos') || document.body.innerHTML.includes('frmPrincipal')",
                timeout_sec=20.0
            )
            
        body = await self._page.evaluate("document.body.innerHTML")
        if "Comprobantes electrónicos" not in body and "frmPrincipal" not in body:
            raise SRITimeoutError("No se pudo cargar el módulo de comprobantes recibidos.")

    async def _seleccionar_periodo_y_consultar(self) -> int:
        self._log.info("seleccionando_filtros")
        page = self._page
        assert page is not None

        async def _snapshot_filtros() -> dict:
            return await page.evaluate(
                """
                () => {
                    const readSelect = (id) => {
                        const el = document.getElementById(id);
                        if (!el) return null;
                        const idx = el.selectedIndex;
                        const opt = idx >= 0 ? el.options[idx] : null;
                        return {
                            id,
                            value: el.value || '',
                            label: opt ? (opt.textContent || '').trim() : '',
                            options: el.options ? el.options.length : 0,
                        };
                    };
                    return {
                        anio: readSelect('frmPrincipal:ano'),
                        mes: readSelect('frmPrincipal:mes'),
                        dia: readSelect('frmPrincipal:dia'),
                        tipo: readSelect('frmPrincipal:cmbTipoComprobante'),
                    };
                }
                """
            )

        async def _configurar_criterio_ruc() -> dict:
            return await page.evaluate(
                """
                ({ ruc }) => {
                    const isVisible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && el.offsetParent !== null;
                    };
                    const normalize = (value) => (value || '').trim().toLowerCase();
                    const form = document.getElementById('frmPrincipal')
                        || document.querySelector('form[id="frmPrincipal"]')
                        || document.querySelector('form');
                    const result = {
                        radioMatched: null,
                        textInput: null,
                    };
                    if (!form) {
                        return result;
                    }

                    const radios = Array.from(form.querySelectorAll('input[type="radio"]'));
                    for (const radio of radios) {
                        const label = document.querySelector(`label[for="${radio.id}"]`);
                        const labelText = normalize(
                            (label && label.textContent)
                            || radio.closest('label')?.textContent
                            || radio.parentElement?.textContent
                            || ''
                        );
                        if (
                            !result.radioMatched
                            && (
                                labelText.includes('ruc')
                                || labelText.includes('cédula')
                                || labelText.includes('cedula')
                                || labelText.includes('pasaporte')
                            )
                        ) {
                            radio.checked = true;
                            radio.dispatchEvent(new Event('input', { bubbles: true }));
                            radio.dispatchEvent(new Event('change', { bubbles: true }));
                            radio.click();
                            result.radioMatched = radio.id || radio.name || 'matched';
                        }
                    }

                    const textInputs = Array.from(
                        form.querySelectorAll('input[type="text"]')
                    ).filter((el) => !el.disabled && !el.readOnly && isVisible(el));
                    const target = textInputs.find((input) => {
                        const label = document.querySelector(`label[for="${input.id}"]`);
                        const meta = normalize(
                            `${label?.textContent || ''} ${input.placeholder || ''} ${input.name || ''} ${input.id || ''}`
                        );
                        return (
                            meta.includes('ruc')
                            || meta.includes('cedula')
                            || meta.includes('cédula')
                            || meta.includes('pasaporte')
                            || meta.includes('ident')
                        );
                    }) || (textInputs.length === 1 ? textInputs[0] : null);

                    if (target) {
                        target.focus();
                        target.value = ruc;
                        target.dispatchEvent(new Event('input', { bubbles: true }));
                        target.dispatchEvent(new Event('change', { bubbles: true }));
                        target.dispatchEvent(new Event('blur', { bubbles: true }));
                        result.textInput = {
                            id: target.id || '',
                            name: target.name || '',
                            value: target.value || '',
                        };
                    }
                    return result;
                }
                """,
                {"ruc": self._ruc},
            )

        async def _set_select(
            element_id: str,
            *,
            label: str | None = None,
            value: str | None = None,
        ) -> dict:
            return await page.evaluate(
                """
                ({ elementId, label, value }) => {
                    const el = document.getElementById(elementId);
                    if (!el) {
                        return {
                            found: false,
                            matched: false,
                            value: '',
                            label: '',
                            options: 0,
                        };
                    }
                    let matched = false;
                    const desiredLabel = label == null ? null : String(label).trim();
                    const desiredValue = value == null ? null : String(value);
                    for (const opt of Array.from(el.options || [])) {
                        const optLabel = (opt.textContent || '').trim();
                        const optValue = String(opt.value || '');
                        if (
                            (desiredLabel !== null && optLabel === desiredLabel)
                            || (desiredValue !== null && optValue === desiredValue)
                        ) {
                            el.value = opt.value;
                            matched = true;
                            break;
                        }
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    const idx = el.selectedIndex;
                    const opt = idx >= 0 ? el.options[idx] : null;
                    return {
                        found: true,
                        matched,
                        value: el.value || '',
                        label: opt ? (opt.textContent || '').trim() : '',
                        options: el.options ? el.options.length : 0,
                    };
                }
                """,
                {
                    "elementId": element_id,
                    "label": label,
                    "value": value,
                },
            )
        
        criterio = await _configurar_criterio_ruc()
        self._log.info("criterio_busqueda_configurado", criterio=criterio)

        # Seleccionar Año
        await _set_select('frmPrincipal:ano', label=str(self._anio))
        await asyncio.sleep(2)
        
        # Seleccionar Mes
        mes_str = MESES[self._mes - 1]
        await _set_select('frmPrincipal:mes', label=mes_str)
        await asyncio.sleep(2)

        # Día → Todos
        await _set_select('frmPrincipal:dia', value='0')
        await asyncio.sleep(1)

        # Tipo de comprobante
        await _set_select(
            'frmPrincipal:cmbTipoComprobante',
            label=self._tipo,
        )
        await asyncio.sleep(2)

        self._log.info(
            "filtros_seleccionados",
            filtros=await _snapshot_filtros(),
            esperado={
                "anio": self._anio,
                "mes": mes_str,
                "tipo": self._tipo,
            },
        )

        max_intentos = 12
        attempt_plan = self._build_captcha_attempt_plan(max_intentos)

        # Trap detection: check for honeypots and sitekey changes before CAPTCHA
        if self._settings.trap_detection:
            try:
                trap_result = await run_full_trap_check(
                    page,
                    known_sitekey=self._known_sitekey,
                    extract_asset_fn=self._extraer_site_key_recaptcha,
                )
                if not trap_result.get("safe"):
                    self._log.warning(
                        "trap_warnings_pre_captcha",
                        warnings=trap_result.get("warnings"),
                    )
                # Update known sitekey for future comparison
                sk = trap_result.get("sitekey", {}).get("current_sitekey")
                if sk:
                    self._known_sitekey = sk
            except Exception as exc:
                self._log.debug("trap_detection_error", error=str(exc))

        # Adaptive reorder: sort variants by historical success rate
        if self._adaptive_tracker:
            try:
                provider_attempts = [a for a in attempt_plan if a.get("mode") == "provider"]
                other_attempts = [a for a in attempt_plan if a.get("mode") != "provider"]
                if provider_attempts:
                    provider_attempts = await self._adaptive_tracker.get_ordered_variants(
                        provider_attempts,
                    )
                # Rebuild: native first, then reordered providers, then assisted
                native = [a for a in other_attempts if a.get("mode") == "native"]
                assisted = [a for a in other_attempts if a.get("mode") == "assisted"]
                attempt_plan = native + provider_attempts + assisted
            except Exception as e:
                self._log.warning("adaptive_reorder_error", error=str(e))

        captcha_exitoso = False
        sin_datos_detectado = False
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
                # Advanced behavior simulation before provider attempt
                try:
                    if self._settings.behavior_simulation and self._behavior_profile:
                        await simulate_pre_captcha_activity(
                            page,
                            profile=self._behavior_profile,
                        )
                    else:
                        await simular_actividad_humana(page)
                except Exception as exc:
                    self._log.warning(
                        "actividad_humana_pre_provider_error",
                        error=str(exc),
                    )
                site_key = await self._obtener_site_key_consulta()

                # Verify if we should skip this provider due to a previous ProviderError
                if getattr(self, "_failed_providers", None) and attempt.get("provider") in self._failed_providers:
                    self._log.warning("saltando_proveedor_fallido", provider=attempt["provider"])
                    continue

                import time as _time
                _solve_start = _time.monotonic()
                try:
                    token = await self._resolver_token_con_proveedor(
                        attempt["resolver"],
                        attempt["provider"],
                        site_key,
                        attempt,
                    )
                except ProviderError as e:
                    self._log.error("proveedor_fallo_duro_saltando_variantes", provider=attempt["provider"])
                    if not hasattr(self, "_failed_providers"):
                        self._failed_providers = set()
                    self._failed_providers.add(attempt["provider"])
                    await self._resetear_recaptcha()
                    continue

                _solve_duration = _time.monotonic() - _solve_start

                if not token:
                    self._log.warning(
                        "captcha_provider_sin_token",
                        intento=intento,
                        provider=attempt["provider"],
                        variant=attempt.get("variant"),
                    )
                    await self._resetear_recaptcha()
                    continue

                # Token pre-validation
                if self._settings.token_prevalidation:
                    validation = validate_token(
                        token,
                        variant=attempt.get("variant", ""),
                        provider=attempt.get("provider", ""),
                        solve_duration_sec=_solve_duration,
                    )
                    if not validation.should_submit:
                        self._log.warning(
                            "token_rechazado_pre_validacion",
                            confidence=validation.confidence,
                            reason=validation.reason,
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
            table_container_html_len = query_result.get(
                "tableContainerHtmlLen", 0
            )
            table_rows = query_result.get("tableRows", 0)
            table_html_len = query_result.get("tableHtmlLen", 0)
            error = query_result.get("error")
            empty_partial = bool(query_result.get("emptyPartialResponse"))
            has_token = any(
                (item.get("len") or 0) > 0
                for item in query_result.get("textareas", [])
            )

            if (
                panel_len > 50
                or table_container_html_len > 100
                or table_rows > 0
                or table_html_len > 100
            ):
                panel_html = (
                    query_result.get("panelHtml")
                    or query_result.get("documentsPanelHtml")
                    or query_result.get("tableContainerHtml", "")
                    or query_result.get("tableHtml", "")
                )
                captcha_exitoso = True
                # Record adaptive success
                if self._adaptive_tracker and attempt.get("mode") == "provider":
                    try:
                        await self._adaptive_tracker.record_variant_result(
                            attempt.get("variant", "unknown"),
                            attempt.get("provider", "unknown"),
                            success=True,
                        )
                    except Exception:
                        pass
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
                    # Record adaptive failure with block flag
                    if self._adaptive_tracker:
                        try:
                            await self._adaptive_tracker.record_variant_result(
                                attempt.get("variant", "unknown"),
                                attempt.get("provider", "unknown"),
                                success=False,
                                blocked=True,
                            )
                        except Exception:
                            pass
                await self._resetear_recaptcha()
                await asyncio.sleep(2)
                continue
            if empty_partial:
                self._log.warning(
                    "respuesta_parcial_vacia",
                    intento=intento,
                    provider=attempt.get("provider"),
                    variant=attempt.get("variant"),
                    has_token=has_token,
                )
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

            sin_datos_detectado = True
            self._log.info(
                "sin_datos",
                intento=intento,
                empty_partial=empty_partial,
            )
            break

        if sin_datos_detectado:
            return 0

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

    # ── SOAP XML download ─────────────────────────────────────────────────

    async def _descargar_xml_via_soap(
        self,
        session,
        clave: str,
    ) -> tuple[bytes | None, str | None]:
        """Download XML from SRI SOAP endpoint."""
        import aiohttp

        soap_body = SOAP_TEMPLATE.format(clave=clave)
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "",
        }
        try:
            async with session.post(
                SRI_SOAP_URL,
                data=soap_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    return None, f"HTTP {resp.status}"
                xml_text = await resp.text()
        except Exception as e:
            return None, str(e)

        xml_bytes = self._extraer_xml_de_soap(xml_text)
        if xml_bytes:
            return xml_bytes, None
        return None, self._extraer_error_de_soap(xml_text)

    def _extraer_xml_de_soap(self, soap_response: str) -> bytes | None:
        """Extract the authorized XML from a SOAP response."""
        try:
            root = etree.fromstring(soap_response.encode("utf-8"))
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if isinstance(elem.tag, str) else ""
                if tag == "autorizacion":
                    return etree.tostring(elem, encoding="utf-8")
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if isinstance(elem.tag, str) else ""
                if tag == "comprobante":
                    text = elem.text
                    if text and text.strip():
                        return text.strip().encode("utf-8")
        except Exception as e:
            self._log.warning("soap_xml_parse_error", error=str(e))
        return None

    def _extraer_error_de_soap(self, soap_response: str) -> str:
        try:
            root = etree.fromstring(soap_response.encode("utf-8"))
            estado = mensaje = info = None
            for elem in root.iter():
                tag = elem.tag.split("}")[-1]
                text = (elem.text or "").strip()
                if tag == "estado" and text:
                    estado = text
                elif tag == "mensaje" and text and mensaje is None:
                    mensaje = text
                elif tag == "informacionAdicional" and text and info is None:
                    info = text
            partes = [p for p in [estado, mensaje, info] if p]
            return " | ".join(partes) if partes else "XML no encontrado en SOAP"
        except Exception as e:
            self._log.warning("soap_error_parse", error=str(e))
            return "XML no encontrado en SOAP"

    # ── Estado anómalo ─────────────────────────────────────────────────────

    async def _detectar_estado_anomalo(self) -> EstadoPortal:
        """Check for maintenance or session expiry."""
        page = self._page
        assert page is not None
        try:
            body_text = (await page.evaluate("document.body.innerText") or "").lower()
        except Exception:
            return EstadoPortal.NORMAL

        if "mantenimiento" in body_text:
            return EstadoPortal.MANTENIMIENTO
        if "sesión" in body_text and ("expirada" in body_text or "caducada" in body_text):
            return EstadoPortal.SESION_EXPIRADA
        if "auth/realms" in (page.url or ""):
            return EstadoPortal.SESION_EXPIRADA
        return EstadoPortal.NORMAL

    # ── Pagination + XML download ──────────────────────────────────────────

    async def _procesar_todas_las_paginas(self) -> list[dict]:
        """Iterate through all result pages, download XMLs via SOAP."""
        import aiohttp

        page = self._page
        assert page is not None
        resultados: list[dict] = []
        pagina_actual = 1

        # If we already extracted claves from the first query, start with those
        # Then check for pagination
        async with aiohttp.ClientSession() as http_session:
            while True:
                self._log.info("procesando_pagina", pagina=pagina_actual)

                # Check for anomalous state
                estado = await self._detectar_estado_anomalo()
                if estado == EstadoPortal.SESION_EXPIRADA:
                    raise SRISessionExpiredError("Sesión expirada en paginación")
                if estado == EstadoPortal.MANTENIMIENTO:
                    raise SRIMaintenanceError("SRI en mantenimiento")

                # Extract claves from current page
                claves_pagina = await self._extraer_claves_pagina_actual()
                self._log.info(
                    "claves_en_pagina",
                    pagina=pagina_actual,
                    total=len(claves_pagina),
                )

                resultados_pagina: list[dict] = []
                for idx, clave in enumerate(claves_pagina):
                    fila_data: dict = {
                        "clave_acceso": clave,
                        "pagina": pagina_actual,
                        "numero_fila": idx + 1,
                    }

                    # Skip if already downloaded
                    if self._should_skip_download is not None:
                        if await self._should_skip_download(clave):
                            fila_data["omitido_existente"] = True
                            fila_data["fuente_descarga"] = "cache"
                            resultados_pagina.append(fila_data)
                            if self._collect_results:
                                resultados.append(fila_data)
                            continue

                    # Download via SOAP
                    self._log.info(
                        "descargando_xml",
                        idx=idx + 1,
                        total=len(claves_pagina),
                        clave=clave[:20] + "...",
                    )
                    try:
                        xml_bytes, error = await self._descargar_xml_via_soap(
                            http_session, clave,
                        )
                        if xml_bytes:
                            fila_data["xml_bytes"] = xml_bytes
                            fila_data["fuente_descarga"] = "soap"
                            self._log.info(
                                "xml_descargado",
                                clave=clave[:20] + "...",
                                size=len(xml_bytes),
                            )
                        else:
                            fila_data["error"] = error or "No se pudo descargar XML"
                    except Exception as e:
                        fila_data["error"] = str(e)
                        self._log.warning("xml_download_exception", error=str(e))

                    resultados_pagina.append(fila_data)
                    if self._collect_results:
                        resultados.append(fila_data)

                    await delay_humano(
                        self._settings.delay_min_ms,
                        self._settings.delay_max_ms,
                    )

                self._ultima_pagina_procesada = pagina_actual

                # Persist page results via callback
                if self._on_page_processed and resultados_pagina:
                    await self._on_page_processed(pagina_actual, resultados_pagina)

                # Try next page
                has_next = await self._ir_siguiente_pagina()
                if not has_next:
                    self._log.info("ultima_pagina", pagina=pagina_actual)
                    break

                pagina_actual += 1
                await delay_humano(
                    self._settings.delay_between_pages_ms,
                    self._settings.delay_between_pages_ms + 1000,
                )

        return resultados

    async def _extraer_claves_pagina_actual(self) -> list[str]:
        """Extract 49-digit access keys from the current results page."""
        page = self._page
        assert page is not None

        html = await page.evaluate(
            """
            (() => {
                const panel = document.getElementById('frmPrincipal:tablaCompRecibidos')
                    || document.getElementById('frmPrincipal:pnldocumentosrecibidos')
                    || document.getElementById('frmPrincipal:panelListaComprobantes');
                return panel ? panel.innerHTML : document.body.innerHTML;
            })()
            """
        )
        claves = list(set(re.findall(r'\b(\d{49})\b', html or "")))
        return claves

    async def _ir_siguiente_pagina(self) -> bool:
        """Click the next page button if available. Returns True if navigated."""
        page = self._page
        assert page is not None

        has_next = await page.evaluate(
            """
            (() => {
                const selectors = [
                    'a.ui-paginator-next:not(.ui-state-disabled)',
                    'button.ui-paginator-next:not(.ui-state-disabled)',
                    'span.ui-paginator-next:not(.ui-state-disabled)',
                    '.rf-ds-btn-next:not(.rf-ds-dis)',
                    "[id$='_ds_next']:not(.rf-ds-dis)",
                ];
                // Scope to the results table paginator
                const scopes = [
                    document.getElementById('frmPrincipal:tablaCompRecibidos_paginator_bottom'),
                    document.getElementById('frmPrincipal:tablaCompRecibidos'),
                    document.getElementById('frmPrincipal:pnldocumentosrecibidos'),
                    document.body,
                ];
                for (const scope of scopes) {
                    if (!scope) continue;
                    for (const sel of selectors) {
                        const btn = scope.querySelector(sel);
                        if (btn) {
                            btn.click();
                            return true;
                        }
                    }
                }
                return false;
            })()
            """
        )
        if has_next:
            # Wait for table to update
            await asyncio.sleep(2)
            await self._wait_for_condition(
                """
                (() => {
                    const tbody = document.getElementById('frmPrincipal:tablaCompRecibidos_data')
                        || document.querySelector('tbody.ui-datatable-data');
                    return tbody && tbody.children.length > 0;
                })()
                """,
                timeout_sec=10.0,
            )
        return bool(has_next)
